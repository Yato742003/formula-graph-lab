import json
import os
import subprocess
from uuid import uuid4

import pytest

from app.numerical_worker import SUITE_VERSION
from app.sandbox import bounded_process, container_command, run_numerical_sandbox, run_sandbox

pytestmark = pytest.mark.sandbox


def image(name="TEST_SANDBOX_IMAGE"):
    assert os.getenv(name), "Run test-sandbox.ps1 to build pinned test images"
    return os.environ[name]


def test_fixed_symbolic_worker_in_real_container():
    for expression, outcome in (("2", "supported"), ("3", "refuted")):
        result = run_sandbox({"formula_a": "1+1", "formula_b": expression, "format": "latex"},
                             timeout_ms=5000, image=image())
        assert result["outcome"] == outcome, result


def test_symbolic_worker_respects_reviewed_assumptions_in_real_container():
    pos = run_sandbox(
        {
            "formula_a": r"\sqrt{x^2}",
            "formula_b": "x",
            "format": "latex",
            "symbol_assumptions": {"x": {"category": "scalar", "domain": "positive"}},
        },
        timeout_ms=5000,
        image=image(),
    )
    assert pos == {
        "outcome": "supported",
        "witness": {"method": "sympy_assumptions_simplify", "difference": "0"},
    }

    real_only = run_sandbox(
        {
            "formula_a": r"\sqrt{x^2}",
            "formula_b": "x",
            "format": "latex",
            "symbol_assumptions": {"x": {"category": "scalar", "domain": "real"}},
        },
        timeout_ms=5000,
        image=image(),
    )
    assert real_only["outcome"] == "unknown", real_only

    noncomm = run_sandbox(
        {
            "formula_a": "A * B",
            "formula_b": "B * A",
            "format": "latex",
            "symbol_assumptions": {
                "A": {"category": "matrix", "domain": "real"},
                "B": {"category": "matrix", "domain": "real"},
            },
        },
        timeout_ms=5000,
        image=image(),
    )
    assert noncomm["outcome"] == "unknown", noncomm


def test_fixed_numerical_worker_in_real_container():
    result = run_numerical_sandbox({
        "suite_version": SUITE_VERSION,
        "candidate_hash": "b" * 64,
        "seed": 17,
        "dtype": "float32",
        "lambda": 0.37,
        "left_rank": 3,
        "right_rank": 5,
    }, timeout_ms=5000, image=image())
    assert result["outcome"] == "passed_suite", result
    assert result["checks"]["causal_future_perturbation"] is True


@pytest.mark.parametrize("mode", ["controls", "timeout", "flood", "memory"])
def test_adversarial_container_boundaries(mode, monkeypatch):
    monkeypatch.setenv("SERVICE_TOKEN", "do-not-inherit-this-secret")
    name = "fgl-check-" + uuid4().hex
    command = container_command(image("TEST_SANDBOX_PROBE_IMAGE"), name, 5000)
    command[-1] = "app.sandbox_probe"  # Test-owned fixed module, never caller supplied.
    try:
        code, raw, overflow = bounded_process(command, json.dumps({"mode": mode}).encode(), 15)
        if mode == "controls":
            assert code == 0 and not overflow, raw
            assert all(json.loads(raw).values()), raw
        elif mode == "flood":
            assert overflow and len(raw) <= 65536
        elif mode == "timeout":
            assert code in (124, 137)
            assert subprocess.check_output([
                "docker", "inspect", "--format={{.State.Running}}", name,
            ]).strip() == b"false"
        else:
            assert code != 0
            assert subprocess.check_output([
                "docker", "inspect", "--format={{.State.OOMKilled}}", name,
            ]).strip() == b"true"
    finally:
        subprocess.run(["docker", "rm", "-f", name], check=True, capture_output=True)
    assert subprocess.run(["docker", "inspect", name], capture_output=True).returncode != 0
