"""Workspace-scoped, source-referential bundle for deterministic compiler replay."""

from __future__ import annotations

import hashlib
import math
from datetime import date, datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_serializer, model_validator

from app.admission import AdmissionDecision, AdmissionReplayInput, replay_admission
from app.analysis_versions import canonical_json
from app.candidate_verification import (
    CANDIDATE_CHECKER_VERSION,
    CandidateCheckResult,
    verify_compiled_candidate,
)
from app.compatibility import CompatibilityReview
from app.lineage import LineageAssertionRecord
from app.numerical_verification import (
    NumericalFixtureReceipt,
    build_numerical_suite_input,
    numerical_fixture_input_hash,
)
from app.numerical_worker import SUITE_VERSION, _evaluate
from app.problem_spec import FrozenInput
from app.proposals import ProposalReviewRecord, ResearchProposal, parse_source_span_id
from app.research_case import (
    ImplementationBindingReceipt,
    ResearchCaseReceipt,
    ResearchProtocolReview,
    protocol_admission_context,
    protocol_scope_key,
    run_registered_research_case,
)
from app.research_case_worker import evaluate as evaluate_research_case
from app.research_compiler import (
    CompileContext,
    CompiledCandidate,
    TransformationActivity,
    _verify_candidate_identity,
    replay_compiled_candidate,
)
from app.symbol_contracts import ContractReview

_HASH = r"^[0-9a-f]{64}$"
MAX_BUNDLE_BYTES = 2 * 1024 * 1024
FLOAT64_REPLAY_ABS_TOL = 1e-12


def _same_scientific_result(actual, expected):
    """Pinned float64 replay tolerance only; decisions/thresholds/IDs stay exact."""
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            _same_scientific_result(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _same_scientific_result(a, e) for a, e in zip(actual, expected, strict=True)
        )
    if isinstance(expected, float):
        return math.isclose(actual, expected, rel_tol=0, abs_tol=FLOAT64_REPLAY_ABS_TOL)
    return actual == expected


