from __future__ import annotations

import collections
import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

from starlette.responses import JSONResponse


class UnsafePaperUrl(ValueError):
    """Raised when a user-provided paper URL is outside the ingestion policy."""


ALLOWED_PAPER_HOSTS = ("arxiv.org", "export.arxiv.org")
_ALLOWED_HOSTS = set(ALLOWED_PAPER_HOSTS)
_MODERN_ARXIV_ID = re.compile(r"^(?P<id>\d{4}\.\d{4,5})(?P<version>v\d+)?$")


def normalize_arxiv_html_url(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        raise UnsafePaperUrl("Paper URL is required.")
    if any(character in candidate for character in ("\\", "\r", "\n", "\t", "%")):
        raise UnsafePaperUrl("Encoded or ambiguous URLs are not accepted.")

    try:
        parsed = urlsplit(candidate)
    except ValueError as exc:
        raise UnsafePaperUrl("Paper URL is malformed.") from exc

    if parsed.scheme != "https":
        raise UnsafePaperUrl("Only HTTPS paper URLs are accepted.")
    if parsed.hostname not in _ALLOWED_HOSTS:
        raise UnsafePaperUrl("Only arxiv.org HTML papers are supported in V1.")
    if parsed.username or parsed.password:
        raise UnsafePaperUrl("Credentials are not allowed in paper URLs.")
    try:
        if parsed.port is not None:
            raise UnsafePaperUrl("Custom ports are not allowed in paper URLs.")
    except ValueError as exc:
        raise UnsafePaperUrl("Paper URL contains an invalid port.") from exc

    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) != 2 or path_parts[0] not in {"abs", "html"}:
        raise UnsafePaperUrl("Use an arXiv /html/<paper-id> or /abs/<paper-id> URL.")

    match = _MODERN_ARXIV_ID.fullmatch(path_parts[1])
    if match is None:
        raise UnsafePaperUrl("Only modern numeric arXiv identifiers are supported in V1.")

    canonical_id = match.group("id") + (match.group("version") or "")
    return urlunsplit(("https", "arxiv.org", f"/html/{canonical_id}", "", ""))


def arxiv_identity(canonical_url: str) -> tuple[str, int | None]:
    paper_token = urlsplit(canonical_url).path.removeprefix("/html/")
    version_match = re.search(r"v(?P<version>\d+)$", paper_token)
    if version_match is None:
        return paper_token, None
    return paper_token[: version_match.start()], int(version_match.group("version"))


# --- OWASP Controls & Guardrails (FGL-602) ---

MAX_REQUEST_BODY_BYTES = 2 * 1024 * 1024  # 2 MB general JSON body cap
MAX_PAPER_IMPORT_BYTES = 4 * 1024  # Import JSON contains a URL, not uploaded HTML.


class PayloadTooLargeError(ValueError):
    """Raised when request payload exceeds defined size caps."""


def check_payload_size(
    content_length: int | None, max_bytes: int = MAX_REQUEST_BODY_BYTES
) -> None:
    if content_length is not None and content_length > max_bytes:
        raise PayloadTooLargeError(
            f"Payload size {content_length} exceeds limit of {max_bytes} bytes."
        )


