"""Offline trust-boundary tests for the G3 API.

Compatibility can only be created from persisted Evidence and ContractReview
records, so its happy path belongs to the real-Neo4j integration suite.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.main import app
from app.research_store import Neo4jResearchStore

AUTH_HEADERS = {
    "Authorization": "Bearer test-service-secret",
    "X-FGL-Actor-ID": "researcher_42",
    "X-FGL-Actor-Role": "researcher",
    "X-FGL-Workspace-ID": "ws-compat-1",
}


@pytest.fixture(autouse=True)
def configure_test_env(monkeypatch):
    monkeypatch.setenv("SERVICE_TOKEN", "test-service-secret")
    app.state.research_store = Neo4jResearchStore(driver=None)


def _mapping_payload():
    return {
        "mapping": {
            "producer_port": {
                "equation_id": "invented-producer",
                "version": 1,
                "scoped_symbol_id": "client-does-not-own-this",
                "symbol_name": "x",
                "domain": "real",
                "shape": [1],
            },
            "consumer_port": {
                "equation_id": "invented-consumer",
                "version": 1,
                "scoped_symbol_id": "client-does-not-own-this-either",
                "symbol_name": "x",
                "domain": "real",
                "shape": [1],
            },
        }
    }


def test_compatibility_unauthorized():
    response = TestClient(app).post("/v1/research/compatibility", json=_mapping_payload())
    assert response.status_code == 401


def test_client_cannot_supply_dependencies_evidence_or_approval():
    payload = _mapping_payload()
    payload["resolved_dependencies"] = []
    response = TestClient(app).post(
        "/v1/research/compatibility", headers=AUTH_HEADERS, json=payload
    )
    assert response.status_code == 422

    payload = _mapping_payload()
    payload["mapping"]["explicit_binding_reviewed"] = True
    response = TestClient(app).post(
        "/v1/research/compatibility", headers=AUTH_HEADERS, json=payload
    )
    assert response.status_code == 422


def test_compatibility_never_accepts_fake_equations_from_browser():
    response = TestClient(app).post(
        "/v1/research/compatibility",
        headers={**AUTH_HEADERS, "X-Idempotency-Key": "compatibility-create-test"},
        json=_mapping_payload(),
    )
    assert response.status_code == 503
    assert "storage" in response.json()["detail"].lower()


def test_compatibility_create_requires_bounded_idempotency_key():
    client = TestClient(app)
    for key in (None, b"\xe9", "x" * 201):
        headers = AUTH_HEADERS if key is None else {**AUTH_HEADERS, "X-Idempotency-Key": key}
        response = client.post(
            "/v1/research/compatibility", headers=headers, json=_mapping_payload()
        )
        assert response.status_code == 400


def test_compatibility_review_requires_idempotency_and_rationale():
    response = TestClient(app).post(
        "/v1/research/compatibility/map_fake/reviews",
        headers=AUTH_HEADERS,
        json={"decision": "reviewed", "notes": ""},
    )
    assert response.status_code == 422

    response = TestClient(app).post(
        "/v1/research/compatibility/map_fake/reviews",
        headers=AUTH_HEADERS,
        json={"decision": "reviewed", "notes": "Reviewed complete source scope."},
    )
    assert response.status_code == 400


def test_compatibility_fails_closed_when_research_store_is_absent():
    app.state.research_store = None
    response = TestClient(app).post(
        "/v1/research/compatibility", headers=AUTH_HEADERS, json=_mapping_payload()
    )
    assert response.status_code == 503
