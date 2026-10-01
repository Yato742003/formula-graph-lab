"""Candidate-bound V2/V3 receipt for the registered zero-cost CPU research case."""

from __future__ import annotations

import hashlib
import math
import statistics
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_serializer, model_validator

from app.admission import AdmissionContext
from app.analysis_versions import canonical_json
from app.candidate_verification import (
    CANDIDATE_CHECKER_VERSION,
    CandidateCheckResult,
    discharged_obligations,
)
from app.numerical_verification import build_numerical_suite_input
from app.problem_spec import (
    ArtifactRef,
    FrozenInput,
    NotApplicable,
    ProblemSpecSnapshot,
    definition_hash,
)
from app.research_case_worker import (
    PROTOCOL_VERSION,
    SEED_SPLITS,
    SEEDS,
    SEQUENCE_LENGTHS,
)
from app.research_compiler import CompiledCandidate, ParentRef, _verify_candidate_identity
from app.sandbox import run_research_case_sandbox

MAX_WALL_TIME_MS = 30_000
PER_TRIAL_TIMEOUT_MS = 5_000
T95_DF1 = 12.706204736432095
PRIMARY_METRIC = "candidate_regret_vs_best_parent"
EXPECTED_BASELINES = {
    "exact-causal-softmax": hashlib.sha256(b"exact-causal-softmax.v1").hexdigest(),
    "positive-random-exp": hashlib.sha256(b"positive-random-exp.v1").hexdigest(),
    "positive-elu": hashlib.sha256(b"positive-elu.v1").hexdigest(),
}


def _hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def worker_source_hash() -> str:
    return hashlib.sha256(
        Path(__file__).with_name("research_case_worker.py").read_bytes()
    ).hexdigest()


def protocol_config() -> dict[str, object]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "input_generator": "fgl-xorshift32-v1",
        "seed_splits": {str(seed): SEED_SPLITS[seed] for seed in SEEDS},
        "sequence_lengths": list(SEQUENCE_LENGTHS),
        "dtype": "float64",
        "reference": "exact-causal-softmax.v1",
        "parent_a": "positive-random-exp.v1",
        "parent_b": "positive-elu.v1",
        "candidate": "rank-matched-weighted-feature-mixture.v1",
        "primary_metric": PRIMARY_METRIC,
        "resources": {
            "cpu_count": 1,
            "memory_mib": 256,
            "max_wall_time_ms": MAX_WALL_TIME_MS,
            "per_trial_timeout_ms": PER_TRIAL_TIMEOUT_MS,
            "cost_usd": 0,
        },
        "claim_scope": "synthetic_operator_only_no_product_claim",
    }


def protocol_config_hash() -> str:
    return _hash(protocol_config())


def dataset_manifest() -> dict[str, object]:
    return {
        "name": "deterministic-synthetic-qkv",
        "version": "1",
        "generator": "fgl-xorshift32-v1",
        "seeds": list(SEEDS),
        "splits": {str(seed): SEED_SPLITS[seed] for seed in SEEDS},
        "sequence_lengths": list(SEQUENCE_LENGTHS),
    }


def dataset_artifact_hash() -> str:
    return _hash(dataset_manifest())


def dataset_split_hash(role: str) -> str:
    return _hash({
        "dataset": dataset_artifact_hash(),
        "role": role,
        "seeds": [seed for seed in SEEDS if SEED_SPLITS[seed] == role],
    })


def registered_protocol_descriptor() -> dict[str, object]:
    """Public immutable values needed to freeze the built-in no-cost protocol."""
    return {
        "protocol_version": PROTOCOL_VERSION,
        "dataset_artifact_hash": dataset_artifact_hash(),
        "dataset_splits": {
            role: dataset_split_hash(role)
            for role in ("search", "validation", "holdout")
        },
        "evaluator_implementation_hash": worker_source_hash(),
        "evaluator_config_hash": protocol_config_hash(),
        "baselines": EXPECTED_BASELINES,
        "seeds": list(SEEDS),
        "hardware": "cpu",
        "backend": {"name": "python-stdlib", "version": "3.12"},
        "dtype": "float64",
        "primary_metric": PRIMARY_METRIC,
        "quality_constraint": {"relation": "<=", "threshold": 0.01},
        "cost_usd": 0,
        "claim_scope": "synthetic_operator_only_no_product_claim",
    }


