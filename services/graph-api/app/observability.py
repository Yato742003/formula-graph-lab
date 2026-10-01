"""Redacted structured telemetry; not an immutable audit sink or billing ledger."""
from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from uuid import uuid4

from app.security import SensitiveDataFilter, request_context

logger = logging.getLogger("fgl.telemetry")


def configure_telemetry() -> None:
    logger.setLevel(logging.INFO)
    if not logger.hasHandlers():
        logger.addHandler(logging.StreamHandler())


def emit_event(event: str, **fields: object) -> None:
    payload = SensitiveDataFilter()._sanitize_value("", {
        **fields, **request_context.get(), "event": event,
        "timestamp": datetime.now(UTC).isoformat(),
    })
    # Emit at INFO; deployments must explicitly collect fgl.telemetry at INFO.
    logger.info("%s", json.dumps(payload, separators=(",", ":"), allow_nan=False))


class RequestTelemetryMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = str(uuid4())  # Never accept a caller's correlation/identity header.
        token = request_context.set({"request_id": request_id})
        started, status = time.perf_counter(), 499

        async def correlated_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message = {**message, "headers": [
                    (key, value) for key, value in message.get("headers", [])
                    if key.lower() != b"x-request-id"
                ] + [(b"x-request-id", request_id.encode("ascii"))]}
            await send(message)

        try:
            await self.app(scope, receive, correlated_send)
        except Exception:
            status = 500
            raise
        finally:
            try:
                # Template only; raw URL/query/body/headers may contain secrets.
                emit_event("http_request", route=getattr(scope.get("route"), "path", "unmatched"),
                           status=status, duration_ms=round((time.perf_counter()-started)*1000, 3))
            finally:
                request_context.reset(token)
