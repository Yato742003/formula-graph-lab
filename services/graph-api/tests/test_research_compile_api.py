"""Offline authorization checks for the bounded D2 compiler endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app, get_research_store
from app.research_store import Neo4jResearchStore


@pytest.fixture(autouse=True)
def compiler_config(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SERVICE_TOKEN", "gateway-secret-for-compiler-api")
    monkeypatch.setenv("FGL_ENABLE_RESEARCH_COMPILER", "true")
    app.state.research_store = Neo4jResearchStore(driver=None)
    yield
    app.dependency_overrides.clear()


def _body(**extra: object) -> dict[str, object]:
    return {
        "workspace_id": "workspace-1",
        "spec_id": "spec-1",
        "mapping_id": "mapping-1",
        "transform": {
            "operator": "mix_positive_feature_maps",
            "operator_version": "1",
            "target_node_id": "equation-1",
            "parameters": {"lambda": 0.25},
            "bindings": {"left": "symbol-1", "right": "symbol-2"},
        },
        **extra,
    }


def _headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer gateway-secret-for-compiler-api",
        "X-FGL-Actor-ID": "researcher-1",
        "X-FGL-Actor-Role": "researcher",
        "X-FGL-Workspace-ID": "workspace-1",
        "X-Idempotency-Key": "compiler-test-key-01",
    }


def test_compile_requires_authenticated_actor_and_explicit_enablement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(app)
    path = "/v1/research/candidates/compile"
    response = client.post(path, json=_body())
    assert response.status_code == 401

    monkeypatch.setenv("FGL_ENABLE_RESEARCH_COMPILER", "false")
    response = client.post(path, json=_body(), headers=_headers())
    assert response.status_code == 503


def test_compile_enforces_human_role_workspace_and_server_owned_fields() -> None:
    client = TestClient(app)
    path = "/v1/research/candidates/compile"
    assert (
        client.post(
            path,
            json=_body(),
            headers=_headers() | {"X-FGL-Actor-Role": "proposer"},
        ).status_code
        == 403
    )

    assert (
        client.post(
            path,
            json=_body(workspace_id="workspace-2"),
            headers=_headers(),
        ).status_code
        == 403
    )

    assert (
        client.post(
            path,
            json=_body(),
            headers=_headers() | {"X-FGL-Workspace-ID": "workspace-2"},
        ).status_code
        == 403
    )

    body = _body()
    body["approved"] = True
    assert client.post(path, json=body, headers=_headers()).status_code == 422


def test_compile_fails_closed_without_durable_store() -> None:
    app.dependency_overrides[get_research_store] = lambda: Neo4jResearchStore(driver=None)
    response = TestClient(app).post(
        "/v1/research/candidates/compile",
        json=_body(),
        headers=_headers(),
    )
    assert response.status_code == 503
    assert "storage" in response.json()["detail"].lower()


def test_candidate_history_requires_authenticated_workspace_and_durable_storage() -> None:
    client = TestClient(app)
    path = "/v1/research/candidates"
    assert client.get(path).status_code == 401

    response = client.get(
        path + "?limit=20&offset=0",
        headers=_headers(),
    )
    assert response.status_code == 503
    assert "history" in response.json()["detail"].lower()


def test_admission_accepts_only_a_gate_request_and_never_client_verdicts() -> None:
    path = "/v1/research/candidates/cand_" + "a" * 32 + "/admission"
    headers = _headers()
    safe = {"action": "can_run_numerical"}
    assert TestClient(app).post(path, json=safe, headers=headers).status_code == 503
    assert (
        TestClient(app)
        .post(path, json=safe | {"verification": {"symbolic": "supported"}}, headers=headers)
        .status_code
        == 422
    )
    assert (
        TestClient(app)
        .post(
            path,
            json={"action": "can_run_numerical", "claim_scope": "protocol"},
            headers=headers,
        )
        .status_code
        == 422
    )


def test_candidate_verification_is_separately_feature_gated_and_accepts_no_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = "/v1/research/candidates/cand_" + "a" * 32 + "/verify"
    headers = _headers()
    client = TestClient(app)
    monkeypatch.setenv("FGL_ENABLE_CANDIDATE_CHECKS", "false")
    assert client.post(path, json={}, headers=headers).status_code == 503

    monkeypatch.setenv("FGL_ENABLE_CANDIDATE_CHECKS", "true")
    assert client.post(path, json={"outcome": "supported"}, headers=headers).status_code == 422
    assert client.post(path, json={}, headers=headers).status_code == 503
    assert (
        client.post(
            path,
            json={},
            headers=headers | {"X-FGL-Actor-Role": "proposer"},
        ).status_code
        == 403
    )

# Pre-FGL-601 API semantics only; strict authentication is covered in test_service_auth.py.
pytestmark = pytest.mark.usefixtures("legacy_phase5_service_auth")
