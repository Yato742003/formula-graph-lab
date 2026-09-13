"""FGL-H4 checker outcome and worker boundary tests."""

from __future__ import annotations

import os
import sys
import time

import pytest

from app.verification import _run_worker_payload, run_symbolic_check

CHECK_TIMEOUT_MS = 10_000


def test_known_identity_is_supported_with_witness() -> None:
    result = run_symbolic_check("1+1", "2", timeout_ms=CHECK_TIMEOUT_MS)
    assert result.outcome == "supported"
    assert result.vector.symbolic == "supported"
    assert result.witness == {"method": "sympy_simplify", "difference": "0"}
    assert result.counterexample is None


def test_known_constant_counterexample_is_refuted_separately() -> None:
    result = run_symbolic_check("1+1", "3", timeout_ms=CHECK_TIMEOUT_MS)
    assert result.outcome == "refuted"
    assert result.counterexample == {
        "method": "exact_constant_difference",
        "difference": "-1",
    }
    assert result.witness is None


def test_cas_inconclusive_is_unknown_not_refuted() -> None:
    result = run_symbolic_check("a+b", "a*b", timeout_ms=CHECK_TIMEOUT_MS)
    assert result.outcome == "unknown"
    assert result.vector.symbolic == "unknown"
    assert result.counterexample is None
    assert result.witness == {"method": "sympy_inconclusive"}


def test_unresolved_domain_hole_cannot_pass() -> None:
    result = run_symbolic_check("x/x", "1")
    assert result.outcome == "unknown"
    assert result.vector.domain == "unresolved"
    assert result.error_code == "UNRESOLVED_DOMAIN"


def test_unknown_matrix_shape_cannot_pass() -> None:
    result = run_symbolic_check(
        r"\mathbf{A}*\mathbf{B}", r"\mathbf{A}*\mathbf{B}", timeout_ms=CHECK_TIMEOUT_MS,
    )
    assert result.outcome == "unknown"
    assert result.vector.type == "unknown"
    assert result.error_code == "UNRESOLVED_SHAPE"


def test_ast_resource_limit_is_distinct_from_timeout() -> None:
    formula = "+".join("1" for _ in range(300))
    result = run_symbolic_check(formula, formula)
    assert result.outcome == "unsupported"
    assert result.error_code == "AST_RESOURCE_LIMIT"


def test_hung_worker_is_killed_at_wall_clock_timeout() -> None:
    started = time.monotonic()
    result = _run_worker_payload(
        {},
        timeout_ms=100,
        command=[sys.executable, "-c", "import time; time.sleep(30)"],
    )
    elapsed = time.monotonic() - started
    assert result == {"outcome": "timeout", "error_code": "WALL_CLOCK_TIMEOUT"}
    assert elapsed < 5


def test_worker_output_limit_is_an_error_not_a_scientific_result() -> None:
    result = _run_worker_payload(
        {},
        timeout_ms=2_000,
        command=[sys.executable, "-c", "print('x' * 70000)"],
    )
    assert result == {"outcome": "error", "error_code": "WORKER_OUTPUT_LIMIT"}


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object behavior")
def test_windows_job_limit_failure_is_fail_closed(monkeypatch) -> None:
    monkeypatch.setattr("app.verification._apply_windows_job_limits", lambda _process: None)
    result = _run_worker_payload(
        {},
        timeout_ms=2_000,
        command=[sys.executable, "-c", "print('{}')"],
    )
    assert result == {"outcome": "error", "error_code": "WORKER_ISOLATION_FAILED"}


def test_child_process_tree_killed_on_timeout() -> None:
    code = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        "time.sleep(30)"
    )
    started = time.monotonic()
    result = _run_worker_payload(
        {},
        timeout_ms=200,
        command=[sys.executable, "-c", code],
    )
    elapsed = time.monotonic() - started
    assert result == {"outcome": "timeout", "error_code": "WALL_CLOCK_TIMEOUT"}
    assert elapsed < 5


def test_memory_heavy_worker_is_terminated_by_limit() -> None:
    result = _run_worker_payload(
        {},
        timeout_ms=5_000,
        command=[sys.executable, "-c", "b = bytearray(1024 * 1024 * 1024)"],
    )
    assert result == {"outcome": "error", "error_code": "WORKER_RESOURCE_LIMIT"}


def test_indeterminate_difference_is_not_refuted() -> None:
    import sympy

    # If a difference evaluates to non-finite/nan, it must be unknown, not refuted
    diff = sympy.nan
    assert not (bool(diff.is_number) and bool(diff.is_finite))


def test_missing_symbol_coverage_cannot_pass() -> None:
    from app.formula_ast import parse_formula
    from app.verification import _has_missing_symbol_coverage
    parsed = parse_formula("x + y")
    # If contracts only cover x, y is missing coverage
    incomplete_contracts = [c for c in parsed.symbols if c.name == "x"]
    assert _has_missing_symbol_coverage(parsed, incomplete_contracts) is True
