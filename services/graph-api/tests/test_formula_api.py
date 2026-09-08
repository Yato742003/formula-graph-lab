from fastapi.testclient import TestClient

from app.main import app


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SERVICE_TOKEN", "formula-secret")
    return TestClient(app)


def _headers() -> dict[str, str]:
    return {"authorization": "Bearer formula-secret"}


def test_formula_parse_requires_service_authentication(monkeypatch) -> None:
    client = _client(monkeypatch)
    response = client.post("/v1/formulas/parse", json={"latex": "x+y"})
    assert response.status_code == 401


def test_formula_parse_returns_contracts_and_validation(monkeypatch) -> None:
    client = _client(monkeypatch)
    response = client.post(
        "/v1/formulas/parse",
        headers=_headers(),
        json={
            "latex": r"\frac{x}{b}",
            "section_id": "section-2",
            "extraction_confidence": 0.4,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["canonical_hash"]) == 64
    assert body["result_shape"] == []
    assert body["requires_confirmation"] is True
    assert body["shape_errors"] == []
    assert body["domain_errors"] == []
    contracts = {contract["name"]: contract for contract in body["contracts"]}
    assert contracts["b"]["constraints"] == ["!= 0"]
    assert contracts["x"]["scope"] == "section-2"


def test_human_confirmations_clear_low_confidence_gate(monkeypatch) -> None:
    client = _client(monkeypatch)
    response = client.post(
        "/v1/formulas/parse",
        headers=_headers(),
        json={
            "latex": "x+y",
            "extraction_confidence": 0.2,
            "confirmations": [
                {"name": "x", "category": "scalar", "shape": []},
                {"name": "y", "category": "scalar", "shape": []},
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["requires_confirmation"] is False
    assert all(contract["confirmed"] for contract in body["contracts"])
    assert all(contract["confidence"] == 1 for contract in body["contracts"])


def test_formula_parse_returns_structured_parser_error(monkeypatch) -> None:
    client = _client(monkeypatch)
    response = client.post(
        "/v1/formulas/parse",
        headers=_headers(),
        json={"latex": r"\frac{x}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "EXPECTED_GROUP"


def test_formula_compare_uses_canonical_structure(monkeypatch) -> None:
    client = _client(monkeypatch)
    response = client.post(
        "/v1/formulas/compare",
        headers=_headers(),
        json={"formula_a": "a+b", "formula_b": "b+a"},
    )
    assert response.status_code == 200
    assert response.json()["structurally_equal"] is True


def test_formula_confirmation_rejects_unknown_symbol(monkeypatch) -> None:
    client = _client(monkeypatch)
    response = client.post(
        "/v1/formulas/parse",
        headers=_headers(),
        json={
            "latex": "x",
            "confirmations": [
                {"name": "fabricated", "category": "scalar", "shape": []}
            ],
        },
    )
    assert response.status_code == 422
    assert "unknown symbol" in response.json()["detail"]


def test_formula_confirmation_cannot_escape_section_scope(monkeypatch) -> None:
    client = _client(monkeypatch)
    response = client.post(
        "/v1/formulas/parse",
        headers=_headers(),
        json={
            "latex": "x",
            "section_id": "section-a",
            "confirmations": [
                {
                    "name": "x",
                    "category": "scalar",
                    "shape": [],
                    "scope": "section-b",
                }
            ],
        },
    )
    assert response.status_code == 422
    assert "scope" in response.json()["detail"]
