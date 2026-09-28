"""G1 ProblemSpec schema, identity, canonical hashing, and validation."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.analysis_versions import canonical_json

SCHEMA_VERSION: Literal["problem-spec.v1"] = "problem-spec.v1"
MAX_PROBLEM_SPEC_PAYLOAD_BYTES: int = 64 * 1024  # 64 KiB
MAX_STRING_LENGTH: int = 2048
HEX_64_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


def _reject_whitespace_and_length(v: str, max_len: int, name: str) -> str:
    if not isinstance(v, str):
        raise ValueError(f"{name} must be a string.")
    trimmed = v.strip()
    if not trimmed:
        raise ValueError(f"{name} cannot be empty or whitespace only.")
    if len(v) > max_len:
        raise ValueError(f"{name} exceeds maximum length of {max_len} characters.")
    return trimmed


def _reject_auto_latest(v: str, name: str) -> str:
    v_clean = _reject_whitespace_and_length(v, 100, name)
    if v_clean.lower() in ("auto", "latest"):
        raise ValueError(f'{name} cannot be "{v_clean}"; explicit values are required.')
    return v_clean


def _validate_finite_float(v: float, name: str) -> float:
    if isinstance(v, bool):
        raise ValueError(f"{name} cannot be a boolean.")
    try:
        val = float(v)
    except (TypeError, ValueError) as err:
        raise ValueError(f"{name} must be a valid float.") from err
    if not math.isfinite(val):
        raise ValueError(f"{name} must be finite (no NaN or Infinity).")
    return val


def _validate_strict_int(v: int, name: str) -> int:
    if type(v) is bool:
        raise ValueError(f"{name} cannot be a boolean.")
    if not isinstance(v, int):
        raise ValueError(f"{name} must be an integer.")
    return v


class FrozenInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class MetricDefinition(FrozenInput):
    name: str = Field(min_length=1, max_length=100)
    unit: str = Field(min_length=1, max_length=50)
    direction: Literal["minimize", "maximize"]

    @field_validator("name", "unit", mode="before")
    @classmethod
    def check_strings(cls, v: Any, info: Any) -> str:
        max_l = 100 if info.field_name == "name" else 50
        return _reject_whitespace_and_length(v, max_l, info.field_name)


class QualityConstraint(FrozenInput):
    metric: str = Field(min_length=1, max_length=100)
    relation: Literal["<=", ">=", "<", ">", "=="]
    threshold: float

    @field_validator("metric", mode="before")
    @classmethod
    def check_metric(cls, v: Any) -> str:
        return _reject_whitespace_and_length(v, 100, "metric")

    @field_validator("threshold", mode="before")
    @classmethod
    def check_threshold(cls, v: Any) -> float:
        return _validate_finite_float(v, "threshold")


class ArtifactRef(FrozenInput):
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=100)
    sha256: str = Field(min_length=64, max_length=64)
    readiness: Literal["unverified", "verified"] = "unverified"

    @field_validator("name", "version", mode="before")
    @classmethod
    def check_strings(cls, v: Any, info: Any) -> str:
        max_l = 200 if info.field_name == "name" else 100
        return _reject_whitespace_and_length(v, max_l, info.field_name)

    @field_validator("sha256", mode="before")
    @classmethod
    def check_sha256(cls, v: Any) -> str:
        if not isinstance(v, str) or not HEX_64_PATTERN.match(v.strip()):
            raise ValueError("sha256 must be a 64-character hexadecimal string.")
        return v.strip().lower()


class NotApplicable(FrozenInput):
    status: Literal["not_applicable"] = "not_applicable"
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason", mode="before")
    @classmethod
    def check_reason(cls, v: Any) -> str:
        return _reject_whitespace_and_length(v, 500, "reason")


class DatasetSplit(FrozenInput):
    role: Literal["search", "validation", "holdout"]
    split_hash: str = Field(min_length=64, max_length=64)

    @field_validator("split_hash", mode="before")
    @classmethod
    def check_hash(cls, v: Any) -> str:
        if not isinstance(v, str) or not HEX_64_PATTERN.match(v.strip()):
            raise ValueError("split_hash must be a 64-character hexadecimal string.")
        return v.strip().lower()


class DatasetSpec(FrozenInput):
    artifact_hash: str = Field(min_length=64, max_length=64)
    version: str = Field(min_length=1, max_length=100)
    splits: tuple[DatasetSplit, ...] = Field(min_length=1, max_length=8)

    @field_validator("artifact_hash", mode="before")
    @classmethod
    def check_artifact_hash(cls, v: Any) -> str:
        if not isinstance(v, str) or not HEX_64_PATTERN.match(v.strip()):
            raise ValueError("artifact_hash must be a 64-character hexadecimal string.")
        return v.strip().lower()

    @field_validator("version", mode="before")
    @classmethod
    def check_version(cls, v: Any) -> str:
        return _reject_whitespace_and_length(v, 100, "version")


class HardwareSpec(FrozenInput):
    target: str = Field(min_length=1, max_length=100)

    @field_validator("target", mode="before")
    @classmethod
    def check_target(cls, v: Any) -> str:
        return _reject_auto_latest(v, "hardware.target")


class BackendSpec(FrozenInput):
    name: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=50)

    @field_validator("name", mode="before")
    @classmethod
    def check_name(cls, v: Any) -> str:
        return _reject_auto_latest(v, "backend.name")

    @field_validator("version", mode="before")
    @classmethod
    def check_version(cls, v: Any) -> str:
        return _reject_auto_latest(v, "backend.version")


class EvaluatorSpec(FrozenInput):
    name: str = Field(min_length=1, max_length=100)
    protocol_version: str = Field(min_length=1, max_length=50)
    implementation_hash: str = Field(min_length=64, max_length=64)
    config_hash: str = Field(min_length=64, max_length=64)

    @field_validator("name", "protocol_version", mode="before")
    @classmethod
    def check_strings(cls, v: Any, info: Any) -> str:
        max_l = 100 if info.field_name == "name" else 50
        return _reject_whitespace_and_length(v, max_l, info.field_name)

    @field_validator("implementation_hash", "config_hash", mode="before")
    @classmethod
    def check_hashes(cls, v: Any, info: Any) -> str:
        if not isinstance(v, str) or not HEX_64_PATTERN.match(v.strip()):
            raise ValueError(f"{info.field_name} must be a 64-character hexadecimal string.")
        return v.strip().lower()


class BudgetSpec(FrozenInput):
    max_candidates: int = Field(gt=0)
    max_generations: int = Field(gt=0)
    wall_time_ms: int = Field(gt=0)
    compute_budget: float = Field(gt=0)
    compute_unit: str = Field(min_length=1, max_length=50)

    @field_validator("max_candidates", "max_generations", "wall_time_ms", mode="before")
    @classmethod
    def check_ints(cls, v: Any, info: Any) -> int:
        val = _validate_strict_int(v, info.field_name)
        if val <= 0:
            raise ValueError(f"{info.field_name} must be positive.")
        return val

    @field_validator("compute_budget", mode="before")
    @classmethod
    def check_budget(cls, v: Any) -> float:
        val = _validate_finite_float(v, "compute_budget")
        if val <= 0:
            raise ValueError("compute_budget must be positive.")
        return val

    @field_validator("compute_unit", mode="before")
    @classmethod
    def check_unit(cls, v: Any) -> str:
        return _reject_whitespace_and_length(v, 50, "compute_unit")


class TransformDeclaration(FrozenInput):
    name: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=50)

    @field_validator("name", "version", mode="before")
    @classmethod
    def check_strings(cls, v: Any, info: Any) -> str:
        max_l = 100 if info.field_name == "name" else 50
        return _reject_whitespace_and_length(v, max_l, info.field_name)


class StopCondition(FrozenInput):
    metric: str = Field(min_length=1, max_length=100)
    target_value: float
    relation: Literal["<=", ">=", "<", ">", "=="]

    @field_validator("metric", mode="before")
    @classmethod
    def check_metric(cls, v: Any) -> str:
        return _reject_whitespace_and_length(v, 100, "metric")

    @field_validator("target_value", mode="before")
    @classmethod
    def check_target(cls, v: Any) -> float:
        return _validate_finite_float(v, "target_value")


class ProblemDefinition(FrozenInput):
    task: str = Field(min_length=1, max_length=500)
    method_family: str = Field(min_length=1, max_length=100)
    metrics: tuple[MetricDefinition, ...] = Field(min_length=1, max_length=16)
    quality_constraints: tuple[QualityConstraint, ...] = Field(default=(), max_length=32)
    baselines: tuple[ArtifactRef, ...] = Field(min_length=1, max_length=16)
    dataset: DatasetSpec
    model: ArtifactRef | NotApplicable
    tokenizer: ArtifactRef | NotApplicable
    hardware: HardwareSpec
    backend: BackendSpec
    dtype: Literal["float32", "float64", "bfloat16"]
    evaluator: EvaluatorSpec
    seeds: tuple[int, ...] = Field(min_length=1, max_length=32)
    budget: BudgetSpec
    allowed_transforms: tuple[TransformDeclaration, ...] = Field(default=(), max_length=64)
    stop_conditions: tuple[StopCondition, ...] = Field(default=(), max_length=16)

    @field_validator("task", mode="before")
    @classmethod
    def check_task(cls, v: Any) -> str:
        return _reject_whitespace_and_length(v, 500, "task")

    @field_validator("method_family", mode="before")
    @classmethod
    def check_family(cls, v: Any) -> str:
        return _reject_whitespace_and_length(v, 100, "method_family")

    @field_validator("seeds", mode="before")
    @classmethod
    def check_seeds(cls, v: Any) -> tuple[int, ...]:
        if not isinstance(v, (list, tuple)):
            raise ValueError("seeds must be a sequence of integers.")
        validated: list[int] = []
        for s in v:
            val = _validate_strict_int(s, "seed")
            validated.append(val)
        if len(validated) != len(set(validated)):
            raise ValueError("seeds must be distinct (duplicate seeds rejected).")
        return tuple(validated)

    @field_validator("metrics", mode="before")
    @classmethod
    def check_metrics_uniqueness(cls, v: Any) -> Any:
        if isinstance(v, (list, tuple)):
            names = [m.get("name") if isinstance(m, dict) else getattr(m, "name", None) for m in v]
            cleaned_names = [n.strip() for n in names if isinstance(n, str)]
            if len(cleaned_names) != len(set(cleaned_names)):
                raise ValueError("metrics must have unique names.")
        return v

    @field_validator("baselines", mode="before")
    @classmethod
    def check_baselines_uniqueness(cls, v: Any) -> Any:
        if isinstance(v, (list, tuple)):
            names = [m.get("name") if isinstance(m, dict) else getattr(m, "name", None) for m in v]
            cleaned_names = [name_item.strip() for name_item in names if isinstance(name_item, str)]
            if len(cleaned_names) != len(set(cleaned_names)):
                raise ValueError("baselines must have unique names.")
        return v

    @model_validator(mode="after")
    def validate_cross_field_references(self) -> ProblemDefinition:
        declared_metric_names = {m.name for m in self.metrics}
        for qc in self.quality_constraints:
            if qc.metric not in declared_metric_names:
                raise ValueError(
                    f"Quality constraint refers to undeclared metric '{qc.metric}'. "
                    f"Declared: {sorted(declared_metric_names)}"
                )
        for sc in self.stop_conditions:
            if sc.metric not in declared_metric_names:
                raise ValueError(
                    f"Stop condition refers to undeclared metric '{sc.metric}'. "
                    f"Declared: {sorted(declared_metric_names)}"
                )
        return self


def normalize_definition_for_hashing(definition: ProblemDefinition) -> dict[str, Any]:
    """Normalize only declared set-like arrays, preserving order-sensitive fields."""
    data = definition.model_dump(mode="json")
    data["seeds"] = sorted(data["seeds"])
    data["allowed_transforms"] = sorted(
        data["allowed_transforms"],
        key=lambda x: (x["name"], x["version"]),
    )
    data["quality_constraints"] = sorted(
        data["quality_constraints"],
        key=lambda x: (x["metric"], x["relation"], x["threshold"]),
    )
    return data


def definition_hash(definition: ProblemDefinition) -> str:
    """Hash the versioned schema using the project's deterministic JSON serializer."""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "definition": normalize_definition_for_hashing(definition),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class ProblemCreateRequest(FrozenInput):
    definition: ProblemDefinition
    parent_spec_id: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def reject_client_verification(self) -> ProblemCreateRequest:
        artifacts = [*self.definition.baselines, self.definition.model, self.definition.tokenizer]
        if any(
            isinstance(item, ArtifactRef) and item.readiness != "unverified" for item in artifacts
        ):
            raise ValueError("Artifact verification is server-owned, not a client declaration.")
        return self

    @field_validator("parent_spec_id", mode="before")
    @classmethod
    def check_parent(cls, v: Any) -> str | None:
        if v is None:
            return None
        return _reject_whitespace_and_length(v, 200, "parent_spec_id")