def validate_registered_spec(spec: ProblemSpecSnapshot) -> float:
    definition = spec.definition
    descriptor = registered_protocol_descriptor()
    splits = {item.role: item.split_hash for item in definition.dataset.splits}
    baselines = {item.name: item.sha256 for item in definition.baselines}
    constraints = [
        item for item in definition.quality_constraints if item.metric == PRIMARY_METRIC
    ]
    if (
        definition_hash(definition) != spec.content_hash
        or definition.dataset.artifact_hash != descriptor["dataset_artifact_hash"]
        or splits != descriptor["dataset_splits"]
        or baselines != EXPECTED_BASELINES
        or any(not isinstance(item, ArtifactRef) for item in definition.baselines)
        or not isinstance(definition.model, NotApplicable)
        or not isinstance(definition.tokenizer, NotApplicable)
        or definition.hardware.target != "cpu"
        or definition.backend.name != "python-stdlib"
        or definition.backend.version != "3.12"
        or definition.dtype != "float64"
        or definition.evaluator.name != "fgl-attention-mashup"
        or definition.evaluator.protocol_version != PROTOCOL_VERSION
        or definition.evaluator.implementation_hash != worker_source_hash()
        or definition.evaluator.config_hash != protocol_config_hash()
        or tuple(definition.seeds) != SEEDS
        or len(constraints) != 1
        or constraints[0].relation != "<="
        or not 0 <= constraints[0].threshold <= 1
        or definition.budget.wall_time_ms < MAX_WALL_TIME_MS
        or definition.budget.compute_unit != "CPU-seconds"
        or definition.budget.compute_budget < MAX_WALL_TIME_MS / 1000
    ):
        raise ValueError("ProblemSpec does not match the registered CPU research protocol.")
    return constraints[0].threshold


class ImplementationBindingReceipt(FrozenInput):
    schema_version: Literal["implementation-binding.v1"] = "implementation-binding.v1"
    binding_id: str = Field(pattern=r"^bind_[0-9a-f]{32}$")
    binding_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    workspace_id: str = Field(min_length=1, max_length=200)
    candidate_id: str = Field(pattern=r"^cand_[0-9a-f]{32}$")
    candidate_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_refs: tuple[ParentRef, ...] = Field(min_length=1, max_length=8)
    problem_spec_id: str = Field(min_length=1, max_length=200)
    problem_spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_parent_refs: tuple[ParentRef, ...] = Field(min_length=3, max_length=3)
    protocol_version: Literal["attention-mashup-cpu-synthetic.v1"] = PROTOCOL_VERSION
    protocol_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    worker_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_image: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    mixture_weight: float = Field(gt=0, lt=1)
    branch_rank: int = Field(ge=1, le=64, strict=True)
    feature_budget: int = Field(ge=2, le=128, strict=True)
    binding_scope: Literal["server_reference_implementation"] = (
        "server_reference_implementation"
    )
    author_code_claim: Literal[False] = False
    created_at: datetime

    @model_validator(mode="after")
    def validate_identity(self) -> ImplementationBindingReceipt:
        if self.feature_budget != self.branch_rank * 2:
            raise ValueError("Binding feature budget does not match both branches.")
        payload = self.model_dump(mode="json", exclude={"binding_id", "binding_hash"})
        digest = _hash(payload)
        if self.binding_hash != digest or self.binding_id != f"bind_{digest[:32]}":
            raise ValueError("Implementation binding identity does not match its content.")
        return self


class ResearchCaseMeasurement(FrozenInput):
    sequence_length: int = Field(gt=0, strict=True)
    feature_budget: int = Field(gt=0, strict=True)
    parent_a_mean_abs_error_vs_exact: float
    parent_b_mean_abs_error_vs_exact: float
    candidate_mean_abs_error_vs_exact: float
    candidate_regret_vs_best_parent: float
    causal_future_perturbation_max_abs_error: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_metrics(self) -> ResearchCaseMeasurement:
        values = self.model_dump()
        if any(not math.isfinite(value) for value in values.values()):
            raise ValueError("Research measurements must be finite.")
        if any(value < 0 for name, value in values.items()
               if name != "candidate_regret_vs_best_parent"):
            raise ValueError("Absolute errors cannot be negative.")
        expected = self.candidate_mean_abs_error_vs_exact - min(
            self.parent_a_mean_abs_error_vs_exact, self.parent_b_mean_abs_error_vs_exact
        )
        if self.candidate_regret_vs_best_parent != expected:
            raise ValueError("Regret must be derived from the measured parent errors.")
        return self


