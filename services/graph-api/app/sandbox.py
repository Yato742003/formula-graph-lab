"""Trusted controller for fixed, pinned, networkless Linux math workers."""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import threading
import time
from contextlib import suppress
from typing import Literal
from uuid import uuid4

from app.experiment_worker import PROTOCOL_VERSION, SEQUENCE_LENGTHS
from app.numerical_worker import SUITE_VERSION, TOLERANCES
from app.paper_artifact_manifest import PERFORMER_COMMIT, PERFORMER_SOURCE_SHA256
from app.paper_attention_worker import PROTOCOL_VERSION as PERFORMER_PROTOCOL_VERSION
from app.paper_attention_worker import SEEDS as PERFORMER_SEEDS
from app.paper_attention_worker import SEQUENCE_LENGTHS as PERFORMER_SEQUENCE_LENGTHS
from app.research_case_worker import PROTOCOL_VERSION as RESEARCH_CASE_PROTOCOL_VERSION
from app.research_case_worker import SEEDS as RESEARCH_CASE_SEEDS
from app.research_case_worker import SEQUENCE_LENGTHS as RESEARCH_CASE_SEQUENCE_LENGTHS

MAX_INPUT_BYTES = 192 * 1024
MAX_OUTPUT_BYTES = 64 * 1024
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")


def configured_image() -> str:
    image = os.getenv("FGL_SANDBOX_IMAGE", "")
    if not IMAGE_ID.fullmatch(image):
        raise ValueError("A local immutable sandbox image ID is required.")
    return image


def configured_performer_image() -> str:
    image = os.getenv("FGL_PERFORMER_SANDBOX_IMAGE", "")
    if not IMAGE_ID.fullmatch(image):
        raise ValueError("A local immutable Performer sandbox image ID is required.")
    return image


def container_command(
    image: str,
    name: str,
    timeout_ms: int,
    *,
    worker_kind: Literal[
        "symbolic", "numerical", "experiment", "performer", "research_case"
    ] = "symbolic",
) -> list[str]:
    if not IMAGE_ID.fullmatch(image) or not re.fullmatch(r"fgl-check-[0-9a-f]{32}", name):
        raise ValueError("Invalid sandbox identity.")
    if not 10 <= timeout_ms <= 30_000:
        raise ValueError("Invalid sandbox deadline.")
    worker_modules = {
        "symbolic": "app.symbolic_worker",
        "numerical": "app.numerical_worker",
        "experiment": "app.experiment_worker",
        "performer": "app.paper_attention_worker",
        "research_case": "app.research_case_worker",
    }
    if worker_kind not in worker_modules:
        raise ValueError("Unknown sandbox worker kind.")
    worker_module = worker_modules[worker_kind]
    performer_cpu = (
        f"--cpuset-cpus={min(os.sched_getaffinity(0))}"
        if worker_kind == "performer" and hasattr(os, "sched_getaffinity")
        else "--cpuset-cpus=0"
        if worker_kind == "performer"
        else None
    )
    return [
        "docker", "run", "--name", name, "--pull=never", "-i",
        "--network=none", "--read-only", "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true", "--user=65534:65534",
        "--memory=256m", "--memory-swap=256m", "--cpus=1", "--pids-limit=16",
        *([performer_cpu] if performer_cpu else []),
        "--ulimit=nofile=64:64", "--ulimit=core=0:0", "--ulimit=cpu=30:30",
        "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777",
        "--shm-size=4m", "--ipc=private", "--log-driver=none", "--workdir=/opt/worker",
        "--entrypoint=/usr/bin/timeout", image,
        # This daemon-side deadline survives controller death. No shell/model code.
        "--signal=KILL", f"{timeout_ms / 1000:.3f}s",
        "/usr/local/bin/python", "-B", "-m", worker_module,
    ]