class ReplaySourceReference(FrozenInput):
    equation_id: str = Field(min_length=1, max_length=200)
    equation_source_hash: str = Field(pattern=_HASH)
    paper_id: str = Field(pattern=r"^\d{4}\.\d{4,5}$")
    paper_version: int = Field(ge=1, strict=True)
    paper_version_id: str = Field(min_length=1, max_length=200)
    paper_html_hash: str = Field(pattern=_HASH)
    source_url: str = Field(min_length=1, max_length=2048)
    anchor: str = Field(min_length=1, max_length=200)
    anchor_is_source: bool
    resolver: Literal["arxiv-html-anchor-sha256.v1"] = "arxiv-html-anchor-sha256.v1"

    @field_validator("source_url")
    @classmethod
    def require_allowlisted_https_source(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"arxiv.org", "export.arxiv.org"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or not parsed.path.startswith("/html/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Replay source must be a canonical HTTPS arXiv HTML URL.")
        return value

    @model_validator(mode="after")
    def require_source_anchor(self) -> ReplaySourceReference:
        parsed = urlsplit(self.source_url)
        if not self.anchor_is_source:
            raise ValueError("Replay source must resolve from an exact HTML anchor.")
        if parsed.path not in {
            f"/html/{self.paper_id}",
            f"/html/{self.paper_id}v{self.paper_version}",
        }:
            raise ValueError("Replay source URL must match its exact paper revision.")
        return self


class CompilerReplayBundle(FrozenInput):
    schema_version: Literal["compiler-replay-bundle.v1"] = "compiler-replay-bundle.v1"
    workspace_id: str = Field(min_length=1, max_length=200)
    candidate: CompiledCandidate
    activity: TransformationActivity
    parent_candidates: tuple[CompiledCandidate, ...] = Field(max_length=4)
    sources: tuple[ReplaySourceReference, ...] = Field(min_length=1, max_length=16)
    lineage_assertions: tuple[LineageAssertionRecord, ...] = Field(max_length=200)
    compatibility_reviews: tuple[CompatibilityReview, ...] = Field(max_length=16)
    contract_reviews: tuple[ContractReview, ...] = Field(max_length=16)
    proposal: ResearchProposal | None = None
    proposal_review: ProposalReviewRecord | None = None
    candidate_checks: tuple[CandidateCheckResult, ...] = Field(max_length=32)
    admission_decisions: tuple[AdmissionDecision, ...] = Field(max_length=32)
    admission_replay_inputs: tuple[AdmissionReplayInput, ...] = Field(
        default=(), max_length=32,
    )
    numerical_fixtures: tuple[NumericalFixtureReceipt, ...] = Field(max_length=32)
    implementation_bindings: tuple[ImplementationBindingReceipt, ...] = Field(
        default=(), max_length=32,
    )
    research_cases: tuple[ResearchCaseReceipt, ...] = Field(default=(), max_length=32)
    protocol_reviews: tuple[ResearchProtocolReview, ...] = Field(default=(), max_length=32)
    bundle_hash: str = Field(pattern=_HASH)

    @model_serializer(mode="wrap")
    def serialize_bundle(self, handler):
        payload = handler(self)
        if not self.protocol_reviews:
            payload.pop("protocol_reviews", None)
        return payload

    @model_validator(mode="after")
    def validate_bundle(self) -> CompilerReplayBundle:
        if self.workspace_id != self.candidate.workspace_id or (
            self.activity.workspace_id != self.workspace_id
            or self.activity.candidate_id != self.candidate.candidate_id
            or self.activity.candidate_hash != self.candidate.content_hash
        ):
            raise ValueError("Replay bundle candidate and activity scopes do not match.")
        _verify_candidate_identity(self.candidate)

        expected_parents = {
            parent.entity_id: parent.content_hash
            for parent in self.candidate.parents
            if parent.entity_id.startswith("cand_")
        }
        actual_parents = {item.candidate_id: item.content_hash for item in self.parent_candidates}
        if len(actual_parents) != len(self.parent_candidates) or actual_parents != expected_parents:
            raise ValueError("Replay bundle does not contain the exact candidate parents.")
        for parent in self.parent_candidates:
            if parent.workspace_id != self.workspace_id:
                raise ValueError("Replay bundle parent belongs to another workspace.")
            _verify_candidate_identity(parent)
        candidate_parent = next(
            (
                parent for parent in self.parent_candidates
                if self.candidate.parents
                and parent.candidate_id == self.candidate.parents[0].entity_id
            ),
            None,
        )
        for check in self.candidate_checks:
            if (
                check.workspace_id != self.workspace_id
                or check.candidate_id != self.candidate.candidate_id
                or check.candidate_hash != self.candidate.content_hash
            ):
                raise ValueError("Replay bundle contains a check for another candidate.")
        if any(
            item.workspace_id != self.workspace_id
            or item.candidate_id != self.candidate.candidate_id
            for item in self.admission_decisions
        ):
            raise ValueError("Replay bundle contains an admission for another candidate.")
        decisions_by_id = {item.decision_id: item for item in self.admission_decisions}
        replay_inputs_by_id = {
            item.decision_id: item for item in self.admission_replay_inputs
        }
        if (
            len(decisions_by_id) != len(self.admission_decisions)
            or len(replay_inputs_by_id) != len(self.admission_replay_inputs)
            or set(replay_inputs_by_id) - set(decisions_by_id)
        ):
            raise ValueError("Replay bundle admission inputs do not match its decisions.")
        if any(
            item.workspace_id != self.workspace_id
            or item.candidate_id != self.candidate.candidate_id
            or item.candidate_hash != self.candidate.content_hash
            or item.problem_spec_id != self.candidate.problem_spec_id
            or item.problem_spec_hash != self.candidate.problem_spec_hash
            or item.parent_refs != self.candidate.parents
            or item.performance_claim
            for item in self.numerical_fixtures
        ):
            raise ValueError("Replay bundle contains a misbound or promotional numerical fixture.")

        bindings_by_id = {item.binding_id: item for item in self.implementation_bindings}
        if len(bindings_by_id) != len(self.implementation_bindings):
            raise ValueError("Replay bundle implementation bindings must be unique.")
        for binding in self.implementation_bindings:
            if (
                binding.workspace_id != self.workspace_id
                or binding.candidate_id != self.candidate.candidate_id
                or binding.candidate_hash != self.candidate.content_hash
                or binding.problem_spec_id != self.candidate.problem_spec_id
                or binding.problem_spec_hash != self.candidate.problem_spec_hash
                or binding.parent_refs != self.candidate.parents
                or candidate_parent is None
                or binding.source_parent_refs != candidate_parent.parents
            ):
                raise ValueError("Replay bundle contains a misbound implementation binding.")
        result_ids = {item.result_id for item in self.research_cases}
        if len(result_ids) != len(self.research_cases):
            raise ValueError("Replay bundle research cases must be unique.")
        for result in self.research_cases:
            binding = bindings_by_id.get(result.binding_id)
            if (
                result.workspace_id != self.workspace_id
                or result.candidate_id != self.candidate.candidate_id
                or result.candidate_hash != self.candidate.content_hash
                or result.problem_spec_id != self.candidate.problem_spec_id
                or result.problem_spec_hash != self.candidate.problem_spec_hash
                or result.parent_refs != self.candidate.parents
                or binding is None
                or result.binding_hash != binding.binding_hash
                or result.protocol_hash != binding.protocol_hash
            ):
                raise ValueError("Replay bundle contains a misbound research case.")

        if self.activity.compiler_context_json is None:
            raise ValueError("Replay bundle requires a versioned compiler context.")
        context = CompileContext.model_validate_json(self.activity.compiler_context_json)
        reviews_by_id = {item.review_id: item for item in self.protocol_reviews}
        if len(reviews_by_id) != len(self.protocol_reviews):
            raise ValueError("Protocol review identities must be unique.")
        for decision_id, replay_input in replay_inputs_by_id.items():
            if replay_input.protocol_evidence_scope != "none":
                if len(replay_input.empirical_result_ids) != 1 or (
                    len(replay_input.symbolic_result_ids) != 1
                    or len(replay_input.review_result_ids) > 1
                ):
                    raise ValueError("Scoped admission needs exact experiment/check/review refs.")
                receipt = next((r for r in self.research_cases
                                if r.result_id == replay_input.empirical_result_ids[0]), None)
                check = next((c for c in self.candidate_checks
                              if c.check_id == replay_input.symbolic_result_ids[0]), None)
                review = reviews_by_id.get(replay_input.review_result_ids[0]) \
                    if replay_input.review_result_ids else None
                if receipt is None or check is None or candidate_parent is None:
                    raise ValueError("Scoped admission evidence is missing from the bundle.")
                binding = bindings_by_id[receipt.binding_id]
                if replay_input.review_result_ids and (
                    review is None or review.decision != "accept_protocol_scope"
                    or review.problem_spec_hash != context.spec.content_hash
                    or review.scope_key != protocol_scope_key(binding)
                    or review.reviewed_at > decisions_by_id[decision_id].decided_at
                ):
                    raise ValueError("Protocol review does not authorize this frozen family.")
                authoritative = protocol_admission_context(
                    self.candidate, context.spec, candidate_parent, check, binding, receipt,
                    reviewed=review is not None, review_id=review.review_id if review else None,
                    mapping_freshness=replay_input.mapping_freshness,
                    mapping_usable=replay_input.mapping_usable,
                    quota_available=replay_input.quota_available,
                )
                expected = authoritative.model_dump(exclude={"candidate", "spec"})
                if any(getattr(replay_input, field) != getattr(authoritative, field)
                       for field in expected):
                    raise ValueError("Scoped admission flags disagree with retained evidence.")
            replay_admission(
                replay_input,
                decisions_by_id[decision_id],
                self.candidate,
                context.spec,
            )
        numerical_parent = next(
            (
                parent for parent in self.parent_candidates
                if any(
                    reference.entity_id == parent.candidate_id
                    for reference in self.candidate.parents
                )
            ),
            None,
        )
        for fixture in self.numerical_fixtures:
            expected_input = build_numerical_suite_input(
                self.candidate,
                context.spec,
                seed=fixture.seed,
                parent_candidate=numerical_parent,
            )
            if fixture.input_hash != numerical_fixture_input_hash(expected_input):
                raise ValueError(
                    "Replay numerical fixture input does not match its frozen candidate."
                )
        expected_sources = {
            context.target_parent.entity_id,
            context.left.port.equation_id,
            context.right.port.equation_id,
        }
        expected_sources = {item for item in expected_sources if not item.startswith("cand_")}
        source_ids = {item.equation_id for item in self.sources}
        if len(source_ids) != len(self.sources) or source_ids != expected_sources:
            raise ValueError("Replay bundle source references do not match the compiler inputs.")
        sources_by_id = {item.equation_id: item for item in self.sources}
        for feature in (context.left, context.right):
            if sources_by_id[feature.port.equation_id].equation_source_hash != feature.source_hash:
                raise ValueError("Replay source hash does not match the frozen compiler input.")
        if (
            not any(
                item.mapping_id == context.mapping.mapping_id and item.decision == "reviewed"
                for item in self.compatibility_reviews
            )
            or any(
                item.mapping_id != context.mapping.mapping_id
                for item in self.compatibility_reviews
            )
        ):
            raise ValueError("Replay bundle contains a review for another compatibility mapping.")
        expected_contracts = {
            (context.left.port.equation_id, context.left.port.symbol_name): (
                context.left.contract_hash
            ),
            (context.right.port.equation_id, context.right.port.symbol_name): (
                context.right.contract_hash
            ),
        }
        actual_contracts = {
            (item.scope, item.symbol_name): item
            for item in self.contract_reviews
        }
        if (
            len(actual_contracts) != len(self.contract_reviews)
            or set(actual_contracts) != set(expected_contracts)
        ):
            raise ValueError("Replay bundle must contain the exact reviewed feature contracts.")
        for key, expected_hash in expected_contracts.items():
            review = actual_contracts[key]
            if (
                review.decision != "accepted"
                or review.reviewed_contract is None
                or hashlib.sha256(
                    canonical_json(review.reviewed_contract.model_dump(mode="json")).encode("utf-8")
                ).hexdigest() != expected_hash
            ):
                raise ValueError("Replay bundle contract review differs from the compiler input.")
        if any(item.workspace_id != self.workspace_id for item in self.lineage_assertions):
            raise ValueError("Replay bundle lineage contains a foreign workspace record.")
        if self.proposal is None:
            if self.proposal_review is not None or self.activity.source_proposal_id is not None:
                raise ValueError("Replay bundle proposal provenance is incomplete.")
        elif (
            self.proposal.workspace_id != self.workspace_id
            or self.proposal.proposal_id != self.activity.source_proposal_id
            or self.proposal_review is None
            or self.proposal_review.workspace_id != self.workspace_id
            or self.proposal_review.proposal_id != self.proposal.proposal_id
            or self.proposal_review.proposal_hash != self.proposal.content_hash
            or self.proposal_review.decision != "accept_for_compilation"
            or self.proposal.problem_spec_id != context.spec.spec_id
            or self.proposal.problem_spec_hash != context.spec.content_hash
            or self.proposal.operator != self.activity.operator
            or self.proposal.operator_version != self.activity.operator_version
            or self.proposal.transform_json != self.activity.request_json
            or not {
                context.target_parent.entity_id,
                context.left.port.equation_id,
                context.right.port.equation_id,
            }.issubset(self.proposal.parent_ids)
            or any(
                equation_id not in sources_by_id
                or not any(
                    item.equation_id == equation_id and item.anchor == anchor
                    for item in self.sources
                )
                for equation_id, anchor in map(
                    parse_source_span_id, self.proposal.source_span_ids
                )
            )
        ):
            raise ValueError("Replay bundle proposal review does not match its compilation.")

        identity = self.model_dump(mode="json", exclude={"bundle_hash"})
        serialized = canonical_json(identity)
        if len(serialized.encode("utf-8")) > MAX_BUNDLE_BYTES:
            raise ValueError("Replay bundle exceeds the export size limit.")
        expected_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        if self.bundle_hash != expected_hash:
            legacy_identity = self.model_dump(
                mode="json",
                exclude={
                    "bundle_hash",
                    "admission_replay_inputs",
                    "implementation_bindings",
                    "research_cases",
                    "protocol_reviews",
                },
            )
            legacy_hash = hashlib.sha256(
                canonical_json(legacy_identity).encode("utf-8")
            ).hexdigest()
            if (
                self.admission_replay_inputs
                or self.implementation_bindings
                or self.research_cases
                or self.protocol_reviews
                or self.bundle_hash != legacy_hash
            ):
                raise ValueError("Replay bundle hash does not match its immutable content.")
        return self


def make_compiler_replay_bundle(**values: object) -> CompilerReplayBundle:
    def to_json_value(value: object) -> object:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, (tuple, list)):
            return [to_json_value(item) for item in value]
        if isinstance(value, dict):
            return {key: to_json_value(item) for key, item in value.items()}
        return value

    identity = {
        "schema_version": "compiler-replay-bundle.v1",
        "admission_replay_inputs": [],
        "implementation_bindings": [],
        "research_cases": [],
        **{key: to_json_value(value) for key, value in values.items()},
    }
    if not identity.get("protocol_reviews"):
        identity.pop("protocol_reviews", None)
    bundle_hash = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
    return CompilerReplayBundle.model_validate(identity | {"bundle_hash": bundle_hash})