class ResearchCaseTrial(FrozenInput):
    seed: int = Field(strict=True)
    split: Literal["search", "validation", "holdout"]
    outcome: Literal["passed_suite", "counterexample", "timeout", "error"]
    input_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    checks: dict[str, bool] = Field(default_factory=dict, max_length=8)
    measurements: tuple[ResearchCaseMeasurement, ...] = Field(default=(), max_length=8)
    environment: dict[str, str] = Field(default_factory=dict, max_length=8)
    error_code: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_outcome(self) -> ResearchCaseTrial:
        complete = self.outcome in {"passed_suite", "counterexample"}
        if self.seed not in SEED_SPLITS or self.split != SEED_SPLITS[self.seed]:
            raise ValueError("Trial split must match the frozen seed assignment.")
        if complete != (self.input_hash is not None and bool(self.measurements)):
            raise ValueError("Completed trials require measurements and input identity.")
        if complete == (self.error_code is not None):
            raise ValueError("Only incomplete trials require an error code.")
        if complete:
            if tuple(item.sequence_length for item in self.measurements) != SEQUENCE_LENGTHS:
                raise ValueError("Trial must contain every frozen sequence length.")
            if set(self.checks) != {
                "matched_feature_budget", "causal_future_perturbation", "finite_outputs"
            } or (self.outcome == "passed_suite") != all(self.checks.values()):
                raise ValueError("Trial outcome must agree with its required checks.")
        elif self.measurements or self.checks or self.input_hash or self.environment:
            raise ValueError("Incomplete trials cannot contain successful measurements.")
        return self


class ResearchCaseReceipt(FrozenInput):
    schema_version: Literal["research-case-result.v1", "research-case-result.v2"] = (
        "research-case-result.v1"
    )
    evaluation_role: Literal["legacy_full", "search", "holdout"] = "legacy_full"
    evolution_id: str | None = Field(default=None, pattern=r"^evo_[0-9a-f]{32}$")
    result_id: str = Field(pattern=r"^exp_[0-9a-f]{32}$")
    result_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    actor_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    candidate_id: str = Field(pattern=r"^cand_[0-9a-f]{32}$")
    candidate_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_refs: tuple[ParentRef, ...] = Field(min_length=1, max_length=8)
    problem_spec_id: str = Field(min_length=1, max_length=200)
    problem_spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    binding_id: str = Field(pattern=r"^bind_[0-9a-f]{32}$")
    binding_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    protocol_version: Literal["attention-mashup-cpu-synthetic.v1"] = PROTOCOL_VERSION
    protocol_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    outcome: Literal["supported_on_protocol", "failed_on_protocol", "inconclusive"]
    quality_constraints_met: bool
    primary_metric: Literal["candidate_regret_vs_best_parent"] = PRIMARY_METRIC
    quality_threshold: float = Field(ge=0, le=1)
    holdout_mean: float | None = None
    holdout_ci95_low: float | None = None
    holdout_ci95_high: float | None = None
    trials: tuple[ResearchCaseTrial, ...] = Field(min_length=2, max_length=5)
    search_cost: dict[str, int | float] = Field(max_length=8)
    claim_scope: Literal["synthetic_operator_only_no_product_claim"] = (
        "synthetic_operator_only_no_product_claim"
    )
    performance_claim: Literal[False] = False
    created_at: datetime

    @model_validator(mode="after")
    def validate_identity(self) -> ResearchCaseReceipt:
        if (set(self.search_cost) != {"candidate_count", "trial_count", "completed_trial_count",
                                     "wall_time_ms", "cost_usd"}
                or self.search_cost["candidate_count"] != 1
                or self.search_cost["trial_count"] != len(self.trials)
                or self.search_cost["completed_trial_count"] != sum(
                    trial.outcome in {"passed_suite", "counterexample"} for trial in self.trials
                )
                or self.search_cost["cost_usd"] != 0
                or any(isinstance(value, bool) or not math.isfinite(value) or value < 0
                       for value in self.search_cost.values())):
            raise ValueError("Protocol cost must describe the retained zero-cost trials.")
        if (self.schema_version == "research-case-result.v1") != (
            self.evaluation_role == "legacy_full"
        ):
            raise ValueError("Legacy receipts and scoped receipts require distinct versions.")
        if self.evaluation_role == "holdout" and self.evolution_id is None:
            raise ValueError("Holdout receipts require a frozen evolution campaign.")
        if tuple(item.seed for item in self.trials) != evaluation_seeds(self.evaluation_role):
            raise ValueError("Research case must retain every frozen seed in order.")
        if self.outcome == "inconclusive":
            if self.quality_constraints_met or any(value is not None for value in (
                self.holdout_mean, self.holdout_ci95_low, self.holdout_ci95_high
            )):
                raise ValueError("Incomplete research cases cannot claim a holdout result.")
        elif self.evaluation_role != "search" and None in (
            self.holdout_mean, self.holdout_ci95_low, self.holdout_ci95_high
        ):
            raise ValueError("Completed research cases require holdout uncertainty.")
        if self.evaluation_role == "search" and any(value is not None for value in (
            self.holdout_mean, self.holdout_ci95_low, self.holdout_ci95_high
        )):
            raise ValueError("Search receipts cannot expose holdout results.")
        if self.quality_constraints_met != (self.outcome == "supported_on_protocol"):
            raise ValueError("Protocol support must equal the frozen quality decision.")
        correct = all(item.outcome == "passed_suite" for item in self.trials)
        if correct != (self.outcome != "inconclusive"):
            raise ValueError("Protocol outcome must agree with all retained trials.")
        if correct:
            mean, low, high = quality_summary(self.trials, self.evaluation_role)
            if self.evaluation_role != "search" and (
                self.holdout_mean, self.holdout_ci95_low, self.holdout_ci95_high
            ) != (mean, low, high):
                raise ValueError("Holdout summary must reproduce from the frozen trials.")
            if self.quality_constraints_met != (high <= self.quality_threshold):
                raise ValueError("Quality decision must reproduce from the retained trials.")
        excluded = {"result_id", "result_hash"}
        if self.evaluation_role == "legacy_full":
            excluded.update({"evaluation_role", "evolution_id"})
        payload = self.model_dump(mode="json", exclude=excluded)
        digest = _hash(payload)
        if self.result_hash != digest or self.result_id != f"exp_{digest[:32]}":
            raise ValueError("Research case identity does not match its content.")
        return self

    @model_serializer(mode="wrap")
    def serialize_receipt(self, handler):
        payload = handler(self)
        if self.evaluation_role == "legacy_full":
            payload.pop("evaluation_role", None)
            payload.pop("evolution_id", None)
        return payload


