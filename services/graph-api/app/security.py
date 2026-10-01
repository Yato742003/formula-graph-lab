from __future__ import annotations

import collections
import json
import logging
import math
import os
import re
import time
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit


class UnsafePaperUrl(ValueError):
    """Raised when a user-provided paper URL is outside the ingestion policy."""


_ALLOWED_HOSTS = {"arxiv.org", "export.arxiv.org"}
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
MAX_PAPER_IMPORT_BYTES = 10 * 1024 * 1024  # 10 MB paper HTML cap


class PayloadTooLargeError(ValueError):
    """Raised when request payload exceeds defined size caps."""


def check_payload_size(
    content_length: int | None, max_bytes: int = MAX_REQUEST_BODY_BYTES
) -> None:
    if content_length is not None and content_length > max_bytes:
        raise PayloadTooLargeError(
            f"Payload size {content_length} exceeds limit of {max_bytes} bytes."
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
}
_BEARER_PATTERN = re.compile(r"Bearer\s+[a-zA-Z0-9_\-\.]+", re.IGNORECASE)
_HTML_PATTERN = re.compile(
    r"<(html|head|body|script|style|svg|div)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL
)


class SensitiveDataFilter(logging.Filter):
    """OWASP FGL-602 log sanitization filter to prevent secret/HTML/prompt leakage."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._sanitize_text(record.msg)

        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._sanitize_value(k, v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    self._sanitize_text(str(a)) if isinstance(a, str) else a for a in record.args
                )

        for key in list(record.__dict__.keys()):
            if key.lower() in _SENSITIVE_KEYS:
                record.__dict__[key] = "[REDACTED]"

        return True

    def _sanitize_text(self, text: str) -> str:
        text = _BEARER_PATTERN.sub("Bearer [REDACTED]", text)
        for env_var in (
            "SERVICE_TOKEN",
            "GRAPH_API_SERVICE_TOKEN",
            "OPENAI_API_KEY",
            "NEO4J_PASSWORD",
        ):
            val = os.getenv(env_var)
            if val and len(val) >= 8 and val in text:
                text = text.replace(val, "[REDACTED]")
        if "<html" in text.lower() or "<body" in text.lower():
            text = _HTML_PATTERN.sub("[REDACTED_HTML]", text)
        return text

    def _sanitize_value(self, key: str, value: object) -> object:
        if key.lower() in _SENSITIVE_KEYS:
            return "[REDACTED]"
        if isinstance(value, str):
            return self._sanitize_text(value)
        return value


_audit_logger = logging.getLogger("fgl.audit")


def log_audit_event(
    event_type: str,
    actor_id: str | None = None,
    workspace_id: str | None = None,
    details: dict | None = None,
) -> None:
    """Logs structured immutable audit events (tamper, quota exceeded, access denied)."""
    sanitized_details = {}
    if details:
        for k, v in details.items():
            if k.lower() in _SENSITIVE_KEYS:
                sanitized_details[k] = "[REDACTED]"
            else:
                sanitized_details[k] = v

    payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        "audit_event": event_type,
        "actor_id": actor_id or "anonymous",
        "workspace_id": workspace_id or "unknown",
        "details": sanitized_details,
    }
    _audit_logger.warning("AUDIT: %s", json.dumps(payload, separators=(",", ":")))


def install_log_sanitizer() -> None:
    """Installs SensitiveDataFilter on root and uvicorn loggers."""
    log_filter = SensitiveDataFilter()
    root_logger = logging.getLogger()
    if log_filter not in root_logger.filters:
        root_logger.addFilter(log_filter)
    for handler in root_logger.handlers:
        if log_filter not in handler.filters:
            handler.addFilter(log_filter)
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "fgl.audit"):
        logger = logging.getLogger(name)
        if log_filter not in logger.filters:
            logger.addFilter(log_filter)

