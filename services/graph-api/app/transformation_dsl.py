"""The first two versioned research moves; model output is only untrusted input."""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter, field_validator

from app.analysis_versions import canonical_json
from app.problem_spec import FrozenInput, TransformDeclaration

_ID = r"^[A-Za-z0-9_:-]{1,200}$"
MAX_TRANSFORM_BYTES = 8 * 1024


class MixParameters(FrozenInput):
    lambda_weight: float = Field(alias="lambda", ge=0, le=1)

    @field_validator("lambda_weight", mode="before")
    @classmethod
    def require_finite_number(cls, value: Any) -> float:
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("lambda must be a finite number, not a string or boolean.")
        return float(value)


class MixBindings(FrozenInput):
    left: str = Field(pattern=_ID)
    right: str = Field(pattern=_ID)


class MixPositiveFeatureMaps(FrozenInput):
    operator: Literal["mix_positive_feature_maps"]
    operator_version: Literal["1"]
    target_node_id: str = Field(pattern=_ID)
    parameters: MixParameters
    bindings: MixBindings


class EmptyParameters(FrozenInput):
    pass


class ConcatenationBindings(FrozenInput):
    mixture: str = Field(pattern=_ID)


class LowerMixtureToConcatenation(FrozenInput):
    operator: Literal["lower_mixture_to_concatenation"]
    operator_version: Literal["1"]
    target_node_id: str = Field(pattern=_ID)
    parameters: EmptyParameters
    bindings: ConcatenationBindings


Transform = Annotated[
    MixPositiveFeatureMaps | LowerMixtureToConcatenation,
    Field(discriminator="operator"),
]
_TRANSFORM_ADAPTER = TypeAdapter(Transform)


@dataclass(frozen=True)
class OperatorManifest:
    name: str
    version: str
    semantics_class: Literal["preserving", "approximation", "hypothesis_changing"]
    matched_ir_type: str
    parameter_schema: type[FrozenInput]
    typed_ports: tuple[str, ...]
    preconditions: tuple[str, ...]
    generated_obligations: tuple[str, ...]
    postconditions: tuple[str, ...]
    lowering_template: str
    resource_estimate: str


MANIFESTS = MappingProxyType({
    ("mix_positive_feature_maps", "1"): OperatorManifest(
        name="mix_positive_feature_maps",
        version="1",
        semantics_class="hypothesis_changing",
        matched_ir_type="positive_feature_map_pair.v1",
        parameter_schema=MixParameters,
        typed_ports=("left", "right"),
        preconditions=("same_input_contract", "real_feature_outputs"),
        generated_obligations=(
            "lambda_in_unit_interval", "compatible_ports", "nonzero_denominator",
            "causal_mask_preserved", "output_rank_sum",
        ),
        postconditions=("mixed_kernel_hypothesis",),
        lowering_template="kernel_weighted_sum.v1",
        resource_estimate="rank=r_left+r_right; no performance claim",
    ),
    ("lower_mixture_to_concatenation", "1"): OperatorManifest(
        name="lower_mixture_to_concatenation",
        version="1",
        semantics_class="preserving",
        matched_ir_type="positive_feature_map_mixture.v1",
        parameter_schema=EmptyParameters,
        typed_ports=("mixture",),
        preconditions=("lambda_in_unit_interval", "real_feature_outputs"),
        generated_obligations=("kernel_identity", "normalization_domain"),
        postconditions=("feature_concatenation_representation",),
        lowering_template="feature_concatenation.v1",
        resource_estimate="rank=r_left+r_right; no performance claim",
    ),
})


def validate_transform(
    raw: object, allowed_transforms: tuple[TransformDeclaration, ...]
) -> tuple[MixPositiveFeatureMaps | LowerMixtureToConcatenation, OperatorManifest]:
    """Reject undeclared/version-unknown moves before any lookup, rewrite or execution."""
    try:
        encoded = canonical_json(raw).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("Transform must be finite JSON data.") from exc
    if len(encoded) > MAX_TRANSFORM_BYTES:
        raise ValueError("Transform exceeds the input size limit.")
    transform = _TRANSFORM_ADAPTER.validate_python(raw)
    key = (transform.operator, transform.operator_version)
    manifest = MANIFESTS.get(key)
    if manifest is None or key not in {(item.name, item.version) for item in allowed_transforms}:
        raise ValueError("Transform operator/version is not allowed by the frozen ProblemSpec.")
    return transform, manifest
