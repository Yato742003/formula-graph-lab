"""Fixed, stdlib-only V2 numerical regression fixture; no candidate code is executed."""

from __future__ import annotations

import hashlib
import json
import math
import platform
import struct
import sys
from typing import Any

SUITE_VERSION = "feature-kernel-fixture.v1"
MAX_INPUT_BYTES = 8_192
MAX_FEATURE_RANK = 256
TOLERANCES = {"float64": 1e-10, "float32": 1e-5, "bfloat16": 5e-2}
DENOMINATOR_FLOORS = {"float64": 1e-12, "float32": 1e-7, "bfloat16": 1e-3}
UNDERFLOW_TEST_MAGNITUDES = {"float64": 1e-200, "float32": 1e-30, "bfloat16": 1e-25}


def _quantize(value: float, dtype: str) -> float:
    if dtype == "float64":
        return float(value)
    try:
        bits = struct.unpack(">I", struct.pack(">f", value))[0]
    except OverflowError:
        return math.copysign(math.inf, value)
    if dtype == "float32":
        return struct.unpack(">f", struct.pack(">I", bits))[0]
    upper, lower = bits >> 16, bits & 0xFFFF
    if lower > 0x8000 or (lower == 0x8000 and upper & 1):
        upper += 1
    return struct.unpack(">f", struct.pack(">I", (upper & 0xFFFF) << 16))[0]


def _next(state: int) -> tuple[int, float]:
    state ^= state << 13 & 0xFFFFFFFF
    state ^= state >> 17
    state ^= state << 5 & 0xFFFFFFFF
    state &= 0xFFFFFFFF
    return state, (state / 0xFFFFFFFF) * 2.0 - 1.0


def _vectors(seed: int, left_rank: int, right_rank: int, dtype: str):
    state = seed & 0xFFFFFFFF or 0x6D2B79F5
    vectors = []
    for rank in (left_rank, left_rank, right_rank, right_rank):
        row = []
        for _ in range(rank):
            state, value = _next(state)
            row.append(_quantize(value, dtype))
        vectors.append(row)
    return vectors


def _dot(left: list[float], right: list[float], dtype: str) -> float:
    total = 0.0
    for a, b in zip(left, right, strict=True):
        total = _quantize(total + _quantize(a * b, dtype), dtype)
    return total


def _safe_ratio(numerator: float, denominator: float, dtype: str) -> float | None:
    if not math.isfinite(denominator) or denominator <= DENOMINATOR_FLOORS[dtype]:
        return None
    result = numerator / denominator
    return result if math.isfinite(result) else None


def _attention(query, keys, values, dtype: str):
    result = []
    scale = math.sqrt(len(query[0]))
    for index, query_row in enumerate(query):
        scores = [_dot(query_row, keys[j], dtype) / scale for j in range(index + 1)]
        peak = max(scores)
        exp_scores = [_quantize(math.exp(score - peak), dtype) for score in scores]
        denominator = _quantize(sum(exp_scores), dtype)
        weights = [_quantize(score / denominator, dtype) for score in exp_scores]
        result.append([
            _quantize(sum(weights[j] * values[j][column] for j in range(index + 1)), dtype)
            for column in range(len(values[0]))
        ])
    return result


def _close(left: float, right: float, tolerance: float) -> bool:
    return math.isfinite(left) and math.isfinite(right) and abs(left - right) <= tolerance