def replay_candidate_from_bundle(bundle: CompilerReplayBundle) -> CompiledCandidate:
    """Recompile without network access; source URLs remain resolution instructions."""
    validated = CompilerReplayBundle.model_validate(bundle.model_dump(mode="json"))
    parent = validated.parent_candidates[0] if validated.parent_candidates else None
    replayed = replay_compiled_candidate(
        validated.candidate, validated.activity, parent_candidate=parent
    )

    current_checks = [
        item for item in validated.candidate_checks
        if item.checker_version == CANDIDATE_CHECKER_VERSION
    ]
    if current_checks:
        replayed_check = verify_compiled_candidate(
            replayed, parent_candidate=parent,
        )
        if any(item.check_id != replayed_check.check_id for item in current_checks):
            raise ValueError("Current-version candidate check does not reproduce from this bundle.")
    for fixture in validated.numerical_fixtures:
        if fixture.suite_version != SUITE_VERSION or fixture.outcome not in {
            "passed_suite", "counterexample",
        }:
            continue
        numerical_input = build_numerical_suite_input(
            replayed,
            CompileContext.model_validate_json(validated.activity.compiler_context_json).spec,
            seed=fixture.seed,
            parent_candidate=parent,
        )
        actual = _evaluate(numerical_input)
        expected = {
            "outcome": fixture.outcome,
            "checks": fixture.checks,
            "measurements": fixture.measurements,
            "counterexample": fixture.counterexample,
            "error_code": fixture.error_code,
        }
        reproduced = {
            "outcome": actual.get("outcome"),
            "checks": actual.get("checks", {}),
            "measurements": actual.get("measurements", {}),
            "counterexample": actual.get("counterexample"),
            "error_code": actual.get("error_code"),
        }
        if canonical_json(reproduced) != canonical_json(expected):
            raise ValueError(
                "Current-version numerical fixture outcome does not reproduce from this bundle."
            )
    bindings_by_id = {item.binding_id: item for item in validated.implementation_bindings}
    replay_context = CompileContext.model_validate_json(validated.activity.compiler_context_json)
    for receipt in validated.research_cases:
        if receipt.outcome == "inconclusive":
            continue
        binding = bindings_by_id[receipt.binding_id]
        reproduced = run_registered_research_case(
            binding,
            replayed,
            replay_context.spec,
            parent_candidate=parent,
            actor_id=receipt.actor_id,
            run_id=receipt.run_id,
            worker_runner=lambda payload, **_kwargs: evaluate_research_case(payload),
            now=receipt.created_at,
            evaluation_role=receipt.evaluation_role,
            evolution_id=receipt.evolution_id,
        )
        expected = receipt.model_dump(mode="json")
        actual = reproduced.model_dump(mode="json")
        # Wall-clock measurements are operational metadata, not scientific evidence.
        expected["search_cost"].pop("wall_time_ms", None)
        actual["search_cost"].pop("wall_time_ms", None)
        expected.pop("result_id", None)
        expected.pop("result_hash", None)
        actual.pop("result_id", None)
        actual.pop("result_hash", None)
        # Original environment/measurements remain immutable in the receipt.
        # CPython patch/libm differences may change final float64 bits, never the
        # frozen threshold or support decision. Other runtime fields stay exact.
        for item in (*expected["trials"], *actual["trials"]):
            version = item["environment"].get("python", "")
            item["environment"]["python"] = ".".join(version.split(".")[:2])
        if not _same_scientific_result(actual, expected):
            raise ValueError(
                "Current-version research-case outcome does not reproduce from this bundle."
            )
    return replayed