class ResearchCaseReceiptResponse(FrozenInput):
    binding: ImplementationBindingReceipt
    result: ResearchCaseReceipt
    replayed: bool


class ResearchCaseReviewRequest(FrozenInput):
    result_id: str = Field(pattern=r"^exp_[0-9a-f]{32}$")
    decision: Literal["accept_protocol_scope", "reject"]
    notes: str = Field(min_length=1, max_length=2000)


class ResearchProtocolReview(FrozenInput):
    review_id: str = Field(pattern=r"^prv_[0-9a-f]{32}$")
    candidate_id: str = Field(pattern=r"^cand_[0-9a-f]{32}$")
    result_id: str = Field(pattern=r"^exp_[0-9a-f]{32}$")
    check_id: str = Field(min_length=1, max_length=200)
    candidate_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    problem_spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer_id: str = Field(min_length=1, max_length=200)
    reviewer_role: Literal["researcher", "reviewer", "admin"]
    decision: Literal["accept_protocol_scope", "reject"]
    notes: str = Field(min_length=1, max_length=2000)
    scope: Literal["synthetic_operator_only_no_product_claim"]
    scope_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    authorization: Literal["bounded_parameter_search_for_this_frozen_feature_map_family"]
    reviewed_at: datetime

    @model_validator(mode="after")
    def validate_review_time(self):
        if self.reviewed_at.tzinfo is None or self.reviewed_at.utcoffset() is None:
            raise ValueError("Protocol review must have a timezone-aware timestamp.")
        return self


def evaluation_seeds(role: str) -> tuple[int, ...]:
    if role not in {"legacy_full", "search", "holdout"}:
        raise ValueError("Unknown research evaluation role.")
    return tuple(seed for seed in SEEDS if role == "legacy_full" or (
        (SEED_SPLITS[seed] != "holdout") if role == "search"
        else SEED_SPLITS[seed] == "holdout"
    ))


