"""Registered, rank-matched CPU protocol for one formula-mashup research case."""

from __future__ import annotations

import hashlib
import json
import math
import platform
import sys
from typing import Any

from app.numerical_worker import _next

PROTOCOL_VERSION = "attention-mashup-cpu-synthetic.v1"
SEEDS = (42, 43, 44, 45, 46)
SEED_SPLITS = {42: "search", 43: "search", 44: "validation", 45: "holdout", 46: "holdout"}
SEQUENCE_LENGTHS = (16, 64)
INPUT_DIM = 6
MAX_BRANCH_RANK = 64
MAX_INPUT_BYTES = 2048
CORRECTNESS_TOLERANCE = 1e-10


def _inputs(seed: int, length: int) -> tuple[list[list[float]], ...]:
    state = seed & 0xFFFFFFFF or 0x6D2B79F5
    tensors: list[list[list[float]]] = []
    for _ in range(3):
        rows: list[list[float]] = []
        for _ in range(length):
            row: list[float] = []
            for _ in range(INPUT_DIM):
                state, value = _next(state)
                row.append(value)
            rows.append(row)
        tensors.append(rows)
    return tuple(tensors)


def _projection(seed: int, feature: int) -> list[float]:
    state = ((seed + 1) * 0x9E3779B1 + feature * 0x85EBCA6B) & 0xFFFFFFFF
    row = []
    for _ in range(INPUT_DIM):
        state, value = _next(state)
        row.append(value)
    norm = math.sqrt(sum(value * value for value in row)) or 1.0
    return [value / norm for value in row]


