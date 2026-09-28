"""Tests for G1 ProblemSpec schema, identity, canonical hashing, and validation (T1)."""

from typing import Any

import pytest
from pydantic import ValidationError

from app.problem_spec import (
    NotApplicable,
    ProblemDefinition,
    ProblemSpecSnapshot,
    definition_hash,
)

DUMMY_SHA = "a" * 64
DUMMY_SHA_B = "b" * 64
DUMMY_SHA_C = "c" * 64


def make_valid_definition_payload() -> dict[str, Any]:
    return {
        "task": "Attention kernel runtime and precision optimization",
        "method_family": "FlashAttention-variant",
        "metrics": [
            {"name": "latency_us", "unit": "microseconds", "direction": "minimize"},
            {"name": "perplexity", "unit": "score", "direction": "minimize"},
        ],
        "quality_constraints": [
            {"metric": "perplexity", "relation": "<=", "threshold": 12.5},
        ],
        "baselines": [
            {
                "name": "flash_v2",
                "version": "2.5.6",
                "sha256": DUMMY_SHA,
                "readiness": "unverified",
            },
        ],
        "dataset": {
            "artifact_hash": DUMMY_SHA,
            "version": "1.0",
            "splits": [
                {"role": "search", "split_hash": DUMMY_SHA},
                {"role": "validation", "split_hash": DUMMY_SHA_B},
                {"role": "holdout", "split_hash": DUMMY_SHA_C},
            ],
        },
        "model": {
            "name": "llama3_8b",
            "version": "1.0.0",
            "sha256": DUMMY_SHA,
            "readiness": "unverified",
        },
        "tokenizer": {
            "status": "not_applicable",
            "reason": "Evaluator operates directly on dense token embeddings.",
        },
        "hardware": {
            "target": "NVIDIA-H100-SXM5-80GB",
        },
        "backend": {
            "name": "torch-triton",
            "version": "3.0.0",
        },
        "dtype": "float32",
        "evaluator": {
            "name": "benchmark_attention",
            "protocol_version": "v1",
            "implementation_hash": DUMMY_SHA,
            "config_hash": DUMMY_SHA_B,
        },
        "seeds": [42, 100, 2026],
        "budget": {
            "max_candidates": 100,
            "max_generations": 10,
            "wall_time_ms": 3600000,
            "compute_budget": 24.0,
            "compute_unit": "GPU-hours",
        },
        "allowed_transforms": [
            {"name": "loop_tiling", "version": "1.0"},
            {"name": "online_softmax", "version": "2.0"},
        ],
        "stop_conditions": [
            {"metric": "latency_us", "relation": "<=", "target_value": 45.0},
        ],
    }


def test_valid_definition_roundtrip():
    payload = make_valid_definition_payload()
    definition = ProblemDefinition.model_validate(payload)
    assert definition.task == "Attention kernel runtime and precision optimization"
    assert len(definition.metrics) == 2
    assert definition.dtype == "float32"
    assert isinstance(definition.tokenizer, NotApplicable)
    assert definition.tokenizer.status == "not_applicable"
    h1 = definition_hash(definition)
    assert len(h1) == 64
    assert h1 == definition_hash(definition)


def test_missing_required_fields():
    fields = (
        "task", "method_family", "metrics", "dataset",
        "hardware", "backend", "evaluator", "seeds", "budget",
    )
    for field in fields:
        corrupted = make_valid_definition_payload()
        del corrupted[field]
        with pytest.raises(ValidationError):
            ProblemDefinition.model_validate(corrupted)


def test_invalid_enum_rejected():
    payload = make_valid_definition_payload()
    # Invalid dtype
    corrupted = dict(payload, dtype="int32")
    with pytest.raises(ValidationError):
        ProblemDefinition.model_validate(corrupted)

    # Invalid metric direction
    corrupted = make_valid_definition_payload()
    corrupted["metrics"][0]["direction"] = "optimize_best"
    with pytest.raises(ValidationError):
        ProblemDefinition.model_validate(corrupted)

    # Invalid dataset split role
    corrupted = make_valid_definition_payload()
    corrupted["dataset"]["splits"][0]["role"] = "test_set"
    with pytest.raises(ValidationError):
        ProblemDefinition.model_validate(corrupted)


def test_boolean_as_integer_strictly_rejected():
    payload = make_valid_definition_payload()
    # Pass True/False as seed
    corrupted = dict(payload, seeds=[True, 100])
    with pytest.raises(ValidationError, match="boolean"):
        ProblemDefinition.model_validate(corrupted)

    # Pass True/False in budget
    corrupted = make_valid_definition_payload()
    corrupted["budget"]["max_candidates"] = True
    with pytest.raises(ValidationError, match="boolean"):
        ProblemDefinition.model_validate(corrupted)


def test_nan_and_inf_rejected():
    # NaN threshold in quality constraint
    corrupted = make_valid_definition_payload()
    corrupted["quality_constraints"][0]["threshold"] = float("nan")
    with pytest.raises(ValidationError, match="finite"):
        ProblemDefinition.model_validate(corrupted)

    # Inf in stop condition
    corrupted = make_valid_definition_payload()
    corrupted["stop_conditions"][0]["target_value"] = float("inf")
    with pytest.raises(ValidationError, match="finite"):
        ProblemDefinition.model_validate(corrupted)

    # -Inf in compute budget
    corrupted = make_valid_definition_payload()
    corrupted["budget"]["compute_budget"] = float("-inf")
    with pytest.raises(ValidationError, match="finite"):
        ProblemDefinition.model_validate(corrupted)


