"""Fixed synthetic attention pilot; no candidate or paper code is executed."""

from __future__ import annotations

import hashlib
import json
import math
import platform
import sys
from typing import Any

from app.numerical_worker import _next

PROTOCOL_VERSION = "attention-kernel-synthetic-pilot.v1"
MAX_INPUT_BYTES = 512
SEQUENCE_LENGTHS = (16, 64, 256)
SEEDS = (42, 43, 44, 45, 46)
FEATURE_DIM = 5
LEFT_RANK = 3
RIGHT_RANK = 2
MIX_WEIGHT = 0.3
TOLERANCE = 1e-10


def _inputs(seed: int, length: int):
    state = seed & 0xFFFFFFFF or 0x6D2B79F5
    result = []
    for width in (FEATURE_DIM, FEATURE_DIM, FEATURE_DIM):
        rows = []
        for _ in range(length):
            row = []
            for _ in range(width):
                state, value = _next(state)
                row.append(value)
            rows.append(row)
        result.append(rows)
    return result


def _dot(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def _features(vector: list[float], start: int, stop: int) -> list[float]:
    scale = math.sqrt(FEATURE_DIM)
    return [math.exp(vector[index] / scale) for index in range(start, stop)]


def _kernel(method: str, query: list[float], key: list[float]) -> float:
    if method == "exact_softmax":
        return _dot(query, key) / math.sqrt(FEATURE_DIM)
    left = _dot(_features(query, 0, LEFT_RANK), _features(key, 0, LEFT_RANK))
    if method == "parent_a":
        return left
    right = _dot(_features(query, LEFT_RANK, FEATURE_DIM), _features(key, LEFT_RANK, FEATURE_DIM))
    if method == "parent_b":
        return right
    if method == "mixed_direct":
        return MIX_WEIGHT * left + (1.0 - MIX_WEIGHT) * right
    if method == "mixed_concatenated":
        query_features = [math.sqrt(MIX_WEIGHT) * value for value in _features(query, 0, LEFT_RANK)]
        query_features.extend(
            math.sqrt(1.0 - MIX_WEIGHT) * value
            for value in _features(query, LEFT_RANK, FEATURE_DIM)
        )
        key_features = [math.sqrt(MIX_WEIGHT) * value for value in _features(key, 0, LEFT_RANK)]
        key_features.extend(
            math.sqrt(1.0 - MIX_WEIGHT) * value
            for value in _features(key, LEFT_RANK, FEATURE_DIM)
        )
        return _dot(query_features, key_features)
    raise ValueError("Unknown registered attention method.")


def _attend(
    method: str,
    queries: list[list[float]],
    keys: list[list[float]],
    values: list[list[float]],
) -> list[list[float]]:
    outputs = []
    for index, query in enumerate(queries):
        scores = [_kernel(method, query, keys[j]) for j in range(index + 1)]
        if method == "exact_softmax":
            peak = max(scores)
            weights = [math.exp(score - peak) for score in scores]
        else:
            weights = scores
        denominator = sum(weights)
        if not math.isfinite(denominator) or denominator <= 0:
            raise ArithmeticError("Attention denominator is not finite and positive.")
        outputs.append([
            sum(weights[j] * values[j][column] for j in range(index + 1)) / denominator
            for column in range(len(values[0]))
        ])
    return outputs


def _mean_abs(left: list[list[float]], right: list[list[float]]) -> float:
    errors = [
        abs(a - b)
        for left_row, right_row in zip(left, right, strict=True)
        for a, b in zip(left_row, right_row, strict=True)
    ]
    return sum(errors) / len(errors)


def evaluate(seed: int) -> dict[str, Any]:
    if type(seed) is not int or seed not in SEEDS:
        raise ValueError("Seed is not declared by the frozen synthetic protocol.")
    measurements = []
    all_outputs: list[list[list[float]]] = []
    hash_inputs = []
    for length in SEQUENCE_LENGTHS:
        queries, keys, values = _inputs(seed, length)
        exact = _attend("exact_softmax", queries, keys, values)
        parent_a = _attend("parent_a", queries, keys, values)
        parent_b = _attend("parent_b", queries, keys, values)
        mixed_direct = _attend("mixed_direct", queries, keys, values)
        mixed_concatenated = _attend("mixed_concatenated", queries, keys, values)

        changed_keys = [list(row) for row in keys]
        changed_values = [list(row) for row in values]
        changed_keys[-1] = [value + 100.0 for value in changed_keys[-1]]
        changed_values[-1] = [value - 100.0 for value in changed_values[-1]]
        perturbed = _attend("mixed_concatenated", queries, changed_keys, changed_values)
        causal_error = max(
            abs(mixed_concatenated[row][column] - perturbed[row][column])
            for row in range(length - 1)
            for column in range(len(values[0]))
        )
        identity_error = max(
            abs(mixed_direct[row][column] - mixed_concatenated[row][column])
            for row in range(length)
            for column in range(len(values[0]))
        )
        methods = (exact, parent_a, parent_b, mixed_direct, mixed_concatenated, perturbed)
        all_outputs.extend(methods)
        hash_inputs.append({"length": length, "q": queries, "k": keys, "v": values})
        measurements.append({
            "sequence_length": length,
            "parent_a_mean_abs_error_vs_exact": _mean_abs(parent_a, exact),
            "parent_b_mean_abs_error_vs_exact": _mean_abs(parent_b, exact),
            "candidate_mean_abs_error_vs_exact": _mean_abs(mixed_concatenated, exact),
            "candidate_mean_abs_delta_vs_parent_a": _mean_abs(mixed_concatenated, parent_a),
            "candidate_mean_abs_delta_vs_parent_b": _mean_abs(mixed_concatenated, parent_b),
            "mixture_concatenation_max_abs_error": identity_error,
            "causal_future_perturbation_max_abs_error": causal_error,
        })

    finite = all(math.isfinite(value) for result in all_outputs for row in result for value in row)
    checks = {
        "mixture_concatenation_identity": all(
            item["mixture_concatenation_max_abs_error"] <= TOLERANCE for item in measurements
        ),
        "causal_future_perturbation": all(
            item["causal_future_perturbation_max_abs_error"] <= TOLERANCE
            for item in measurements
        ),
        "finite_outputs": finite,
    }
    identity = json.dumps(
        {"protocol": PROTOCOL_VERSION, "seed": seed, "inputs": hash_inputs},
        sort_keys=True, separators=(",", ":"), allow_nan=False,
    )
    return {
        "outcome": "passed_suite" if all(checks.values()) else "counterexample",
        "protocol_version": PROTOCOL_VERSION,
        "seed": seed,
        "input_hash": hashlib.sha256(identity.encode()).hexdigest(),
        "checks": checks,
        "measurements": measurements,
        "environment": {
            "implementation": platform.python_implementation(),
            "python": platform.python_version(),
            "protocol": PROTOCOL_VERSION,
        },
        "scope": "synthetic_operator_diagnostic_no_performance_claim",
    }


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("oversized payload")
        payload = json.loads(raw)
        if (
            not isinstance(payload, dict)
            or set(payload) != {"protocol_version", "seed"}
            or payload["protocol_version"] != PROTOCOL_VERSION
        ):
            raise ValueError("invalid pilot payload")
        result = evaluate(payload["seed"])
    except (ValueError, TypeError, KeyError, OverflowError, json.JSONDecodeError):
        result = {"outcome": "error", "error_code": "INVALID_EXPERIMENT_INPUT"}
    sys.stdout.write(json.dumps(result, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
