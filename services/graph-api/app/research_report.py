"""Honest, replay-bound summary for one compiled research activity."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import Field, model_validator

from app.admission import AdmissionDecision
from app.analysis_versions import canonical_json
from app.candidate_verification import CANDIDATE_CHECKER_VERSION, CandidateCheckResult
from app.numerical_verification import NumericalFixtureReceipt
from app.problem_spec import FrozenInput
from app.replay_bundle import (
    CompilerReplayBundle,
    ReplaySourceReference,
    replay_candidate_from_bundle,
)

_HASH = r"^[0-9a-f]{64}$"


class CheckEvidence(FrozenInput):
    check_id: str = Field(pattern=r"^chk_[0-9a-f]{32}$")
    checker_version: str = Field(min_length=1, max_length=100)
    claim: str = Field(min_length=1, max_length=500)
    scope: Literal["candidate_structure", "feature_kernel_identity"]
    replay_status: Literal["replayed", "historical"]
    outcome: Literal["supported", "refuted", "unknown", "unsupported", "timeout", "error"]
    type_status: Literal["well_typed", "ill_typed", "unknown"]
    domain_status: Literal[
        "discharged", "conditional", "unresolved", "contradictory", "unsupported"
    ]
    numerical_status: Literal["passed_suite", "counterexample", "not_run", "timeout", "error"]
    empirical_status: Literal[
        "supported_on_protocol", "failed_on_protocol", "inconclusive", "not_run"
    ]


class PolicyEvidence(FrozenInput):
    decision_id: str = Field(pattern=r"^pol_[0-9a-f]{32}$")
    action: str = Field(min_length=1, max_length=100)
    outcome: Literal["allowed", "conditional", "denied"]
    reasons: tuple[str, ...] = Field(max_length=32)
    replay_status: Literal["replayed", "stored_only"]


class FixtureEvidence(FrozenInput):
    result_id: str = Field(pattern=r"^num_[0-9a-f]{32}$")
    outcome: Literal["passed_suite", "counterexample", "unknown", "unsupported", "timeout", "error"]
    seed: int = Field(ge=0, strict=True)
    scope: Literal["synthetic_feature_kernel_fixture"]
    replay_status: Literal["replayed", "stored_only"]
    performance_claim: Literal[False] = False


class CompilerReplayReport(FrozenInput):
    schema_version: Literal["compiler-replay-report.v1"] = "compiler-replay-report.v1"
    report_hash: str = Field(pattern=_HASH)
    status: Literal["partial"] = "partial"
    scope: Literal["compiler_replay_only"] = "compiler_replay_only"
    workspace_id: str = Field(min_length=1, max_length=200)
    candidate_id: str = Field(pattern=r"^cand_[0-9a-f]{32}$")
    candidate_hash: str = Field(pattern=_HASH)
    activity_id: str = Field(pattern=r"^act_[0-9a-f]{32}$")
    bundle_hash: str = Field(pattern=_HASH)
    operator: str = Field(min_length=1, max_length=100)
    operator_version: str = Field(min_length=1, max_length=50)
    semantics_class: Literal["preserving", "approximation", "hypothesis_changing"]
    source_refs: tuple[ReplaySourceReference, ...] = Field(min_length=1, max_length=16)
    lineage_assertion_ids: tuple[str, ...] = Field(max_length=200)
    compatibility_mapping_id: str = Field(min_length=1, max_length=200)
    proposal_review_status: Literal["not_applicable", "accepted_for_compilation"]
    compiler_replay: Literal["reproduced"] = "reproduced"
    checks: tuple[CheckEvidence, ...] = Field(max_length=32)
    policy_decisions: tuple[PolicyEvidence, ...] = Field(max_length=32)
    numerical_fixtures: tuple[FixtureEvidence, ...] = Field(max_length=32)
    empirical_experiment: Literal["not_run"] = "not_run"
    limitations: tuple[str, ...] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_report_identity(self) -> CompilerReplayReport:
        if len({item.equation_id for item in self.source_refs}) != len(self.source_refs):
            raise ValueError("Report source references must be unique.")
        if len(set(self.lineage_assertion_ids)) != len(self.lineage_assertion_ids):
            raise ValueError("Report lineage assertion references must be unique.")
        identity = self.model_dump(mode="json", exclude={"report_hash"})
        expected = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
        if self.report_hash != expected:
            raise ValueError("Report hash does not match its immutable content.")
        return self


def build_compiler_replay_report(bundle: CompilerReplayBundle) -> CompilerReplayReport:
    """Recompile locally, then summarize evidence without inventing a V3 result."""
    replayed = replay_candidate_from_bundle(bundle)
    if replayed != bundle.candidate:
        raise ValueError("Compiler replay did not reproduce the frozen candidate.")

    checks = tuple(_check_evidence(item) for item in bundle.candidate_checks)
    replayed_policy_ids = {item.decision_id for item in bundle.admission_replay_inputs}
    policies = tuple(
        _policy_evidence(item, replayed=item.decision_id in replayed_policy_ids)
        for item in bundle.admission_decisions
    )
    fixtures = tuple(_fixture_evidence(item) for item in bundle.numerical_fixtures)
    payload = {
        "schema_version": "compiler-replay-report.v1",
        "status": "partial",
        "scope": "compiler_replay_only",
        "workspace_id": bundle.workspace_id,
        "candidate_id": bundle.candidate.candidate_id,
        "candidate_hash": bundle.candidate.content_hash,
        "activity_id": bundle.activity.activity_id,
        "bundle_hash": bundle.bundle_hash,
        "operator": bundle.activity.operator,
        "operator_version": bundle.activity.operator_version,
        "semantics_class": bundle.activity.semantics_class,
        "source_refs": [item.model_dump(mode="json") for item in bundle.sources],
        "lineage_assertion_ids": sorted(item.assertion_id for item in bundle.lineage_assertions),
        "compatibility_mapping_id": bundle.activity.mapping_id,
        "proposal_review_status": (
            "not_applicable" if bundle.proposal is None else "accepted_for_compilation"
        ),
        "compiler_replay": "reproduced",
        "checks": [item.model_dump(mode="json") for item in checks],
        "policy_decisions": [item.model_dump(mode="json") for item in policies],
        "numerical_fixtures": [item.model_dump(mode="json") for item in fixtures],
        "empirical_experiment": "not_run",
        "limitations": [
            (
                "The compiler is replayed; when present, current-version static checks "
                "are rerun and matched to their receipts. Historical checker versions "
                "are not rerun."
            ),
            (
                "Admission decisions with frozen server-input snapshots and completed "
                "current-version numerical fixtures are rerun. Legacy policy records and "
                "incomplete numerical outcomes remain stored receipts. Bundle and report "
                "hashes are not signatures; synthetic checks are not performance evidence."
            ),
            (
                "No frozen dataset/model artifact, matched-control evaluator, or "
                "protected holdout run is bound here."
            ),
        ],
    }
    report_hash = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return CompilerReplayReport.model_validate(payload | {"report_hash": report_hash})


def _check_evidence(item: CandidateCheckResult) -> CheckEvidence:
    return CheckEvidence(
        check_id=item.check_id,
        checker_version=item.checker_version,
        claim=item.claim,
        scope=item.scope,
        replay_status=(
            "replayed" if item.checker_version == CANDIDATE_CHECKER_VERSION else "historical"
        ),
        outcome=item.outcome,
        type_status=item.vector.type,
        domain_status=item.vector.domain,
        numerical_status=item.vector.numerical,
        empirical_status=item.vector.empirical,
    )


def _policy_evidence(item: AdmissionDecision, *, replayed: bool) -> PolicyEvidence:
    return PolicyEvidence(
        decision_id=item.decision_id,
        action=item.action,
        outcome=item.outcome,
        reasons=item.reasons,
        replay_status="replayed" if replayed else "stored_only",
    )


def _fixture_evidence(item: NumericalFixtureReceipt) -> FixtureEvidence:
    return FixtureEvidence(
        result_id=item.result_id,
        outcome=item.outcome,
        seed=item.seed,
        scope=item.fixture_scope,
        replay_status=(
            "replayed"
            if item.suite_version == "feature-kernel-fixture.v1"
            and item.outcome in {"passed_suite", "counterexample"}
            else "stored_only"
        ),
        performance_claim=item.performance_claim,
    )
