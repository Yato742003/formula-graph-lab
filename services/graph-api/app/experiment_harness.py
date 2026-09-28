"""Reproducible, zero-cost CPU pilot orchestration for the fixed experiment worker."""

from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
import time
from collections.abc import Callable
from typing import Any

from app.analysis_versions import canonical_json
from app.experiment_worker import (
    FEATURE_DIM,
    LEFT_RANK,
    MIX_WEIGHT,
    PROTOCOL_VERSION,
    RIGHT_RANK,
    SEEDS,
    SEQUENCE_LENGTHS,
    TOLERANCE,
)
from app.sandbox import (
    configured_image,
    run_experiment_sandbox,
    validate_experiment_worker_result,
)

MAX_WALL_TIME_MS = 30_000
PER_TRIAL_TIMEOUT_MS = 5_000
METRICS = (
    "parent_a_mean_abs_error_vs_exact",
    "parent_b_mean_abs_error_vs_exact",
    "candidate_mean_abs_error_vs_exact",
    "candidate_mean_abs_delta_vs_parent_a",
    "candidate_mean_abs_delta_vs_parent_b",
    "mixture_concatenation_max_abs_error",
    "causal_future_perturbation_max_abs_error",
)
T95_DF4 = 2.7764451051977987


def protocol_manifest(image: str) -> dict[str, Any]:
    """Return the full, content-hashable pilot configuration and its limitations."""
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise ValueError("A verified immutable sandbox image is required.")
    return {
        "protocol_version": PROTOCOL_VERSION,
        "input_generator": "fgl-xorshift32-attention-inputs.v1",
        "seeds": list(SEEDS),
        "sequence_lengths": list(SEQUENCE_LENGTHS),
        "feature_ranks": {
            "parent_a": LEFT_RANK,
            "parent_b": RIGHT_RANK,
            "candidate_total": FEATURE_DIM,
        },
        "mixture_weight": MIX_WEIGHT,
        "dtype": "float64",
        "identity_tolerance": TOLERANCE,
        "reference": "exact_causal_scaled_dot_product_softmax.v1",
        "parents": ["positive_feature_map_a.v1", "positive_feature_map_b.v1"],
        "candidate": "weighted_positive_feature_map_mixture.v1",
        "representation_transform": "sqrt_weight_feature_concatenation.v1",
        "worker_image": image,
        "resources": {
            "cpu_count": 1,
            "memory_mib": 256,
            "max_wall_time_ms": MAX_WALL_TIME_MS,
            "per_trial_timeout_ms": PER_TRIAL_TIMEOUT_MS,
            "cost_usd": 0,
        },
        "claims": {
            "scope": "synthetic_operator_diagnostic",
            "performance_claim": False,
            "model_quality_claim": False,
            "empirical_vector": "not_run",
            "rank_matched_performance_comparison": False,
        },
    }


def _mean_and_interval(values: list[float]) -> dict[str, float | int]:
    if len(values) != len(SEEDS) or any(not math.isfinite(value) for value in values):
        raise ValueError("Pilot uncertainty summary requires every finite seed result.")
    mean = statistics.fmean(values)
    deviation = statistics.stdev(values)
    margin = T95_DF4 * deviation / math.sqrt(len(values))
    return {
        "n": len(values),
        "mean": mean,
        "sample_sd": deviation,
        "ci95_low": mean - margin,
        "ci95_high": mean + margin,
    }


def _trial_result(seed: int, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {
            "seed": seed,
            "outcome": "error",
            "error_code": "INVALID_EXPERIMENT_WORKER_RESPONSE",
        }
    if raw.get("outcome") in {"timeout", "error", "unsupported"}:
        return {
            "seed": seed,
            "outcome": raw["outcome"],
            "error_code": raw.get("error_code", "EXPERIMENT_WORKER_FAILED"),
        }
    try:
        encoded = json.dumps(raw, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError, RecursionError):
        return {
            "seed": seed,
            "outcome": "error",
            "error_code": "INVALID_EXPERIMENT_WORKER_RESPONSE",
        }
    result = validate_experiment_worker_result(encoded)
    if result.get("outcome") == "error":
        return {
            "seed": seed,
            "outcome": "error",
            "error_code": str(result.get("error_code", "INVALID_EXPERIMENT_WORKER_RESPONSE")),
        }
    if (
        result.get("seed") != seed
        or result.get("protocol_version") != PROTOCOL_VERSION
    ):
        return {"seed": seed, "outcome": "error", "error_code": "EXPERIMENT_RESULT_SCOPE_MISMATCH"}
    return {
        "seed": seed,
        "outcome": result["outcome"],
        "input_hash": result["input_hash"],
        "checks": result["checks"],
        "measurements": result["measurements"],
        "environment": result["environment"],
        "scope": result["scope"],
    }


def run_attention_synthetic_pilot(
    *,
    image: str,
    worker_runner: Callable[..., dict[str, Any]] = run_experiment_sandbox,
) -> dict[str, Any]:
    """Run all fixed seeds; incomplete/failed trials remain explicit, never zero scores."""
    manifest = protocol_manifest(image)
    protocol_hash = hashlib.sha256(canonical_json(manifest).encode()).hexdigest()
    started = time.monotonic()
    trials = []
    for seed in SEEDS:
        remaining_ms = MAX_WALL_TIME_MS - int((time.monotonic() - started) * 1000)
        if remaining_ms < 10:
            trials.append({
                "seed": seed, "outcome": "timeout", "error_code": "PILOT_BUDGET_EXHAUSTED",
            })
            continue
        try:
            raw = worker_runner(
                {"protocol_version": PROTOCOL_VERSION, "seed": seed},
                timeout_ms=min(PER_TRIAL_TIMEOUT_MS, remaining_ms),
                image=image,
            )
        except Exception:
            trials.append({
                "seed": seed, "outcome": "error", "error_code": "EXPERIMENT_WORKER_FAILED",
            })
            continue
        trials.append(_trial_result(seed, raw))

    completed = all(trial["outcome"] in {"passed_suite", "counterexample"} for trial in trials)
    summary: dict[str, Any] | None = None
    if completed:
        summary = {
            str(length): {
                metric: _mean_and_interval([
                    next(
                        item[metric]
                        for item in trial["measurements"]
                        if item["sequence_length"] == length
                    )
                    for trial in trials
                ])
                for metric in METRICS
            }
            for length in SEQUENCE_LENGTHS
        }
    elapsed_ms = min(MAX_WALL_TIME_MS, int((time.monotonic() - started) * 1000))
    report = {
        "protocol_hash": protocol_hash,
        "protocol": manifest,
        "run_status": "completed" if completed else "incomplete",
        "empirical_outcome": "not_run",
        "scope": "synthetic_operator_diagnostic_no_performance_claim",
        "trials": trials,
        "summary": summary,
        "search_cost": {
            "candidate_count": 1,
            "trial_count": len(trials),
            "completed_trial_count": sum(
                trial["outcome"] in {"passed_suite", "counterexample"} for trial in trials
            ),
            "wall_time_ms": elapsed_ms,
            "cost_usd": 0,
        },
    }
    report["report_hash"] = hashlib.sha256(canonical_json(report).encode()).hexdigest()
    return report


def main() -> int:
    image = configured_image()
    report = run_attention_synthetic_pilot(image=image)
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if report["run_status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
