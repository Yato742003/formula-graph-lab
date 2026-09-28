import json

from app.paper_attention_worker import (
    PERFORMER_COMMIT,
    PERFORMER_SOURCE_SHA256,
    PROTOCOL_VERSION,
    evaluate_payload,
)
from app.sandbox import container_command, validate_performer_worker_result

IMAGE = "sha256:" + "a" * 64


def _result():
    return {
        "outcome": "passed_suite",
        "protocol_version": PROTOCOL_VERSION,
        "implementation_commit": PERFORMER_COMMIT,
        "source_sha256": PERFORMER_SOURCE_SHA256,
        "seed": 42,
        "sequence_length": 16,
        "input_hash": "b" * 64,
        "checks": {
            "finite_outputs": True,
            "causal_future_key_perturbation": True,
            "causal_future_extreme_key_perturbation": True,
            "causal_future_value_perturbation": True,
        },
        "measurements": {
            "mean_abs_error_vs_exact": 0.0,
            "causal_future_key_perturbation_max_abs_error": 0.0,
            "causal_future_extreme_key_perturbation_max_abs_error": 0.0,
            "causal_future_value_perturbation_max_abs_error": 0.0,
        },
        "environment": {"python": "3.12", "jax": "0.4.35", "backend": "cpu"},
        "scope": "paper_implementation_diagnostic_no_performance_claim",
    }


def test_worker_rejects_unbounded_or_unregistered_request_without_loading_code():
    result = evaluate_payload(json.dumps({"protocol_version": PROTOCOL_VERSION}).encode())
    assert result == {"outcome": "error", "error_code": "invalid_scope"}


def test_validator_accepts_only_the_registered_paper_diagnostic_contract():
    result = _result()
    assert validate_performer_worker_result(json.dumps(result).encode()) == result

    result["implementation_commit"] = "f" * 40
    assert validate_performer_worker_result(json.dumps(result).encode())["outcome"] == "error"


def test_validator_rejects_unverified_fitness_claims_and_nonfinite_measurements():
    result = _result()
    result["fitness"] = 1.0
    assert validate_performer_worker_result(json.dumps(result).encode())["outcome"] == "error"

    result = _result()
    result["measurements"]["mean_abs_error_vs_exact"] = float("inf")
    assert validate_performer_worker_result(
        json.dumps(result, allow_nan=True).encode()
    )["outcome"] == "error"


def test_worker_entrypoint_is_a_fixed_networkless_container_command():
    command = container_command(
        IMAGE, "fgl-check-" + "a" * 32, 30_000, worker_kind="performer",
    )
    assert command[-1] == "app.paper_attention_worker"
    assert "--network=none" in command
    assert "--read-only" in command
    assert any(arg.startswith("--cpuset-cpus=") for arg in command)
