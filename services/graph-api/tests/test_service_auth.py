"""FGL-601 strict auth gate. No legacy switch or dependency auth bypass."""

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated
from unittest.mock import AsyncMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth import ServiceActor, require_human_service_actor, require_research_service_actor
from app.main import app, require_search_service
from app.models import EvidenceSearchResponse
from app.research_store import Neo4jResearchStore
from app.security import workspace_rate_limiter
from tests.service_auth_helpers import TEST_KEY, TEST_WORKSPACE, sign_request


class Nonces:
    def __init__(self):
        self.used = set()

    async def consume_service_nonce(self, nonce, _expires_at):
        if nonce in self.used:
            return False
        self.used.add(nonce)
        return True


@pytest.fixture(autouse=True)
def strict_auth(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SERVICE_TOKEN", TEST_KEY)
    monkeypatch.delenv("FGL_ALLOW_LEGACY_SERVICE_AUTH", raising=False)
    monkeypatch.setattr(app.state, "evidence_store", Nonces(), raising=False)
    monkeypatch.setattr(app.state, "research_store", Neo4jResearchStore(driver=None), raising=False)
    app.dependency_overrides[require_search_service] = lambda: SimpleNamespace(
        search=AsyncMock(
            return_value=EvidenceSearchResponse(hits=[], next_cursor=None, semantic_available=False)
        ),
    )
    workspace_rate_limiter.reset()
    yield
    app.dependency_overrides.clear()


def search_body(workspace=TEST_WORKSPACE):
    return json.dumps({"query": "attention", "workspace_id": workspace}).encode()


def test_webcrypto_vector_is_accepted_and_cannot_be_replayed(monkeypatch):
    vector = json.loads((Path(__file__).parent / "fixtures/service-request-v1.json").read_text())
    monkeypatch.setattr("app.auth.time.time", lambda: vector["claims"]["iat"])
    headers = {"authorization": "Bearer " + vector["token"], "content-type": "application/json"}
    client = TestClient(app)
    assert client.post("/v1/search", headers=headers, content=vector["body"]).status_code == 200
    assert client.post("/v1/search", headers=headers, content=vector["body"]).status_code == 401
    headers = sign_request("POST", "/v1/search", vector["body"].encode(), role="graph_read")
    assert client.post("/v1/search", headers=headers, content=vector["body"]).status_code == 200


@pytest.mark.parametrize("environment", ["development", "production", "staging"])
def test_default_denies_anonymous_static_bearer_and_forged_identity(environment, monkeypatch):
    monkeypatch.setenv("APP_ENV", environment)
    client = TestClient(app)
    for headers in [
        {},
        {"authorization": "Bearer " + TEST_KEY},
        {
            "x-fgl-actor-id": "admin",
            "x-fgl-actor-role": "admin",
            "x-fgl-workspace-id": TEST_WORKSPACE,
        },
        {"authorization": "Bearer " + "v" * 40},
    ]:
        assert (
            client.post(
                "/v1/search",
                content=search_body(),
                headers={
                    "content-type": "application/json",
                    **headers,
                },
            ).status_code
            == 401
        )


def test_obsolete_legacy_flag_cannot_enable_raw_bearer(monkeypatch):
    monkeypatch.setenv("FGL_ALLOW_LEGACY_SERVICE_AUTH", "true")
    assert (
        TestClient(app)
        .post(
            "/v1/search",
            content=search_body(),
            headers={
                "authorization": "Bearer " + TEST_KEY,
                "content-type": "application/json",
            },
        )
        .status_code
        == 401
    )


@pytest.mark.parametrize(
    "claims,status",
    [
        ({"exp": 0}, 401),
        ({"iat": 1, "exp": 9999999999}, 401),
        ({"iat": 9999999999, "exp": 10000000059}, 401),
        ({"iss": "evil"}, 401),
        ({"aud": "worker"}, 401),
        ({"v": 2}, 401),
        ({"v": True}, 401),
        ({"exp": True}, 401),
        ({"extra": "field"}, 401),
        ({"jti": "not-a-nonce"}, 401),
        ({"actor_role": "proposer"}, 403),
        ({"service_role": "graph_write"}, 403),
        ({"service_role": "research_read"}, 403),
        ({"service_role": "worker"}, 401),
        ({"workspace_id": "ws_" + "f" * 48}, 403),
    ],
)
def test_signed_but_invalid_claims_fail_closed(claims, status):
    body = search_body()
    headers = sign_request("POST", "/v1/search", body, role="graph_read", overrides=claims)
    assert TestClient(app).post("/v1/search", content=body, headers=headers).status_code == status


def test_expiration_is_strict_at_boundary(monkeypatch):
    now = int(time.time())
    body = search_body()
    headers = sign_request("POST", "/v1/search", body, role="graph_read")
    monkeypatch.setattr("app.auth.time.time", lambda: now + 60)
    assert TestClient(app).post("/v1/search", content=body, headers=headers).status_code == 401


@pytest.mark.parametrize(
    "header,value",
    [
        ("x-fgl-actor-id", "another-user"),
        ("x-fgl-actor-role", "admin"),
        ("x-fgl-workspace-id", "ws_" + "f" * 48),
        ("x-idempotency-key", "tampered"),
    ],
)
def test_context_header_tampering_is_denied(header, value):
    body = search_body()
    headers = sign_request("POST", "/v1/search", body, role="graph_read")
    headers[header] = value
    assert TestClient(app).post("/v1/search", content=body, headers=headers).status_code == 401


def test_changed_body_path_query_method_or_signature_is_denied():
    body = search_body()
    headers = sign_request("POST", "/v1/search", body, role="graph_read")
    client = TestClient(app)
    assert (
        client.post("/v1/search", content=search_body("foreign"), headers=headers).status_code
        == 401
    )
    assert client.post("/v1/search?limit=10", content=body, headers=headers).status_code == 401
    assert client.post("/v1/graphs/snapshot", content=body, headers=headers).status_code == 401
    read = sign_request("GET", "/v1/research/problems")
    assert client.post("/v1/research/problems", content=b"{}", headers=read).status_code == 401
    forged = sign_request("POST", "/v1/search", body, role="graph_read", key="wrong-key" * 8)
    assert client.post("/v1/search", content=body, headers=forged).status_code == 401


def test_signed_cross_workspace_body_or_selector_is_denied():
    client = TestClient(app)
    body = search_body("ws_" + "f" * 48)
    headers = sign_request("POST", "/v1/search", body, role="graph_read")
    assert client.post("/v1/search", content=body, headers=headers).status_code == 403
    path = "/v1/research/problems?workspace_id=foreign"
    assert client.get(path, headers=sign_request("GET", path)).status_code == 403


def test_nonce_storage_outage_is_not_an_authentication_bypass(monkeypatch):
    monkeypatch.setattr(app.state, "evidence_store", None)
    body = search_body()
    response = TestClient(app).post(
        "/v1/search",
        content=body,
        headers=sign_request("POST", "/v1/search", body, role="graph_read"),
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "Service replay protection unavailable."


def test_duplicate_authorization_or_context_headers_are_rejected():
    body = search_body()
    headers = sign_request("POST", "/v1/search", body, role="graph_read")
    client = TestClient(app)
    assert (
        client.post(
            "/v1/search",
            content=body,
            headers=[*headers.items(), ("authorization", headers["authorization"])],
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/v1/search",
            content=body,
            headers=[
                *headers.items(),
                ("x-fgl-workspace-id", TEST_WORKSPACE),
                ("x-fgl-workspace-id", TEST_WORKSPACE),
            ],
        ).status_code
        == 401
    )


def test_one_request_can_use_both_auth_dependencies_without_consuming_nonce_twice():
    probe = FastAPI()
    probe.state.evidence_store = Nonces()
    path = "/v1/research/auth-cache-test"

    @probe.get(path)
    async def read(
        actor: Annotated[ServiceActor, Depends(require_human_service_actor)],
        context: Annotated[tuple[ServiceActor, str], Depends(require_research_service_actor)],
    ):
        return {"actor": actor.actor_id, "workspace": context[1]}

    client = TestClient(probe)
    headers = sign_request("GET", path)
    assert client.get(path, headers=headers).status_code == 200
    assert len(probe.state.evidence_store.used) == 1
    assert client.get(path, headers=headers).status_code == 401


@pytest.mark.parametrize("key", [None, "short", " " * 32, "é" * 32])
def test_missing_or_invalid_signing_configuration_is_fail_closed(key, monkeypatch):
    if key is None:
        monkeypatch.delenv("SERVICE_TOKEN", raising=False)
    else:
        monkeypatch.setenv("SERVICE_TOKEN", key)
    client = TestClient(app)
    assert (
        client.post(
            "/v1/search",
            content=search_body(),
            headers={
                "content-type": "application/json",
            },
        ).status_code
        == 503
    )
    assert client.get("/health").status_code == 200


def test_research_read_token_cannot_create_spec_or_review(monkeypatch):
    monkeypatch.setenv("FGL_ENABLE_PROPOSALS", "true")
    client = TestClient(app)
    for path in [
        "/v1/research/problems",
        "/v1/research/compatibility/map_1/reviews",
        "/v1/research/candidates/cand_1/admission",
    ]:
        body = b"{}"
        response = client.post(
            path, content=body, headers=sign_request("POST", path, body, role="research_read")
        )
        assert response.status_code == 403


def test_worker_gateway_separation_is_preserved(monkeypatch):
    monkeypatch.setenv("FGL_ENABLE_PROPOSALS", "true")
    monkeypatch.setenv(
        "FGL_WORKER_IDENTITIES",
        json.dumps(
            [
                {
                    "identity": "proposer",
                    "token": "p" * 40,
                    "role": "proposer",
                    "workspaces": [TEST_WORKSPACE],
                }
            ]
        ),
    )
    path = "/v1/research/proposals"
    body = json.dumps({"workspace_id": TEST_WORKSPACE, "output_json": "{}"}).encode()
    assert (
        TestClient(app)
        .post(path, content=body, headers=sign_request("POST", path, body))
        .status_code
        == 401
    )
    assert (
        TestClient(app)
        .get(
            "/v1/research/problems",
            headers={
                "authorization": "Bearer " + "p" * 40,
            },
        )
        .status_code
        == 401
    )


def test_token_without_actor_headers_still_uses_signed_actor_and_workspace(monkeypatch):
    export = AsyncMock(
        return_value=SimpleNamespace(
            model_dump=lambda mode: {"bundle_hash": "a" * 64},
        )
    )
    monkeypatch.setattr(
        app.state, "research_store", SimpleNamespace(export_compiler_replay_bundle=export)
    )
    path = f"/v1/research/candidates/cand_{'a' * 32}/activities/act_{'b' * 32}/bundle"
    response = TestClient(app).get(path, headers=sign_request("GET", path))
    assert response.status_code == 200
    assert export.await_args.kwargs["workspace_id"] == TEST_WORKSPACE
    assert response.headers["cache-control"] == "no-store"


def test_payload_too_large_is_rejected_with_413():
    client = TestClient(app)
    body = search_body()
    headers = sign_request("POST", "/v1/search", body, role="graph_read")
    headers["content-length"] = str(3 * 1024 * 1024)
    response = client.post("/v1/search", content=body, headers=headers)
    assert response.status_code == 413
    assert "exceeds limit" in response.json()["detail"]


def test_workspace_rate_limiting_returns_429(monkeypatch):
    monkeypatch.setattr(workspace_rate_limiter, "default_limit", 2)
    client = TestClient(app)
    body = search_body()
    h1 = sign_request("POST", "/v1/search", body, role="graph_read")
    assert client.post("/v1/search", content=body, headers=h1).status_code == 200
    h2 = sign_request("POST", "/v1/search", body, role="graph_read")
    assert client.post("/v1/search", content=body, headers=h2).status_code == 200
    h3 = sign_request("POST", "/v1/search", body, role="graph_read")
    resp = client.post("/v1/search", content=body, headers=h3)
    assert resp.status_code == 429
    assert resp.json()["detail"] == "Workspace quota exceeded. Please slow down."
    assert "Retry-After" in resp.headers


def test_tampered_request_triggers_audit_event(caplog):
    import logging

    body = search_body()
    headers = sign_request("POST", "/v1/search", body, role="graph_read")
    headers["x-fgl-actor-id"] = "tampered-actor"
    with caplog.at_level(logging.WARNING, logger="fgl.audit"):
        response = TestClient(app).post("/v1/search", content=body, headers=headers)
    assert response.status_code == 401
    audit_msgs = [r.message for r in caplog.records if r.message.startswith("AUDIT: ")]
    assert len(audit_msgs) >= 1
    assert "tamper_detected" in audit_msgs[0]


def test_ops_dashboard_and_job_recovery_endpoints():
    client = TestClient(app)
    path = "/v1/research/ops/dashboard"
    headers = sign_request("GET", path)
    response = client.get(path, headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("ok", "degraded")
    assert "subsystems" in data
    assert "import" in data["subsystems"]
    assert "checker" in data["subsystems"]
    assert "worker" in data["subsystems"]
    assert data["subsystems"]["checker"]["max_ram_bytes"] == 256 * 1024 * 1024

    recover_path = "/v1/research/ops/jobs/recover"
    post_headers = sign_request("POST", recover_path, b"{}")
    rec_response = client.post(recover_path, content=b"{}", headers=post_headers)
    assert rec_response.status_code == 200
    rec_data = rec_response.json()
    assert "recovered_count" in rec_data
    assert "recovered_job_ids" in rec_data


