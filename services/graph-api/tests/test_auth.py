from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.evidence_store import ImportReceipt
from app.main import app, require_evidence_store


def clear_graph_configuration(monkeypatch) -> None:
    for name in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD"):
        monkeypatch.delenv(name, raising=False)


def test_health_is_public(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("SERVICE_TOKEN", raising=False)

    response = TestClient(app).get("/health")

    assert response.status_code == 200


def test_production_requires_service_token_configuration(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("SERVICE_TOKEN", raising=False)

    response = TestClient(app).post(
        "/v1/extractions/preview",
        json={"url": "https://arxiv.org/html/1706.03762"},
    )

    assert response.status_code == 503


def test_rejects_invalid_service_token_before_fetch(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SERVICE_TOKEN", "correct-secret")

    response = TestClient(app).post(
        "/v1/extractions/preview",
        headers={"authorization": "Bearer wrong-secret"},
        json={"url": "https://arxiv.org/html/1706.03762"},
    )

    assert response.status_code == 401


def test_wrong_source_identity_returns_validation_error(monkeypatch) -> None:
    import app.main as main

    async def wrong_paper(_url):
        return (
            "<html><div id='watermark-tr'>arXiv:2402.00001v1</div></html>",
            "https://arxiv.org/html/2402.08954v1",
        )

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("SERVICE_TOKEN", raising=False)
    monkeypatch.setattr(main, "fetch_paper_html", wrong_paper)
    response = TestClient(app).post(
        "/v1/extractions/preview", json={"url": "https://arxiv.org/html/2402.08954v1"},
    )
    assert response.status_code == 422
    assert "watermark" in response.json()["detail"]


def test_exact_import_requires_configured_storage(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("SERVICE_TOKEN", raising=False)
    clear_graph_configuration(monkeypatch)
    with TestClient(app) as client:
        response = client.post(
            "/v1/imports",
            json={"url": "https://arxiv.org/html/1706.03762", "workspace_id": "lab"},
        )
    assert response.status_code == 503
    assert response.json()["detail"] == "Exact evidence storage is not configured."


def test_exact_import_returns_source_and_idempotency_receipt(monkeypatch) -> None:
    import app.main as main

    class FakeStore:
        def __init__(self):
            self.graph = None

        async def ingest(self, graph):
            self.graph = graph
            return ImportReceipt(
                import_uuid=graph.import_uuid, node_count=len(graph.nodes),
                edge_count=len(graph.edges), episode_count=len(graph.episodes),
                replayed=False,
            )

    async def paper(_url):
        return (
            "<html><div id='watermark-tr'>arXiv:2402.08954v1 [cs.AI] "
            "14 Feb 2024</div><section id='S1'><h2>1 Method</h2>"
            "<math id='S1.E1' display='block' alttext='x=1'></math></section></html>",
            "https://arxiv.org/html/2402.08954v1",
        )

    store = FakeStore()
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("SERVICE_TOKEN", raising=False)
    clear_graph_configuration(monkeypatch)
    monkeypatch.setattr(main, "fetch_paper_html", paper)
    app.dependency_overrides[require_evidence_store] = lambda: store
    try:
        with TestClient(app) as client:
            response = client.post(
                "/v1/imports",
                json={
                    "url": "https://arxiv.org/abs/2402.08954",
                    "workspace_id": "user/site-scoped-id",
                },
            )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    body = response.json()
    assert body["paper"]["source_url"] == "https://arxiv.org/html/2402.08954v1"
    assert body["paper"]["equations"][0]["anchor"] == "S1.E1"
    assert body["receipt"]["replayed"] is False
    assert store.graph.paper_id == "2402.08954"


def test_exact_import_rejects_blank_workspace_before_fetch(monkeypatch) -> None:
    import app.main as main

    fetch = AsyncMock()
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("SERVICE_TOKEN", raising=False)
    clear_graph_configuration(monkeypatch)
    monkeypatch.setattr(main, "fetch_paper_html", fetch)
    app.dependency_overrides[require_evidence_store] = lambda: object()
    try:
        with TestClient(app) as client:
            response = client.post(
                "/v1/imports",
                json={"url": "https://arxiv.org/html/2402.08954", "workspace_id": "   "},
            )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422
    fetch.assert_not_awaited()
