"""FGL-H4: scoped checker outcomes and bounded symbolic worker execution."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.formula_ast import (
    FormulaFormat,
    FormulaParseError,
    ParsedFormula,
    ast_node_count,
    parse_formula,
)
from app.symbol_contracts import (
    SymbolContract,
    assess_domain_obligations,
    infer_contracts,
    infer_domain_obligations,
    infer_expression_shape,
)

CHECKER_VERSION = "symbolic-checker.v1"
MAX_CHECK_AST_NODES = 256
MAX_WORKER_OUTPUT_BYTES = 64 * 1024
MAX_WORKER_RAM_BYTES = 256 * 1024 * 1024

SymbolicOutcome = Literal[
    "supported", "refuted", "unknown", "unsupported", "timeout", "error",
]


class VerificationVector(BaseModel):
    parse: Literal["supported", "unsupported", "error"]
    type: Literal["well_typed", "ill_typed", "unknown"]
    domain: Literal[
        "discharged", "conditional", "unresolved", "contradictory", "unsupported",
    ]
    symbolic: SymbolicOutcome
    numerical: Literal[
        "passed_suite", "counterexample", "not_run", "timeout", "error",
    ] = "not_run"
    empirical: Literal[
        "supported_on_protocol", "failed_on_protocol", "inconclusive", "not_run",
    ] = "not_run"
    human_review: Literal["pending", "accepted_scope", "rejected_scope"] = "pending"

    model_config = {"frozen": True, "extra": "forbid"}


class CheckResult(BaseModel):
    check_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    target_uuid: str = Field(min_length=1, max_length=200)
    checker: Literal["symbolic"] = "symbolic"
    checker_version: Literal["symbolic-checker.v1"] = CHECKER_VERSION
    input_hashes: tuple[str, str] | None = None
    request_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    assumptions: list[str] = Field(default_factory=list, max_length=100)
    outcome: SymbolicOutcome
    vector: VerificationVector
    witness: dict[str, object] | None = None
    counterexample: dict[str, object] | None = None
    error_code: str | None = Field(default=None, max_length=100)
    duration_ms: int = Field(ge=0)
    created_at: datetime
    schema_version: Literal["check-result.v1"] = "check-result.v1"
    job_id: str | None = None
    execution_image: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")

    model_config = {"frozen": True, "extra": "forbid"}


class CheckResultResponse(CheckResult):
    replayed: bool


def symbolic_request_hash(
    formula_a: str, formula_b: str, *, source_format: FormulaFormat = "latex",
    timeout_ms: int = 2_000, execution_image: str | None = None,
) -> str:
    from app.formula_ast import CANONICALIZER_VERSION, SYNTAX_HASH_VERSION

    payload = [
        "symbolic-request.v1", formula_a, formula_b, source_format, timeout_ms,
        CHECKER_VERSION, CANONICALIZER_VERSION, SYNTAX_HASH_VERSION,
        MAX_CHECK_AST_NODES,
    ]
    if execution_image is not None:
        payload.extend(["sandbox-policy.v1", execution_image])
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def run_symbolic_check(
    formula_a: str,
    formula_b: str,
    *,
    source_format: FormulaFormat = "latex",
    timeout_ms: int = 2_000,
    worker_runner: Callable | None = None,
) -> CheckResult:
    """Run one symbolic comparison with conservative preflight gates."""
    started = time.monotonic()
    base = {
        "check_id": "pending-server-identity",
        "workspace_id": "pending-server-scope",
        "target_uuid": "pending-server-target",
        "duration_ms": 0,
        "created_at": datetime.now(UTC),
        "request_hash": symbolic_request_hash(
            formula_a, formula_b, source_format=source_format, timeout_ms=timeout_ms,
        ),
    }
    try:
        parsed_a = parse_formula(formula_a, source_format=source_format)
        parsed_b = parse_formula(formula_b, source_format=source_format)
    except FormulaParseError as exc:
        return CheckResult(
            **base,
            outcome="unsupported",
            vector=VerificationVector(
                parse="unsupported", type="unknown", domain="unsupported",
                symbolic="unsupported",
            ),
            error_code=exc.code,
        )

    input_hashes = (parsed_a.syntax_hash, parsed_b.syntax_hash)
    if (
        ast_node_count(parsed_a.root) > MAX_CHECK_AST_NODES
        or ast_node_count(parsed_b.root) > MAX_CHECK_AST_NODES
    ):
        return _result(
            base,
            started,
            input_hashes,
            "unsupported",
            parse="supported",
            type_status="unknown",
            domain="unsupported",
            error_code="AST_RESOURCE_LIMIT",
        )

    contracts_a = infer_contracts(parsed_a)
    contracts_b = infer_contracts(parsed_b)
    _, errors_a = infer_expression_shape(contracts_a, parsed_a)
    _, errors_b = infer_expression_shape(contracts_b, parsed_b)
    if errors_a or errors_b:
        return _result(
            base,
            started,
            input_hashes,
            "unsupported",
            parse="supported",
            type_status="ill_typed",
            domain="unsupported",
            error_code="ILL_TYPED",
        )
    if (
        _contracts_have_unknown_shape(contracts_a + contracts_b)
        or _has_missing_symbol_coverage(parsed_a, contracts_a)
        or _has_missing_symbol_coverage(parsed_b, contracts_b)
    ):
        return _result(
            base,
            started,
            input_hashes,
            "unknown",
            parse="supported",
            type_status="unknown",
            domain="unresolved",
            error_code="UNRESOLVED_SHAPE",
        )

    domain_a = assess_domain_obligations(infer_domain_obligations(contracts_a, parsed_a))
    domain_b = assess_domain_obligations(infer_domain_obligations(contracts_b, parsed_b))
    domain_status = _combined_domain_status(domain_a.status, domain_b.status)
    if domain_status in {"unresolved", "conditional", "contradictory", "unsupported"}:
        return _result(
            base,
            started,
            input_hashes,
            "unknown" if domain_status != "unsupported" else "unsupported",
            parse="supported",
            type_status="well_typed",
            domain=domain_status,
            error_code="UNRESOLVED_DOMAIN" if domain_status != "unsupported" else None,
        )

    worker = (worker_runner or _run_worker_payload)(
        {
            "formula_a": formula_a,
            "formula_b": formula_b,
            "format": source_format,
        },
        timeout_ms=timeout_ms,
    )
    outcome = worker.get("outcome")
    if outcome not in {
        "supported", "refuted", "unknown", "unsupported", "timeout", "error",
    }:
        outcome = "error"
        worker = {"outcome": "error", "error_code": "INVALID_WORKER_RESPONSE"}
    return _result(
        base,
        started,
        input_hashes,
        outcome,
        parse="supported",
        type_status="well_typed",
        domain="discharged",
        witness=worker.get("witness") if isinstance(worker.get("witness"), dict) else None,
        counterexample=(
            worker.get("counterexample")
            if isinstance(worker.get("counterexample"), dict)
            else None
        ),
        error_code=(
            worker.get("error_code") if isinstance(worker.get("error_code"), str) else None
        ),
    )


def _result(
    base: dict[str, object],
    started: float,
    input_hashes: tuple[str, str],
    outcome: SymbolicOutcome,
    *,
    parse: Literal["supported", "unsupported", "error"],
    type_status: Literal["well_typed", "ill_typed", "unknown"],
    domain: Literal[
        "discharged", "conditional", "unresolved", "contradictory", "unsupported",
    ],
    witness: dict[str, object] | None = None,
    counterexample: dict[str, object] | None = None,
    error_code: str | None = None,
) -> CheckResult:
    return CheckResult(
        **{
            **base,
            "duration_ms": max(0, round((time.monotonic() - started) * 1_000)),
        },
        input_hashes=input_hashes,
        outcome=outcome,
        vector=VerificationVector(
            parse=parse,
            type=type_status,
            domain=domain,
            symbolic=outcome,
        ),
        witness=witness,
        counterexample=counterexample,
        error_code=error_code,
    )


def _contracts_have_unknown_shape(contracts: list[object]) -> bool:
    return any(
        shape is not None and any(dimension == "?" for dimension in shape)
        for contract in contracts
        if (shape := getattr(contract, "shape", None)) is not None
    )


def _has_missing_symbol_coverage(
    parsed: ParsedFormula, contracts: list[SymbolContract],
) -> bool:
    contract_names = {c.name for c in contracts}
    return any(sym not in contract_names for sym in parsed.free_variables)


def _combined_domain_status(left: str, right: str) -> str:
    priority = ["contradictory", "unsupported", "unresolved", "conditional", "discharged"]
    return min((left, right), key=priority.index)


def _posix_worker_limits() -> None:
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (MAX_WORKER_RAM_BYTES, MAX_WORKER_RAM_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))


def _apply_windows_job_limits(
    process: subprocess.Popen[str], max_bytes: int = MAX_WORKER_RAM_BYTES,
) -> object | None:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x00000100 | 0x00000200 | 0x00002000
        info.ProcessMemoryLimit = max_bytes
        info.JobMemoryLimit = max_bytes
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(job)
            return None
        if not kernel32.AssignProcessToJobObject(job, int(process._handle)):
            kernel32.CloseHandle(job)
            return None
        return job
    except Exception:
        return None


def _close_windows_job(job: object | None) -> None:
    if job and os.name == "nt":
        try:
            import ctypes

            ctypes.windll.kernel32.CloseHandle(job)
        except Exception:
            pass


def _run_worker_payload(
    payload: dict[str, object],
    *,
    timeout_ms: int,
    command: list[str] | None = None,
) -> dict[str, object]:
    """Execute the fixed worker with CPU, RAM, output, and process isolation."""
    if timeout_ms < 10 or timeout_ms > 30_000:
        raise ValueError("Symbolic timeout must be between 10 and 30000 ms.")
    worker_command = command or [sys.executable, "-m", "app.symbolic_worker"]
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    # ponytail: env allowlist, ceiling: host process group, upgrade: sandbox if untrusted workers.
    worker_env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "WINDIR": os.environ.get("WINDIR", ""),
        "PYTHONPATH": os.environ.get("PYTHONPATH", "."),
        "TEMP": os.environ.get("TEMP", ""),
        "TMP": os.environ.get("TMP", ""),
        "PYTHONNOUSERSITE": "1",
        "PYTHONUNBUFFERED": "1",
        "HTTP_PROXY": "http://127.0.0.1:0",
        "HTTPS_PROXY": "http://127.0.0.1:0",
        "NO_PROXY": "",
    }
    try:
        process = subprocess.Popen(  # noqa: S603 - command is server-owned
            worker_command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=creationflags,
            start_new_session=os.name != "nt",
            preexec_fn=_posix_worker_limits if os.name != "nt" else None,
            env=worker_env,
        )
    except (OSError, subprocess.SubprocessError):
        return {"outcome": "error", "error_code": "WORKER_ISOLATION_FAILED"}
    job = _apply_windows_job_limits(process)
    if os.name == "nt" and job is None:
        _terminate_process_tree(process)
        process.communicate()
        return {"outcome": "error", "error_code": "WORKER_ISOLATION_FAILED"}
    try:
        try:
            stdout, stderr = process.communicate(
                json.dumps(payload, separators=(",", ":")),
                timeout=timeout_ms / 1_000,
            )
        except subprocess.TimeoutExpired:
            _close_windows_job(job)
            job = None
            _terminate_process_tree(process)
            try:
                process.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
            return {"outcome": "timeout", "error_code": "WALL_CLOCK_TIMEOUT"}
        if len(stdout.encode("utf-8")) > MAX_WORKER_OUTPUT_BYTES:
            _terminate_process_tree(process)
            return {"outcome": "error", "error_code": "WORKER_OUTPUT_LIMIT"}
        if process.returncode != 0:
            error_code = (
                "WORKER_RESOURCE_LIMIT"
                if "MemoryError" in (stderr or "") or process.returncode in (-9, 137)
                else "WORKER_FAILED"
            )
            return {"outcome": "error", "error_code": error_code}
        try:
            result = json.loads(stdout)
        except json.JSONDecodeError:
            return {"outcome": "error", "error_code": "INVALID_WORKER_RESPONSE"}
        return result if isinstance(result, dict) else {
            "outcome": "error", "error_code": "INVALID_WORKER_RESPONSE",
        }
    finally:
        _close_windows_job(job)


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(  # noqa: S603 - exact server-created PID
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if process.poll() is None:
            process.kill()
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except ProcessLookupError:
        return
