"""Tests for G1 ProblemSpec API and Persistence (T2)."""

import pytest
from starlette.testclient import TestClient

from app.main import app
from app.research_store import Neo4jResearchStore
from tests.test_problem_spec import make_valid_definition_payload

AUTH_HEADERS = {
    "Authorization": "Bearer test-service-secret",
    "X-FGL-Actor-ID": "researcher_42",
    "X-FGL-Actor-Role": "researcher",
    "X-FGL-Workspace-ID": "ws_alpha",
    "X-Idempotency-Key": "idemp_test_001",
}


def test_client_cannot_self_verify_artifacts():
    definition = make_valid_definition_payload()
    definition["baselines"][0]["readiness"] = "verified"
    response = TestClient(app).post(
        "/v1/research/problems", headers=AUTH_HEADERS, json={"definition": definition},
    )
    assert response.status_code == 422
    assert "server-owned" in response.text


def test_readiness_and_comparison_resolve_frozen_context_in_workspace():
    client = TestClient(app)
    definition = make_valid_definition_payload()
    first_response = client.post(
        "/v1/research/problems", headers=AUTH_HEADERS, json={"definition": definition},
    )
    assert first_response.status_code == 201
    first = first_response.json()
    readiness = client.get(
        "/v1/research/problems/readiness", headers=AUTH_HEADERS,
        params={"spec_id": first["spec_id"]},
    )
    assert readiness.status_code == 200
    assert readiness.json()["ready_to_run"] is False
    assert "evaluator_availability_unverified" in readiness.json()["blocked_reasons"]
    definition["task"] = "A different objective"
    second = client.post(
        "/v1/research/problems", headers={**AUTH_HEADERS, "X-Idempotency-Key": "revision-key"},
        json={"definition": definition, "parent_spec_id": first["spec_id"]},
    ).json()
    endpoint = "/v1/research/problems/compare"
    pair = {"left_spec_id": first["spec_id"], "right_spec_id": first["spec_id"]}
    assert client.get(endpoint, headers=AUTH_HEADERS, params=pair).status_code == 200
    pair["right_spec_id"] = second["spec_id"]
    assert client.get(endpoint, headers=AUTH_HEADERS, params=pair).status_code == 409
    foreign = {**AUTH_HEADERS, "X-FGL-Workspace-ID": "foreign"}
    assert client.get(endpoint, headers=foreign, params=pair).status_code == 404
    assert client.get("/v1/research/problems/readiness", headers=foreign,
                      params={"spec_id": first["spec_id"]}).status_code == 404


@pytest.fixture(autouse=True)
def configure_test_env(monkeypatch):
    monkeypatch.setenv("SERVICE_TOKEN", "test-service-secret")
    # Unit tests opt in to the non-durable store explicitly. Production does not.
    store = Neo4jResearchStore(driver=None)
    app.state.research_store = store


