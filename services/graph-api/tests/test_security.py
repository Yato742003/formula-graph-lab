import json
import logging

import pytest

from app.security import (
    MAX_REQUEST_BODY_BYTES,
    PayloadTooLargeError,
    SensitiveDataFilter,
    UnsafePaperUrl,
    WorkspaceRateLimiter,
    arxiv_identity,
    check_payload_size,
    install_log_sanitizer,
    log_audit_event,
    normalize_arxiv_html_url,
    worker_execution_limits,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "https://arxiv.org/html/1706.03762",
            "https://arxiv.org/html/1706.03762",
        ),
        (
            "https://arxiv.org/abs/1706.03762v7?download=1#page",
            "https://arxiv.org/html/1706.03762v7",
        ),
        (
            "https://export.arxiv.org/html/2402.08954",
            "https://arxiv.org/html/2402.08954",
        ),
    ],
)
def test_normalizes_supported_arxiv_urls(value: str, expected: str) -> None:
    assert normalize_arxiv_html_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "http://arxiv.org/html/1706.03762",
        "https://evil.example/html/1706.03762",
        "https://arxiv.org.evil.example/html/1706.03762",
        "https://user@arxiv.org/html/1706.03762",
        "https://arxiv.org:443/html/1706.03762",
        "https://arxiv.org/pdf/1706.03762",
        "https://arxiv.org/html/../admin",
        "https://arxiv.org/html/1706%2e03762",
        "https://arxiv.org/html/hep-th/9901001",
    ],
)
def test_rejects_urls_outside_v1_policy(value: str) -> None:
    with pytest.raises(UnsafePaperUrl):
        normalize_arxiv_html_url(value)


def test_splits_paper_identity_and_version() -> None:
    assert arxiv_identity("https://arxiv.org/html/1706.03762v7") == (
        "1706.03762",
        7,
    )
    assert arxiv_identity("https://arxiv.org/html/2402.08954") == (
        "2402.08954",
        None,
    )


def test_check_payload_size_enforces_caps() -> None:
    check_payload_size(None)
    check_payload_size(1024)
    check_payload_size(MAX_REQUEST_BODY_BYTES)
    with pytest.raises(PayloadTooLargeError) as exc_info:
        check_payload_size(MAX_REQUEST_BODY_BYTES + 1)
    assert "exceeds limit" in str(exc_info.value)


def test_workspace_rate_limiter_sliding_window() -> None:
    limiter = WorkspaceRateLimiter(default_limit=3, window_seconds=10.0)
    assert limiter.check("ws_test", now=100.0) == (True, 0.0)
    assert limiter.check("ws_test", now=101.0) == (True, 0.0)
    assert limiter.check("ws_test", now=102.0) == (True, 0.0)
    allowed, retry_after = limiter.check("ws_test", now=103.0)
    assert not allowed
    assert retry_after == 7.0
    assert limiter.check("ws_test", now=110.1) == (True, 0.0)
    assert limiter.check("ws_other", now=103.0) == (True, 0.0)
    limiter.reset()
    assert limiter.check("ws_test", now=103.0) == (True, 0.0)


def test_sensitive_data_filter_redacts_tokens_secrets_and_html(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SERVICE_TOKEN", "super-secret-service-token-12345")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-testkeyabcdef1234567890")
    log_filter = SensitiveDataFilter()

    rec = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="Authenticated Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9 for request",
        args=(),
        exc_info=None,
    )
    assert log_filter.filter(rec)
    assert "Bearer [REDACTED] for request" in str(rec.msg)

    rec2 = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg=(
            "Connecting with key sk-proj-testkeyabcdef1234567890"
            " and token super-secret-service-token-12345"
        ),
        args=(),
        exc_info=None,
    )
    assert log_filter.filter(rec2)
    assert "sk-proj-testkey" not in str(rec2.msg)
    assert "super-secret-service-token" not in str(rec2.msg)
    assert "[REDACTED]" in str(rec2.msg)

    rec3 = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="Fetched HTML: <html><body><script>malicious()</script></body></html>",
        args=(),
        exc_info=None,
    )
    assert log_filter.filter(rec3)
    assert "malicious()" not in str(rec3.msg)
    assert "[REDACTED_HTML]" in str(rec3.msg)

    rec4 = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="Context dump: %s",
        args={"token": "raw_secret", "user": "alice", "raw_html": "<p>hi</p>"},
        exc_info=None,
    )
    assert log_filter.filter(rec4)
    assert rec4.args["token"] == "[REDACTED]"
    assert rec4.args["raw_html"] == "[REDACTED]"
    assert rec4.args["user"] == "alice"


def test_log_audit_event_formats_immutable_json_record(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="fgl.audit"):
        log_audit_event(
            "tamper_detected",
            actor_id="researcher-1",
            workspace_id="ws_abc123",
            details={"error": "Signature mismatch", "token": "secret-value"},
        )
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.message.startswith("AUDIT: ")
    payload = json.loads(record.message.removeprefix("AUDIT: "))
    assert payload["audit_event"] == "tamper_detected"
    assert payload["actor_id"] == "researcher-1"
    assert payload["workspace_id"] == "ws_abc123"
    assert payload["details"]["error"] == "Signature mismatch"
    assert payload["details"]["token"] == "[REDACTED]"
    assert "timestamp" in payload


def test_install_log_sanitizer_attaches_to_all_relevant_loggers() -> None:
    install_log_sanitizer()
    root_filters = [f for f in logging.getLogger().filters if isinstance(f, SensitiveDataFilter)]
    assert len(root_filters) >= 1
    audit_filters = [
        f for f in logging.getLogger("fgl.audit").filters if isinstance(f, SensitiveDataFilter)
    ]
    assert len(audit_filters) >= 1


def test_worker_execution_limits_defaults_and_clamping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FGL_WORKER_RAM_LIMIT_MB", raising=False)
    monkeypatch.delenv("FGL_WORKER_TIMEOUT_MS", raising=False)
    monkeypatch.delenv("FGL_WORKER_CPU_CORES", raising=False)
    limits = worker_execution_limits()
    assert limits.ram_bytes == 256 * 1024 * 1024
    assert limits.timeout_ms == 10_000
    assert limits.cpu_cores == 1

    monkeypatch.setenv("FGL_WORKER_RAM_LIMIT_MB", "1024")
    monkeypatch.setenv("FGL_WORKER_TIMEOUT_MS", "60000")
    monkeypatch.setenv("FGL_WORKER_CPU_CORES", "8")
    clamped = worker_execution_limits()
    assert clamped.ram_bytes == 512 * 1024 * 1024
    assert clamped.timeout_ms == 30_000
    assert clamped.cpu_cores == 2

    monkeypatch.setenv("FGL_WORKER_RAM_LIMIT_MB", "16")
    monkeypatch.setenv("FGL_WORKER_TIMEOUT_MS", "100")
    monkeypatch.setenv("FGL_WORKER_CPU_CORES", "0")
    min_clamped = worker_execution_limits()
    assert min_clamped.ram_bytes == 64 * 1024 * 1024
    assert min_clamped.timeout_ms == 1_000
    assert min_clamped.cpu_cores == 1

