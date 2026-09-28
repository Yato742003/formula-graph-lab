"""Offline API guards for source-backed lineage.

Happy-path lineage is exercised against real Neo4j evidence in the integration
suite. An in-memory store must never make a browser-provided source look real.
"""

from __future__ import annotations

import asyncio

import pytest
from starlette.testclient import TestClient

from app.lineage import PaperCoverageRecord
from app.main import app
from app.research_store import Neo4jResearchStore

AUTH_HEADERS = {
    "Authorization": "Bearer test-service-secret",
    "X-FGL-Actor-ID": "researcher_42",
    "X-FGL-Actor-Role": "researcher",
    "X-FGL-Workspace-ID": "ws-lineage-1",
}


@pytest.fixture(autouse=True)
def configure_test_env(monkeypatch):
    monkeypatch.setenv("SERVICE_TOKEN", "test-service-secret")
    app.state.research_store = Neo4jResearchStore(driver=None)


def _fake_assertion():
    return {
        "relation_type": "mathematical_derivation",
        "source": {"kind": "equation", "id": "invented-a", "version": 1},
        "target": {"kind": "equation", "id": "invented-b", "version": 1},
        "evidence": [
            {
                "source_entity_id": "invented-a",
                "anchor": "eq.1",
                "source_hash": "a" * 64,
            }
        ],
    }


def test_lineage_unauthorized():
    response = TestClient(app).post("/v1/research/lineage", json=_fake_assertion())
    assert response.status_code == 401


def test_lineage_schema_rejects_self_loop_before_persistence():
    payload = _fake_assertion()
    payload["relation_type"] = "citation"
    payload["target"] = payload["source"]
    response = TestClient(app).post("/v1/research/lineage", headers=AUTH_HEADERS, json=payload)
    assert response.status_code == 422


def test_lineage_relation_kind_is_not_interchangeable():
    payload = _fake_assertion()
    payload["relation_type"] = "citation"
    response = TestClient(app).post("/v1/research/lineage", headers=AUTH_HEADERS, json=payload)
    assert response.status_code == 422
    payload["relation_type"] = "mathematical_derivation"
    payload["source"]["kind"] = "paper"
    response = TestClient(app).post("/v1/research/lineage", headers=AUTH_HEADERS, json=payload)
    assert response.status_code == 422


def test_lineage_never_accepts_browser_invented_sources_without_evidence_graph():
    response = TestClient(app).post(
        "/v1/research/lineage",
        headers={**AUTH_HEADERS, "X-Idempotency-Key": "lineage-create-test"},
        json=_fake_assertion(),
    )
    assert response.status_code == 503
    assert "storage" in response.json()["detail"].lower()


def test_lineage_create_requires_bounded_idempotency_key():
    client = TestClient(app)
    for key in (None, b"\xe9", "x" * 201):
        headers = AUTH_HEADERS if key is None else {**AUTH_HEADERS, "X-Idempotency-Key": key}
        response = client.post("/v1/research/lineage", headers=headers, json=_fake_assertion())
        assert response.status_code == 400


def test_lineage_review_requires_idempotency_and_rationale():
    response = TestClient(app).post(
        "/v1/research/lineage/lineage_fake/reviews",
        headers=AUTH_HEADERS,
        json={"decision": "reviewed", "notes": ""},
    )
    assert response.status_code == 422

    response = TestClient(app).post(
        "/v1/research/lineage/lineage_fake/reviews",
        headers=AUTH_HEADERS,
        json={"decision": "reviewed", "notes": "Checked source scope."},
    )
    assert response.status_code == 400


def test_lineage_coverage_keeps_metadata_only_gap_visible():
    store = app.state.research_store
    asyncio.run(
        store.register_paper_coverage(
            workspace_id="ws-lineage-1",
            paper=PaperCoverageRecord(
                paper_id="2401.00003",
                title="Metadata-only paper",
                has_html=False,
                warnings=("html_missing",),
                ingested_at="2026-09-14T00:00:00+00:00",
            ),
        )
    )
    response = TestClient(app).get("/v1/research/lineage/coverage", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert response.json()["coverage_gaps"] == ["2401.00003"]


def test_lineage_fails_closed_when_research_store_is_absent():
    app.state.research_store = None
    response = TestClient(app).post(
        "/v1/research/lineage", headers=AUTH_HEADERS, json=_fake_assertion()
    )
    assert response.status_code == 503


def test_source_context_lookup_requires_auth_and_bounded_id():
    client = TestClient(app)
    assert client.get(
        "/v1/research/lineage/sources", params={"source_id": "eq-a"},
    ).status_code == 401
    assert client.get(
        "/v1/research/lineage/sources", headers=AUTH_HEADERS,
        params={"source_id": "x" * 201},
    ).status_code == 422


@pytest.mark.parametrize("overrides", [
    {"max_nodes": 501}, {"max_nodes": 0}, {"max_depth": 9}, {"max_depth": -1},
    {"direction": "sideways"}, {"endpoint_kind": "workspace"}, {"endpoint_version": 0},
])
def test_traversal_rejects_invalid_limits_before_storage(overrides):
    response = TestClient(app).get(
        "/v1/research/lineage/traverse", headers=AUTH_HEADERS,
        params={"endpoint_kind": "equation", "endpoint_id": "test", **overrides},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("injected", [{"has_html": True}, {"equation_count": 2},
                                      {"registered_by": "forged"}])
def test_metadata_registration_cannot_claim_imported_evidence(injected):
    response = TestClient(app).post(
        "/v1/research/lineage/coverage", headers=AUTH_HEADERS,
        json={"paper_id": "paper-c", "title": "Missing HTML",
              "source_reference": "Bibliography entry in paper A", **injected},
    )
    assert response.status_code == 422


def test_metadata_registration_requires_durable_storage_and_retry_key():
    payload = {"paper_id": "paper-c", "title": "Missing HTML", "source_reference": "Citation"}
    client = TestClient(app)
    assert client.post("/v1/research/lineage/coverage", headers=AUTH_HEADERS,
                       json=payload).status_code == 400
    response = client.post(
        "/v1/research/lineage/coverage",
        headers={**AUTH_HEADERS, "X-Idempotency-Key": "metadata-key"},
        json=payload,
    )
    assert response.status_code == 503
