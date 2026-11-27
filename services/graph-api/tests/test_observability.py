import asyncio
import json
import logging
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest

from app.observability import RequestTelemetryMiddleware, emit_event
from app.security import RequestBodyLimitMiddleware, request_context


@pytest.mark.asyncio
async def test_correlation_is_server_owned_isolated_and_redacted(caplog):
    async def endpoint(scope, receive, send):
        scope["route"] = SimpleNamespace(path="/v1/research/candidates/{candidate_id}")
        body = await receive()
        await asyncio.sleep(0)
        emit_event("candidate_receipt", candidate_id="cand_test", outcome="unknown",
                   nested={"prompt": body["body"].decode(), "secret": "PRIVATE_KEY"})
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    app = RequestTelemetryMiddleware(endpoint)
    with caplog.at_level(logging.INFO, logger="fgl.telemetry"):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as c:
            responses = await asyncio.gather(*[
                c.post("/SECRET_PATH?token=PRIVATE_QUERY", content="PRIVATE_PROMPT",
                       headers={"x-request-id": "forged"}) for _ in range(2)
            ])
    ids = [str(UUID(r.headers["x-request-id"])) for r in responses]
    assert len(set(ids)) == 2 and not request_context.get()
    records = [json.loads(r.message) for r in caplog.records if r.name == "fgl.telemetry"]
    for request_id in ids:
        related = [r for r in records if r["request_id"] == request_id]
        assert {r["event"] for r in related} == {"candidate_receipt", "http_request"}
        receipt = next(r for r in related if r["event"] == "candidate_receipt")
        assert receipt["outcome"] == "unknown"
        http = next(r for r in related if r["event"] == "http_request")
        assert http["status"] == 200 and http["duration_ms"] >= 0
        assert http["route"] == "/v1/research/candidates/{candidate_id}"
    assert all(s not in caplog.text for s in [
        "PRIVATE_PROMPT", "PRIVATE_KEY", "PRIVATE_QUERY", "SECRET_PATH", "forged",
    ])


@pytest.mark.asyncio
async def test_slow_body_has_total_deadline_and_correlated_failure(caplog):
    called, sent = False, []

    async def endpoint(scope, receive, send):
        nonlocal called
        called = True

    async def receive():
        # Empty chunks cannot evade a total read deadline or grow a chunk list.
        await asyncio.sleep(0.003)
        return {"type": "http.request", "body": b"", "more_body": True}

    async def send(message):
        sent.append(message)

    app = RequestTelemetryMiddleware(
        RequestBodyLimitMiddleware(endpoint, body_timeout_seconds=0.01),
    )
    with caplog.at_level(logging.INFO):
        await app({"type": "http", "path": "/v1/imports", "headers": []}, receive, send)
    assert not called and sent[0]["status"] == 408
    headers = dict(sent[0]["headers"])
    request_id = headers[b"x-request-id"].decode()
    assert request_id in caplog.text and "body_read_timeout" in caplog.text
    assert not request_context.get()


def test_operator_notes_and_rationale_are_redacted_and_alerts_include_severity(caplog):
    from app.security import log_audit_event

    with caplog.at_level(logging.INFO, logger="fgl.telemetry"), caplog.at_level(
        logging.WARNING, logger="fgl.audit"
    ):
        emit_event(
            "dead_letter_alert",
            level=logging.WARNING,
            code="JOB_EXPIRED_OR_CRASHED",
            job_id="11111111-1111-4111-8111-111111111111",
            notes="OPERATOR_PRIVATE_NOTES",
            rationale="OPERATOR_PRIVATE_RATIONALE",
            reservation_retained=True,
        )
        log_audit_event(
            "dead_letter_reconciled",
            actor_id="user-1",
            workspace_id="ws_test",
            details={
                "job_id": "11111111-1111-4111-8111-111111111111",
                "notes": "AUDIT_SECRET_NOTE",
            },
        )

    telemetry = next(
        json.loads(r.message) for r in caplog.records if r.name == "fgl.telemetry"
    )
    assert telemetry["severity"] == "warning"
    assert telemetry["notes"] == "[REDACTED]"
    assert telemetry["rationale"] == "[REDACTED]"
    assert telemetry["reservation_retained"] is True
    assert "OPERATOR_PRIVATE_NOTES" not in caplog.text
    assert "OPERATOR_PRIVATE_RATIONALE" not in caplog.text
    assert "AUDIT_SECRET_NOTE" not in caplog.text


