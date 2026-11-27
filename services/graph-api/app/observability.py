"""Redacted structured telemetry & workspace-scoped latency window; not an immutable audit sink."""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections import OrderedDict, deque
from datetime import UTC, datetime
from typing import NamedTuple
from uuid import uuid4

from app.security import SensitiveDataFilter, request_context

logger = logging.getLogger("fgl.telemetry")

DEFAULT_TELEMETRY_WINDOW_SECONDS = 900  # 15-minute sliding window
MAX_SAMPLES_PER_WORKSPACE = 2_000
MAX_TRACKED_WORKSPACES = 256
MAX_ROUTES_RETURNED = 12


class _RequestSample(NamedTuple):
    recorded_mono: float
    route: str
    method: str
    status: int
    duration_ms: float


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Compute linear-interpolated percentile over a non-empty sorted list."""
    if len(sorted_values) == 1:
        return round(sorted_values[0], 2)
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return round(sorted_values[lo], 2)
    weight = rank - lo
    value = sorted_values[lo] * (1.0 - weight) + sorted_values[hi] * weight
    return round(value, 2)


class WorkspaceTelemetryTracker:
    """Bounded, thread-safe per-workspace sliding-window latency and error-rate tracker.

    Strictly partitions samples by authenticated ``workspace_id`` so tenants never observe
    each other's traffic patterns. Operates purely in process memory with zero DB write cost.
    """

    def __init__(
        self,
        *,
        window_seconds: int = DEFAULT_TELEMETRY_WINDOW_SECONDS,
        max_samples_per_workspace: int = MAX_SAMPLES_PER_WORKSPACE,
        max_workspaces: int = MAX_TRACKED_WORKSPACES,
    ) -> None:
        if window_seconds < 1 or max_samples_per_workspace < 1 or max_workspaces < 1:
            raise ValueError("Telemetry tracker bounds must be positive integers.")
        self.window_seconds = window_seconds
        self.max_samples_per_workspace = max_samples_per_workspace
        self.max_workspaces = max_workspaces
        self._lock = threading.Lock()
        self._buckets: OrderedDict[str, deque[_RequestSample]] = OrderedDict()

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()

    def record(
        self,
        workspace_id: str,
        *,
        route: str,
        method: str,
        status: int,
        duration_ms: float,
        now_mono: float | None = None,
    ) -> None:
        if (
            not workspace_id
            or not isinstance(workspace_id, str)
            or not math.isfinite(duration_ms)
            or duration_ms < 0
        ):
            return
        now = time.monotonic() if now_mono is None else now_mono
        cutoff = now - self.window_seconds
        sample = _RequestSample(
            recorded_mono=now,
            route=route[:200] or "unmatched",
            method=method[:16] or "GET",
            status=int(status),
            duration_ms=float(duration_ms),
        )
        with self._lock:
            bucket = self._buckets.get(workspace_id)
            if bucket is None:
                if len(self._buckets) >= self.max_workspaces:
                    self._buckets.popitem(last=False)
                bucket = deque(maxlen=self.max_samples_per_workspace)
                self._buckets[workspace_id] = bucket
            else:
                self._buckets.move_to_end(workspace_id)
            while bucket and bucket[0].recorded_mono < cutoff:
                bucket.popleft()
            bucket.append(sample)

    def snapshot(
        self,
        workspace_id: str,
        *,
        now_mono: float | None = None,
    ) -> dict[str, object]:
        now = time.monotonic() if now_mono is None else now_mono
        cutoff = now - self.window_seconds
        with self._lock:
            bucket = self._buckets.get(workspace_id)
            if bucket is None:
                samples: list[_RequestSample] = []
            else:
                while bucket and bucket[0].recorded_mono < cutoff:
                    bucket.popleft()
                samples = list(bucket)

        count = len(samples)
        if count == 0:
            return {
                "scope": "process_memory_window",
                "window_seconds": self.window_seconds,
                "max_samples_per_workspace": self.max_samples_per_workspace,
                "sample_count": 0,
                "request_count": 0,
                "success_count": 0,
                "error_4xx_count": 0,
                "error_5xx_count": 0,
                "error_rate_pct": None,
                "error_free_rate_pct": None,
                "p50_ms": None,
                "p95_ms": None,
                "p99_ms": None,
                "max_ms": None,
                "routes": [],
            }

        durations = sorted(item.duration_ms for item in samples)
        err_4xx = sum(1 for item in samples if 400 <= item.status < 500)
        err_5xx = sum(1 for item in samples if item.status >= 500)
        success = count - err_4xx - err_5xx
        error_rate_pct = round(((err_4xx + err_5xx) / count) * 100.0, 2)
        error_free_rate_pct = round((success / count) * 100.0, 2)

        by_route: dict[tuple[str, str], list[_RequestSample]] = {}
        for item in samples:
            by_route.setdefault((item.method, item.route), []).append(item)

        routes: list[dict[str, object]] = []
        for (method, route), items in by_route.items():
            r_durations = sorted(x.duration_ms for x in items)
            r_count = len(items)
            r_errors = sum(1 for x in items if x.status >= 400)
            routes.append(
                {
                    "method": method,
                    "route": route,
                    "request_count": r_count,
                    "error_count": r_errors,
                    "error_rate_pct": round((r_errors / r_count) * 100.0, 2),
                    "p50_ms": _percentile(r_durations, 50.0),
                    "p95_ms": _percentile(r_durations, 95.0),
                    "p99_ms": _percentile(r_durations, 99.0),
                    "max_ms": round(r_durations[-1], 2),
                }
            )
        routes.sort(
            key=lambda row: (
                -float(row["p95_ms"]),  # type: ignore[arg-type]
                -int(row["request_count"]),  # type: ignore[arg-type]
                str(row["route"]),
            )
        )

        return {
            "scope": "process_memory_window",
            "window_seconds": self.window_seconds,
            "max_samples_per_workspace": self.max_samples_per_workspace,
            "sample_count": count,
            "request_count": count,
            "success_count": success,
            "error_4xx_count": err_4xx,
            "error_5xx_count": err_5xx,
            "error_rate_pct": error_rate_pct,
            "error_free_rate_pct": error_free_rate_pct,
            "p50_ms": _percentile(durations, 50.0),
            "p95_ms": _percentile(durations, 95.0),
            "p99_ms": _percentile(durations, 99.0),
            "max_ms": round(durations[-1], 2),
            "routes": routes[:MAX_ROUTES_RETURNED],
        }


workspace_telemetry = WorkspaceTelemetryTracker()


def configure_telemetry() -> None:
    logger.setLevel(logging.INFO)
    if not logger.hasHandlers():
        logger.addHandler(logging.StreamHandler())


def emit_event(event: str, *, level: int = logging.INFO, **fields: object) -> None:
    extra = {"severity": logging.getLevelName(level).lower()} if level != logging.INFO else {}
    payload = SensitiveDataFilter()._sanitize_value("", {
        **fields, **extra, **request_context.get(), "event": event,
        "timestamp": datetime.now(UTC).isoformat(),
    })
    # Emit at INFO+; deployments must explicitly collect fgl.telemetry at INFO.
    logger.log(level, "%s", json.dumps(payload, separators=(",", ":"), allow_nan=False))


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
                duration_ms = round((time.perf_counter() - started) * 1000, 3)
                route = getattr(scope.get("route"), "path", "unmatched")
                method = scope.get("method", "GET")
                workspace_id = request_context.get().get("workspace_id")
                if workspace_id:
                    workspace_telemetry.record(
                        workspace_id,
                        route=route,
                        method=method,
                        status=status,
                        duration_ms=duration_ms,
                    )
                # Template only; raw URL/query/body/headers may contain secrets.
                emit_event(
                    "http_request",
                    route=route,
                    status=status,
                    duration_ms=duration_ms,
                )
            finally:
                request_context.reset(token)
