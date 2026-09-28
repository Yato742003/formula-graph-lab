"""G3 Pure Compatibility Decision Function and Port Mapping Models."""

from __future__ import annotations

import hashlib
import math
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from app.analysis_versions import canonical_json
from app.problem_spec import FrozenInput, _reject_whitespace_and_length

DomainType = Literal[
    "real",
    "strictly_positive_real",
    "non_negative_real",
    "integer",
    "positive_integer",
    "complex",
    "probability_simplex",
    "unit_interval",
    "boolean",
    "unknown",
]

NormalizationType = Literal[
    "none",
    "l1",
    "l2",
    "softmax",
    "layer_norm",
    "rms_norm",
    "batch_norm",
    "unknown",
]

MaskType = Literal["none", "causal", "padding", "sliding_window", "custom", "missing"]

COMPATIBILITY_POLICY_VERSION: Literal["compatibility-policy.v3"] = "compatibility-policy.v3"


class PortDescriptor(FrozenInput):
    equation_id: str = Field(min_length=1, max_length=200)
    version: int = Field(ge=1)
    scoped_symbol_id: str = Field(min_length=1, max_length=200)
    symbol_name: str = Field(min_length=1, max_length=100)
    domain: DomainType
    domain_is_reviewed: bool = False
    shape: tuple[int | str, ...] | None = None
    normalization: NormalizationType = "unknown"
    mask: MaskType = "missing"
    causal: bool | None = None
    # ponytail: one reviewed resource class, ceiling: no device-memory or dtype comparison,
    # upgrade: implementation-backed ports with measured resource requirements.
    resource_class: Literal["unknown", "not_applicable", "cpu", "gpu"] = "unknown"

    @field_validator("equation_id", "scoped_symbol_id", "symbol_name", mode="before")
    @classmethod
    def check_strings(cls, v: Any, info: Any) -> str:
        max_l = 100 if info.field_name == "symbol_name" else 200
        return _reject_whitespace_and_length(v, max_l, info.field_name)

    @field_validator("shape", mode="before")
    @classmethod
    def check_shape(cls, value: Any) -> tuple[int | str, ...] | None:
        if value is None:
            return None
        if not isinstance(value, (list, tuple)) or len(value) > 16:
            raise ValueError("shape must be a bounded sequence or null; () denotes a scalar.")
        dimensions: list[int | str] = []
        for dimension in value:
            if type(dimension) is int and dimension > 0:
                dimensions.append(dimension)
            elif isinstance(dimension, str):
                dimensions.append(_reject_whitespace_and_length(dimension, 100, "shape dimension"))
            else:
                raise ValueError("shape dimensions must be positive integers or symbols.")
        return tuple(dimensions)


class PortMapping(FrozenInput):
    mapping_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    producer_port: PortDescriptor
    consumer_port: PortDescriptor
    explicit_binding_reviewed: bool = False
    conversion_rule: str | None = None
    embedding_similarity: float | None = None

    @field_validator("mapping_id", "workspace_id", mode="before")
    @classmethod
    def check_ids(cls, v: Any, info: Any) -> str:
        return _reject_whitespace_and_length(v, 200, info.field_name)


class PortReference(FrozenInput):
    equation_id: str = Field(min_length=1, max_length=200)
    version: int = Field(ge=1, strict=True)
    symbol_name: str = Field(min_length=1, max_length=100)

    @field_validator("equation_id", "symbol_name", mode="before")
    @classmethod
    def require_text(cls, value: Any, info: Any) -> str:
        return _reject_whitespace_and_length(
            value, 200 if info.field_name == "equation_id" else 100, info.field_name,
        )


class PortMappingCreateRequest(FrozenInput):
    producer_port: PortReference | PortDescriptor
    consumer_port: PortReference | PortDescriptor
    conversion_rule: str | None = None
    embedding_similarity: float | None = None

    @field_validator("conversion_rule", mode="before")
    @classmethod
    def check_conversion_rule(cls, value: Any) -> str | None:
        if value is None:
            return None
        return _reject_whitespace_and_length(value, 200, "conversion_rule")

    @field_validator("embedding_similarity", mode="before")
    @classmethod
    def check_similarity(cls, value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool):
            raise ValueError("embedding_similarity must be a finite number.")
        try:
            similarity = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("embedding_similarity must be a finite number.") from exc
        if not math.isfinite(similarity) or not -1 <= similarity <= 1:
            raise ValueError("embedding_similarity must be between -1 and 1.")
        return similarity