def test_workspace_telemetry_tracker_computes_percentiles_isolates_tenants_and_expires():
    from app.observability import WorkspaceTelemetryTracker

    tracker = WorkspaceTelemetryTracker(
        window_seconds=60,
        max_samples_per_workspace=50,
        max_workspaces=2,
    )

    empty = tracker.snapshot("ws_empty", now_mono=100.0)
    assert empty["scope"] == "process_memory_window"
    assert empty["sample_count"] == 0
    assert empty["p50_ms"] is None
    assert empty["p95_ms"] is None
    assert empty["p99_ms"] is None
    assert empty["error_rate_pct"] is None
    assert empty["routes"] == []

    # Record 10 deterministic samples for ws_a (10, 20, ..., 100 ms) with one 422 and one 503
    for idx, dur in enumerate([10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]):
        status = 422 if idx == 7 else (503 if idx == 9 else 200)
        route = "/v1/checks/symbolic/quick" if idx >= 5 else "/v1/evidence/import-tex"
        tracker.record(
            "ws_a",
            route=route,
            method="POST",
            status=status,
            duration_ms=dur,
            now_mono=100.0 + idx,
        )

    # Tenant ws_b gets completely independent samples
    tracker.record(
        "ws_b",
        route="/v1/research/ops/dashboard",
        method="GET",
        status=200,
        duration_ms=5.0,
        now_mono=105.0,
    )

    snap_a = tracker.snapshot("ws_a", now_mono=110.0)
    assert snap_a["sample_count"] == 10
    assert snap_a["success_count"] == 8
    assert snap_a["error_4xx_count"] == 1
    assert snap_a["error_5xx_count"] == 1
    assert snap_a["error_rate_pct"] == 20.0
    assert snap_a["error_free_rate_pct"] == 80.0
    assert snap_a["p50_ms"] == 55.0
    assert snap_a["p95_ms"] == 95.5
    assert snap_a["p99_ms"] == 99.1
    assert snap_a["max_ms"] == 100.0
    assert len(snap_a["routes"]) == 2
    assert snap_a["routes"][0]["route"] == "/v1/checks/symbolic/quick"

    # Tenant ws_b must never see ws_a latency or routes
    snap_b = tracker.snapshot("ws_b", now_mono=110.0)
    assert snap_b["sample_count"] == 1
    assert snap_b["p50_ms"] == 5.0
    assert snap_b["routes"][0]["route"] == "/v1/research/ops/dashboard"

    # Sliding window expiration drops stale samples cleanly
    expired_a = tracker.snapshot("ws_a", now_mono=200.0)
    assert expired_a["sample_count"] == 0
    assert expired_a["p50_ms"] is None

    # LRU eviction caps tracked workspaces at max_workspaces=2
    tracker.record(
        "ws_c",
        route="/v1/health",
        method="GET",
        status=200,
        duration_ms=1.0,
        now_mono=201.0,
    )
    assert tracker.snapshot("ws_a", now_mono=201.0)["sample_count"] == 0


@pytest.mark.asyncio
async def test_request_telemetry_middleware_records_workspace_latency():
    from app.observability import workspace_telemetry

    workspace_telemetry.reset()

    async def endpoint(scope, receive, send):
        _ = receive
        scope["route"] = SimpleNamespace(path="/v1/research/ops/dashboard")
        request_context.set({**request_context.get(), "workspace_id": "ws_middleware_test"})
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    app = RequestTelemetryMiddleware(endpoint)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        resp = await client.get("/v1/research/ops/dashboard")
    assert resp.status_code == 200
    snap = workspace_telemetry.snapshot("ws_middleware_test")
    assert snap["sample_count"] == 1
    assert snap["routes"][0]["route"] == "/v1/research/ops/dashboard"
    workspace_telemetry.reset()
