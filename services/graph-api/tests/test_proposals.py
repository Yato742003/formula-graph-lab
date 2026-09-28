"""P1 deterministic proposal trust-boundary tests; no model/provider required."""

import json

import pytest
from pydantic import ValidationError

from app.problem_spec import ProblemDefinition, ProblemSpecSnapshot, definition_hash
from app.proposals import (
    MAX_PROPOSAL_ATTEMPTS,
    MAX_PROPOSAL_BYTES,
    ProposalGenerationError,
    generate_validated_proposal,
    make_source_span_id,
    parse_source_span_id,
    validate_model_proposal,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
MIX = {
    "operator": "mix_positive_feature_maps",
    "operator_version": "1",
    "target_node_id": "eq-left",
    "parameters": {"lambda": 0.25},
    "bindings": {"left": "symbol-left", "right": "symbol-right"},
}
SPAN_LEFT = make_source_span_id("eq-left", "S1.E1")
SPAN_RIGHT = make_source_span_id("eq-right", "S2.E3")


def _spec() -> ProblemSpecSnapshot:
    definition = ProblemDefinition.model_validate(
        {
            "task": "Test feature-map mixture",
            "method_family": "linear-attention",
            "metrics": [{"name": "latency", "unit": "ms", "direction": "minimize"}],
            "baselines": [{"name": "baseline", "version": "1", "sha256": HASH_A}],
            "dataset": {
                "artifact_hash": HASH_B,
                "version": "1",
                "splits": [{"role": "search", "split_hash": HASH_C}],
            },
            "model": {"name": "model", "version": "1", "sha256": HASH_A},
            "tokenizer": {"status": "not_applicable", "reason": "Unit test."},
            "hardware": {"target": "cpu"},
            "backend": {"name": "numpy", "version": "2"},
            "dtype": "float64",
            "evaluator": {
                "name": "test-evaluator",
                "protocol_version": "1",
                "implementation_hash": HASH_B,
                "config_hash": HASH_C,
            },
            "seeds": [1],
            "budget": {
                "max_candidates": 2,
                "max_generations": 1,
                "wall_time_ms": 1000,
                "compute_budget": 1,
                "compute_unit": "CPU-seconds",
            },
            "allowed_transforms": [{"name": "mix_positive_feature_maps", "version": "1"}],
        }
    )
    return ProblemSpecSnapshot(
        content_hash=definition_hash(definition),
        spec_id="spec-test",
        workspace_id="ws-test",
        campaign_id="campaign-test",
        created_by="reviewer",
        created_at="2026-09-25T00:00:00Z",
        definition=definition,
    )


def _draft() -> dict:
    return {
        "problem_spec_id": "spec-test",
        "parent_ids": ["eq-left:v1", "eq-right:v1"],
        "source_span_ids": [SPAN_LEFT, SPAN_RIGHT],
        "transform": MIX,
        "assumptions": ["Both feature maps are positive on the reviewed domain."],
        "rationale": "Explore a convex feature-map mixture.",
        "expected_effect": "A new kernel hypothesis; quality remains to be measured.",
    }


def _validate(raw: object):
    return validate_model_proposal(
        raw,
        workspace_id="ws-test",
        spec=_spec(),
        allowed_parent_ids=frozenset({"eq-left:v1", "eq-right:v1"}),
        allowed_source_span_ids=frozenset({SPAN_LEFT, SPAN_RIGHT}),
        allowed_target_node_ids=frozenset({"eq-left", "eq-right"}),
    )


def _generate(invoke):
    return generate_validated_proposal(
        invoke,
        workspace_id="ws-test",
        spec=_spec(),
        allowed_parent_ids=frozenset({"eq-left:v1", "eq-right:v1"}),
        allowed_source_span_ids=frozenset({SPAN_LEFT, SPAN_RIGHT}),
        allowed_target_node_ids=frozenset({"eq-left", "eq-right"}),
    )


def test_proposal_fields_and_semantics_are_server_assigned():
    proposal = _validate(_draft())
    assert proposal.proposal_id.startswith("prop_")
    assert proposal.semantics_class == "hypothesis_changing"
    assert proposal.review_state == "pending"
    assert proposal.assumptions[0].origin == "ai_proposed"
    assert proposal.assumptions[0].discharged is False
    changed = proposal.model_dump(mode="json") | {"expected_effect": "Claim a win."}
    with pytest.raises(ValidationError, match="immutable content"):
        type(proposal).model_validate(changed)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("parent_ids", ["forged-parent"]),
        ("source_span_ids", ["forged-span"]),
        ("problem_spec_id", "other-spec"),
    ],
)
def test_fabricated_or_out_of_scope_references_are_rejected(field, value):
    draft = _draft()
    draft[field] = value
    with pytest.raises(ValueError):
        _validate(draft)


