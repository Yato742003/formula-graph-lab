from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app


def test_replay_bundle_export_uses_authenticated_workspace_and_is_not_cached(monkeypatch):
    monkeypatch.setenv("SERVICE_TOKEN", "gateway-secret-for-replay-bundle")
    calls = []

    class Store:
        async def export_compiler_replay_bundle(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                bundle_hash="a" * 64,
                model_dump=lambda mode: {
                    "schema_version": "compiler-replay-bundle.v1",
                    "bundle_hash": "a" * 64,
                }
            )

    monkeypatch.setattr(app.state, "research_store", Store(), raising=False)
    client = TestClient(app)
    path = f"/v1/research/candidates/cand_{'a' * 32}/activities/act_{'b' * 32}/bundle"
    auth_headers = {
        "Authorization": "Bearer gateway-secret-for-replay-bundle",
        "X-FGL-Actor-ID": "user-1",
        "X-FGL-Actor-Role": "researcher",
        "X-FGL-Workspace-ID": "workspace-authorized-by-gateway",
    }

    assert client.get(path).status_code == 401
    assert calls == []

    response = client.get(
        path + "?bundle_hash=" + "a" * 64,
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.json()["schema_version"] == "compiler-replay-bundle.v1"
    assert client.get(path + "?bundle_hash=invalid", headers=auth_headers).status_code == 422
    stale = client.get(
        path + "?bundle_hash=" + "b" * 64,
        headers=auth_headers,
    )
    assert stale.status_code == 409
    assert calls == [{
        "workspace_id": "workspace-authorized-by-gateway",
        "candidate_id": "cand_" + "a" * 32,
        "activity_id": "act_" + "b" * 32,
    }] * 2


def test_replay_report_recompiles_in_authorized_workspace_and_is_not_cached(monkeypatch):
    import app.main as main

    monkeypatch.setenv("SERVICE_TOKEN", "gateway-secret-for-replay-report")
    calls = []

    class Store:
        async def export_compiler_replay_bundle(self, **kwargs):
            calls.append(kwargs)
            return object()

    class Report:
        def model_dump(self, mode):
            assert mode == "json"
            return {
                "schema_version": "compiler-replay-report.v1",
                "status": "partial",
                "empirical_experiment": "not_run",
            }

    monkeypatch.setattr(app.state, "research_store", Store(), raising=False)
    monkeypatch.setattr(main, "build_compiler_replay_report", lambda bundle: Report())
    client = TestClient(app)
    candidate = "cand_" + "c" * 32
    activity = "act_" + "d" * 32
    path = f"/v1/research/candidates/{candidate}/activities/{activity}/report"
    headers = {
        "Authorization": "Bearer gateway-secret-for-replay-report",
        "X-FGL-Actor-ID": "user-1",
        "X-FGL-Actor-Role": "researcher",
        "X-FGL-Workspace-ID": "workspace-authorized-by-gateway",
    }

    assert client.get(path).status_code == 401
    assert calls == []

    response = client.get(path, headers=headers)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.json()["status"] == "partial"
    assert response.json()["empirical_experiment"] == "not_run"
    assert calls == [{
        "workspace_id": "workspace-authorized-by-gateway",
        "candidate_id": candidate,
        "activity_id": activity,
    }]