def test_missing_backend_credential_fails_closed_503(monkeypatch):
    monkeypatch.delenv("SERVICE_TOKEN", raising=False)
    client = TestClient(app)
    response = client.post(
        "/v1/research/problems",
        json={"definition": make_valid_definition_payload()},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


def test_invalid_bearer_token_returns_401():
    client = TestClient(app)
    bad_headers = dict(AUTH_HEADERS, Authorization="Bearer wrong-secret")
    response = client.post(
        "/v1/research/problems",
        json={"definition": make_valid_definition_payload()},
        headers=bad_headers,
    )
    assert response.status_code == 401


def test_missing_actor_returns_401():
    client = TestClient(app)
    bad_headers = dict(AUTH_HEADERS)
    del bad_headers["X-FGL-Actor-ID"]
    response = client.post(
        "/v1/research/problems",
        json={"definition": make_valid_definition_payload()},
        headers=bad_headers,
    )
    assert response.status_code == 401


def test_worker_or_invalid_role_returns_403():
    client = TestClient(app)
    bad_headers = dict(AUTH_HEADERS, **{"X-FGL-Actor-Role": "worker"})
    response = client.post(
        "/v1/research/problems",
        json={"definition": make_valid_definition_payload()},
        headers=bad_headers,
    )
    assert response.status_code == 403
    assert "human" in response.json()["detail"]


def test_missing_workspace_header_returns_400():
    client = TestClient(app)
    bad_headers = dict(AUTH_HEADERS)
    del bad_headers["X-FGL-Workspace-ID"]
    response = client.post(
        "/v1/research/problems",
        json={"definition": make_valid_definition_payload()},
        headers=bad_headers,
    )
    assert response.status_code == 400


def test_missing_idempotency_key_returns_400():
    client = TestClient(app)
    bad_headers = dict(AUTH_HEADERS)
    del bad_headers["X-Idempotency-Key"]
    response = client.post(
        "/v1/research/problems",
        json={"definition": make_valid_definition_payload()},
        headers=bad_headers,
    )
    assert response.status_code == 400
    assert "idempotency key is required" in response.json()["detail"]


def test_successful_freeze_creates_server_owned_fields():
    client = TestClient(app)
    payload = {"definition": make_valid_definition_payload()}
    response = client.post(
        "/v1/research/problems",
        json=payload,
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["workspace_id"] == "ws_alpha"
    assert data["version"] == 1
    assert data["parent_spec_id"] is None
    assert data["spec_id"].startswith("spec_")
    assert data["campaign_id"].startswith("cmp_")
    assert data["created_by"] == "researcher_42"
    assert data["created_at"]
    assert response.headers["cache-control"] == "no-store"


def test_idempotency_replay_returns_200_and_same_receipt():
    client = TestClient(app)
    payload = {"definition": make_valid_definition_payload()}
    resp1 = client.post(
        "/v1/research/problems",
        json=payload,
        headers=AUTH_HEADERS,
    )
    assert resp1.status_code == 201
    data1 = resp1.json()

    # Second request with identical key and intent
    resp2 = client.post(
        "/v1/research/problems",
        json=payload,
        headers=AUTH_HEADERS,
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data1["spec_id"] == data2["spec_id"]
    assert data1["campaign_id"] == data2["campaign_id"]
    assert data1["created_at"] == data2["created_at"]


def test_same_frozen_content_with_new_key_reuses_immutable_campaign():
    client = TestClient(app)
    payload = {"definition": make_valid_definition_payload()}
    first = client.post("/v1/research/problems", json=payload, headers=AUTH_HEADERS)
    second = client.post(
        "/v1/research/problems",
        json=payload,
        headers={**AUTH_HEADERS, "X-Idempotency-Key": "idemp_test_002"},
    )
    assert first.status_code == second.status_code == 201
    assert first.json()["spec_id"] == second.json()["spec_id"]
    assert first.json()["campaign_id"] == second.json()["campaign_id"]
    assert first.json()["created_at"] == second.json()["created_at"]


def test_idempotency_conflict_on_altered_intent_returns_409():
    client = TestClient(app)
    payload1 = {"definition": make_valid_definition_payload()}
    resp1 = client.post(
        "/v1/research/problems",
        json=payload1,
        headers=AUTH_HEADERS,
    )
    assert resp1.status_code == 201

    # Alter task intent under the same idempotency key
    payload2 = {"definition": make_valid_definition_payload()}
    payload2["definition"]["task"] = "Completely different altered research task"
    resp2 = client.post(
        "/v1/research/problems",
        json=payload2,
        headers=AUTH_HEADERS,
    )
    assert resp2.status_code == 409
    assert "altered problem spec intent" in resp2.json()["detail"]


def test_parent_spec_in_different_workspace_returns_404():
    client = TestClient(app)
    # 1. Create spec in workspace beta
    beta_headers = dict(
        AUTH_HEADERS,
        **{
            "X-FGL-Workspace-ID": "ws_beta",
            "X-Idempotency-Key": "idemp_beta_001",
        },
    )
    resp_beta = client.post(
        "/v1/research/problems",
        json={"definition": make_valid_definition_payload()},
        headers=beta_headers,
    )
    assert resp_beta.status_code == 201
    spec_beta_id = resp_beta.json()["spec_id"]

    # 2. Try to use spec_beta_id as parent in workspace alpha
    alpha_headers = dict(
        AUTH_HEADERS,
        **{
            "X-FGL-Workspace-ID": "ws_alpha",
            "X-Idempotency-Key": "idemp_alpha_revision",
        },
    )
    resp_alpha = client.post(
        "/v1/research/problems",
        json={
            "definition": make_valid_definition_payload(),
            "parent_spec_id": spec_beta_id,
        },
        headers=alpha_headers,
    )
    assert resp_alpha.status_code == 404
    assert "not found in this workspace" in resp_alpha.json()["detail"]


def test_valid_revision_increments_version_and_creates_new_campaign():
    client = TestClient(app)
    # 1. Create v1
    v1_headers = dict(AUTH_HEADERS, **{"X-Idempotency-Key": "idemp_parent"})
    resp_v1 = client.post(
        "/v1/research/problems",
        json={"definition": make_valid_definition_payload()},
        headers=v1_headers,
    )
    assert resp_v1.status_code == 201
    v1_data = resp_v1.json()
    assert v1_data["version"] == 1

    # 2. Create v2 revision
    v2_headers = dict(AUTH_HEADERS, **{"X-Idempotency-Key": "idemp_child"})
    child_def = make_valid_definition_payload()
    child_def["task"] = "Revision 2: extended budget attention search"
    resp_v2 = client.post(
        "/v1/research/problems",
        json={
            "definition": child_def,
            "parent_spec_id": v1_data["spec_id"],
        },
        headers=v2_headers,
    )
    assert resp_v2.status_code == 201
    v2_data = resp_v2.json()
    assert v2_data["version"] == 2
    assert v2_data["parent_spec_id"] == v1_data["spec_id"]
    assert v2_data["campaign_id"] != v1_data["campaign_id"]
    assert v2_data["spec_id"] != v1_data["spec_id"]


def test_two_workspaces_same_definition_have_different_spec_ids_and_tenant_isolation():
    client = TestClient(app)
    h_a = dict(
        AUTH_HEADERS,
        **{
            "X-FGL-Workspace-ID": "ws_a",
            "X-Idempotency-Key": "idemp_same_def",
        },
    )
    h_b = dict(
        AUTH_HEADERS,
        **{
            "X-FGL-Workspace-ID": "ws_b",
            "X-Idempotency-Key": "idemp_same_def",
        },
    )
    resp_a = client.post(
        "/v1/research/problems",
        json={"definition": make_valid_definition_payload()},
        headers=h_a,
    )
    resp_b = client.post(
        "/v1/research/problems",
        json={"definition": make_valid_definition_payload()},
        headers=h_b,
    )
    assert resp_a.status_code == 201
    assert resp_b.status_code == 201
    id_a = resp_a.json()["spec_id"]
    id_b = resp_b.json()["spec_id"]
    assert id_a != id_b

    # A cannot read B's spec
    get_a_from_b = client.get(f"/v1/research/problems/{id_b}", headers=h_a)
    assert get_a_from_b.status_code == 404

    # B cannot read A's spec
    get_b_from_a = client.get(f"/v1/research/problems/{id_a}", headers=h_b)
    assert get_b_from_a.status_code == 404


def test_list_problem_specs_pagination_and_scoping():
    client = TestClient(app)
    # Create 3 specs in ws_alpha
    for i in range(3):
        h = dict(AUTH_HEADERS, **{"X-Idempotency-Key": f"idemp_batch_{i}"})
        p = make_valid_definition_payload()
        p["task"] = f"Task batch number {i}"
        resp = client.post("/v1/research/problems", json={"definition": p}, headers=h)
        assert resp.status_code == 201

    # List with limit 2, offset 0
    list_resp = client.get("/v1/research/problems?limit=2&offset=0", headers=AUTH_HEADERS)
    assert list_resp.status_code == 200
    res_data = list_resp.json()
    assert res_data["total"] == 3
    assert len(res_data["items"]) == 2
    assert res_data["limit"] == 2
    assert res_data["offset"] == 0

    # List with offset 2
    list_resp2 = client.get("/v1/research/problems?limit=2&offset=2", headers=AUTH_HEADERS)
    assert list_resp2.status_code == 200
    assert len(list_resp2.json()["items"]) == 1


def test_client_cannot_pass_server_owned_fields():
    client = TestClient(app)
    corrupted_body = {
        "definition": make_valid_definition_payload(),
        "spec_id": "client_forged_id",
        "campaign_id": "client_forged_cmp",
        "created_by": "client_actor",
    }
    resp = client.post("/v1/research/problems", json=corrupted_body, headers=AUTH_HEADERS)
    assert resp.status_code == 422

# Pre-FGL-601 API semantics only; strict authentication is covered in test_service_auth.py.
pytestmark = pytest.mark.usefixtures("legacy_phase5_service_auth")