def bounded_process(command: list[str], payload: bytes, timeout: float) -> tuple[int, bytes, bool]:
    """Drain both pipes with an aggregate streaming byte ceiling."""
    process = subprocess.Popen(  # noqa: S603 - controller-owned arguments
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    output = bytearray()
    total = 0
    lock = threading.Lock()
    overflow = threading.Event()

    def drain(pipe, retain):
        nonlocal total
        try:
            while chunk := pipe.read1(4096):
                with lock:
                    total += len(chunk)
                    if total > MAX_OUTPUT_BYTES:
                        overflow.set()
                        process.kill()
                        break
                    if retain:
                        output.extend(chunk)
        finally:
            pipe.close()

    def write():
        try:
            process.stdin.write(payload)
            process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            process.stdin.close()

    threads = [threading.Thread(target=drain, args=(process.stdout, True), daemon=True),
               threading.Thread(target=drain, args=(process.stderr, False), daemon=True),
               threading.Thread(target=write, daemon=True)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + timeout
    try:
        process.wait(timeout=timeout)
        for thread in threads:
            thread.join(timeout=max(0, deadline - time.monotonic()))
        if any(thread.is_alive() for thread in threads):
            raise subprocess.TimeoutExpired(command[0], timeout)
        return process.returncode, bytes(output), overflow.is_set()
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def run_sandbox(payload: dict[str, object], *, timeout_ms: int, image: str) -> dict[str, object]:
    return _run_sandbox(payload, timeout_ms=timeout_ms, image=image, worker_kind="symbolic")


def run_numerical_sandbox(
    payload: dict[str, object], *, timeout_ms: int, image: str,
) -> dict[str, object]:
    return _run_sandbox(payload, timeout_ms=timeout_ms, image=image, worker_kind="numerical")


def run_experiment_sandbox(
    payload: dict[str, object], *, timeout_ms: int, image: str,
) -> dict[str, object]:
    return _run_sandbox(payload, timeout_ms=timeout_ms, image=image, worker_kind="experiment")


def run_performer_sandbox(
    payload: dict[str, object], *, timeout_ms: int, image: str,
) -> dict[str, object]:
    return _run_sandbox(payload, timeout_ms=timeout_ms, image=image, worker_kind="performer")


def run_research_case_sandbox(
    payload: dict[str, object], *, timeout_ms: int, image: str,
) -> dict[str, object]:
    return _run_sandbox(
        payload, timeout_ms=timeout_ms, image=image, worker_kind="research_case"
    )


def _run_sandbox(
    payload: dict[str, object],
    *,
    timeout_ms: int,
    image: str,
    worker_kind: Literal[
        "symbolic", "numerical", "experiment", "performer", "research_case"
    ],
) -> dict[str, object]:
    raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
    if len(raw) > MAX_INPUT_BYTES:
        return {"outcome": "error", "error_code": "WORKER_INPUT_LIMIT"}
    name = "fgl-check-" + uuid4().hex
    command = container_command(image, name, timeout_ms, worker_kind=worker_kind)
    try:
        code, output, overflow = bounded_process(command, raw, timeout_ms / 1000 + 10)
        if overflow:
            return {"outcome": "error", "error_code": "WORKER_OUTPUT_LIMIT"}
        if code in (124, 137):
            state = subprocess.run(  # noqa: S603 - exact server-created container
                ["docker", "inspect", "--format={{.State.OOMKilled}}", name],
                capture_output=True, timeout=5,
            )
            if state.returncode != 0:
                return {"outcome": "error", "error_code": "SANDBOX_STATE_UNAVAILABLE"}
            if state.stdout.strip() == b"true":
                return {"outcome": "error", "error_code": "WORKER_RESOURCE_LIMIT"}
            return {"outcome": "timeout", "error_code": "SANDBOX_RESOURCE_OR_TIME_LIMIT"}
        if code:
            return {"outcome": "error", "error_code": "SANDBOX_FAILED"}
        validators = {
            "symbolic": validate_worker_result,
            "numerical": validate_numerical_worker_result,
            "experiment": validate_experiment_worker_result,
            "performer": validate_performer_worker_result,
            "research_case": validate_research_case_worker_result,
        }
        validator = validators[worker_kind]
        return validator(output)
    except subprocess.TimeoutExpired:
        return {"outcome": "timeout", "error_code": "WALL_CLOCK_TIMEOUT"}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"outcome": "error", "error_code": "SANDBOX_UNAVAILABLE"}
    finally:
        # Exact call-owned container only; in-container timeout is the crash fail-safe.
        with suppress(OSError, subprocess.SubprocessError):
            subprocess.run(["docker", "rm", "--force", name],  # noqa: S603
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)


def validate_worker_result(output: bytes) -> dict[str, object]:
    """Never persist raw stdout, stderr, arbitrary witnesses, or log text."""
    invalid = {"outcome": "error", "error_code": "INVALID_WORKER_RESPONSE"}
    try:
        result = json.loads(output)
    except (ValueError, UnicodeError):
        return invalid
    if result in (
        {"outcome": "supported", "witness": {"method": "sympy_simplify", "difference": "0"}},
        {"outcome": "unknown", "witness": {"method": "sympy_inconclusive"}},
    ):
        return result
    if not isinstance(result, dict):
        return invalid
    if set(result) == {"outcome", "counterexample"} and result["outcome"] == "refuted":
        example = result["counterexample"]
        if (isinstance(example, dict) and set(example) == {"method", "difference"}
                and example["method"] == "exact_constant_difference"
                and isinstance(example["difference"], str)
                and re.fullmatch(r"-?[1-9][0-9]{0,63}(?:/[1-9][0-9]{0,63})?",
                                 example["difference"])):
            return result
    if set(result) == {"outcome", "error_code"} and result["outcome"] in {"unsupported", "error"}:
        return {"outcome": result["outcome"], "error_code": "SANDBOX_INPUT_OR_COMPUTE_ERROR"}
    return invalid


def validate_numerical_worker_result(output: bytes) -> dict[str, object]:
    """Accept only the fixed V2 suite schema; discard arbitrary worker diagnostics."""
    invalid = {"outcome": "error", "error_code": "INVALID_NUMERICAL_WORKER_RESPONSE"}
    try:
        result = json.loads(output)
        json.dumps(result, allow_nan=False)
    except (ValueError, TypeError, UnicodeError):
        return invalid
    if result == {"outcome": "error", "error_code": "INVALID_NUMERICAL_SUITE_INPUT"}:
        return {"outcome": "error", "error_code": "INVALID_NUMERICAL_SUITE_INPUT"}
    if not isinstance(result, dict) or set(result) != {
        "outcome", "suite_version", "candidate_hash", "input_hash", "seed", "dtype",
        "tolerance", "checks", "measurements", "counterexample", "environment",
    }:
        return invalid
    if (
        result["outcome"] not in {"passed_suite", "counterexample"}
        or result["suite_version"] != SUITE_VERSION
        or not isinstance(result["candidate_hash"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", result["candidate_hash"])
        or not isinstance(result["input_hash"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", result["input_hash"])
        or type(result["seed"]) is not int
        or not 0 <= result["seed"] <= 2**31 - 1
        or not isinstance(result["dtype"], str)
        or result["dtype"] not in TOLERANCES
        or result["tolerance"] != TOLERANCES[result["dtype"]]
    ):
        return invalid
    check_names = {
        "kernel_identity", "causal_future_perturbation", "finite_gradient",
        "limiting_weights", "near_zero_denominator", "extreme_norm_overflow",
        "underflow_detection",
    }
    checks = result["checks"]
    if not isinstance(checks, dict) or set(checks) != check_names or any(
        type(passed) is not bool for passed in checks.values()
    ):
        return invalid
    measurements = result["measurements"]
    if not isinstance(measurements, dict) or set(measurements) != {
        "kernel_max_abs_error", "future_perturbation_max_abs_error", "gradient_max_abs_error",
    } or any(
        type(value) not in (int, float) or not math.isfinite(value) or value < 0
        for value in measurements.values()
    ):
        return invalid
    environment = result["environment"]
    if not isinstance(environment, dict) or set(environment) != {
        "implementation", "python", "suite",
    } or any(
        not isinstance(value, str) or not 1 <= len(value) <= 100
        for value in environment.values()
    ):
        return invalid
    if result["outcome"] == "passed_suite":
        if result["counterexample"] is not None or not all(checks.values()):
            return invalid
    else:
        witness = result["counterexample"]
        if (
            not isinstance(witness, dict)
            or set(witness) != {
                "failed_checks", "seed", "dtype", "lambda", "left_query", "left_key",
                "right_query", "right_key", "baseline_attention", "perturbed_attention",
            }
            or not isinstance(witness["failed_checks"], list)
            or not witness["failed_checks"]
            or any(not isinstance(name, str) or name not in check_names
                   or checks.get(name) is not False
                   for name in witness["failed_checks"])
            or len(witness["failed_checks"]) != len(set(witness["failed_checks"]))
            or witness["seed"] != result["seed"]
            or witness["dtype"] != result["dtype"]
            or type(witness["lambda"]) not in (int, float)
            or not math.isfinite(witness["lambda"])
            or not 0 <= witness["lambda"] <= 1
        ):
            return invalid
        for name in ("left_query", "left_key", "right_query", "right_key"):
            vector = witness[name]
            if (
                not isinstance(vector, list)
                or not 1 <= len(vector) <= 256
                or any(type(item) not in (int, float) or not math.isfinite(item) for item in vector)
            ):
                return invalid
        for name in ("baseline_attention", "perturbed_attention"):
            matrix = witness[name]
            if (
                not isinstance(matrix, list)
                or len(matrix) != 4
                or any(
                    not isinstance(row, list)
                    or len(row) != 2
                    or any(
                        type(item) not in (int, float) or not math.isfinite(item)
                        for item in row
                    )
                    for row in matrix
                )
            ):
                return invalid
    return result


def validate_experiment_worker_result(output: bytes) -> dict[str, object]:
    """Accept only the versioned, fixed CPU synthetic pilot result schema."""
    invalid = {"outcome": "error", "error_code": "INVALID_EXPERIMENT_WORKER_RESPONSE"}
    try:
        result = json.loads(output)
        json.dumps(result, allow_nan=False)
    except (ValueError, TypeError, UnicodeError):
        return invalid
    if result == {"outcome": "error", "error_code": "INVALID_EXPERIMENT_INPUT"}:
        return result
    if not isinstance(result, dict) or set(result) != {
        "outcome", "protocol_version", "seed", "input_hash", "checks",
        "measurements", "environment", "scope",
    }:
        return invalid
    if (
        result["outcome"] not in {"passed_suite", "counterexample"}
        or result["protocol_version"] != PROTOCOL_VERSION
        or type(result["seed"]) is not int
        or result["seed"] not in {42, 43, 44, 45, 46}
        or not isinstance(result["input_hash"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", result["input_hash"])
        or result["scope"] != "synthetic_operator_diagnostic_no_performance_claim"
    ):
        return invalid
    checks = result["checks"]
    if not isinstance(checks, dict) or set(checks) != {
        "mixture_concatenation_identity", "causal_future_perturbation", "finite_outputs",
    } or any(type(value) is not bool for value in checks.values()):
        return invalid
    measurements = result["measurements"]
    metric_names = {
        "parent_a_mean_abs_error_vs_exact", "parent_b_mean_abs_error_vs_exact",
        "candidate_mean_abs_error_vs_exact", "candidate_mean_abs_delta_vs_parent_a",
        "candidate_mean_abs_delta_vs_parent_b", "mixture_concatenation_max_abs_error",
        "causal_future_perturbation_max_abs_error",
    }
    if not isinstance(measurements, list) or len(measurements) != len(SEQUENCE_LENGTHS):
        return invalid
    if any(
        not isinstance(item, dict)
        or set(item) != {"sequence_length", *metric_names}
        or type(item["sequence_length"]) is not int
        or item["sequence_length"] != SEQUENCE_LENGTHS[index]
        or any(
            type(item[name]) not in (int, float)
            or not math.isfinite(item[name])
            or item[name] < 0
            for name in metric_names
        )
        for index, item in enumerate(measurements)
    ):
        return invalid
    environment = result["environment"]
    if not isinstance(environment, dict) or set(environment) != {
        "implementation", "python", "protocol",
    } or environment["protocol"] != PROTOCOL_VERSION or any(
        not isinstance(value, str) or not 1 <= len(value) <= 100
        for value in environment.values()
    ):
        return invalid
    if result["outcome"] == "passed_suite":
        if not all(checks.values()):
            return invalid
    elif all(checks.values()):
        return invalid
    return result


def validate_performer_worker_result(output: bytes) -> dict[str, object]:
    """Accept only bounded results from the pinned Performer diagnostic worker."""
    invalid = {"outcome": "error", "error_code": "INVALID_PERFORMER_WORKER_RESPONSE"}
    try:
        result = json.loads(output)
        json.dumps(result, allow_nan=False)
    except (ValueError, TypeError, UnicodeError):
        return invalid
    if not isinstance(result, dict):
        return invalid
    if result.get("outcome") == "error":
        if set(result) == {"outcome", "error_code"} and result.get("error_code") in {
            "input_limit", "invalid_json", "invalid_scope", "invalid_source_encoding",
            "source_hash_mismatch", "CPU_BACKEND_REQUIRED", "PERFORMER_EXECUTION_FAILED",
            "NONFINITE_OUTPUT",
        }:
            return result
        return invalid
    if set(result) != {
        "outcome", "protocol_version", "implementation_commit", "source_sha256",
        "seed", "sequence_length", "input_hash", "checks", "measurements",
        "environment", "scope",
    } or (
        result.get("outcome") not in {"passed_suite", "counterexample"}
        or result.get("protocol_version") != PERFORMER_PROTOCOL_VERSION
        or result.get("implementation_commit") != PERFORMER_COMMIT
        or result.get("source_sha256") != PERFORMER_SOURCE_SHA256
        or type(result.get("seed")) is not int
        or result["seed"] not in PERFORMER_SEEDS
        or type(result.get("sequence_length")) is not int
        or result["sequence_length"] not in PERFORMER_SEQUENCE_LENGTHS
        or not isinstance(result.get("input_hash"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", result["input_hash"])
        or result.get("scope") != "paper_implementation_diagnostic_no_performance_claim"
    ):
        return invalid
    checks = result["checks"]
    if not isinstance(checks, dict) or set(checks) != {
        "finite_outputs", "causal_future_key_perturbation",
        "causal_future_extreme_key_perturbation",
        "causal_future_value_perturbation",
    } or any(type(value) is not bool for value in checks.values()):
        return invalid
    measurements = result["measurements"]
    if not isinstance(measurements, dict) or set(measurements) != {
        "mean_abs_error_vs_exact", "causal_future_key_perturbation_max_abs_error",
        "causal_future_extreme_key_perturbation_max_abs_error",
        "causal_future_value_perturbation_max_abs_error",
    } or any(
        type(value) not in (int, float) or not math.isfinite(value) or value < 0
        for value in measurements.values()
    ):
        return invalid
    environment = result["environment"]
    if not isinstance(environment, dict) or set(environment) != {
        "python", "jax", "backend",
    } or any(
        not isinstance(value, str) or not 1 <= len(value) <= 100
        for value in environment.values()
    ) or environment["backend"] != "cpu":
        return invalid
    if result["outcome"] == "passed_suite" and not all(checks.values()):
        return invalid
    if result["outcome"] == "counterexample" and all(checks.values()):
        return invalid
    return result


def validate_research_case_worker_result(output: bytes) -> dict[str, object]:
    """Accept only the registered rank-matched research-case result schema."""
    invalid = {"outcome": "error", "error_code": "INVALID_RESEARCH_CASE_RESPONSE"}
    try:
        result = json.loads(output)
        json.dumps(result, allow_nan=False)
    except (ValueError, TypeError, UnicodeError):
        return invalid
    if result == {"outcome": "error", "error_code": "INVALID_RESEARCH_CASE_INPUT"}:
        return result
    if not isinstance(result, dict) or set(result) != {
        "outcome", "protocol_version", "candidate_hash", "seed", "split", "input_hash",
        "checks", "measurements", "environment",
    }:
        return invalid
    if (
        result["outcome"] not in {"passed_suite", "counterexample"}
        or result["protocol_version"] != RESEARCH_CASE_PROTOCOL_VERSION
        or not isinstance(result["candidate_hash"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", result["candidate_hash"])
        or type(result["seed"]) is not int
        or result["seed"] not in RESEARCH_CASE_SEEDS
        or result["split"] not in {"search", "validation", "holdout"}
        or not isinstance(result["input_hash"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", result["input_hash"])
    ):
        return invalid
    checks = result["checks"]
    if not isinstance(checks, dict) or set(checks) != {
        "matched_feature_budget", "causal_future_perturbation", "finite_outputs",
    } or any(type(value) is not bool for value in checks.values()):
        return invalid
    metrics = {
        "feature_budget", "parent_a_mean_abs_error_vs_exact",
        "parent_b_mean_abs_error_vs_exact", "candidate_mean_abs_error_vs_exact",
        "candidate_regret_vs_best_parent", "causal_future_perturbation_max_abs_error",
    }
    measurements = result["measurements"]
    if not isinstance(measurements, list) or len(measurements) != len(
        RESEARCH_CASE_SEQUENCE_LENGTHS
    ) or any(
        not isinstance(item, dict)
        or set(item) != {"sequence_length", *metrics}
        or item["sequence_length"] != RESEARCH_CASE_SEQUENCE_LENGTHS[index]
        or type(item["feature_budget"]) is not int
        or item["feature_budget"] <= 0
        or any(
            type(item[name]) not in (int, float) or not math.isfinite(item[name])
            for name in metrics - {"feature_budget"}
        )
        for index, item in enumerate(measurements)
    ):
        return invalid
    environment = result["environment"]
    if not isinstance(environment, dict) or set(environment) != {
        "implementation", "python", "backend", "protocol",
    } or environment.get("backend") != "stdlib-cpu" or environment.get(
        "protocol"
    ) != RESEARCH_CASE_PROTOCOL_VERSION or any(
        not isinstance(value, str) or not 1 <= len(value) <= 100
        for value in environment.values()
    ):
        return invalid
    if result["outcome"] == "passed_suite" and not all(checks.values()):
        return invalid
    if result["outcome"] == "counterexample" and all(checks.values()):
        return invalid
    return result
