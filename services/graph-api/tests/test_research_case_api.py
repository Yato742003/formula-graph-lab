"""HTTP contract tests for the gated, persisted CPU research case."""

from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import main
from app.main import app
from app.research_case import (
    ResearchCaseReceiptResponse,
    make_implementation_binding,
    run_registered_research_case,
)
from app.research_case_worker import evaluate
from app.worker_auth import WorkerPrincipal
from tests.test_research_case import IMAGE, NOW, RUN_ID, _candidates


def _headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer gateway-secret-for-research-case-api",
        "X-FGL-Actor-ID": "researcher-1",
        "X-FGL-Actor-Role": "researcher",
        "X-FGL-Workspace-ID": "ws-test",
        "X-Idempotency-Key": "research-case-api-test",
    }


def _configure(monkeypatch) -> None:
    monkeypatch.setenv("SERVICE_TOKEN", "gateway-secret-for-research-case-api")
    monkeypatch.setenv("FGL_ENABLE_RESEARCH_CASE", "true")
    monkeypatch.setenv("FGL_SANDBOX_IMAGE", IMAGE)
    monkeypatch.setenv("FGL_RESEARCH_QUEUE_SECRET", "q" * 40)
    monkeypatch.setenv(
        "FGL_WORKER_IDENTITIES",
        json.dumps([{
            "identity": "experiment-worker",
            "token": "worker-token-distinct-from-gateway-123456",
            "role": "experiment",
            "workspaces": ["ws-test"],
        }]),
    )


def test_research_case_endpoint_is_disabled_by_default(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setenv("FGL_ENABLE_RESEARCH_CASE", "false")

    response = TestClient(app).post(
        "/v1/research/candidates/cand_" + "a" * 32 + "/research-case",
        json={},
        headers=_headers(),
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Research-case execution is disabled."


def test_research_case_endpoint_finishes_atomically(monkeypatch):
    _configure(monkeypatch)
    spec, parent, candidate = _candidates()
    binding = make_implementation_binding(
        candidate, spec, parent_candidate=parent, execution_image=IMAGE, now=NOW
    )
    result = run_registered_research_case(
        binding,
        candidate,
        spec,
        parent_candidate=parent,
        actor_id="experiment-worker",
        run_id=RUN_ID,
        worker_runner=lambda payload, **_kwargs: evaluate(payload),
        now=NOW,
        evaluation_role="search",
    )

    class FakeStore:
        async def prepare_registered_research_case(self, *, workspace_id, candidate_id):
            assert workspace_id == "ws-test"
            assert candidate_id == candidate.candidate_id
            return candidate, spec, parent

    class FakeQueue:
        def __init__(self, _store, _key):
            self.ticket = SimpleNamespace(
                result=None,
                envelope=SimpleNamespace(job_id=RUN_ID),
            )

        async def research_case_retry(self, *_args):
            return None, None

        async def admit_research_case(self, worker, **kwargs):
            assert worker.identity == "experiment-worker"
            assert kwargs["binding"] == binding
            assert kwargs["reserved_ms"] == 18_000
            assert kwargs["evaluation_role"] == "search"
            return self.ticket

        async def claim(self, ticket, worker, payload, image):
            assert ticket is self.ticket
            assert worker.identity == "experiment-worker"
            assert payload["binding_id"] == binding.binding_id
            assert image == IMAGE

        async def finish_research_case(
            self, ticket, *, research_store, idempotency_key, binding, result
        ):
            assert ticket is self.ticket
            assert research_store is store
            assert idempotency_key == "research-case-api-test"
            assert binding == result_binding
            assert result.result_id == result_receipt.result_id
            return ResearchCaseReceiptResponse(
                binding=binding,
                result=result,
                replayed=False,
            )

    store = FakeStore()
    result_binding = binding
    result_receipt = result
    monkeypatch.setattr(app.state, "research_store", store, raising=False)
    monkeypatch.setattr(main, "configured_image", lambda: IMAGE)
    monkeypatch.setattr(main, "configured_queue_key", lambda: b"q" * 40)
    monkeypatch.setattr(main, "configured_worker_principal", lambda *_args: WorkerPrincipal(
        "experiment-worker", "experiment", frozenset({"ws-test"})
    ))
    monkeypatch.setattr(main, "ResearchQueue", FakeQueue)
    monkeypatch.setattr(main, "make_implementation_binding", lambda *args, **kwargs: binding)
    monkeypatch.setattr(main, "run_registered_research_case", lambda *args, **kwargs: result)

    response = TestClient(app).post(
        "/v1/research/candidates/" + candidate.candidate_id + "/research-case",
        json={},
        headers=_headers(),
    )

    assert response.status_code == 201
    assert response.json()["binding"]["binding_id"] == binding.binding_id
    assert response.json()["result"]["result_id"] == result.result_id
    assert response.json()["replayed"] is False


def test_research_case_endpoint_marks_idempotent_replay(monkeypatch):
    _configure(monkeypatch)
    spec, parent, candidate = _candidates()
    binding = make_implementation_binding(
        candidate, spec, parent_candidate=parent, execution_image=IMAGE, now=NOW
    )
    result = run_registered_research_case(
        binding,
        candidate,
        spec,
        parent_candidate=parent,
        actor_id="experiment-worker",
        run_id=RUN_ID,
        worker_runner=lambda payload, **_kwargs: evaluate(payload),
        now=NOW,
        evaluation_role="search",
    )
    saved = ResearchCaseReceiptResponse(binding=binding, result=result, replayed=False)

    class FakeStore:
        async def prepare_registered_research_case(self, **_kwargs):
            return candidate, spec, parent

    class FakeQueue:
        def __init__(self, _store, _key):
            self.ticket = SimpleNamespace(
                result=saved.model_dump_json(),
                envelope=SimpleNamespace(job_id=RUN_ID),
            )

        async def research_case_retry(self, *_args):
            return None, saved.model_copy(update={"replayed": True})

        async def admit_research_case(self, *_args, **_kwargs):
            return self.ticket

    monkeypatch.setattr(app.state, "research_store", FakeStore(), raising=False)
    monkeypatch.setattr(main, "configured_image", lambda: IMAGE)
    monkeypatch.setattr(main, "configured_queue_key", lambda: b"q" * 40)
    monkeypatch.setattr(main, "configured_worker_principal", lambda *_args: WorkerPrincipal(
        "experiment-worker", "experiment", frozenset({"ws-test"})
    ))
    monkeypatch.setattr(main, "ResearchQueue", FakeQueue)

    response = TestClient(app).post(
        "/v1/research/candidates/" + candidate.candidate_id + "/research-case",
        json={},
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.json()["replayed"] is True
