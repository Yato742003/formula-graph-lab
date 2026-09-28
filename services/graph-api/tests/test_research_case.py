import json
import subprocess
import sys
from datetime import UTC, datetime

import pytest

from app.problem_spec import ProblemDefinition, ProblemSpecSnapshot, definition_hash
from app.research_case import (
    PRIMARY_METRIC,
    make_implementation_binding,
    registered_protocol_descriptor,
    run_registered_research_case,
    validate_registered_spec,
)
from app.research_case_worker import PROTOCOL_VERSION, SEEDS, evaluate
from app.research_compiler import compile_transform
from app.sandbox import container_command, validate_research_case_worker_result
from tests.test_research_compiler import MIX, _context

IMAGE = "sha256:" + "a" * 64
NOW = datetime(2026, 9, 28, tzinfo=UTC)


def _spec() -> ProblemSpecSnapshot:
    descriptor = registered_protocol_descriptor()
    definition = ProblemDefinition.model_validate({
        "task": "Compare one frozen feature-map mashup with rank-matched parents.",
        "method_family": "linear-attention",
        "metrics": [
            {"name": PRIMARY_METRIC, "unit": "mean-absolute-error", "direction": "minimize"}
        ],
        "quality_constraints": [descriptor["quality_constraint"] | {"metric": PRIMARY_METRIC}],
        "baselines": [
            {"name": name, "version": "1", "sha256": digest}
            for name, digest in descriptor["baselines"].items()
        ],
        "dataset": {
            "artifact_hash": descriptor["dataset_artifact_hash"],
            "version": "1",
            "splits": [
                {"role": role, "split_hash": digest}
                for role, digest in descriptor["dataset_splits"].items()
            ],
        },
        "model": {"status": "not_applicable", "reason": "Operator-only synthetic case."},
        "tokenizer": {"status": "not_applicable", "reason": "No text model is used."},
        "hardware": {"target": "cpu"},
        "backend": descriptor["backend"],
        "dtype": descriptor["dtype"],
        "evaluator": {
            "name": "fgl-attention-mashup",
            "protocol_version": PROTOCOL_VERSION,
            "implementation_hash": descriptor["evaluator_implementation_hash"],
            "config_hash": descriptor["evaluator_config_hash"],
        },
        "seeds": descriptor["seeds"],
        "budget": {
            "max_candidates": 1,
            "max_generations": 1,
            "wall_time_ms": 30_000,
            "compute_budget": 30,
            "compute_unit": "CPU-seconds",
        },
        "allowed_transforms": [
            {"name": "mix_positive_feature_maps", "version": "1"},
            {"name": "lower_mixture_to_concatenation", "version": "1"},
        ],
    })
    return ProblemSpecSnapshot(
        content_hash=definition_hash(definition),
        spec_id="spec-reference-case",
        workspace_id="ws-test",
        campaign_id="campaign-reference-case",
        created_by="researcher",
        created_at="2026-09-28T00:00:00Z",
        definition=definition,
    )


def _candidates():
    spec = _spec()
    context = _context().model_copy(update={"spec": spec})
    context = context.model_copy(update={
        "right": context.right.model_copy(update={"feature_rank": context.left.feature_rank})
    })
    mixture = compile_transform(MIX, context=context)
    lowering = compile_transform(
        {
            "operator": "lower_mixture_to_concatenation",
            "operator_version": "1",
            "target_node_id": mixture.candidate_id,
            "parameters": {},
            "bindings": {"mixture": mixture.candidate_id},
        },
        context=context,
        parent_candidate=mixture,
    )
    return spec, mixture, lowering


def test_registered_case_binds_exact_candidate_and_completes_all_frozen_splits():
    spec, mixture, candidate = _candidates()
    binding = make_implementation_binding(
        candidate, spec, parent_candidate=mixture, execution_image=IMAGE, now=NOW
    )

    receipt = run_registered_research_case(
        binding,
        candidate,
        spec,
        parent_candidate=mixture,
        worker_runner=lambda payload, **_kwargs: evaluate(payload),
        now=NOW,
    )

    assert binding.candidate_hash == candidate.content_hash
    assert binding.source_parent_refs == mixture.parents
    assert binding.feature_budget == binding.branch_rank * 2
    assert binding.author_code_claim is False
    assert receipt.outcome in {"supported_on_protocol", "failed_on_protocol"}
    assert receipt.performance_claim is False
    assert receipt.result_id == "exp_" + receipt.result_hash[:32]
    assert [item.seed for item in receipt.trials] == list(SEEDS)
    assert [item.split for item in receipt.trials] == [
        "search", "search", "validation", "holdout", "holdout"
    ]
    assert all(item.outcome == "passed_suite" for item in receipt.trials)
    assert all(
        measurement.feature_budget == binding.feature_budget
        for trial in receipt.trials
        for measurement in trial.measurements
    )
    assert receipt.holdout_ci95_low <= receipt.holdout_mean <= receipt.holdout_ci95_high


def test_failed_worker_is_retained_and_cannot_become_protocol_support():
    spec, mixture, candidate = _candidates()
    binding = make_implementation_binding(
        candidate, spec, parent_candidate=mixture, execution_image=IMAGE, now=NOW
    )

    def runner(payload, **_kwargs):
        if payload["seed"] == SEEDS[2]:
            return {"outcome": "timeout", "error_code": "WALL_CLOCK_TIMEOUT"}
        return evaluate(payload)

    receipt = run_registered_research_case(
        binding, candidate, spec, parent_candidate=mixture, worker_runner=runner, now=NOW
    )

    assert receipt.outcome == "inconclusive"
    assert receipt.quality_constraints_met is False
    assert receipt.holdout_mean is None
    assert receipt.trials[2].error_code == "WALL_CLOCK_TIMEOUT"


def test_spec_or_candidate_drift_fails_before_execution():
    spec, mixture, candidate = _candidates()
    binding = make_implementation_binding(
        candidate, spec, parent_candidate=mixture, execution_image=IMAGE, now=NOW
    )
    changed_definition = spec.definition.model_copy(update={
        "backend": spec.definition.backend.model_copy(update={"version": "3.13"})
    })
    changed_spec = spec.model_copy(update={
        "definition": changed_definition,
        "content_hash": definition_hash(changed_definition),
    })

    with pytest.raises(ValueError, match="registered CPU research protocol"):
        validate_registered_spec(changed_spec)
    with pytest.raises(ValueError, match="registered CPU research protocol"):
        run_registered_research_case(
            binding,
            candidate,
            changed_spec,
            parent_candidate=mixture,
            worker_runner=lambda payload, **_kwargs: evaluate(payload),
            now=NOW,
        )


def test_worker_and_controller_reject_unregistered_shapes_and_false_success():
    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "candidate_hash": "b" * 64,
        "seed": SEEDS[0],
        "lambda": 0.25,
        "branch_rank": 4,
    }
    result = evaluate(payload)
    assert validate_research_case_worker_result(json.dumps(result).encode()) == result
    result["checks"]["matched_feature_budget"] = False
    assert validate_research_case_worker_result(json.dumps(result).encode())["outcome"] == "error"

    invalid = payload | {"code": "print('never execute')"}
    completed = subprocess.run(
        [sys.executable, "-m", "app.research_case_worker"],
        input=json.dumps(invalid).encode(),
        capture_output=True,
        check=True,
    )
    assert json.loads(completed.stdout) == {
        "outcome": "error", "error_code": "INVALID_RESEARCH_CASE_INPUT"
    }
    assert container_command(
        IMAGE, "fgl-check-" + "a" * 32, 2_000, worker_kind="research_case"
    )[-1] == "app.research_case_worker"
