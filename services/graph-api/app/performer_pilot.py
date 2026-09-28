"""Frozen CPU-only Performer implementation diagnostic; not empirical evidence."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from collections.abc import Callable
from typing import Any

from app.analysis_versions import canonical_json
from app.paper_artifact_manifest import (
    PERFORMER_COMMIT,
    PERFORMER_LICENSE_SHA256,
    PERFORMER_LICENSE_SIZE,
    PERFORMER_SOURCE_SHA256,
    PERFORMER_SOURCE_SIZE,
)
from app.paper_artifacts import ResolvedPerformerArtifact, resolve_performer_artifact
from app.paper_attention_worker import (
    CAUSAL_TOLERANCE,
    HEAD_DIM,
    KEY_PERTURBATION_DELTAS,
    NB_FEATURES,
    PROTOCOL_VERSION,
    SEEDS,
    SEQUENCE_LENGTHS,
    VALUE_PERTURBATION_DELTA,
)
from app.sandbox import (
    configured_performer_image,
    run_performer_sandbox,
    validate_performer_worker_result,
)

PILOT_WALL_TIME_MS = 180_000
TRIAL_TIMEOUT_MS = 10_000


def protocol_manifest(image: str) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "paper": "arXiv:2009.14794v4",
        "repository": "google-research/google-research",
        "source_path": "performer/fast_self_attention/fast_self_attention.py",
        "implementation_commit": PERFORMER_COMMIT,
        "source_sha256": PERFORMER_SOURCE_SHA256,
        "license_id": "Apache-2.0",
        "license_sha256": PERFORMER_LICENSE_SHA256,
        "seeds": list(SEEDS),
        "sequence_lengths": list(SEQUENCE_LENGTHS),
        "input_generator": "fgl-xorshift32-v1",
        "dtype": "float32",
        "head_dim": HEAD_DIM,
        "random_features": NB_FEATURES,
        "key_perturbation_deltas": list(KEY_PERTURBATION_DELTAS),
        "value_perturbation_delta": VALUE_PERTURBATION_DELTA,
        "causal_tolerance": CAUSAL_TOLERANCE,
        "worker_image": image,
        "resources": {
            "cpu_count": 1,
            "memory_mib": 256,
            "max_wall_time_ms": PILOT_WALL_TIME_MS,
            "trial_timeout_ms": TRIAL_TIMEOUT_MS,
            "cost_usd": 0,
        },
        "scope": "paper_implementation_diagnostic_no_performance_claim",
        "empirical_outcome": "not_run",
    }


def _artifact_source(artifact: ResolvedPerformerArtifact) -> bytes:
    source = artifact.source
    if (
        artifact.paper_id != "arXiv:2009.14794v4"
        or artifact.repository != "google-research/google-research"
        or artifact.path != "performer/fast_self_attention/fast_self_attention.py"
        or artifact.commit != PERFORMER_COMMIT
        or artifact.source_sha256 != PERFORMER_SOURCE_SHA256
        or len(source) != PERFORMER_SOURCE_SIZE
        or hashlib.sha256(source).hexdigest() != PERFORMER_SOURCE_SHA256
        or artifact.license_id != "Apache-2.0"
        or artifact.license_sha256 != PERFORMER_LICENSE_SHA256
        or len(artifact.license_text) != PERFORMER_LICENSE_SIZE
        or hashlib.sha256(artifact.license_text).hexdigest() != PERFORMER_LICENSE_SHA256
    ):
        raise ValueError("The pinned Performer source and license must be verified first.")
    return source


def run_performer_synthetic_diagnostic(
    *,
    artifact: ResolvedPerformerArtifact,
    image: str,
    worker_runner: Callable[..., dict[str, Any]] = run_performer_sandbox,
) -> dict[str, Any]:
    """Run the fixed seed/length matrix without scoring performance or quality."""
    source_b64 = base64.b64encode(_artifact_source(artifact)).decode("ascii")
    manifest = protocol_manifest(image)
    protocol_hash = hashlib.sha256(canonical_json(manifest).encode()).hexdigest()
    started = time.monotonic()
    trials: list[dict[str, Any]] = []

    for seed in SEEDS:
        for length in SEQUENCE_LENGTHS:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            remaining_ms = PILOT_WALL_TIME_MS - elapsed_ms
            # sandbox adds a 10-second controller fail-safe after the worker timeout.
            if remaining_ms <= 10_000:
                trials.append({
                    "seed": seed,
                    "sequence_length": length,
                    "outcome": "timeout",
                    "error_code": "PILOT_BUDGET_EXHAUSTED",
                })
                continue
            try:
                raw = worker_runner(
                    {
                        "protocol_version": PROTOCOL_VERSION,
                        "seed": seed,
                        "sequence_length": length,
                        "source_b64": source_b64,
                    },
                    timeout_ms=min(TRIAL_TIMEOUT_MS, remaining_ms - 10_000),
                    image=image,
                )
            except Exception:
                trials.append({
                    "seed": seed,
                    "sequence_length": length,
                    "outcome": "error",
                    "error_code": "PERFORMER_WORKER_FAILED",
                })
                continue

            if not isinstance(raw, dict):
                trials.append({
                    "seed": seed,
                    "sequence_length": length,
                    "outcome": "error",
                    "error_code": "INVALID_PERFORMER_RESULT",
                })
                continue
            if raw.get("outcome") in {"timeout", "error", "unsupported"}:
                trials.append({
                    "seed": seed,
                    "sequence_length": length,
                    "outcome": raw["outcome"],
                    "error_code": raw.get("error_code", "PERFORMER_WORKER_FAILED"),
                })
                continue

            try:
                encoded = json.dumps(raw, separators=(",", ":"), allow_nan=False).encode()
                result = validate_performer_worker_result(encoded)
            except (TypeError, ValueError, RecursionError):
                result = {"outcome": "error", "error_code": "INVALID_PERFORMER_RESULT"}
            if (
                result.get("outcome") == "error"
                or result.get("seed") != seed
                or result.get("sequence_length") != length
                or result.get("protocol_version") != PROTOCOL_VERSION
                or result.get("source_sha256") != PERFORMER_SOURCE_SHA256
            ):
                trials.append({
                    "seed": seed,
                    "sequence_length": length,
                    "outcome": "error",
                    "error_code": result.get("error_code", "PERFORMER_RESULT_SCOPE_MISMATCH"),
                })
                continue
            trials.append({
                "seed": seed,
                "sequence_length": length,
                "outcome": result["outcome"],
                "input_hash": result["input_hash"],
                "checks": result["checks"],
                "measurements": result["measurements"],
                "environment": result["environment"],
            })

    completed = all(
        trial["outcome"] in {"passed_suite", "counterexample"} for trial in trials
    )
    report: dict[str, Any] = {
        "protocol_hash": protocol_hash,
        "protocol": manifest,
        "run_status": "completed" if completed else "incomplete",
        "empirical_outcome": "not_run",
        "scope": "paper_implementation_diagnostic_no_performance_claim",
        "trials": trials,
        "search_cost": {
            "candidate_count": 0,
            "implementation_count": 1,
            "trial_count": len(trials),
            "completed_trial_count": sum(
                trial["outcome"] in {"passed_suite", "counterexample"} for trial in trials
            ),
            "wall_time_ms": int((time.monotonic() - started) * 1000),
            "cost_usd": 0,
        },
    }
    report["report_hash"] = hashlib.sha256(canonical_json(report).encode()).hexdigest()
    return report


async def main() -> int:
    image = configured_performer_image()
    artifact = await resolve_performer_artifact()
    report = run_performer_synthetic_diagnostic(artifact=artifact, image=image)
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if report["run_status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