def protocol_scope_key(binding: ImplementationBindingReceipt) -> str:
    return _hash([
        binding.problem_spec_hash, binding.protocol_version, binding.worker_source_sha256,
        [item.model_dump(mode="json") for item in binding.source_parent_refs],
    ])


def phase_budget_ms(role: str) -> int:
    return 18_000 if role == "search" else 12_000 if role == "holdout" else MAX_WALL_TIME_MS


def quality_summary(
    trials: tuple[ResearchCaseTrial, ...] | list[ResearchCaseTrial], role: str,
) -> tuple[float, float, float]:
    selected = "search" if role == "search" else "holdout"
    means = [statistics.fmean(
        item.candidate_regret_vs_best_parent for item in trial.measurements
    ) for trial in trials if trial.split == selected]
    mean = statistics.fmean(means)
    margin = T95_DF1 * statistics.stdev(means) / math.sqrt(2)
    high = mean + margin
    if role == "search":
        # ponytail: fixed two-seed search CI plus independent validation, ceiling:
        # this synthetic pilot only; upgrade: register a new protocol for larger studies.
        high = max(high, *(statistics.fmean(
            item.candidate_regret_vs_best_parent for item in trial.measurements
        ) for trial in trials if trial.split == "validation"))
    return mean, mean - margin, high


def protocol_admission_context(
    candidate: CompiledCandidate,
    spec: ProblemSpecSnapshot,
    parent: CompiledCandidate,
    check: CandidateCheckResult,
    binding: ImplementationBindingReceipt,
    result: ResearchCaseReceipt,
    *,
    reviewed: bool,
    review_id: str | None = None,
    mapping_freshness: Literal["current", "stale", "unknown"],
    mapping_usable: bool,
    quota_available: bool,
) -> AdmissionContext:
    """Server-only bridge. Testing a finite protocol never discharges global domain holes."""
    binding = ImplementationBindingReceipt.model_validate(binding.model_dump())
    result = ResearchCaseReceipt.model_validate(result.model_dump())
    expected = make_implementation_binding(
        candidate, spec, parent_candidate=parent,
        execution_image=binding.execution_image, now=binding.created_at,
    )
    if (
        binding != expected or result.evaluation_role == "legacy_full"
        or check.checker_version != CANDIDATE_CHECKER_VERSION
        or check.candidate_id != candidate.candidate_id
        or check.workspace_id != candidate.workspace_id
        or check.candidate_hash != candidate.content_hash
        or result.workspace_id != candidate.workspace_id
        or result.candidate_id != candidate.candidate_id
        or result.candidate_hash != candidate.content_hash
        or result.problem_spec_id != spec.spec_id
        or result.problem_spec_hash != spec.content_hash
        or result.parent_refs != candidate.parents
        or result.binding_id != binding.binding_id
        or result.binding_hash != binding.binding_hash
        or result.protocol_hash != binding.protocol_hash
    ):
        raise ValueError("Protocol evidence is legacy, stale, or outside this candidate scope.")
    complete = all(trial.outcome == "passed_suite" for trial in result.trials)
    if reviewed and not review_id:
        raise ValueError("Human protocol acceptance requires its persisted review identity.")
    vector = check.vector.model_copy(update={
        "numerical": "passed_suite" if complete else "not_run",
        "empirical": result.outcome,
        "human_review": "accepted_scope" if reviewed else "pending",
    })
    return AdmissionContext(
        candidate=candidate, spec=spec, verification=vector,
        discharged_obligation_names=tuple(sorted(discharged_obligations(check))),
        symbolic_result_ids=(check.check_id,),
        numerical_result_ids=(result.result_id,) if complete else (),
        empirical_result_ids=(result.result_id,),
        mapping_freshness=mapping_freshness, mapping_usable=mapping_usable,
        runtime_artifacts_verified=True, protocol_frozen=True,
        quota_available=quota_available, restricted_domain_reviewed=reviewed,
        quality_constraints_met=result.quality_constraints_met,
        protocol_evidence_scope=result.evaluation_role,
        review_result_ids=(review_id,) if reviewed else (),
    )


