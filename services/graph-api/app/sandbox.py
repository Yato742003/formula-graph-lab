"""Trusted controller for fixed, pinned, networkless Linux math workers."""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from contextlib import suppress
from uuid import uuid4

MAX_INPUT_BYTES = 192 * 1024
MAX_OUTPUT_BYTES = 64 * 1024
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")


def configured_image() -> str:
    image = os.getenv("FGL_SANDBOX_IMAGE", "")
    if not IMAGE_ID.fullmatch(image):
        raise ValueError("A local immutable sandbox image ID is required.")
    return image


def container_command(image: str, name: str, timeout_ms: int) -> list[str]:
    if not IMAGE_ID.fullmatch(image) or not re.fullmatch(r"fgl-check-[0-9a-f]{32}", name):
        raise ValueError("Invalid sandbox identity.")
    if not 10 <= timeout_ms <= 30_000:
        raise ValueError("Invalid sandbox deadline.")
    return [
        "docker", "run", "--name", name, "--pull=never", "-i",
        "--network=none", "--read-only", "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true", "--user=65534:65534",
        "--memory=256m", "--memory-swap=256m", "--cpus=1", "--pids-limit=16",
        "--ulimit=nofile=64:64", "--ulimit=core=0:0", "--ulimit=cpu=30:30",
        "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777",
        "--shm-size=4m", "--ipc=private", "--log-driver=none", "--workdir=/opt/worker",
        "--entrypoint=/usr/bin/timeout", image,
        # This daemon-side deadline survives controller death. No shell/model code.
        "--signal=KILL", f"{timeout_ms / 1000:.3f}s",
        "/usr/local/bin/python", "-B", "-m", "app.symbolic_worker",
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
    raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
    if len(raw) > MAX_INPUT_BYTES:
        return {"outcome": "error", "error_code": "WORKER_INPUT_LIMIT"}
    name = "fgl-check-" + uuid4().hex
    command = container_command(image, name, timeout_ms)
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
        return validate_worker_result(output)
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
                and re.fullmatch(r"-?[0-9]{1,64}(?:/[1-9][0-9]{0,63})?", example["difference"])
                and example["difference"] not in {"0", "-0"}):
            return result
    if set(result) == {"outcome", "error_code"} and result["outcome"] in {"unsupported", "error"}:
        return {"outcome": result["outcome"], "error_code": "SANDBOX_INPUT_OR_COMPUTE_ERROR"}
    return invalid
