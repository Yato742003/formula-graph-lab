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

from pydantic import Field, model_validator

from app.analysis_versions import canonical_json
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
        if complete != (self.input_hash is not None and bool(self.measurements)):
            raise ValueError("Completed trials require measurements and input identity.")
        if complete == (self.error_code is not None):
            raise ValueError("Only incomplete trials require an error code.")
        return self


class ResearchCaseReceipt(FrozenInput):
    schema_version: Literal["research-case-result.v1"] = "research-case-result.v1"
    result_id: str = Field(pattern=r"^exp_[0-9a-f]{32}$")
    result_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
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
    trials: tuple[ResearchCaseTrial, ...] = Field(min_length=5, max_length=5)
    search_cost: dict[str, int | float] = Field(max_length=8)
    claim_scope: Literal["synthetic_operator_only_no_product_claim"] = (
        "synthetic_operator_only_no_product_claim"
    )
    performance_claim: Literal[False] = False
    created_at: datetime

    @model_validator(mode="after")
    def validate_identity(self) -> ResearchCaseReceipt:
        if tuple(item.seed for item in self.trials) != SEEDS:
            raise ValueError("Research case must retain every frozen seed in order.")
        if self.outcome == "inconclusive":
            if self.quality_constraints_met or self.holdout_mean is not None:
                raise ValueError("Incomplete research cases cannot claim a holdout result.")
        elif None in (self.holdout_mean, self.holdout_ci95_low, self.holdout_ci95_high):
            raise ValueError("Completed research cases require holdout uncertainty.")
        if self.quality_constraints_met != (self.outcome == "supported_on_protocol"):
            raise ValueError("Protocol support must equal the frozen quality decision.")
        payload = self.model_dump(mode="json", exclude={"result_id", "result_hash"})
        digest = _hash(payload)
        if self.result_hash != digest or self.result_id != f"exp_{digest[:32]}":
            raise ValueError("Research case identity does not match its content.")
        return self


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
    worker_runner: Callable[..., dict[str, Any]] = run_research_case_sandbox,
    now: datetime | None = None,
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
    for seed in SEEDS:
        remaining_ms = MAX_WALL_TIME_MS - int((time.monotonic() - started) * 1000)
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
    holdout_means: list[float] = []
    if correct:
        for trial in trials:
            if trial.split == "holdout":
                holdout_means.append(statistics.fmean(
                    item.candidate_regret_vs_best_parent for item in trial.measurements
                ))
    holdout_mean = holdout_low = holdout_high = None
    quality_met = False
    if len(holdout_means) == 2:
        holdout_mean = statistics.fmean(holdout_means)
        margin = T95_DF1 * statistics.stdev(holdout_means) / math.sqrt(2)
        holdout_low, holdout_high = holdout_mean - margin, holdout_mean + margin
        quality_met = holdout_high <= threshold
    outcome = (
        "inconclusive" if not correct
        else "supported_on_protocol" if quality_met
        else "failed_on_protocol"
    )
    payload = {
        "schema_version": "research-case-result.v1",
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
                MAX_WALL_TIME_MS, int((time.monotonic() - started) * 1000)
            ),
            "cost_usd": 0,
        },
        "claim_scope": "synthetic_operator_only_no_product_claim",
        "performance_claim": False,
        "created_at": (now or datetime.now(UTC)).astimezone(UTC).isoformat().replace(
            "+00:00", "Z"
        ),
    }
    digest = _hash(payload)
    return ResearchCaseReceipt.model_validate(
        payload | {"result_hash": digest, "result_id": f"exp_{digest[:32]}"}
    )