def _dot(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def _features(kind: str, vector: list[float], *, rank: int, seed: int) -> list[float]:
    norm_term = sum(value * value for value in vector) / (2 * INPUT_DIM)
    values = []
    for feature in range(rank):
        projected = _dot(_projection(seed, feature), vector)
        if kind == "positive_random_exp":
            values.append(math.exp(projected - norm_term))
        elif kind == "positive_elu":
            values.append(projected + 1.0 if projected >= 0 else math.exp(projected))
        else:
            raise ValueError("Unknown registered feature map.")
    return values


def _exact_attention(query, keys, values) -> list[list[float]]:
    output = []
    scale = math.sqrt(INPUT_DIM)
    for index, query_row in enumerate(query):
        scores = [_dot(query_row, keys[item]) / scale for item in range(index + 1)]
        peak = max(scores)
        weights = [math.exp(score - peak) for score in scores]
        denominator = sum(weights)
        output.append([
            sum(weights[item] * values[item][column] for item in range(index + 1))
            / denominator
            for column in range(INPUT_DIM)
        ])
    return output


def _feature_attention(
    kind: str,
    query,
    keys,
    values,
    *,
    rank: int,
    seed: int,
    mixture_weight: float,
    branch_rank: int,
) -> list[list[float]]:
    def feature(vector: list[float]) -> list[float]:
        if kind == "candidate":
            left = _features("positive_random_exp", vector, rank=branch_rank, seed=seed)
            right = _features("positive_elu", vector, rank=branch_rank, seed=seed + 10_000)
            return [math.sqrt(mixture_weight) * item for item in left] + [
                math.sqrt(1.0 - mixture_weight) * item for item in right
            ]
        return _features(kind, vector, rank=rank, seed=seed)

    query_features = [feature(row) for row in query]
    key_features = [feature(row) for row in keys]
    output = []
    for index, query_row in enumerate(query_features):
        weights = [_dot(query_row, key_features[item]) for item in range(index + 1)]
        denominator = sum(weights)
        if not math.isfinite(denominator) or denominator <= 0:
            raise ArithmeticError("Registered positive-kernel denominator is invalid.")
        output.append([
            sum(weights[item] * values[item][column] for item in range(index + 1))
            / denominator
            for column in range(INPUT_DIM)
        ])
    return output


def _mean_abs(left: list[list[float]], right: list[list[float]]) -> float:
    errors = [
        abs(a - b)
        for left_row, right_row in zip(left, right, strict=True)
        for a, b in zip(left_row, right_row, strict=True)
    ]
    return sum(errors) / len(errors)


def evaluate(payload: dict[str, Any]) -> dict[str, Any]:
    seed = payload["seed"]
    branch_rank = payload["branch_rank"]
    feature_budget = branch_rank * 2
    mixture_weight = float(payload["lambda"])
    measurements = []
    finite = True
    causal = True
    hashed_inputs = []
    for length in SEQUENCE_LENGTHS:
        query, keys, values = _inputs(seed, length)
        exact = _exact_attention(query, keys, values)
        parent_a = _feature_attention(
            "positive_random_exp", query, keys, values, rank=feature_budget,
            seed=seed, mixture_weight=mixture_weight, branch_rank=branch_rank,
        )
        parent_b = _feature_attention(
            "positive_elu", query, keys, values, rank=feature_budget,
            seed=seed + 10_000, mixture_weight=mixture_weight, branch_rank=branch_rank,
        )
        candidate = _feature_attention(
            "candidate", query, keys, values, rank=feature_budget,
            seed=seed, mixture_weight=mixture_weight, branch_rank=branch_rank,
        )
        changed_keys = [list(row) for row in keys]
        changed_values = [list(row) for row in values]
        changed_keys[-1] = [value + 100.0 for value in changed_keys[-1]]
        changed_values[-1] = [value - 100.0 for value in changed_values[-1]]
        perturbed = _feature_attention(
            "candidate", query, changed_keys, changed_values, rank=feature_budget,
            seed=seed, mixture_weight=mixture_weight, branch_rank=branch_rank,
        )
        future_error = max(
            abs(candidate[row][column] - perturbed[row][column])
            for row in range(length - 1)
            for column in range(INPUT_DIM)
        )
        outputs = (exact, parent_a, parent_b, candidate, perturbed)
        finite = finite and all(
            math.isfinite(value)
            for output in outputs
            for row in output
            for value in row
        )
        causal = causal and future_error <= CORRECTNESS_TOLERANCE
        parent_a_error = _mean_abs(parent_a, exact)
        parent_b_error = _mean_abs(parent_b, exact)
        candidate_error = _mean_abs(candidate, exact)
        measurements.append({
            "sequence_length": length,
            "feature_budget": feature_budget,
            "parent_a_mean_abs_error_vs_exact": parent_a_error,
            "parent_b_mean_abs_error_vs_exact": parent_b_error,
            "candidate_mean_abs_error_vs_exact": candidate_error,
            "candidate_regret_vs_best_parent": candidate_error - min(
                parent_a_error, parent_b_error
            ),
            "causal_future_perturbation_max_abs_error": future_error,
        })
        hashed_inputs.append({"length": length, "q": query, "k": keys, "v": values})

    checks = {
        "matched_feature_budget": all(
            item["feature_budget"] == feature_budget for item in measurements
        ),
        "causal_future_perturbation": causal,
        "finite_outputs": finite,
    }
    identity = json.dumps(
        {
            "protocol": PROTOCOL_VERSION,
            "candidate_hash": payload["candidate_hash"],
            "seed": seed,
            "lambda": mixture_weight,
            "branch_rank": branch_rank,
            "inputs": hashed_inputs,
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return {
        "outcome": "passed_suite" if all(checks.values()) else "counterexample",
        "protocol_version": PROTOCOL_VERSION,
        "candidate_hash": payload["candidate_hash"],
        "seed": seed,
        "split": SEED_SPLITS[seed],
        "input_hash": hashlib.sha256(identity.encode()).hexdigest(),
        "checks": checks,
        "measurements": measurements,
        "environment": {
            "implementation": platform.python_implementation(),
            "python": platform.python_version(),
            "backend": "stdlib-cpu",
            "protocol": PROTOCOL_VERSION,
        },
    }


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("oversized payload")
        payload = json.loads(raw)
        if not isinstance(payload, dict) or set(payload) != {
            "protocol_version", "candidate_hash", "seed", "lambda", "branch_rank"
        }:
            raise ValueError("invalid payload fields")
        if (
            payload["protocol_version"] != PROTOCOL_VERSION
            or type(payload["seed"]) is not int
            or payload["seed"] not in SEEDS
            or type(payload["lambda"]) not in (int, float)
            or not math.isfinite(payload["lambda"])
            or not 0 < payload["lambda"] < 1
            or type(payload["branch_rank"]) is not int
            or not 1 <= payload["branch_rank"] <= MAX_BRANCH_RANK
            or not isinstance(payload["candidate_hash"], str)
            or len(payload["candidate_hash"]) != 64
            or any(char not in "0123456789abcdef" for char in payload["candidate_hash"])
        ):
            raise ValueError("invalid protocol scope")
        result = evaluate(payload)
    except (ValueError, TypeError, KeyError, OverflowError, ArithmeticError, json.JSONDecodeError):
        result = {"outcome": "error", "error_code": "INVALID_RESEARCH_CASE_INPUT"}
    sys.stdout.write(json.dumps(result, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
