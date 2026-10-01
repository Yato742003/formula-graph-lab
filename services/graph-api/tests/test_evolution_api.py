"""Authenticated E1 history/control routes never accept evaluation evidence."""

import pytest
from starlette.testclient import TestClient

from app.main import app
from app.research_store import Neo4jResearchStore
from tests.test_problem_spec import make_valid_definition_payload

HEADERS = {
    "Authorization": "Bearer test-service-secret",
    "X-FGL-Actor-ID": "researcher_42",
    "X-FGL-Actor-Role": "researcher",
    "X-FGL-Workspace-ID": "ws_evolution_api",
    "X-Idempotency-Key": "request-1",
}


@pytest.fixture(autouse=True)
def configure(monkeypatch):
    monkeypatch.setenv("SERVICE_TOKEN", "test-service-secret")
    app.state.research_store = Neo4jResearchStore(driver=None)


def _spec(client: TestClient) -> dict:
    response = client.post(
        "/v1/research/problems",
        headers=HEADERS,
        json={"definition": make_valid_definition_payload()},
    )
    assert response.status_code == 201
    return response.json()


def test_campaign_start_history_report_stop_and_workspace_scope():
    client = TestClient(app)
    spec = _spec(client)
    headers = {**HEADERS, "X-Idempotency-Key": "evolution-start"}
    started = client.post(
        "/v1/research/evolution", headers=headers, json={"spec_id": spec["spec_id"]}
    )
    assert started.status_code == 201
    campaign = started.json()["campaign"]
    assert started.json()["replayed"] is False
    assert campaign["status"] == "active"

    replay = client.post(
        "/v1/research/evolution", headers=headers, json={"spec_id": spec["spec_id"]}
    )
    assert replay.status_code == 200
    assert replay.json()["campaign"] == campaign
    assert replay.json()["replayed"] is True

    evolution_id = campaign["evolution_id"]
    page = client.get("/v1/research/evolution", headers=HEADERS)
    assert page.status_code == 200
    assert page.json()["total"] == 1
    assert page.json()["items"] == [campaign]
    assert client.get(
        f"/v1/research/evolution/{evolution_id}", headers=HEADERS
    ).json() == campaign

    report = client.get(
        f"/v1/research/evolution/{evolution_id}/report", headers=HEADERS
    )
    assert report.status_code == 200
    assert report.json()["compared_candidates"] == []
    assert report.json()["winner_ids"] == []
    assert len(report.json()["report_hash"]) == 64

    foreign = {**HEADERS, "X-FGL-Workspace-ID": "foreign"}
    assert client.get(
        f"/v1/research/evolution/{evolution_id}", headers=foreign
    ).status_code == 404

    stopped = client.post(
        f"/v1/research/evolution/{evolution_id}/stop",
        headers={**HEADERS, "X-Idempotency-Key": "evolution-stop"},
        json={},
    )
    assert stopped.status_code == 200
    assert stopped.json()["campaign"]["stop_reason"] == "user_stop"


def test_invalid_start_and_unearned_finalist_fail_closed():
    client = TestClient(app)
    spec = _spec(client)
    missing_key = {key: value for key, value in HEADERS.items() if key != "X-Idempotency-Key"}
    assert client.post(
        "/v1/research/evolution",
        headers=missing_key,
        json={"spec_id": spec["spec_id"]},
    ).status_code == 400

    started = client.post(
        "/v1/research/evolution",
        headers={**HEADERS, "X-Idempotency-Key": "start-finalist-test"},
        json={"spec_id": spec["spec_id"]},
    ).json()["campaign"]
    response = client.post(
        f"/v1/research/evolution/{started['evolution_id']}/finalists",
        headers={**HEADERS, "X-Idempotency-Key": "freeze-finalist-test"},
        json={"finalist_ids": ["cand_" + "a" * 32]},
    )
    assert response.status_code == 422
    assert "Pareto-eligible" in response.text

# Pre-FGL-601 API semantics only; strict authentication is covered in test_service_auth.py.
pytestmark = pytest.mark.usefixtures("legacy_phase5_service_auth")
