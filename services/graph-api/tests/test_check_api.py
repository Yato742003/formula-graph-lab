from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from app.evidence_store import CheckResultReceipt
from app.main import app, require_evidence_store
from app.symbol_contracts import ContractReview, ReviewedContractValue


@dataclass
class CheckStore:
    replayed: bool = False
    failure: ValueError | None = None
    call: dict[str, object] | None = None
    cached: CheckResultReceipt | None = None
    source_latex: str = "1+1"
    contract_reviews: list[ContractReview] | None = None

    async def lookup_check_result(self, **kwargs):
        if self.cached and self.cached.result.request_hash != kwargs["request_hash"]:
            raise ValueError("Check result idempotency key conflicts with prior content.")
        return self.cached

    async def equation_source(self, **kwargs):
        if self.failure is not None:
            raise self.failure
        return {"latex": self.source_latex, "equation_id": "E1"}

    async def equation_contract_reviews(self, **kwargs):
        return self.contract_reviews or []

    async def append_check_result(self, **kwargs):
        if self.failure is not None:
            raise self.failure
        self.call = kwargs
        result = kwargs["result"].model_copy(update={
            "check_id": str(uuid4()),
            "workspace_id": kwargs["workspace_id"],
            "target_uuid": kwargs["target_uuid"],
        })
        self.cached = CheckResultReceipt(result=result, replayed=True)
        return CheckResultReceipt(result=result, replayed=self.replayed)


def _client(monkeypatch, store: CheckStore) -> TestClient:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SERVICE_TOKEN", "checker-secret")
    monkeypatch.setenv("FGL_ENABLE_RESEARCH_CHECKS", "true")
    monkeypatch.setenv("FGL_SANDBOX_IMAGE", "sha256:" + "a" * 64)
    monkeypatch.setenv("FGL_RESEARCH_QUEUE_SECRET", "q" * 40)
    class Queue:
        def __init__(self, *_args):
            pass

        async def admit(self, *_args, **_kwargs):
            return SimpleNamespace(envelope=SimpleNamespace(job_id="test-job"), result=None)

        async def claim(self, *_args):
            pass

        async def finish(self, *_args):
            pass

    monkeypatch.setattr("app.main.ResearchQueue", Queue)
    monkeypatch.setattr("app.main.run_sandbox", lambda *_args, **_kwargs: {
        "outcome": "supported", "witness": {"method": "sympy_simplify", "difference": "0"},
    })
    monkeypatch.setenv("FGL_WORKER_IDENTITIES", json.dumps([
        {"identity": "verifier-1", "token": "v" * 40, "role": "verifier",
         "workspaces": ["workspace-1"]},
        {"identity": "proposer-1", "token": "p" * 40, "role": "proposer",
         "workspaces": ["workspace-1"]},
    ]))
    app.dependency_overrides[require_evidence_store] = lambda: store
    return TestClient(app)


def _headers() -> dict[str, str]:
    return {
        "authorization": "Bearer " + "v" * 40,
        "idempotency-key": "symbolic-check-0001",
    }


def _body(**extra: object) -> dict[str, object]:
    return {
        "workspace_id": "workspace-1",
        "target_uuid": str(uuid4()),
        "formula_a": "1+1",
        "formula_b": "2",
        **extra,
    }


def test_symbolic_check_persists_worker_derived_result(monkeypatch) -> None:
    store = CheckStore()
    client = _client(monkeypatch, store)
    try:
        response = client.post("/v1/checks/symbolic", headers=_headers(), json=_body())
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["outcome"] == "supported"
    assert response.json()["vector"]["symbolic"] == "supported"
    assert store.call is not None


