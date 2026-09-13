from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from fastapi.testclient import TestClient

from app.evidence_store import ContractReviewReceipt
from app.main import app, require_evidence_store


@dataclass
class ReviewStore:
    replayed: bool = False
    failure: ValueError | None = None
    call: dict[str, object] | None = None

    async def append_contract_review(self, **kwargs):
        if self.failure is not None:
            raise self.failure
        self.call = kwargs
        review = kwargs["review"].model_copy(update={"review_id": str(uuid4())})
        return ContractReviewReceipt(review=review, replayed=self.replayed)


def _client(monkeypatch, store: ReviewStore) -> TestClient:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SERVICE_TOKEN", "review-secret")
    app.dependency_overrides[require_evidence_store] = lambda: store
    return TestClient(app)


def _headers(**extra: str) -> dict[str, str]:
    return {
        "authorization": "Bearer review-secret",
        "x-fgl-actor-id": "human-user-1",
        "x-fgl-actor-role": "researcher",
        "idempotency-key": "review-request-0001",
        **extra,
    }


def _body(**extra: object) -> dict[str, object]:
    return {
        "workspace_id": "workspace-1",
        "equation_uuid": str(uuid4()),
        "symbol_name": "x",
        "decision": "accepted",
        "reviewed_contract": {
            "name": "x",
            "category": "scalar",
            "shape": [],
            "domain": "real",
            "constraints": [],
        },
        "evidence": ["source-anchor:S1.E1"],
        **extra,
    }


def test_review_uses_service_actor_not_request_identity(monkeypatch) -> None:
    store = ReviewStore()
    client = _client(monkeypatch, store)
    try:
        response = client.post(
            "/v1/contract-reviews",
            headers=_headers(),
            json=_body(),
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["reviewer_id"] == "human-user-1"
    assert response.json()["replayed"] is False
    assert store.call is not None
    assert store.call["review"].reviewer_id == "human-user-1"


def test_review_rejects_client_approval_and_reviewer_identity(monkeypatch) -> None:
    client = _client(monkeypatch, ReviewStore())
    try:
        response = client.post(
            "/v1/contract-reviews",
            headers=_headers(),
            json=_body(approved=True, reviewer_id="model-pretending-to-be-human"),
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422


def test_review_requires_a_human_service_actor(monkeypatch) -> None:
    client = _client(monkeypatch, ReviewStore())
    headers = _headers()
    headers.pop("x-fgl-actor-id")
    try:
        response = client.post("/v1/contract-reviews", headers=headers, json=_body())
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 401


def test_accepted_review_requires_contract_values(monkeypatch) -> None:
    client = _client(monkeypatch, ReviewStore())
    body = _body()
    body["reviewed_contract"] = None
    try:
        response = client.post("/v1/contract-reviews", headers=_headers(), json=body)
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422


def test_review_storage_scope_failure_is_not_hidden(monkeypatch) -> None:
    client = _client(
        monkeypatch,
        ReviewStore(failure=ValueError("Reviewed equation and symbol must exist.")),
    )
    try:
        response = client.post(
            "/v1/contract-reviews",
            headers=_headers(),
            json=_body(),
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422
    assert "must exist" in response.json()["detail"]


def test_replay_returns_original_timestamp_not_retry_timestamp(
    monkeypatch,
) -> None:
    store = ReviewStore(replayed=True)
    client = _client(monkeypatch, store)
    try:
        response = client.post(
            "/v1/contract-reviews",
            headers=_headers(),
            json=_body(),
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    data = response.json()
    assert data["replayed"] is True
    assert "reviewed_at" in data


def test_idempotency_conflict_returns_422(monkeypatch) -> None:
    client = _client(
        monkeypatch,
        ReviewStore(
            failure=ValueError(
                "Contract review idempotency key conflicts"
                " with prior content.",
            ),
        ),
    )
    try:
        response = client.post(
            "/v1/contract-reviews",
            headers=_headers(),
            json=_body(),
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422
    assert "idempotency key conflicts" in response.json()["detail"]