class CompatibilityEvidence(FrozenInput):
    evidence_id: str = Field(min_length=1, max_length=200)
    scope_equation_id: str = Field(min_length=1, max_length=200)
    scope_version: int = Field(ge=1)
    check_type: str = Field(min_length=1, max_length=100)
    passed: bool
    details: str = Field(default="", max_length=1000)


class ResolvedDependency(FrozenInput):
    entity_id: str = Field(min_length=1, max_length=200)
    version: int = Field(ge=1)
    content_hash: str = Field(min_length=64, max_length=64)


class CompatibilityAssessment(FrozenInput):
    mapping_id: str
    status: Literal["compatible", "incompatible", "unknown"]
    freshness: Literal["current", "stale"] = "current"
    usable: bool
    reasons: tuple[str, ...]
    unresolved_requirements: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    policy_version: str = COMPATIBILITY_POLICY_VERSION
    dependency_fingerprint: str


class CompatibilityReviewCreateRequest(FrozenInput):
    decision: Literal["reviewed", "rejected"]
    notes: str = Field(default="", max_length=1000)

    @field_validator("notes", mode="before")
    @classmethod
    def check_notes(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("notes must be a string.")
        return value.strip()

    @model_validator(mode="after")
    def require_review_rationale(self) -> CompatibilityReviewCreateRequest:
        if not self.notes:
            raise ValueError("A compatibility review requires a non-empty rationale.")
        return self


class CompatibilityReview(FrozenInput):
    review_id: str = Field(min_length=1, max_length=200)
    mapping_id: str = Field(min_length=1, max_length=200)
    reviewer_id: str = Field(min_length=1, max_length=200)
    reviewer_role: str = Field(min_length=1, max_length=100)
    decision: Literal["reviewed", "rejected"]
    notes: str = Field(default="", max_length=1000)
    reviewed_at: str = Field(min_length=1, max_length=100)
    dependency_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


def compute_dependency_fingerprint(dependencies: tuple[ResolvedDependency, ...]) -> str:
    sorted_deps = sorted(
        [d.model_dump(mode="json") for d in dependencies],
        key=lambda x: (x["entity_id"], x["version"]),
    )
    raw = canonical_json(sorted_deps)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_mapping_id(
    workspace_id: str,
    producer: PortDescriptor,
    consumer: PortDescriptor,
    dependencies: tuple[ResolvedDependency, ...] = (),
) -> str:
    raw = canonical_json(
        [
            workspace_id,
            producer.model_dump(mode="json"),
            consumer.model_dump(mode="json"),
            compute_dependency_fingerprint(dependencies),
            COMPATIBILITY_POLICY_VERSION,
        ]
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"map_{digest}"


def assess_mapping(
    mapping: PortMapping,
    resolved_dependencies: tuple[ResolvedDependency, ...],
    evidence: tuple[CompatibilityEvidence, ...] = (),
    expected_fingerprint: str | None = None,
) -> CompatibilityAssessment:
    """Pure decision function for port compatibility (zero I/O, zero model calls)."""
    current_fingerprint = compute_dependency_fingerprint(resolved_dependencies)
    is_stale = False
    if expected_fingerprint is not None and expected_fingerprint != current_fingerprint:
        is_stale = True

    reasons: list[str] = []
    unresolved: list[str] = []
    valid_evidence_refs: list[str] = []

    # 1. Validate evidence scope
    for ev in evidence:
        producer_match = (
            ev.scope_equation_id == mapping.producer_port.equation_id
            and ev.scope_version == mapping.producer_port.version
        )
        consumer_match = (
            ev.scope_equation_id == mapping.consumer_port.equation_id
            and ev.scope_version == mapping.consumer_port.version
        )
        if producer_match or consumer_match:
            valid_evidence_refs.append(ev.evidence_id)
            if not ev.passed:
                unresolved.append("negative_evidence_requires_review")
        else:
            unresolved.append("invalid_evidence_scope")

    p = mapping.producer_port
    c = mapping.consumer_port
    if not resolved_dependencies:
        unresolved.append("missing_dependencies")

    # 2. Check dimension / shape compatibility
    if p.shape is None or c.shape is None:
        unresolved.append("unknown_dimension")
    elif len(p.shape) != len(c.shape):
        reasons.append("dimension_mismatch")
    else:
        for produced, required in zip(p.shape, c.shape, strict=True):
            if isinstance(produced, str) or isinstance(required, str):
                unresolved.append("unknown_dimension")
            elif produced != required:
                reasons.append("dimension_mismatch")

    # 3. Check domain compatibility
    # A producer domain must be a subset of the consumer's accepted domain.
    # Unknown domains and unreviewed inferences never establish that relation.
    accepted_domains = {
        "complex": {"complex", "real", "strictly_positive_real", "non_negative_real",
                    "integer", "positive_integer", "unit_interval"},
        "real": {"real", "strictly_positive_real", "non_negative_real", "integer",
                 "positive_integer", "unit_interval"},
        "non_negative_real": {"non_negative_real", "strictly_positive_real",
                              "positive_integer", "unit_interval"},
        "strictly_positive_real": {"strictly_positive_real", "positive_integer"},
        "integer": {"integer", "positive_integer"},
        "positive_integer": {"positive_integer"},
        "unit_interval": {"unit_interval"},
        "probability_simplex": {"probability_simplex"},
        "boolean": {"boolean"},
    }
    if p.domain == "unknown" or c.domain == "unknown":
        unresolved.append("unknown_domain")
    elif not p.domain_is_reviewed or not c.domain_is_reviewed:
        unresolved.append("unreviewed_inferred_domain")
    elif p.domain not in accepted_domains[c.domain]:
        reasons.append("domain_violation")

    # 4. Check causality / mask
    if c.causal is None or (c.causal is True and p.causal is None):
        unresolved.append("unknown_causality")
    elif c.causal is True and p.causal is False:
        reasons.append("causality_violation")

    if p.mask == "missing" or c.mask == "missing":
        unresolved.append("missing_mask_metadata")
    elif p.mask == "custom" or c.mask == "custom":
        unresolved.append("custom_mask_requires_evidence")
    elif p.mask != c.mask:
        reasons.append("mask_mismatch")

    # 5. Check normalization
    if p.normalization == "unknown" or c.normalization == "unknown":
        unresolved.append("unknown_normalization")
    elif p.normalization != c.normalization:
        reasons.append("normalization_mismatch")
    if mapping.conversion_rule:
        unresolved.append("unverified_conversion_rule")

    if p.resource_class == "unknown" or c.resource_class == "unknown":
        unresolved.append("unknown_resource_class")
    elif p.resource_class == "not_applicable" and c.resource_class != "not_applicable":
        unresolved.append("resource_capability_not_established")
    elif c.resource_class != "not_applicable" and p.resource_class != c.resource_class:
        reasons.append("resource_class_mismatch")

    # 6. Check explicit binding across scopes
    if p.scoped_symbol_id != c.scoped_symbol_id and not mapping.explicit_binding_reviewed:
        unresolved.append("unbound_symbol_scope")

    # Synthesis
    if reasons:
        status = "incompatible"
        usable = False
    elif unresolved:
        status = "unknown"
        usable = False
    else:
        status = "compatible"
        usable = not is_stale

    freshness = "stale" if is_stale else "current"
    if is_stale:
        reasons.append("stale_dependency")

    return CompatibilityAssessment(
        mapping_id=mapping.mapping_id,
        status=status,
        freshness=freshness,
        usable=usable,
        reasons=tuple(sorted(set(reasons))),
        unresolved_requirements=tuple(sorted(set(unresolved))),
        evidence_refs=tuple(valid_evidence_refs),
        policy_version=COMPATIBILITY_POLICY_VERSION,
        dependency_fingerprint=current_fingerprint,
    )
