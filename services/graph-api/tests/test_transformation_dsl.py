"""D1 trust-boundary checks for the deliberately small first DSL allowlist."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.problem_spec import TransformDeclaration
from app.transformation_dsl import validate_transform

ALLOWED = (
    TransformDeclaration(name="mix_positive_feature_maps", version="1"),
    TransformDeclaration(name="lower_mixture_to_concatenation", version="1"),
)
MIX = {
    "operator": "mix_positive_feature_maps",
    "operator_version": "1",
    "target_node_id": "attention-kernel-port",
    "parameters": {"lambda": 0.3},
    "bindings": {"left": "feature-a", "right": "feature-b"},
}


def test_enabled_manifests_are_strict_and_semantically_distinct():
    move, manifest = validate_transform(MIX, ALLOWED)
    assert move.parameters.lambda_weight == 0.3
    assert manifest.semantics_class == "hypothesis_changing"
    assert manifest.generated_obligations
    lower, lowering = validate_transform({
        "operator": "lower_mixture_to_concatenation",
        "operator_version": "1",
        "target_node_id": "mixture-1",
        "parameters": {},
        "bindings": {"mixture": "mixture-1"},
    }, ALLOWED)
    assert lower.bindings.mixture == "mixture-1"
    assert lowering.semantics_class == "preserving"
    assert lowering.lowering_template != manifest.lowering_template


@pytest.mark.parametrize("field,value", [
    ("operator", "arbitrary_python"),
    ("operator_version", "2"),
    ("target_node_id", "__import__('os').system('id')"),
])
def test_unknown_or_executable_identifiers_are_rejected(field, value):
    raw = {**MIX, field: value}
    with pytest.raises((ValidationError, ValueError)):
        validate_transform(raw, ALLOWED)


@pytest.mark.parametrize("value", [-0.1, 1.1, True, "0.3", float("nan"), float("inf")])
def test_lambda_is_finite_numeric_and_bounded(value):
    raw = deepcopy(MIX)
    raw["parameters"]["lambda"] = value
    with pytest.raises((ValidationError, ValueError)):
        validate_transform(raw, ALLOWED)


def test_extra_fields_deep_trees_and_undeclared_operators_fail_closed():
    with pytest.raises(ValidationError):
        validate_transform({**MIX, "fitness": 1.0}, ALLOWED)
    with pytest.raises(ValidationError):
        validate_transform({**MIX, "parameters": {"lambda": 0.3, "code": "print(1)"}}, ALLOWED)
    with pytest.raises(ValueError, match="size limit"):
        validate_transform({**MIX, "tree": {"children": [MIX] * 1000}}, ALLOWED)
    with pytest.raises(ValueError, match="not allowed"):
        validate_transform(MIX, ())


def test_excessive_depth_is_a_validation_error_not_a_server_crash():
    deep: dict = {}
    root = deep
    for _ in range(1100):
        child: dict = {}
        deep["child"] = child
        deep = child
    with pytest.raises(ValueError, match="finite JSON data|size limit"):
        validate_transform({**MIX, "tree": root}, ALLOWED)