def make_implementation_binding(
    candidate: CompiledCandidate,
    spec: ProblemSpecSnapshot,
    *,
    parent_candidate: CompiledCandidate,
    execution_image: str,
    now: datetime | None = None,
) -> ImplementationBindingReceipt:
    _verify_candidate_identity(candidate)
    _verify_candidate_identity(parent_candidate)
    validate_registered_spec(spec)
    suite_input = build_numerical_suite_input(
        candidate, spec, seed=SEEDS[0], parent_candidate=parent_candidate
    )
    if (
        candidate.operator != "lower_mixture_to_concatenation"
        or parent_candidate.candidate_id != candidate.parents[0].entity_id
        or suite_input["left_rank"] != suite_input["right_rank"]
        or not isinstance(execution_image, str)
        or not execution_image.startswith("sha256:")
        or len(execution_image) != 71
    ):
        raise ValueError("Candidate is not supported by the registered implementation.")
    manifest = {
        **protocol_config(),
        "candidate_hash": candidate.content_hash,
        "parent_hash": parent_candidate.content_hash,
        "mixture_weight": suite_input["lambda"],
        "branch_rank": suite_input["left_rank"],
        "feature_budget": int(suite_input["left_rank"]) * 2,
        "worker_source_sha256": worker_source_hash(),
        "execution_image": execution_image,
    }
    payload = {
        "schema_version": "implementation-binding.v1",
        "workspace_id": candidate.workspace_id,
        "candidate_id": candidate.candidate_id,
        "candidate_hash": candidate.content_hash,
        "parent_refs": [item.model_dump(mode="json") for item in candidate.parents],
        "problem_spec_id": spec.spec_id,
        "problem_spec_hash": spec.content_hash,
        "source_parent_refs": [
            item.model_dump(mode="json") for item in parent_candidate.parents
        ],
        "protocol_version": PROTOCOL_VERSION,
        "protocol_hash": _hash(manifest),
        "worker_source_sha256": worker_source_hash(),
        "execution_image": execution_image,
        "mixture_weight": suite_input["lambda"],
        "branch_rank": suite_input["left_rank"],
        "feature_budget": int(suite_input["left_rank"]) * 2,
        "binding_scope": "server_reference_implementation",
        "author_code_claim": False,
        "created_at": (now or datetime.now(UTC)).astimezone(UTC).isoformat().replace(
            "+00:00", "Z"
        ),
    }
    digest = _hash(payload)
    return ImplementationBindingReceipt.model_validate(
        payload | {"binding_hash": digest, "binding_id": f"bind_{digest[:32]}"}
    )


def _trial(seed: int, raw: Any, candidate_hash: str) -> ResearchCaseTrial:
    if not isinstance(raw, dict) or raw.get("outcome") in {
        "timeout", "error", "unsupported"
    }:
        return ResearchCaseTrial(
            seed=seed,
            split=SEED_SPLITS[seed],
            outcome=(
                "timeout"
                if isinstance(raw, dict) and raw.get("outcome") == "timeout"
                else "error"
            ),
            error_code=(
                str(raw.get("error_code", "RESEARCH_CASE_WORKER_FAILED"))
                if isinstance(raw, dict)
                else "INVALID_RESEARCH_CASE_RESPONSE"
            )[:100],
        )
    if (
        raw.get("candidate_hash") != candidate_hash
        or raw.get("seed") != seed
        or raw.get("split") != SEED_SPLITS[seed]
        or raw.get("protocol_version") != PROTOCOL_VERSION
    ):
        return ResearchCaseTrial(
            seed=seed,
            split=SEED_SPLITS[seed],
            outcome="error",
            error_code="RESEARCH_CASE_SCOPE_MISMATCH",
        )
    try:
        return ResearchCaseTrial(
            seed=seed,
            split=SEED_SPLITS[seed],
            outcome=raw["outcome"],
            input_hash=raw["input_hash"],
            checks=raw["checks"],
            measurements=tuple(raw["measurements"]),
            environment=raw["environment"],
        )
    except (TypeError, ValueError, KeyError):
        return ResearchCaseTrial(
            seed=seed,
            split=SEED_SPLITS[seed],
            outcome="error",
            error_code="INVALID_RESEARCH_CASE_RESPONSE",
        )