def test_source_span_id_binds_exact_entity_and_anchor():
    assert parse_source_span_id(SPAN_LEFT) == ("eq-left", "S1.E1")
    with pytest.raises(ValueError, match="invalid"):
        parse_source_span_id(SPAN_LEFT[:-1] + ("A" if SPAN_LEFT[-1] != "A" else "B"))


def test_unknown_target_and_undeclared_operator_are_rejected():
    draft = _draft()
    draft["transform"] = {**MIX, "target_node_id": "fabricated-target"}
    with pytest.raises(ValueError, match="target"):
        _validate(draft)
    draft["transform"] = {**MIX, "operator_version": "999"}
    with pytest.raises(ValidationError):
        _validate(draft)


@pytest.mark.parametrize("field", ["fitness", "approved", "verified", "evaluator", "budget"])
def test_model_cannot_self_assign_trust_or_frozen_run_fields(field):
    draft = _draft()
    draft[field] = True
    with pytest.raises(ValidationError):
        _validate(draft)


def test_nested_transform_extra_fields_are_rejected():
    draft = _draft()
    draft["transform"] = {**MIX, "fitness": 1.0}
    with pytest.raises(ValidationError):
        _validate(draft)


def test_injected_html_is_not_accepted_as_proposal_copy():
    for field in ("rationale", "expected_effect"):
        draft = _draft()
        draft[field] = "<img src=x onerror=alert(1)>"
        with pytest.raises(ValidationError):
            _validate(draft)
    draft = _draft()
    draft["assumptions"] = ["<script>alert(1)</script>"]
    with pytest.raises(ValidationError):
        _validate(draft)


def test_json_output_is_bounded_and_duplicate_keys_are_rejected():
    with pytest.raises(ValueError, match="size limit"):
        _validate(b" " * (MAX_PROPOSAL_BYTES + 1))
    duplicate_key_json = json.dumps(_draft()).replace(
        '"problem_spec_id": "spec-test",',
        '"problem_spec_id": "spec-test", "problem_spec_id": "other",',
    )
    with pytest.raises(ValueError, match="duplicate keys"):
        _validate(duplicate_key_json)


def test_model_assumption_never_becomes_a_review_or_discharged_obligation():
    draft = _draft()
    draft["assumptions"] = ["Assume the denominator is nonzero."]
    proposal = _validate(draft)
    assert proposal.review_state == "pending"
    assert [(item.origin, item.discharged) for item in proposal.assumptions] == [
        ("ai_proposed", False)
    ]


def test_adapter_retries_bad_output_once_then_returns_valid_proposal():
    calls = []

    def invoke(attempt: int, repair: bool):
        calls.append((attempt, repair))
        return {**_draft(), "approved": True} if attempt == 1 else _draft()

    result = _generate(invoke)
    assert result.attempts == 2
    assert result.proposal.review_state == "pending"
    assert calls == [(1, False), (2, True)]


def test_adapter_stops_at_retry_cap_and_does_not_retry_provider_error():
    calls = []

    def malformed(attempt: int, repair: bool):
        calls.append((attempt, repair))
        return {**_draft(), "fitness": 1.0}

    with pytest.raises(ProposalGenerationError) as error:
        _generate(malformed)
    assert error.value.attempts == MAX_PROPOSAL_ATTEMPTS
    assert len(calls) == MAX_PROPOSAL_ATTEMPTS

    provider_calls = 0

    def unavailable(_attempt: int, _repair: bool):
        nonlocal provider_calls
        provider_calls += 1
        raise RuntimeError("provider unavailable")

    with pytest.raises(RuntimeError, match="provider unavailable"):
        _generate(unavailable)
    assert provider_calls == 1
