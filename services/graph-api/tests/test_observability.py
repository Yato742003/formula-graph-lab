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
