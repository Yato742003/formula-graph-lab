"""FGL-V0 policy decisions; callers must build context from server-owned records."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Literal

from pydantic import Field, model_serializer, model_validator

from app.analysis_versions import canonical_json
from app.problem_spec import FrozenInput, ProblemSpecSnapshot
from app.research_compiler import CompiledCandidate, _verify_candidate_identity
from app.verification import VerificationVector

ADMISSION_POLICY_VERSION = "admission-policy.v1"
MAX_RETRIES = 2

AdmissionAction = Literal[
    "can_run_numerical",
    "can_run_experiment",
    "can_enter_parent_pool",
    "can_publish_claim",
]
ClaimScope = Literal["mathematical", "restricted_domain", "protocol"]


class AdmissionContext(FrozenInput):
    """Internal snapshot of authoritative evidence; never deserialize from a request."""

    candidate: CompiledCandidate
    spec: ProblemSpecSnapshot
    verification: VerificationVector | None = None
    discharged_obligation_names: tuple[str, ...] = Field(default=(), max_length=32)
    symbolic_result_ids: tuple[str, ...] = Field(default=(), max_length=100)
    numerical_result_ids: tuple[str, ...] = Field(default=(), max_length=100)
    empirical_result_ids: tuple[str, ...] = Field(default=(), max_length=100)
    mapping_freshness: Literal["current", "stale", "unknown"] = "unknown"
    mapping_usable: bool = False
    runtime_artifacts_verified: bool = False
    protocol_frozen: bool = False
    quota_available: bool = False
    restricted_domain_reviewed: bool = False
    quality_constraints_met: bool = False
    protocol_evidence_scope: Literal["none", "search", "holdout"] = "none"
    review_result_ids: tuple[str, ...] = Field(default=(), max_length=16)
    retry_count: int = Field(default=0, ge=0, le=1000)
    retry_reason: Literal["none", "timeout", "infrastructure_error", "refuted"] = "none"

    @model_validator(mode="after")
    def validate_checker_discharges(self) -> AdmissionContext:
        if set(self.discharged_obligation_names) - {"kernel_identity"}:
            raise ValueError("Only registered checker obligations may be discharged.")
        if self.discharged_obligation_names and not self.symbolic_result_ids:
            raise ValueError("A discharged obligation must reference its checker result.")
        return self

    def validate_scope(self) -> None:
        if (
            self.candidate.workspace_id != self.spec.workspace_id
            or self.candidate.problem_spec_id != self.spec.spec_id
            or self.candidate.problem_spec_hash != self.spec.content_hash
        ):
            raise ValueError("Candidate and frozen ProblemSpec scope do not match.")


class AdmissionDecision(FrozenInput):
    decision_id: str = Field(pattern=r"^pol_[0-9a-f]{32}$")
    policy_version: Literal["admission-policy.v1"] = ADMISSION_POLICY_VERSION
    action: AdmissionAction
    claim_scope: ClaimScope | None = None
    candidate_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    allowed: bool
    outcome: Literal["allowed", "conditional", "denied"]
    rule_id: str = Field(min_length=1, max_length=100)
    reasons: tuple[str, ...] = Field(max_length=32)
    input_result_ids: tuple[str, ...] = Field(max_length=100)
    actor_id: str = Field(min_length=1, max_length=200)
    decided_at: datetime


class AdmissionEvaluationRequest(FrozenInput):
    action: AdmissionAction
    claim_scope: ClaimScope | None = None

    @model_validator(mode="after")
    def validate_claim_scope(self) -> AdmissionEvaluationRequest:
        if self.action == "can_publish_claim" and self.claim_scope is None:
            return self.model_copy(update={"claim_scope": "mathematical"})
        if self.action != "can_publish_claim" and self.claim_scope is not None:
            raise ValueError("claim_scope is only valid for can_publish_claim.")
        return self


class AdmissionEvaluationResponse(FrozenInput):
    decision: AdmissionDecision
    replayed: bool


class AdmissionReplayInput(FrozenInput):
    """Minimal immutable V0 input snapshot; candidate and spec live in the bundle."""

    schema_version: Literal["admission-replay-input.v1"] = "admission-replay-input.v1"
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision_id: str = Field(pattern=r"^pol_[0-9a-f]{32}$")
    candidate_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    problem_spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    verification: VerificationVector | None = None
    discharged_obligation_names: tuple[str, ...] = Field(default=(), max_length=32)
    symbolic_result_ids: tuple[str, ...] = Field(default=(), max_length=100)
    numerical_result_ids: tuple[str, ...] = Field(default=(), max_length=100)
    empirical_result_ids: tuple[str, ...] = Field(default=(), max_length=100)
    mapping_freshness: Literal["current", "stale", "unknown"] = "unknown"
    mapping_usable: bool = False
    runtime_artifacts_verified: bool = False
    protocol_frozen: bool = False
    quota_available: bool = False
    restricted_domain_reviewed: bool = False
    quality_constraints_met: bool = False
    protocol_evidence_scope: Literal["none", "search", "holdout"] = "none"
    review_result_ids: tuple[str, ...] = Field(default=(), max_length=16)
    retry_count: int = Field(default=0, ge=0, le=1000)
    retry_reason: Literal["none", "timeout", "infrastructure_error", "refuted"] = "none"

    @model_validator(mode="after")
    def validate_identity(self) -> AdmissionReplayInput:
        payload = self.model_dump(mode="json", exclude={"input_hash"})
        if self.protocol_evidence_scope == "none":
            payload.pop("protocol_evidence_scope", None)
        if not self.review_result_ids:
            payload.pop("review_result_ids", None)
        expected = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
        if self.input_hash != expected:
            raise ValueError("Admission replay input hash does not match its content.")
        return self

    @model_serializer(mode="wrap")
    def serialize_input(self, handler):
        payload = handler(self)
        if self.protocol_evidence_scope == "none":
            payload.pop("protocol_evidence_scope", None)
        if not self.review_result_ids:
            payload.pop("review_result_ids", None)
        return payload


def make_admission_replay_input(
    context: AdmissionContext,
    decision: AdmissionDecision,
) -> AdmissionReplayInput:
    context.validate_scope()
    payload = {
        "schema_version": "admission-replay-input.v1",
        "decision_id": decision.decision_id,
        "candidate_hash": context.candidate.content_hash,
        "problem_spec_hash": context.spec.content_hash,
        **context.model_dump(
            mode="json",
            exclude={"candidate", "spec"},
        ),
    }
    if context.protocol_evidence_scope == "none":
        payload.pop("protocol_evidence_scope")
    if not context.review_result_ids:
        payload.pop("review_result_ids")
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    replay_input = AdmissionReplayInput.model_validate(payload | {"input_hash": digest})
    replay_admission(replay_input, decision, context.candidate, context.spec)
    return replay_input


def replay_admission(
    replay_input: AdmissionReplayInput,
    decision: AdmissionDecision,
    candidate: CompiledCandidate,
    spec: ProblemSpecSnapshot,
) -> AdmissionDecision:
    if (
        replay_input.decision_id != decision.decision_id
        or replay_input.candidate_hash != candidate.content_hash
        or replay_input.problem_spec_hash != spec.content_hash
        or decision.candidate_id != candidate.candidate_id
        or decision.workspace_id != candidate.workspace_id
        or decision.decided_at.tzinfo is None
        or decision.decided_at.utcoffset() is None
    ):
        raise ValueError("Admission replay input does not match its decision scope.")
    context = AdmissionContext(
        candidate=candidate,
        spec=spec,
        **replay_input.model_dump(
            mode="python",
            exclude={
                "schema_version",
                "input_hash",
                "decision_id",
                "candidate_hash",
                "problem_spec_hash",
            },
        ),
    )
    reproduced = decide_admission(
        context,
        action=decision.action,
        claim_scope=decision.claim_scope or "mathematical",
        actor_id=decision.actor_id,
        now=decision.decided_at,
    )
    if reproduced != decision:
        raise ValueError("Admission decision does not reproduce from its frozen input.")
    return reproduced


def decide_admission(
    context: AdmissionContext,
    *,
    action: AdmissionAction,
    actor_id: str,
    claim_scope: ClaimScope = "mathematical",
    now: datetime | None = None,
) -> AdmissionDecision:
    """Evaluate one gate. No client-supplied assumptions or verdicts are inputs."""
    _verify_candidate_identity(context.candidate)
    context.validate_scope()
    if not actor_id.strip():
        raise ValueError("A trusted actor identity is required.")

    vector = context.verification
    input_result_ids = _result_ids(context, action, claim_scope)
    reasons: list[str] = []
    success_reason: str | None = None
    outcome: Literal["allowed", "conditional", "denied"] = "denied"
    rule_id = "FGL-V0-DENY-DEFAULT"

    obligations = context.candidate.obligations
    discharged = set(context.discharged_obligation_names)
    scoped = context.protocol_evidence_scope != "none" and context.restricted_domain_reviewed
    unresolved = [
        item.name for item in obligations
        if item.status == "unresolved" and item.name not in discharged
        and not (scoped and item.name == "normalization_domain")
    ]
    conditional = [
        item.name for item in obligations
        if item.status == "conditional" and item.name not in discharged
    ]

    is_computation = action != "can_publish_claim"
    if context.mapping_freshness != "current":
        reasons.append(f"mapping_{context.mapping_freshness}")
    elif not context.mapping_usable:
        reasons.append("mapping_not_usable")
    if is_computation and not context.quota_available:
        reasons.append("budget_or_quota_unavailable")
    if action == "can_run_numerical" and context.retry_reason == "refuted":
        reasons.append("scientific_refutation_is_not_retryable")
    if (
        action == "can_run_numerical"
        and context.retry_reason in {"timeout", "infrastructure_error"}
        and context.retry_count >= MAX_RETRIES
    ):
        reasons.append("retry_limit_reached")
    if action == "can_run_numerical":
        if not _static_ready(context, reasons):
            pass
        elif not context.runtime_artifacts_verified:
            reasons.append("runtime_artifacts_not_verified")
        else:
            outcome = "conditional" if conditional or vector.domain == "conditional" else "allowed"
            rule_id = (
                "FGL-V0-NUMERICAL-RESTRICTED"
                if outcome == "conditional"
                else "FGL-V0-NUMERICAL-READY"
            )
            success_reason = (
                "restricted_domain_only" if outcome == "conditional" else "numerical_inputs_ready"
            )
    elif action == "can_run_experiment":
        if not _static_ready(context, reasons):
            pass
        elif not context.runtime_artifacts_verified or not context.protocol_frozen:
            reasons.append("experiment_artifacts_or_protocol_unverified")
        elif vector is None or vector.numerical != "passed_suite":
            reasons.append("numerical_suite_not_passed")
        elif not context.numerical_result_ids:
            reasons.append("numerical_result_ref_missing")
        elif unresolved:
            reasons.extend(f"unresolved_obligation:{name}" for name in unresolved)
        elif conditional and not context.restricted_domain_reviewed:
            reasons.extend(f"conditional_obligation_not_reviewed:{name}" for name in conditional)
        else:
            outcome = "conditional" if conditional else "allowed"
            rule_id = "FGL-V0-EXPERIMENT-RESTRICTED" if conditional else "FGL-V0-EXPERIMENT-READY"
            success_reason = "protocol_and_numerical_gates_passed"
    elif action == "can_enter_parent_pool":
        if context.protocol_evidence_scope == "holdout":
            reasons.append("holdout_cannot_enter_search_parent_pool")
        elif not _static_ready(context, reasons, allow_conditional=scoped):
            pass
        elif not context.runtime_artifacts_verified or not context.protocol_frozen:
            reasons.append("experiment_artifacts_or_protocol_unverified")
        elif (
            vector is None
            or vector.numerical != "passed_suite"
            or vector.empirical != "supported_on_protocol"
        ):
            reasons.append("empirical_protocol_not_supported")
        elif not context.numerical_result_ids or not context.empirical_result_ids:
            reasons.append("experiment_result_ref_missing")
        elif vector.human_review != "accepted_scope":
            reasons.append("human_scope_review_not_accepted")
        elif not context.quality_constraints_met:
            reasons.append("frozen_quality_constraints_not_met")
        elif unresolved or (conditional and not scoped):
            reasons.extend(
                f"obligation_not_discharged:{item.name}"
                for item in obligations
                if item.status != "discharged"
            )
        else:
            outcome = "allowed"
            rule_id = "FGL-V0-PARENT-ELIGIBLE"
            success_reason = (
                "search_protocol_scope_only" if scoped
                else "protocol_quality_and_review_gates_passed"
            )
    else:  # can_publish_claim
        if vector is None:
            reasons.append("verification_vector_missing")
        elif vector.human_review != "accepted_scope":
            reasons.append("human_scope_review_not_accepted")
        elif unresolved:
            reasons.extend(f"unresolved_obligation:{name}" for name in unresolved)
        elif claim_scope == "mathematical" and not _static_ready(
            context, reasons, allow_conditional=False
        ):
            pass
        elif claim_scope == "mathematical" and vector.symbolic != "supported":
            reasons.append("scoped_symbolic_support_not_established")
        elif claim_scope == "restricted_domain" and (
            vector.symbolic != "supported"
            or not _static_ready(context, reasons)
            or not context.restricted_domain_reviewed
        ):
            reasons.append("reviewed_restricted_domain_support_not_established")
        elif claim_scope == "protocol" and (
            not _static_ready(context, reasons)
            or not context.runtime_artifacts_verified
            or not context.protocol_frozen
            or vector.numerical != "passed_suite"
            or vector.empirical != "supported_on_protocol"
            or not context.numerical_result_ids
            or not context.empirical_result_ids
            or not context.quality_constraints_met
        ):
            reasons.append("protocol_evidence_not_supported")
        elif conditional or vector.domain == "conditional":
            outcome = "conditional"
            rule_id = "FGL-V0-CLAIM-RESTRICTED"
            success_reason = "claim_must_state_reviewed_restricted_domain"
        else:
            outcome = "allowed"
            rule_id = "FGL-V0-CLAIM-SCOPED"
            success_reason = "claim_scope_has_supporting_evidence"

    if reasons:
        outcome = "denied"
        rule_id = "FGL-V0-DENY-DEFAULT"
    elif success_reason is not None:
        reasons.append(success_reason)
    allowed = outcome != "denied"
    decided_at = (now or datetime.now(UTC)).astimezone(UTC)
    identity = {
        "policy_version": ADMISSION_POLICY_VERSION,
        "action": action,
        "claim_scope": claim_scope if action == "can_publish_claim" else None,
        "candidate_id": context.candidate.candidate_id,
        "candidate_hash": context.candidate.content_hash,
        "result_ids": input_result_ids,
        "rule_id": rule_id,
        "outcome": outcome,
        "reasons": reasons,
        "actor_id": actor_id,
        "decided_at": decided_at.isoformat(),
    }
    decision_id = "pol_" + hashlib.sha256(canonical_json(identity).encode()).hexdigest()[:32]
    return AdmissionDecision(
        decision_id=decision_id,
        action=action,
        claim_scope=claim_scope if action == "can_publish_claim" else None,
        candidate_id=context.candidate.candidate_id,
        workspace_id=context.candidate.workspace_id,
        allowed=allowed,
        outcome=outcome,
        rule_id=rule_id,
        reasons=tuple(reasons),
        input_result_ids=input_result_ids,
        actor_id=actor_id,
        decided_at=decided_at,
    )


def _static_ready(
    context: AdmissionContext,
    reasons: list[str],
    *,
    allow_conditional: bool = True,
) -> bool:
    ready = True
    vector = context.verification
    if vector is None:
        reasons.append("verification_vector_missing")
        ready = False
    if not context.symbolic_result_ids:
        reasons.append("symbolic_result_ref_missing")
        ready = False
    if vector is not None and (vector.parse != "supported" or vector.type != "well_typed"):
        reasons.append("static_parse_or_type_gate_not_passed")
        ready = False
    if vector is not None and vector.domain in {"contradictory", "unsupported", "unresolved"}:
        reasons.append("domain_gate_not_passed")
        ready = False
    if vector is not None and vector.domain == "conditional" and (
        not allow_conditional or not context.restricted_domain_reviewed
    ):
        reasons.append("conditional_domain_not_reviewed")
        ready = False
    unresolved = [
        item.name for item in context.candidate.obligations
        if item.status == "unresolved" and item.name not in context.discharged_obligation_names
        and not (context.protocol_evidence_scope != "none"
                 and context.restricted_domain_reviewed
                 and item.name == "normalization_domain")
    ]
    if unresolved:
        reasons.extend(f"unresolved_obligation:{name}" for name in unresolved)
        ready = False
    conditional = [
        item.name for item in context.candidate.obligations
        if item.status == "conditional" and item.name not in context.discharged_obligation_names
    ]
    if conditional and (not allow_conditional or not context.restricted_domain_reviewed):
        reasons.extend(f"conditional_obligation_not_reviewed:{name}" for name in conditional)
        ready = False
    return ready


def _result_ids(
    context: AdmissionContext,
    action: AdmissionAction,
    claim_scope: ClaimScope,
) -> tuple[str, ...]:
    if action == "can_run_numerical" or (
        action == "can_publish_claim" and claim_scope in {"mathematical", "restricted_domain"}
    ):
        return context.symbolic_result_ids
    if action == "can_run_experiment":
        return (*context.symbolic_result_ids, *context.numerical_result_ids)
    if action == "can_enter_parent_pool" or (
        action == "can_publish_claim" and claim_scope == "protocol"
    ):
        return (
            *context.symbolic_result_ids,
            *context.numerical_result_ids,
            *context.empirical_result_ids,
            *context.review_result_ids,
        )
    return ()