def _evaluate(payload: dict[str, Any]) -> dict[str, Any]:
    seed, dtype = payload["seed"], payload["dtype"]
    weight = _quantize(payload["lambda"], dtype)
    left_rank, right_rank = payload["left_rank"], payload["right_rank"]
    tolerance = TOLERANCES[dtype]
    left_q, left_k, right_q, right_k = _vectors(seed, left_rank, right_rank, dtype)
    left_dot = _dot(left_q, left_k, dtype)
    right_dot = _dot(right_q, right_k, dtype)
    mixed = _quantize(weight * left_dot + (1.0 - weight) * right_dot, dtype)
    left_scale, right_scale = math.sqrt(weight), math.sqrt(1.0 - weight)
    concat_query = [_quantize(left_scale * value, dtype) for value in left_q]
    concat_query.extend(_quantize(right_scale * value, dtype) for value in right_q)
    concat_key = [_quantize(left_scale * value, dtype) for value in left_k]
    concat_key.extend(_quantize(right_scale * value, dtype) for value in right_k)
    concatenated = _dot(concat_query, concat_key, dtype)
    kernel_error = abs(mixed - concatenated)

    eps = {"float64": 1e-5, "float32": 2e-3, "bfloat16": 1e-1}[dtype]

    def scalar(q_left: list[float], q_right: list[float]) -> float:
        return weight * sum(a * b for a, b in zip(q_left, left_k, strict=True)) + (
            1.0 - weight
        ) * sum(a * b for a, b in zip(q_right, right_k, strict=True))

    gradient_error = 0.0
    finite_gradient = True
    for branch, key_row, coefficient in (
        (0, left_k, weight),
        (1, right_k, 1.0 - weight),
    ):
        for index, exact in enumerate(key_row):
            plus_left, minus_left = list(left_q), list(left_q)
            plus_right, minus_right = list(right_q), list(right_q)
            if branch == 0:
                plus_left[index] += eps
                minus_left[index] -= eps
            else:
                plus_right[index] += eps
                minus_right[index] -= eps
            estimate = (scalar(plus_left, plus_right) - scalar(minus_left, minus_right)) / (2 * eps)
            expected = coefficient * exact
            finite_gradient = finite_gradient and math.isfinite(estimate)
            gradient_error = max(gradient_error, abs(estimate - expected))

    seq = 4
    state = seed & 0xFFFFFFFF or 0x6D2B79F5
    query, keys, values = [], [], []
    for target in (query, keys, values):
        for _ in range(seq):
            row = []
            for _ in range(2):
                state, value = _next(state)
                row.append(_quantize(value, dtype))
            target.append(row)
    baseline = _attention(query, keys, values, dtype)
    changed_keys = [list(row) for row in keys]
    changed_values = [list(row) for row in values]
    changed_keys[-1] = [_quantize(value + 100.0, dtype) for value in changed_keys[-1]]
    changed_values[-1] = [_quantize(value - 100.0, dtype) for value in changed_values[-1]]
    perturbed = _attention(query, changed_keys, changed_values, dtype)
    future_error = max(
        abs(baseline[row][column] - perturbed[row][column])
        for row in range(seq - 1)
        for column in range(2)
    )
    causal_ok = future_error <= tolerance

    branch_limits_ok = (
        _close(_quantize(0.0 * left_dot + right_dot, dtype), right_dot, tolerance)
        and _close(_quantize(left_dot + 0.0 * right_dot, dtype), left_dot, tolerance)
    )
    denominator_floor = DENOMINATOR_FLOORS[dtype]
    singularity_guarded = (
        _safe_ratio(1.0, 0.0, dtype) is None
        and _safe_ratio(1.0, denominator_floor / 10.0, dtype) is None
    )
    huge = 1e308 if dtype == "float64" else 1e30
    extreme_guarded = _safe_ratio(_dot([huge], [huge], dtype), 1.0, dtype) is None
    underflow_operand = _quantize(UNDERFLOW_TEST_MAGNITUDES[dtype], dtype)
    underflow_product = _quantize(underflow_operand * underflow_operand, dtype)
    underflow_guarded = (
        math.isfinite(underflow_operand)
        and underflow_operand > 0.0
        and underflow_product == 0.0
    )
    checks = {
        "kernel_identity": kernel_error <= tolerance,
        "causal_future_perturbation": causal_ok,
        "finite_gradient": finite_gradient and gradient_error <= max(tolerance, 1e-8),
        "limiting_weights": branch_limits_ok,
        "near_zero_denominator": singularity_guarded,
        "extreme_norm_overflow": extreme_guarded,
        "underflow_detection": underflow_guarded,
    }
    failures = [name for name, passed in checks.items() if not passed]
    counterexample = None
    if failures:
        counterexample = {
            "failed_checks": failures,
            "seed": seed,
            "dtype": dtype,
            "lambda": weight,
            "left_query": left_q,
            "left_key": left_k,
            "right_query": right_q,
            "right_key": right_k,
            "baseline_attention": baseline,
            "perturbed_attention": perturbed,
        }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {
        "outcome": "counterexample" if failures else "passed_suite",
        "suite_version": SUITE_VERSION,
        "candidate_hash": payload["candidate_hash"],
        "input_hash": hashlib.sha256(encoded.encode()).hexdigest(),
        "seed": seed,
        "dtype": dtype,
        "tolerance": tolerance,
        "checks": checks,
        "measurements": {
            "kernel_max_abs_error": kernel_error,
            "future_perturbation_max_abs_error": future_error,
            "gradient_max_abs_error": gradient_error,
        },
        "counterexample": counterexample,
        "environment": {
            "implementation": platform.python_implementation(),
            "python": platform.python_version(),
            "suite": SUITE_VERSION,
        },
    }


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("oversized payload")
        payload = json.loads(raw)
        if not isinstance(payload, dict) or set(payload) != {
            "suite_version", "candidate_hash", "seed", "dtype", "lambda", "left_rank", "right_rank",
        }:
            raise ValueError("invalid payload fields")
        if payload["suite_version"] != SUITE_VERSION:
            raise ValueError("unknown suite version")
        if (
            type(payload["seed"]) is not int
            or not 0 <= payload["seed"] <= 2**31 - 1
            or payload["dtype"] not in TOLERANCES
            or type(payload["lambda"]) not in (int, float)
            or not math.isfinite(payload["lambda"])
            or not 0 <= payload["lambda"] <= 1
            or type(payload["left_rank"]) is not int
            or type(payload["right_rank"]) is not int
            or not 1 <= payload["left_rank"] <= MAX_FEATURE_RANK
            or not 1 <= payload["right_rank"] <= MAX_FEATURE_RANK
            or not isinstance(payload["candidate_hash"], str)
            or len(payload["candidate_hash"]) != 64
            or any(char not in "0123456789abcdef" for char in payload["candidate_hash"])
        ):
            raise ValueError("invalid bounded suite parameters")
        result = _evaluate(payload)
    except (ValueError, TypeError, KeyError, OverflowError, json.JSONDecodeError):
        result = {"outcome": "error", "error_code": "INVALID_NUMERICAL_SUITE_INPUT"}
    sys.stdout.write(json.dumps(result, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