class ProblemSpecSnapshot(FrozenInput):
    schema_version: Literal["problem-spec.v1"] = SCHEMA_VERSION
    content_hash: str = Field(min_length=64, max_length=64)
    spec_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    parent_spec_id: str | None = None
    version: int = Field(default=1, ge=1)
    campaign_id: str = Field(min_length=1, max_length=200)
    created_by: str = Field(min_length=1, max_length=200)
    created_at: str = Field(min_length=1, max_length=100)
    definition: ProblemDefinition


def require_comparable_specs(left: ProblemSpecSnapshot, right: ProblemSpecSnapshot) -> None:
    """Require identical frozen scientific context; never merge campaign results."""
    if (
        left.workspace_id != right.workspace_id
        or left.spec_id != right.spec_id
        or left.campaign_id != right.campaign_id
        or left.content_hash != right.content_hash
        or left.definition.evaluator != right.definition.evaluator
    ):
        raise ValueError("Result comparison requires the same frozen spec, campaign and evaluator.")


def problem_readiness(snapshot: ProblemSpecSnapshot) -> dict[str, Any]:
    """5A saves descriptors, not executable campaigns or artifact certificates."""
    reasons = ["execution_not_available_in_phase_5a"]
    artifacts = [*snapshot.definition.baselines, snapshot.definition.model,
                 snapshot.definition.tokenizer]
    reasons.extend(
        f"unverified_artifact:{artifact.name}" for artifact in artifacts
        if isinstance(artifact, ArtifactRef) and artifact.readiness == "unverified"
    )
    # Dataset/evaluator hashes identify inputs; they do not certify availability.
    reasons.extend(["dataset_availability_unverified", "evaluator_availability_unverified"])
    return {"spec_id": snapshot.spec_id, "campaign_id": snapshot.campaign_id,
            "ready_to_run": False, "blocked_reasons": reasons}


def generate_spec_id(
    workspace_id: str,
    content_hash: str,
    parent_spec_id: str | None,
    version: int,
) -> str:
    raw = canonical_json([workspace_id, content_hash, parent_spec_id, version])
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"spec_{digest}"


def generate_campaign_id(
    workspace_id: str,
    spec_id: str,
    seed: str | None = None,
) -> str:
    """Return the immutable campaign identity for a frozen ProblemSpec.

    ``seed`` remains accepted for callers from the initial 5A implementation,
    but a retry key must not create a second campaign for identical frozen
    content.  A revision has a new ``spec_id`` and therefore a new campaign.
    """
    del seed
    raw = canonical_json([workspace_id, spec_id])
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"cmp_{digest}"
