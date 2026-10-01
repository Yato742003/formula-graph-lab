"""Real Neo4j replay and tenant isolation; no identity/auth dependency overrides."""

import asyncio
import hashlib
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from app import main
from app.evidence_store import Neo4jEvidenceStore
from app.main import app
from app.research_store import Neo4jResearchStore
from app.search import EvidenceSearchService, SearchCursorCodec
from tests.service_auth_helpers import TEST_KEY, sign_request
from tests.test_evidence_integration import connection
from tests.test_problem_spec import make_valid_definition_payload

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_durable_nonce_is_atomic_across_connections_and_keeps_expiry_grace():
    uri, user, password = connection()
    first = Neo4jEvidenceStore.connect(uri, user, password)
    second = Neo4jEvidenceStore.connect(uri, user, password)
    await first.initialize()
    nonce = str(uuid4())
    expires = int(time.time()) + 60
    try:
        results = await asyncio.gather(
            *[store.consume_service_nonce(nonce, expires) for store in [first, second] * 3]
        )
        assert sum(results) == 1
        assert await second.consume_service_nonce(nonce, expires) is False
        grace_nonce, old_nonce = str(uuid4()), str(uuid4())
        assert await first.consume_service_nonce(grace_nonce, int(time.time()) - 10)
        assert await first.consume_service_nonce(old_nonce, int(time.time()) - 600)
        rows, _, _ = await first.driver.execute_query(
            "MATCH (n:ServiceRequestNonce) WHERE n.id IN $ids RETURN n.id AS id",
            ids=[grace_nonce, old_nonce],
            database_=first.database,
        )
        assert [row["id"] for row in rows] == [grace_nonce]
    finally:
        await first.close()
        await second.close()


async def test_signed_http_import_search_spec_and_replica_replay(monkeypatch):
    uri, user, password = connection()
    first = Neo4jEvidenceStore.connect(uri, user, password)
    second = Neo4jEvidenceStore.connect(uri, user, password)
    await first.initialize()
    research = Neo4jResearchStore(first.driver, database=first.database)
    await research.initialize()
    actors = ["auth-test-" + uuid4().hex for _ in range(2)]
    workspaces = ["ws_" + hashlib.sha256(actor.encode()).hexdigest()[:48] for actor in actors]
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SERVICE_TOKEN", TEST_KEY)
    monkeypatch.delenv("FGL_ALLOW_LEGACY_SERVICE_AUTH", raising=False)
    monkeypatch.setattr(app.state, "evidence_store", first, raising=False)
    monkeypatch.setattr(app.state, "research_store", research, raising=False)
    monkeypatch.setattr(app.state, "semantic_store", None, raising=False)
    monkeypatch.setattr(
        app.state,
        "search_service",
        EvidenceSearchService(
            first,
            cursor_codec=SearchCursorCodec("separate-test-cursor-secret-at-least-32"),
        ),
        raising=False,
    )
    html = (Path(__file__).parent / "fixtures/arxiv_sample.html").read_text(encoding="utf-8")

    async def offline_paper(_url):
        return html, "https://arxiv.org/html/1706.03762v7"

    monkeypatch.setattr(main, "fetch_paper_html", offline_paper)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://api.test"
        ) as client:
            body = json.dumps(
                {"url": "https://arxiv.org/html/1706.03762v7", "workspace_id": workspaces[0]}
            ).encode()
            signed = sign_request("POST", "/v1/imports", body, actor=actors[0], role="graph_write")
            assert (
                await client.post("/v1/imports", content=body, headers=signed)
            ).status_code == 200
            # Another API instance uses another driver, but the replay ledger is shared.
            monkeypatch.setattr(app.state, "evidence_store", second)
            assert (
                await client.post("/v1/imports", content=body, headers=signed)
            ).status_code == 401
            signed = sign_request("POST", "/v1/imports", body, actor=actors[1], role="graph_write")
            assert (
                await client.post("/v1/imports", content=body, headers=signed)
            ).status_code == 403
            snapshot = json.dumps(
                {"paper_id": "1706.03762", "version": 7, "workspace_id": workspaces[1]}
            ).encode()
            response = await client.post(
                "/v1/graphs/snapshot",
                content=snapshot,
                headers=sign_request(
                    "POST",
                    "/v1/graphs/snapshot",
                    snapshot,
                    actor=actors[1],
                    role="graph_read",
                ),
            )
            assert response.status_code == 200 and not response.json()["nodes"]
            query = json.dumps({"query": "attention", "workspace_id": workspaces[0]}).encode()
            response = await client.post(
                "/v1/search",
                content=query,
                headers=sign_request(
                    "POST",
                    "/v1/search",
                    query,
                    actor=actors[0],
                    role="graph_read",
                ),
            )
            assert response.status_code == 200 and response.json()["hits"]
            spec = json.dumps({"definition": make_valid_definition_payload()}).encode()
            response = await client.post(
                "/v1/research/problems",
                content=spec,
                headers=sign_request(
                    "POST",
                    "/v1/research/problems",
                    spec,
                    actor=actors[0],
                    idempotency_key="freeze-auth-spec",
                ),
            )
            assert response.status_code == 201
            assert response.json()["created_by"] == actors[0]
            path = "/v1/research/problems/" + response.json()["spec_id"]
            assert (
                await client.get(path, headers=sign_request("GET", path, actor=actors[0]))
            ).status_code == 200
            assert (
                await client.get(path, headers=sign_request("GET", path, actor=actors[1]))
            ).status_code == 404
    finally:
        await first.close()
        await second.close()
