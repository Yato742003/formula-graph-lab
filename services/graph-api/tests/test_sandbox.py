import json
import subprocess
import sys
import time

import pytest

from app.experiment_worker import PROTOCOL_VERSION, evaluate
from app.sandbox import (
    bounded_process,
    container_command,
    run_sandbox,
    validate_experiment_worker_result,
    validate_worker_result,
)

IMAGE = "sha256:" + "a" * 64


def test_container_has_fixed_caps_and_no_host_mounts_or_env():
    command = container_command(IMAGE, "fgl-check-" + "a" * 32, 2000)
    for flag in ("--network=none", "--read-only", "--cap-drop=ALL", "--pids-limit=16",
                 "--security-opt=no-new-privileges:true", "--memory=256m",
                 "--memory-swap=256m", "--cpus=1", "--user=65534:65534", "--log-driver=none"):
        assert flag in command
    assert not any(v in command for v in ("--privileged", "--volume", "--mount", "--env", "-v"))
    assert command[-1] == "app.symbolic_worker"
    assert command[command.index("--entrypoint=/usr/bin/timeout") + 1] == IMAGE
    experiment_command = container_command(
        IMAGE, "fgl-check-" + "b" * 32, 2000, worker_kind="experiment",
    )
    assert experiment_command[-1] == "app.experiment_worker"
    with pytest.raises(ValueError):
        container_command("python:latest", "fgl-check-" + "a" * 32, 2000)


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_streaming_output_limit_kills_before_unbounded_allocation(stream):
    started = time.monotonic()
    code = f"import sys; s=sys.{stream}.buffer\nwhile True: s.write(b'x'*8192); s.flush()"
    _, data, overflow = bounded_process([sys.executable, "-c", code], b"{}", 5)
    assert overflow and len(data) <= 65536
    assert time.monotonic() - started < 5


def test_sandbox_failure_never_falls_back_to_host(monkeypatch):
    monkeypatch.setattr("app.sandbox.bounded_process", lambda *a: (_ for _ in ()).throw(
        FileNotFoundError("docker missing")))
    monkeypatch.setattr("app.sandbox.subprocess.run", lambda *a, **kw: None)
    assert run_sandbox({}, timeout_ms=2000, image=IMAGE) == {
        "outcome": "error", "error_code": "SANDBOX_UNAVAILABLE",
    }


def test_worker_output_cannot_smuggle_secrets_or_approval(caplog):
    for result in (
        {"outcome": "supported", "witness": {"secret": "TOKEN"}},
        {"outcome": "supported", "fitness": 1},
        {"outcome": "refuted", "counterexample": {"method": "exact_constant_difference",
                                                  "difference": "nan"}},
        {"outcome": "refuted", "counterexample": {"method": "exact_constant_difference",
                                                  "difference": "0"}},
    ):
        assert validate_worker_result(json.dumps(result).encode())["outcome"] == "error"
    assert "TOKEN" not in caplog.text
    assert validate_worker_result(b'{"outcome":"error","error_code":"TOKEN"}') == {
        "outcome": "error", "error_code": "SANDBOX_INPUT_OR_COMPUTE_ERROR",
    }


def test_blocking_stdin_has_a_wall_clock_deadline():
    with pytest.raises(subprocess.TimeoutExpired):
        bounded_process([sys.executable, "-c", "import time; time.sleep(30)"],
                        b"x" * 192000, 0.1)


@pytest.mark.parametrize("difference", ["00", "-00", "0/2", "-0/3"])
def test_zero_cannot_be_accepted_as_a_counterexample(difference):
    result = {"outcome": "refuted", "counterexample": {
        "method": "exact_constant_difference", "difference": difference,
    }}
    assert validate_worker_result(json.dumps(result).encode())["outcome"] == "error"


def test_synthetic_attention_pilot_is_deterministic_and_protocol_scoped():
    result = evaluate(42)
    assert result == evaluate(42)
    assert result["outcome"] == "passed_suite"
    assert result["protocol_version"] == PROTOCOL_VERSION
    assert result["scope"] == "synthetic_operator_diagnostic_no_performance_claim"
    assert [item["sequence_length"] for item in result["measurements"]] == [16, 64, 256]
    assert all(result["checks"].values())


def test_experiment_result_validator_rejects_promoted_or_malformed_results():
    result = evaluate(42)
    assert validate_experiment_worker_result(
        json.dumps(result, allow_nan=False).encode(),
    ) == result
    assert validate_experiment_worker_result(
        json.dumps(result | {"fitness": 1.0}).encode(),
    )["outcome"] == "error"
    changed = result | {"checks": result["checks"] | {"empirical": True}}
    assert validate_experiment_worker_result(json.dumps(changed).encode())["outcome"] == "error"
