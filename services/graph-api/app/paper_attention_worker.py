"""Run the byte-pinned Performer FAVOR+ implementation on fixed synthetic inputs."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import math
import platform
import sys
import tempfile
from pathlib import Path
from typing import Any

from app.numerical_worker import _next
from app.paper_artifact_manifest import (
    PERFORMER_COMMIT,
    PERFORMER_SOURCE_SHA256,
    PERFORMER_SOURCE_SIZE,
)

PROTOCOL_VERSION = "performer-favor+-implementation-diagnostic.v1"
SEEDS = (42, 43, 44, 45, 46)
SEQUENCE_LENGTHS = (16, 32, 64)
HEAD_DIM = 4
NB_FEATURES = 16
CAUSAL_TOLERANCE = 1e-5
KEY_PERTURBATION_DELTAS = (1.0, 100.0)
VALUE_PERTURBATION_DELTA = -100.0
MAX_INPUT_BYTES = 64 * 1024
MAX_BASE64_BYTES = 40 * 1024


class InvalidPerformerInput(ValueError):
    pass


def _decode_payload(payload: bytes) -> tuple[dict[str, Any], bytes]:
    if len(payload) > MAX_INPUT_BYTES:
        raise InvalidPerformerInput("input_limit")
    try:
        value = json.loads(payload)
    except (ValueError, UnicodeError) as exc:
        raise InvalidPerformerInput("invalid_json") from exc
    if (
        not isinstance(value, dict)
        or set(value) != {"protocol_version", "seed", "sequence_length", "source_b64"}
        or value.get("protocol_version") != PROTOCOL_VERSION
        or type(value.get("seed")) is not int
        or value["seed"] not in SEEDS
        or type(value.get("sequence_length")) is not int
        or value["sequence_length"] not in SEQUENCE_LENGTHS
        or not isinstance(value.get("source_b64"), str)
        or len(value["source_b64"]) > MAX_BASE64_BYTES
    ):
        raise InvalidPerformerInput("invalid_scope")
    try:
        source = base64.b64decode(value["source_b64"], validate=True)
    except (ValueError, TypeError) as exc:
        raise InvalidPerformerInput("invalid_source_encoding") from exc
    if (
        len(source) != PERFORMER_SOURCE_SIZE
        or hashlib.sha256(source).hexdigest() != PERFORMER_SOURCE_SHA256
    ):
        raise InvalidPerformerInput("source_hash_mismatch")
    return value, source


def _inputs(seed: int, length: int) -> tuple[list[list[float]], ...]:
    state = seed & 0xFFFFFFFF or 0x6D2B79F5
    tensors = []
    for _ in range(3):
        rows = []
        for _ in range(length):
            row = []
            for _ in range(HEAD_DIM):
                state, number = _next(state)
                row.append(number)
            rows.append(row)
        tensors.append(rows)
    return tuple(tensors)


def _exact_attention(query, keys, values) -> list[list[float]]:
    outputs = []
    scale = math.sqrt(HEAD_DIM)
    for index, query_row in enumerate(query):
        scores = [
            sum(a * b for a, b in zip(query_row, keys[j], strict=True)) / scale
            for j in range(index + 1)
        ]
        peak = max(scores)
        weights = [math.exp(score - peak) for score in scores]
        denominator = sum(weights)
        outputs.append([
            sum(weights[j] * values[j][column] for j in range(index + 1)) / denominator
            for column in range(HEAD_DIM)
        ])
    return outputs


def _load_verified_module(source: bytes):
    with tempfile.TemporaryDirectory(prefix="fgl-performer-") as directory:
        module_path = Path(directory) / "fast_self_attention.py"
        module_path.write_bytes(source)
        spec = importlib.util.spec_from_file_location("fgl_pinned_performer", module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("module_load_failed")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def evaluate_payload(payload: bytes) -> dict[str, Any]:
    """Verify and run only the registered Google Research source revision."""
    try:
        request, source = _decode_payload(payload)
    except InvalidPerformerInput as exc:
        return {"outcome": "error", "error_code": str(exc)}

    try:
        import jax
        import jax.numpy as jnp

        if jax.default_backend() != "cpu":
            return {"outcome": "error", "error_code": "CPU_BACKEND_REQUIRED"}
        module = _load_verified_module(source)
        length = request["sequence_length"]
        query, key, value = _inputs(request["seed"], length)
        query_array = jnp.asarray(query, dtype=jnp.float32)[None, :, None, :]
        key_array = jnp.asarray(key, dtype=jnp.float32)[None, :, None, :]
        value_array = jnp.asarray(value, dtype=jnp.float32)[None, :, None, :]
        attention = module.make_fast_softmax_attention(
            qkv_dim=HEAD_DIM,
            renormalize_attention=True,
            numerical_stabilizer=1e-6,
            nb_features=NB_FEATURES,
            ortho_features=True,
            ortho_scaling=0.0,
            redraw_features=False,
            unidirectional=True,
            nonnegative_features=True,
            lax_scan_unroll=1,
        )
        output = attention(query_array, key_array, value_array, axis=(1,))
        baseline = output[0, :, 0, :].tolist()

        key_perturbations = []
        for delta in KEY_PERTURBATION_DELTAS:
            changed_keys = list(key)
            changed_keys[-1] = [number + delta for number in changed_keys[-1]]
            key_perturbations.append(attention(
                query_array,
                jnp.asarray(changed_keys, dtype=jnp.float32)[None, :, None, :],
                value_array,
                axis=(1,),
            )[0, :, 0, :].tolist())
        changed_values = list(value)
        changed_values[-1] = [number + VALUE_PERTURBATION_DELTA for number in changed_values[-1]]
        value_perturbed = attention(
            query_array,
            key_array,
            jnp.asarray(changed_values, dtype=jnp.float32)[None, :, None, :],
            axis=(1,),
        )[0, :, 0, :].tolist()

        causal_key_errors = [max(
            abs(baseline[row][column] - perturbed[row][column])
            for row in range(length - 1)
            for column in range(HEAD_DIM)
        ) for perturbed in key_perturbations]
        causal_value_error = max(
            abs(baseline[row][column] - value_perturbed[row][column])
            for row in range(length - 1)
            for column in range(HEAD_DIM)
        )
        exact = _exact_attention(query, key, value)
        mean_abs_error = sum(
            abs(baseline[row][column] - exact[row][column])
            for row in range(length)
            for column in range(HEAD_DIM)
        ) / (length * HEAD_DIM)
        finite = all(
            math.isfinite(number)
            for matrix in (baseline, *key_perturbations, value_perturbed)
            for row in matrix
            for number in row
        )
        if not finite:
            return {"outcome": "error", "error_code": "NONFINITE_OUTPUT"}
    except Exception:
        return {"outcome": "error", "error_code": "PERFORMER_EXECUTION_FAILED"}

    checks = {
        "finite_outputs": finite,
        "causal_future_key_perturbation": (
            causal_key_errors[0] <= CAUSAL_TOLERANCE
        ),
        "causal_future_extreme_key_perturbation": (
            causal_key_errors[1] <= CAUSAL_TOLERANCE
        ),
        "causal_future_value_perturbation": causal_value_error <= CAUSAL_TOLERANCE,
    }
    input_identity = json.dumps(
        {
            "protocol_version": PROTOCOL_VERSION,
            "source_sha256": PERFORMER_SOURCE_SHA256,
            "seed": request["seed"],
            "sequence_length": length,
            "key_perturbation_deltas": KEY_PERTURBATION_DELTAS,
            "value_perturbation_delta": VALUE_PERTURBATION_DELTA,
            "causal_tolerance": CAUSAL_TOLERANCE,
            "q": query,
            "k": key,
            "v": value,
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return {
        "outcome": "passed_suite" if all(checks.values()) else "counterexample",
        "protocol_version": PROTOCOL_VERSION,
        "implementation_commit": PERFORMER_COMMIT,
        "source_sha256": PERFORMER_SOURCE_SHA256,
        "seed": request["seed"],
        "sequence_length": length,
        "input_hash": hashlib.sha256(input_identity).hexdigest(),
        "checks": checks,
        "measurements": {
            "mean_abs_error_vs_exact": mean_abs_error,
            "causal_future_key_perturbation_max_abs_error": causal_key_errors[0],
            "causal_future_extreme_key_perturbation_max_abs_error": causal_key_errors[1],
            "causal_future_value_perturbation_max_abs_error": causal_value_error,
        },
        "environment": {
            "python": platform.python_version(),
            "jax": jax.__version__,
            "backend": jax.default_backend(),
        },
        "scope": "paper_implementation_diagnostic_no_performance_claim",
    }


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    result = evaluate_payload(raw)
    sys.stdout.write(json.dumps(result, separators=(",", ":"), allow_nan=False))
    return 0 if result.get("outcome") in {"passed_suite", "counterexample"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