def test_whitespace_strings_rejected():
    payload = make_valid_definition_payload()
    for field in ("task", "method_family"):
        corrupted = dict(payload, **{field: "   \t\n  "})
        with pytest.raises(ValidationError, match="whitespace"):
            ProblemDefinition.model_validate(corrupted)


def test_auto_and_latest_hardware_backend_rejected():
    corrupted = make_valid_definition_payload()
    corrupted["hardware"]["target"] = "auto"
    with pytest.raises(ValidationError, match="cannot be \"auto\""):
        ProblemDefinition.model_validate(corrupted)

    corrupted = make_valid_definition_payload()
    corrupted["backend"]["version"] = "latest"
    with pytest.raises(ValidationError, match="cannot be \"latest\""):
        ProblemDefinition.model_validate(corrupted)


def test_duplicate_metrics_rejected():
    payload = make_valid_definition_payload()
    payload["metrics"] = [
        {"name": "latency", "unit": "us", "direction": "minimize"},
        {"name": "latency", "unit": "ms", "direction": "minimize"},
    ]
    with pytest.raises(ValidationError, match="unique names"):
        ProblemDefinition.model_validate(payload)


def test_duplicate_seeds_rejected():
    payload = make_valid_definition_payload()
    payload["seeds"] = [42, 100, 42]
    with pytest.raises(ValidationError, match="distinct"):
        ProblemDefinition.model_validate(payload)


def test_duplicate_baselines_rejected():
    payload = make_valid_definition_payload()
    payload["baselines"] = [
        {"name": "baseline_1", "version": "1.0", "sha256": DUMMY_SHA},
        {"name": "baseline_1", "version": "2.0", "sha256": DUMMY_SHA_B},
    ]
    with pytest.raises(ValidationError, match="unique names"):
        ProblemDefinition.model_validate(payload)


def test_undeclared_metric_references_rejected():
    # Constraint references undeclared metric
    corrupted = make_valid_definition_payload()
    corrupted["quality_constraints"] = [
        {"metric": "non_existent_score", "relation": "<=", "threshold": 0.5}
    ]
    with pytest.raises(ValidationError, match="undeclared metric"):
        ProblemDefinition.model_validate(corrupted)

    # Stop condition references undeclared metric
    corrupted = make_valid_definition_payload()
    corrupted["stop_conditions"] = [
        {"metric": "non_existent_score", "relation": ">=", "target_value": 0.9}
    ]
    with pytest.raises(ValidationError, match="undeclared metric"):
        ProblemDefinition.model_validate(corrupted)


def test_extra_unknown_fields_forbidden():
    payload = make_valid_definition_payload()
    corrupted = dict(payload, approved=True, fitness=0.95, workspace_id="ws_leak")
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ProblemDefinition.model_validate(corrupted)


def test_set_like_reordering_preserves_hash():
    p1 = make_valid_definition_payload()
    p2 = make_valid_definition_payload()

    # Reorder seeds
    p2["seeds"] = [2026, 42, 100]
    # Reorder allowed_transforms
    p2["allowed_transforms"] = [
        {"name": "online_softmax", "version": "2.0"},
        {"name": "loop_tiling", "version": "1.0"},
    ]

    d1 = ProblemDefinition.model_validate(p1)
    d2 = ProblemDefinition.model_validate(p2)

    assert d1.seeds != d2.seeds
    assert definition_hash(d1) == definition_hash(d2)


def test_semantic_change_alters_hash():
    base = ProblemDefinition.model_validate(make_valid_definition_payload())
    h_base = definition_hash(base)

    # Change metric direction
    p_metric = make_valid_definition_payload()
    p_metric["metrics"][0]["direction"] = "maximize"
    assert definition_hash(ProblemDefinition.model_validate(p_metric)) != h_base

    # Change evaluator protocol version
    p_eval = make_valid_definition_payload()
    p_eval["evaluator"]["protocol_version"] = "v2"
    assert definition_hash(ProblemDefinition.model_validate(p_eval)) != h_base

    # Change dtype
    p_dtype = make_valid_definition_payload()
    p_dtype["dtype"] = "bfloat16"
    assert definition_hash(ProblemDefinition.model_validate(p_dtype)) != h_base

    # Change a seed
    p_seed = make_valid_definition_payload()
    p_seed["seeds"] = [43, 100, 2026]
    assert definition_hash(ProblemDefinition.model_validate(p_seed)) != h_base


def test_timestamp_and_actor_do_not_alter_content_hash():
    p = make_valid_definition_payload()
    definition = ProblemDefinition.model_validate(p)
    content_hash = definition_hash(definition)

    s1 = ProblemSpecSnapshot(
        schema_version="problem-spec.v1",
        content_hash=content_hash,
        spec_id="spec_123",
        workspace_id="ws_a",
        campaign_id="cmp_1",
        created_by="user_alice",
        created_at="2026-09-14T10:00:00Z",
        definition=definition,
    )
    s2 = ProblemSpecSnapshot(
        schema_version="problem-spec.v1",
        content_hash=content_hash,
        spec_id="spec_123",
        workspace_id="ws_a",
        campaign_id="cmp_2",
        created_by="user_bob",
        created_at="2026-09-14T14:30:00Z",
        definition=definition,
    )
    h_def = definition_hash(s1.definition)
    assert s1.content_hash == s2.content_hash == h_def


def test_nested_mutation_fails_on_frozen_model():
    p = make_valid_definition_payload()
    definition = ProblemDefinition.model_validate(p)
    with pytest.raises(ValidationError):
        definition.task = "Mutated task"
    with pytest.raises(ValidationError):
        definition.hardware.target = "Mutated target"
