from fastapi.testclient import TestClient

from app.main import app


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
