"""The public fixture endpoint accepts intent only and fails closed by default."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app
from app.research_store import Neo4jResearchStore


def _headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer gateway-secret-for-numerical-api",
        "X-FGL-Actor-ID": "researcher-1",
        "X-FGL-Actor-Role": "researcher",
        "X-FGL-Workspace-ID": "workspace-1",
        "X-Idempotency-Key": "numerical-fixture-api-test",
    }


def test_numerical_fixture_endpoint_is_disabled_and_only_accepts_seed(monkeypatch):
    monkeypatch.setenv("SERVICE_TOKEN", "gateway-secret-for-numerical-api")
    monkeypatch.setenv("FGL_ENABLE_NUMERICAL_FIXTURE", "false")
    monkeypatch.setenv("FGL_SANDBOX_IMAGE", "sha256:" + "a" * 64)
    monkeypatch.setenv("FGL_RESEARCH_QUEUE_SECRET", "q" * 40)
    monkeypatch.setenv(
        "FGL_WORKER_IDENTITIES",
        json.dumps([{
            "identity": "verifier-1",
            "token": "worker-token-distinct-from-gateway-123456",
            "role": "verifier",
            "workspaces": ["workspace-1"],
        }]),
    )
    monkeypatch.setattr(
        app.state, "research_store", Neo4jResearchStore(driver=None), raising=False
    )
    client = TestClient(app)
    path = "/v1/research/candidates/cand_" + "a" * 32 + "/numerical-fixture"

    assert client.post(path, json={"seed": 7}, headers=_headers()).status_code == 503

    monkeypatch.setenv("FGL_ENABLE_NUMERICAL_FIXTURE", "true")
    assert client.post(path, json={"seed": 7}, headers=_headers()).status_code == 503
    assert (
        client.post(path, json={"seed": 7, "outcome": "passed_suite"}, headers=_headers())
        .status_code
        == 422
    )
    assert client.post(path, json={"seed": True}, headers=_headers()).status_code == 422


def test_enabled_fixture_endpoint_uses_atomic_queue_result_finish(monkeypatch):
    monkeypatch.setenv("SERVICE_TOKEN", "gateway-secret-for-numerical-api")
    monkeypatch.setenv("FGL_ENABLE_NUMERICAL_FIXTURE", "true")
    monkeypatch.setenv("FGL_SANDBOX_IMAGE", "sha256:" + "a" * 64)
    monkeypatch.setenv("FGL_RESEARCH_QUEUE_SECRET", "q" * 40)
    monkeypatch.setenv(
        "FGL_WORKER_IDENTITIES",
        json.dumps([{
            "identity": "verifier-1",
            "token": "worker-token-distinct-from-gateway-123456",
            "role": "verifier",
            "workspaces": ["workspace-1"],
        }]),
    )

    candidate = SimpleNamespace(candidate_id="cand_" + "a" * 32)
    spec = SimpleNamespace(
        definition=SimpleNamespace(budget=SimpleNamespace(wall_time_ms=600)))

    class FakeStore:
        atomic_finish = False
        separate_append = False

        async def prepare_candidate_numerical_fixture(self, *, workspace_id, candidate_id, seed):
            assert workspace_id == "workspace-1"
            assert candidate_id == candidate.candidate_id
            assert seed == 29
            return candidate, spec, None

        async def append_candidate_numerical_fixture(self, **_kwargs):
            self.separate_append = True
            raise AssertionError("Route must use atomic queue/result completion.")

    class FakeQueue:
        def __init__(self, _store, _key):
            self.ticket = SimpleNamespace(
                result=None,
                envelope=SimpleNamespace(job_id="00000000-0000-4000-8000-000000000029"),
            )

        async def admit_numerical_fixture(self, _worker, **kwargs):
            assert kwargs["reserved_ms"] == 600
            assert kwargs["payload"] == {
                "candidate_id": candidate.candidate_id,
                "seed": 29,
                "suite_version": "feature-kernel-fixture.v1",
            }
            return self.ticket

        async def claim(self, ticket, _worker, _payload, _image):
            assert ticket is self.ticket

        async def finish_numerical_fixture(
            self, ticket, *, research_store, idempotency_key, result
        ):
            assert ticket is self.ticket
            assert research_store is store
            assert idempotency_key == "numerical-fixture-api-test"
            assert result.candidate_id == candidate.candidate_id
            store.atomic_finish = True
            return SimpleNamespace(
                replayed=False,
                model_dump=lambda mode: {
                    "result_id": "num_" + "b" * 32,
                    "candidate_id": result.candidate_id,
                    "outcome": "passed_suite",
                    "fixture_scope": "synthetic_feature_kernel_fixture",
                    "performance_claim": False,
                },
            )

    store = FakeStore()
    monkeypatch.setattr(app.state, "research_store", store, raising=False)
    monkeypatch.setattr(main, "configured_image", lambda: "sha256:" + "a" * 64)
    monkeypatch.setattr(main, "configured_queue_key", lambda: b"q" * 40)
    monkeypatch.setattr(main, "ResearchQueue", FakeQueue)
    monkeypatch.setattr(
        main,
        "build_numerical_suite_input",
        lambda _candidate, _spec, *, seed, parent_candidate: {
            "candidate_id": candidate.candidate_id,
            "seed": seed,
            "suite_version": "feature-kernel-fixture.v1",
        },
    )
    monkeypatch.setattr(
        main,
        "run_candidate_numerical_fixture",
        lambda *_args, **_kwargs: {"outcome": "passed_suite"},
    )
    monkeypatch.setattr(
        main,
        "make_numerical_fixture_receipt",
        lambda result, **kwargs: SimpleNamespace(
            candidate_id=candidate.candidate_id,
            outcome=result["outcome"],
            workspace_id=kwargs["workspace_id"],
            actor_id=kwargs["actor_id"],
            run_id=kwargs["run_id"],
        ),
    )

    response = TestClient(app).post(
        "/v1/research/candidates/" + candidate.candidate_id + "/numerical-fixture",
        json={"seed": 29},
        headers=_headers(),
    )

    assert response.status_code == 201
    assert response.json()["fixture_scope"] == "synthetic_feature_kernel_fixture"
    assert response.json()["performance_claim"] is False
    assert store.atomic_finish
    assert not store.separate_append

# Pre-FGL-601 API semantics only; strict authentication is covered in test_service_auth.py.
pytestmark = pytest.mark.usefixtures("legacy_phase5_service_auth")