class RequestBodyLimitMiddleware:
    """Bound streamed bodies before FastAPI's JSON parser or auth dependencies run."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        cap = MAX_PAPER_IMPORT_BYTES if scope["path"] == "/v1/imports" else MAX_REQUEST_BODY_BYTES
        lengths = [value for key, value in scope["headers"] if key.lower() == b"content-length"]
        if lengths and (len(lengths) != 1 or len(lengths[0]) > 20 or not lengths[0].isdigit()):
            return await JSONResponse({"detail": "Invalid content length."}, 400)(
                scope, receive, send
            )
        if lengths and int(lengths[0]) > cap:
            return await JSONResponse({"detail": "Request body exceeds limit."}, 413)(
                scope, receive, send
            )
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > cap:
                log_audit_event("payload_limit_exceeded", details={"limit_bytes": cap})
                return await JSONResponse({"detail": "Request body exceeds limit."}, 413)(
                    scope, receive, send
                )
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        return await self.app(scope, bounded_receive, send)


DEFAULT_WORKER_RAM_BYTES = 256 * 1024 * 1024  # 256 MB
MAX_WORKER_RAM_BYTES = 512 * 1024 * 1024  # 512 MB ceiling
DEFAULT_WORKER_TIMEOUT_MS = 10_000  # 10s
MAX_WORKER_TIMEOUT_MS = 30_000  # 30s ceiling


@dataclass(frozen=True)
class WorkerLimits:
    ram_bytes: int
    timeout_ms: int
    cpu_cores: int


def worker_execution_limits() -> WorkerLimits:
    """Returns runtime compute/RAM/timeout execution caps for workers."""
    try:
        ram_mb = int(os.getenv("FGL_WORKER_RAM_LIMIT_MB", "256"))
    except ValueError:
        ram_mb = 256
    ram_mb = max(64, min(ram_mb, 512))

    try:
        timeout_ms = int(os.getenv("FGL_WORKER_TIMEOUT_MS", "10000"))
    except ValueError:
        timeout_ms = 10_000
    timeout_ms = max(1_000, min(timeout_ms, 30_000))

    try:
        cpu_cores = int(os.getenv("FGL_WORKER_CPU_CORES", "1"))
    except ValueError:
        cpu_cores = 1
    cpu_cores = max(1, min(cpu_cores, 2))

    return WorkerLimits(
        ram_bytes=ram_mb * 1024 * 1024,
        timeout_ms=timeout_ms,
        cpu_cores=cpu_cores,
    )


class WorkspaceRateLimiter:
    """Sliding-window in-memory rate limiter per workspace."""

    # ponytail: in-memory sliding window rate limiter, ceiling: single-node instance,
    # upgrade: Redis / Cloudflare Rate Limiting for distributed rate enforcement.
    def __init__(self, default_limit: int = 120, window_seconds: float = 60.0):
        self.default_limit = default_limit
        self.window_seconds = window_seconds
        self._history: dict[str, collections.deque[float]] = collections.defaultdict(
            collections.deque
        )

    def check(
        self,
        workspace_id: str,
        limit: int | None = None,
        now: float | None = None,
    ) -> tuple[bool, float]:
        """Returns (is_allowed, retry_after_seconds)."""
        limit = limit or self.default_limit
        current_time = now if now is not None else time.monotonic()
        cutoff = current_time - self.window_seconds
        queue = self._history[workspace_id]

        while queue and queue[0] <= cutoff:
            queue.popleft()

        if len(queue) >= limit:
            oldest = queue[0]
            retry_after = max(1.0, math.ceil((oldest + self.window_seconds) - current_time))
            return False, retry_after

        queue.append(current_time)
        return True, 0.0

    def reset(self) -> None:
        self._history.clear()


workspace_rate_limiter = WorkspaceRateLimiter()


_SENSITIVE_KEYS = {
    "token",
    "secret",
    "authorization",
    "password",
    "api_key",
    "apikey",
    "raw_html",
    "prompt",
    "body",
    "source_html", "html", "output_json", "input", "content", "source_text",
}
_BEARER_PATTERN = re.compile(r"Bearer\s+[a-zA-Z0-9_\-\.]+", re.IGNORECASE)
_HTML_PATTERN = re.compile(r"</?[a-z][^>]*>", re.IGNORECASE)


def _sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "_", key.lower())
    return normalized in _SENSITIVE_KEYS or any(
        part in normalized for part in ("token", "secret", "password", "api_key", "prompt")
    )


class SensitiveDataFilter(logging.Filter):
    """OWASP FGL-602 log sanitization filter to prevent secret/HTML/prompt leakage."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._sanitize_value("", record.msg)

        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._sanitize_value(k, v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    self._sanitize_value("", a) for a in record.args
                )

        for key in list(record.__dict__.keys()):
            if key not in {"msg", "args", "exc_info", "exc_text", "stack_info"}:
                record.__dict__[key] = self._sanitize_value(key, record.__dict__[key])
        if record.exc_info:
            record.exc_text = f"[REDACTED_EXCEPTION:{record.exc_info[0].__name__}]"
            record.exc_info = None
        record.stack_info = None

        return True

    def _sanitize_text(self, text: str) -> str:
        text = _BEARER_PATTERN.sub("Bearer [REDACTED]", text)
        for env_var in (
            "SERVICE_TOKEN",
            "GRAPH_API_SERVICE_TOKEN",
            "OPENAI_API_KEY",
            "NEO4J_PASSWORD",
            "FGL_RESEARCH_QUEUE_SECRET", "SEARCH_CURSOR_SECRET",
        ):
            val = os.getenv(env_var)
            if val and len(val) >= 8 and val in text:
                text = text.replace(val, "[REDACTED]")
        if _HTML_PATTERN.search(text):
            return "[REDACTED_HTML]"
        text = text.replace("\r", r"\r").replace("\n", r"\n")
        return text

    def _sanitize_value(self, key: str, value: object, depth: int = 0) -> object:
        if _sensitive_key(key) or depth > 8:
            return "[REDACTED]"
        if isinstance(value, dict):
            return {k: self._sanitize_value(str(k), v, depth + 1) for k, v in value.items()}
        if isinstance(value, (tuple, list)):
            return type(value)(self._sanitize_value("", v, depth + 1) for v in value)
        if isinstance(value, str):
            return self._sanitize_text(value)
        if isinstance(value, BaseException):
            return type(value).__name__
        return value


_audit_logger = logging.getLogger("fgl.audit")


def log_audit_event(
    event_type: str,
    actor_id: str | None = None,
    workspace_id: str | None = None,
    details: dict | None = None,
) -> None:
    """Emit sanitized audit telemetry; durability/immutability requires an external sink."""

    payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        "audit_event": event_type,
        "actor_id": actor_id or "anonymous",
        "workspace_id": workspace_id or "unknown",
        "details": details or {},
    }
    payload = SensitiveDataFilter()._sanitize_value("", payload)
    _audit_logger.warning("AUDIT: %s", json.dumps(payload, separators=(",", ":")))


def install_log_sanitizer() -> None:
    """Installs SensitiveDataFilter on root and uvicorn loggers."""
    log_filter = SensitiveDataFilter()
    loggers = [logging.getLogger(), *[
        value for value in logging.Logger.manager.loggerDict.values()
        if isinstance(value, logging.Logger)
    ]]
    for logger in loggers:
        for target in (logger, *logger.handlers):
            if not any(isinstance(f, SensitiveDataFilter) for f in target.filters):
                target.addFilter(log_filter)