def run_registered_research_case(
    binding: ImplementationBindingReceipt,
    candidate: CompiledCandidate,
    spec: ProblemSpecSnapshot,
    *,
    parent_candidate: CompiledCandidate,
    actor_id: str,
    run_id: str,
    worker_runner: Callable[..., dict[str, Any]] = run_research_case_sandbox,
    now: datetime | None = None,
    evaluation_role: Literal["legacy_full", "search", "holdout"] = "legacy_full",
    evolution_id: str | None = None,
) -> ResearchCaseReceipt:
    threshold = validate_registered_spec(spec)
    recreated = make_implementation_binding(
        candidate,
        spec,
        parent_candidate=parent_candidate,
        execution_image=binding.execution_image,
        now=binding.created_at,
    )
    if recreated != binding:
        raise ValueError("Implementation binding is stale or does not match this candidate.")

    started = time.monotonic()
    trials = []
    for seed in evaluation_seeds(evaluation_role):
        remaining_ms = phase_budget_ms(evaluation_role) - int((time.monotonic() - started) * 1000)
        if remaining_ms < 10:
            trials.append(ResearchCaseTrial(
                seed=seed,
                split=SEED_SPLITS[seed],
                outcome="timeout",
                error_code="RESEARCH_CASE_BUDGET_EXHAUSTED",
            ))
            continue
        payload = {
            "protocol_version": PROTOCOL_VERSION,
            "candidate_hash": candidate.content_hash,
            "seed": seed,
            "lambda": binding.mixture_weight,
            "branch_rank": binding.branch_rank,
        }
        try:
            raw = worker_runner(
                payload,
                timeout_ms=min(PER_TRIAL_TIMEOUT_MS, remaining_ms),
                image=binding.execution_image,
            )
        except Exception:
            raw = {"outcome": "error", "error_code": "RESEARCH_CASE_WORKER_FAILED"}
        trials.append(_trial(seed, raw, candidate.content_hash))

    complete = all(item.outcome in {"passed_suite", "counterexample"} for item in trials)
    correct = complete and all(
        item.outcome == "passed_suite" and all(item.checks.values()) for item in trials
    )
    holdout_mean = holdout_low = holdout_high = None
    quality_met = False
    if correct:
        mean, low, high = quality_summary(trials, evaluation_role)
        quality_met = high <= threshold
        if evaluation_role != "search":
            holdout_mean, holdout_low, holdout_high = mean, low, high
    outcome = (
        "inconclusive" if not correct
        else "supported_on_protocol" if quality_met
        else "failed_on_protocol"
    )
    payload = {
        "schema_version": (
            "research-case-result.v1" if evaluation_role == "legacy_full"
            else "research-case-result.v2"
        ),
        "run_id": run_id,
        "actor_id": actor_id,
        "workspace_id": candidate.workspace_id,
        "candidate_id": candidate.candidate_id,
        "candidate_hash": candidate.content_hash,
        "parent_refs": [item.model_dump(mode="json") for item in candidate.parents],
        "problem_spec_id": spec.spec_id,
        "problem_spec_hash": spec.content_hash,
        "binding_id": binding.binding_id,
        "binding_hash": binding.binding_hash,
        "protocol_version": PROTOCOL_VERSION,
        "protocol_hash": binding.protocol_hash,
        "outcome": outcome,
        "quality_constraints_met": quality_met,
        "primary_metric": PRIMARY_METRIC,
        "quality_threshold": threshold,
        "holdout_mean": holdout_mean,
        "holdout_ci95_low": holdout_low,
        "holdout_ci95_high": holdout_high,
        "trials": [item.model_dump(mode="json") for item in trials],
        "search_cost": {
            "candidate_count": 1,
            "trial_count": len(trials),
            "completed_trial_count": sum(
                item.outcome in {"passed_suite", "counterexample"} for item in trials
            ),
            "wall_time_ms": min(
                phase_budget_ms(evaluation_role), int((time.monotonic() - started) * 1000)
            ),
            "cost_usd": 0,
        },
        "claim_scope": "synthetic_operator_only_no_product_claim",
        "performance_claim": False,
        "created_at": (now or datetime.now(UTC)).astimezone(UTC).isoformat().replace(
            "+00:00", "Z"
        ),
    }
    if evaluation_role != "legacy_full":
        payload["evaluation_role"] = evaluation_role
        payload["evolution_id"] = evolution_id
    digest = _hash(payload)
    return ResearchCaseReceipt.model_validate(
        payload | {"result_hash": digest, "result_id": f"exp_{digest[:32]}"}
    )