def test_symbolic_check_uses_only_server_resolved_accepted_contracts(monkeypatch) -> None:
    target_uuid = str(uuid4())
    review = ContractReview(
        review_id="stored-review-1",
        symbol_name="x",
        reviewer_id="human-reviewer",
        reviewer_role="reviewer",
        decision="accepted",
        scope=target_uuid,
        reviewed_contract=ReviewedContractValue(
            name="x", category="scalar", shape=(), scope="E1",
        ),
        reviewed_at=datetime.now(UTC),
    )
    store = CheckStore(source_latex="x+x", contract_reviews=[review])
    client = _client(monkeypatch, store)
    try:
        response = client.post(
            "/v1/checks/symbolic",
            headers=_headers(),
            json=_body(target_uuid=target_uuid, formula_a="x+x", formula_b="2*x"),
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["outcome"] == "supported"
    assert response.json()["schema_version"] == "check-result.v2"
    assert response.json()["contract_review_ids"] == [review.review_id]
    assert response.json()["contract_review_hash"]
    assert store.call is not None
    assert store.call["result"].contract_review_ids == (review.review_id,)


def test_contract_review_change_conflicts_with_same_check_idempotency_key(monkeypatch) -> None:
    target_uuid = str(uuid4())
    review = ContractReview(
        review_id="stored-review-1",
        symbol_name="x",
        reviewer_id="human-reviewer",
        reviewer_role="reviewer",
        decision="accepted",
        scope=target_uuid,
        reviewed_contract=ReviewedContractValue(
            name="x", category="scalar", shape=(), scope="E1",
        ),
        reviewed_at=datetime.now(UTC),
    )
    store = CheckStore(source_latex="x+x", contract_reviews=[review])
    client = _client(monkeypatch, store)
    body = _body(target_uuid=target_uuid, formula_a="x+x", formula_b="2*x")
    try:
        first = client.post("/v1/checks/symbolic", headers=_headers(), json=body)
        store.contract_reviews = [review.model_copy(update={
            "review_id": "stored-review-2",
            "decision": "rejected",
            "reviewed_contract": None,
            "reviewed_at": review.reviewed_at + timedelta(days=1),
        })]
        changed = client.post("/v1/checks/symbolic", headers=_headers(), json=body)
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 200
    assert changed.status_code == 422
    assert "idempotency key conflicts" in changed.json()["detail"]


def test_client_cannot_supply_outcome_or_fitness(monkeypatch) -> None:
    client = _client(monkeypatch, CheckStore())
    try:
        response = client.post(
            "/v1/checks/symbolic",
            headers=_headers(),
            json=_body(outcome="supported", fitness=1.0),
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422


def test_target_scope_failure_is_visible(monkeypatch) -> None:
    client = _client(
        monkeypatch,
        CheckStore(failure=ValueError("Check target must exist in this workspace.")),
    )
    try:
        response = client.post("/v1/checks/symbolic", headers=_headers(), json=_body())
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422
    assert "must exist" in response.json()["detail"]


def test_proposer_cannot_run_checker_even_with_forged_role_header(monkeypatch):
    store = CheckStore()
    client = _client(monkeypatch, store)
    headers = {**_headers(), "authorization": "Bearer " + "p" * 40,
               "x-fgl-actor-role": "verifier"}
    try:
        response = client.post("/v1/checks/symbolic", headers=headers, json=_body())
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403
    assert store.call is None


def test_verifier_cannot_access_ungranted_workspace(monkeypatch):
    store = CheckStore()
    client = _client(monkeypatch, store)
    try:
        response = client.post("/v1/checks/symbolic", headers=_headers(),
                               json=_body(workspace_id="workspace-2"))
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403
    assert store.call is None


def test_unrelated_formula_cannot_get_a_check_attached_to_target(monkeypatch):
    store = CheckStore()
    client = _client(monkeypatch, store)
    try:
        response = client.post("/v1/checks/symbolic", headers=_headers(),
                               json=_body(formula_a="2", formula_b="2"))
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422
    assert store.call is None


def test_research_execution_defaults_to_disabled(monkeypatch):
    store = CheckStore()
    client = _client(monkeypatch, store)
    monkeypatch.delenv("FGL_ENABLE_RESEARCH_CHECKS")
    try:
        response = client.post("/v1/checks/symbolic", headers=_headers(), json=_body())
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 503
    assert store.call is None


def test_shared_gateway_token_cannot_be_configured_as_worker(monkeypatch):
    store = CheckStore()
    client = _client(monkeypatch, store)
    monkeypatch.setenv("SERVICE_TOKEN", "v" * 40)
    try:
        response = client.post("/v1/checks/symbolic", headers=_headers(), json=_body())
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 503
    assert store.call is None


def test_retry_returns_original_result_without_rerunning_worker(monkeypatch):
    store = CheckStore()
    client = _client(monkeypatch, store)
    body = _body()
    try:
        first = client.post("/v1/checks/symbolic", headers=_headers(), json=body)
        assert first.status_code == 200

        def forbidden(*args, **kwargs):
            raise AssertionError("Replay must not execute the checker again")

        monkeypatch.setattr("app.main.run_symbolic_check", forbidden)
        second = client.post("/v1/checks/symbolic", headers=_headers(), json=body)
        assert second.status_code == 200
        assert second.json() == {**first.json(), "replayed": True}
        conflict = client.post("/v1/checks/symbolic", headers=_headers(),
                               json={**body, "timeout_ms": 3_000})
        assert conflict.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_retry_with_runtime_variance_replays_original_receipt(monkeypatch):
    store = CheckStore()
    client = _client(monkeypatch, store)
    body = _body()
    try:
        first = client.post("/v1/checks/symbolic", headers=_headers(), json=body)
        assert first.status_code == 200
        second = client.post("/v1/checks/symbolic", headers=_headers(), json=body)
        assert second.status_code == 200
        assert second.json()["created_at"] == first.json()["created_at"]
        assert second.json()["duration_ms"] == first.json()["duration_ms"]
        assert second.json()["replayed"] is True
    finally:
        app.dependency_overrides.clear()
