"""Server-owned input binding for the synthetic V2 numerical regression suite."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from app.analysis_versions import canonical_json
from app.candidate_verification import verify_compiled_candidate
from app.numerical_worker import MAX_FEATURE_RANK, SUITE_VERSION, TOLERANCES
from app.problem_spec import FrozenInput, ProblemSpecSnapshot, definition_hash
from app.research_compiler import CompiledCandidate, ParentRef, _verify_candidate_identity
from app.sandbox import configured_image, run_numerical_sandbox

CHECK_NAMES = {
    "kernel_identity",
    "causal_future_perturbation",
    "finite_gradient",
    "limiting_weights",
    "near_zero_denominator",
    "extreme_norm_overflow",
    "underflow_detection",
}


class NumericalFixtureRunRequest(FrozenInput):
    seed: int = Field(ge=0, le=2**31 - 1, strict=True)


class NumericalFixtureReceipt(FrozenInput):
    result_id: str = Field(pattern=r"^num_[0-9a-f]{32}$")
    result_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    workspace_id: str = Field(min_length=1, max_length=200)
    actor_id: str = Field(min_length=1, max_length=200)
    candidate_id: str = Field(pattern=r"^cand_[0-9a-f]{32}$")
    candidate_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_refs: tuple[ParentRef, ...] = Field(min_length=1, max_length=8)
    problem_spec_id: str = Field(min_length=1, max_length=200)
    problem_spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    suite_version: Literal["feature-kernel-fixture.v1"] = SUITE_VERSION
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_image: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$",
    )
    seed: int = Field(ge=0, le=2**31 - 1, strict=True)
    dtype: Literal["float32", "float64", "bfloat16"]
    tolerance: float = Field(gt=0)
    outcome: Literal[
        "passed_suite", "counterexample", "unknown", "unsupported", "timeout", "error"
    ]
    checks: dict[str, bool] = Field(max_length=16)
    measurements: dict[str, float] = Field(max_length=16)
    counterexample: dict[str, Any] | None = None
    environment: dict[str, str] = Field(max_length=8)
    error_code: str | None = Field(default=None, max_length=100)
    fixture_scope: Literal["synthetic_feature_kernel_fixture"] = (
        "synthetic_feature_kernel_fixture"
    )
    performance_claim: Literal[False] = False
    created_at: datetime
    schema_version: Literal["numerical-fixture-result.v1"] = "numerical-fixture-result.v1"

    @field_validator("checks", mode="before")
    @classmethod
    def validate_checks(cls, value: Any) -> dict[str, bool]:
        if (
            not isinstance(value, dict)
            or set(value) - CHECK_NAMES
            or any(type(passed) is not bool for passed in value.values())
        ):
            raise ValueError("Numerical fixture checks have an invalid shape.")
        return value

    @field_validator("measurements", mode="before")
    @classmethod
    def validate_measurements(cls, value: Any) -> dict[str, float]:
        if (
            not isinstance(value, dict)
            or any(
                type(number) not in (int, float)
                or not math.isfinite(number)
                or number < 0
                for number in value.values()
            )
        ):
            raise ValueError("Numerical fixture measurements must be finite and nonnegative.")
        return {key: float(number) for key, number in value.items()}

    @model_validator(mode="after")
    def validate_result_identity(self) -> NumericalFixtureReceipt:
        if (
            self.fixture_scope != "synthetic_feature_kernel_fixture"
            or self.performance_claim is not False
            or self.tolerance != TOLERANCES[self.dtype]
        ):
            raise ValueError("Numerical fixture receipt has invalid scope or status.")
        if self.outcome == "passed_suite" and (
            set(self.checks) != CHECK_NAMES
            or not all(self.checks.values())
            or self.counterexample is not None
            or self.error_code is not None
        ):
            raise ValueError("A passed suite must contain only successful checks.")
        if self.outcome == "counterexample" and (
            not self.counterexample
            or not self.checks
            or all(self.checks.values())
            or self.error_code is not None
        ):
            raise ValueError("A counterexample receipt must contain a failed check and witness.")
        if self.outcome in {"unknown", "unsupported", "timeout", "error"} and not self.error_code:
            raise ValueError("Incomplete numerical runs require a bounded error code.")
        excluded = {"result_id", "result_hash"}
        if self.execution_image is None:
            # Old v1 receipts predate image binding; preserve their content identity.
            excluded.add("execution_image")
        identity = self.model_dump(mode="json", exclude=excluded)
        digest = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
        if self.result_hash != digest or self.result_id != f"num_{digest[:32]}":
            raise ValueError("Numerical fixture receipt identity does not match its content.")
        return self


class NumericalFixtureReceiptResponse(FrozenInput):
    result: NumericalFixtureReceipt
    replayed: bool


def make_numerical_fixture_receipt(
    result: dict[str, Any],
    *,
    workspace_id: str,
    actor_id: str,
    run_id: str,
) -> NumericalFixtureReceipt:
    execution_image = result.get("execution_image")
    if not isinstance(execution_image, str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", execution_image,
    ):
        raise ValueError("A verified immutable sandbox image is required for a new receipt.")
    created_at = datetime.now(UTC)
    payload = {
        **result,
        "checks": result.get("checks", {}),
        "measurements": result.get("measurements", {}),
        "counterexample": result.get("counterexample"),
        "environment": result.get("environment", {}),
        "error_code": result.get("error_code"),
        "run_id": run_id,
        "workspace_id": workspace_id,
        "actor_id": actor_id,
        "created_at": created_at.isoformat().replace("+00:00", "Z"),
        "schema_version": "numerical-fixture-result.v1",
    }
    if execution_image is None:
        payload.pop("execution_image", None)
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return NumericalFixtureReceipt.model_validate(
        payload | {"result_hash": digest, "result_id": f"num_{digest[:32]}"}
    )


def numerical_fixture_input_hash(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def build_numerical_suite_input(
    candidate: CompiledCandidate,
    spec: ProblemSpecSnapshot,
    *,
    seed: int,
    parent_candidate: CompiledCandidate | None = None,
) -> dict[str, object]:
    """Resolve a bounded allowlist from immutable server-owned research records."""
    _verify_candidate_identity(candidate)
    if (
        definition_hash(spec.definition) != spec.content_hash
        or candidate.workspace_id != spec.workspace_id
        or candidate.problem_spec_id != spec.spec_id
        or candidate.problem_spec_hash != spec.content_hash
    ):
        raise ValueError("Candidate and frozen ProblemSpec scope do not match.")
    if type(seed) is not int or seed not in spec.definition.seeds:
        raise ValueError("Numerical seed must be declared by the frozen ProblemSpec.")

    if candidate.operator == "mix_positive_feature_maps":
        if parent_candidate is not None:
            raise ValueError("A mixture candidate does not accept a candidate parent.")
        checked = verify_compiled_candidate(candidate)
        source_ir = json.loads(candidate.ir_json)
    elif candidate.operator == "lower_mixture_to_concatenation":
        if parent_candidate is None:
            raise ValueError("The exact mixture parent is required for this candidate.")
        checked = verify_compiled_candidate(candidate, parent_candidate=parent_candidate)
        source_ir = json.loads(parent_candidate.ir_json)
    else:
        raise ValueError("No numerical fixture is registered for this operator.")

    if checked.outcome not in {"supported", "unknown"}:
        raise ValueError("Candidate did not pass its registered structural identity check.")
    left, right = source_ir.get("left"), source_ir.get("right")
    if (
        not isinstance(left, dict)
        or not isinstance(right, dict)
        or type(left.get("feature_rank")) is not int
        or type(right.get("feature_rank")) is not int
        or not 1 <= left["feature_rank"] <= MAX_FEATURE_RANK
        or not 1 <= right["feature_rank"] <= MAX_FEATURE_RANK
    ):
        raise ValueError("Feature ranks exceed the fixed numerical fixture limits.")
    weight = source_ir.get("lambda")
    if type(weight) not in (int, float):
        raise ValueError("Registered candidate IR has no valid mixture weight.")

    return {
        "suite_version": SUITE_VERSION,
        "candidate_hash": candidate.content_hash,
        "seed": seed,
        "dtype": spec.definition.dtype,
        "lambda": weight,
        "left_rank": left["feature_rank"],
        "right_rank": right["feature_rank"],
    }


def run_candidate_numerical_fixture(
    candidate: CompiledCandidate,
    spec: ProblemSpecSnapshot,
    *,
    seed: int,
    parent_candidate: CompiledCandidate | None = None,
) -> dict[str, Any]:
    """Run the fixed synthetic suite and bind its result to candidate/spec provenance."""
    payload = build_numerical_suite_input(
        candidate, spec, seed=seed, parent_candidate=parent_candidate,
    )
    input_hash = numerical_fixture_input_hash(payload)
    timeout_ms = min(30_000, max(10, spec.definition.budget.wall_time_ms))
    image = configured_image()
    result = run_numerical_sandbox(
        payload, timeout_ms=timeout_ms, image=image,
    )
    receipt_scope = {
        "candidate_id": candidate.candidate_id,
        "candidate_hash": candidate.content_hash,
        "parent_refs": [parent.model_dump(mode="json") for parent in candidate.parents],
        "problem_spec_id": spec.spec_id,
        "problem_spec_hash": spec.content_hash,
        "suite_version": SUITE_VERSION,
        "input_hash": input_hash,
        "execution_image": image,
        "seed": seed,
        "dtype": spec.definition.dtype,
        "tolerance": TOLERANCES[spec.definition.dtype],
        "fixture_scope": "synthetic_feature_kernel_fixture",
        "performance_claim": False,
    }
    if result.get("outcome") not in {"passed_suite", "counterexample"}:
        return {
            **receipt_scope,
            "outcome": result.get("outcome", "error"),
            "error_code": result.get("error_code", "NUMERICAL_SUITE_FAILED"),
        }
    if (
        result.get("candidate_hash") != candidate.content_hash
        or result.get("input_hash") != input_hash
        or result.get("seed") != seed
        or result.get("dtype") != spec.definition.dtype
        or result.get("tolerance") != TOLERANCES[spec.definition.dtype]
    ):
        return {
            **receipt_scope,
            "outcome": "error",
            "error_code": "NUMERICAL_RESULT_SCOPE_MISMATCH",
        }
    return {
        **result,
        **receipt_scope,
    }
