import json
import subprocess
import sys

import pytest

from app.numerical_worker import SUITE_VERSION, UNDERFLOW_TEST_MAGNITUDES, _evaluate, _quantize
from app.sandbox import container_command, validate_numerical_worker_result

IMAGE = "sha256:" + "a" * 64


def _payload(*, seed=17, dtype="float64", weight=0.37):
    return {
        "suite_version": SUITE_VERSION,
        "candidate_hash": "b" * 64,
        "seed": seed,
        "dtype": dtype,
        "lambda": weight,
        "left_rank": 3,
        "right_rank": 5,
    }


@pytest.mark.parametrize("dtype", ["float64", "float32", "bfloat16"])
@pytest.mark.parametrize("weight", [0.0, 0.37, 1.0])
def test_fixed_suite_is_reproducible_and_exposes_all_checks(dtype, weight):
    payload = _payload(dtype=dtype, weight=weight)
    first, second = _evaluate(payload), _evaluate(payload)
    assert first == second
    assert first["outcome"] == "passed_suite", first
    assert set(first["checks"]) == {
        "kernel_identity", "causal_future_perturbation", "finite_gradient",
        "limiting_weights", "near_zero_denominator", "extreme_norm_overflow",
        "underflow_detection",
    }


def test_seed_changes_fixture_and_input_hash():
    assert _evaluate(_payload(seed=17))["input_hash"] != _evaluate(_payload(seed=18))["input_hash"]


@pytest.mark.parametrize("dtype", ["float64", "float32", "bfloat16"])
def test_underflow_probe_operands_are_representable_before_the_product(dtype):
    operand = _quantize(UNDERFLOW_TEST_MAGNITUDES[dtype], dtype)
    assert operand > 0.0
    assert _quantize(operand * operand, dtype) == 0.0
    assert _evaluate(_payload(dtype=dtype))["checks"]["underflow_detection"] is True


@pytest.mark.parametrize("change", [
    {"extra": "code"},
    {"suite_version": "unknown"},
    {"seed": True},
    {"dtype": "float16"},
    {"lambda": float("nan")},
    {"left_rank": 257},
    {"candidate_hash": "not-a-hash"},
])
def test_worker_rejects_unrecognized_or_unbounded_input(change):
    payload = _payload()
    payload.update(change)
    result = subprocess.run(
        [sys.executable, "-m", "app.numerical_worker"],
        input=json.dumps(payload, allow_nan=True).encode(),
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout) == {
        "outcome": "error", "error_code": "INVALID_NUMERICAL_SUITE_INPUT",
    }


def test_numerical_result_validator_rejects_false_success_and_nonfinite_measurement():
    result = _evaluate(_payload())
    assert validate_numerical_worker_result(json.dumps(result).encode()) == result
    result["checks"]["kernel_identity"] = False
    assert validate_numerical_worker_result(json.dumps(result).encode())["outcome"] == "error"
    result = _evaluate(_payload())
    result["measurements"]["kernel_max_abs_error"] = float("inf")
    assert validate_numerical_worker_result(json.dumps(result).encode())["outcome"] == "error"
    result = _evaluate(_payload())
    result["dtype"] = []
    assert validate_numerical_worker_result(json.dumps(result).encode())["outcome"] == "error"


def test_numerical_worker_is_fixed_allowlisted_container_entrypoint():
    command = container_command(
        IMAGE, "fgl-check-" + "a" * 32, 2000, worker_kind="numerical",
    )
    assert command[-1] == "app.numerical_worker"
    with pytest.raises(ValueError):
        container_command(IMAGE, "fgl-check-" + "a" * 32, 2000, worker_kind="model-code")
