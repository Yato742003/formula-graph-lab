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
    assert body["syntax_hash"] == body["canonical_hash"]
    assert body["syntax_hash_version"] == "syntax-hash.v8"
    assert body["canonicalizer_version"] == "alpha-canonicalizer.v3"
    assert body["result_shape"] is None
    assert body["requires_review"] is True
    assert body["extraction_assessment"] == {
        "confidence": 0.4,
        "source": "request",
    }
    assert body["shape_errors"] == []
    assert body["domain_assessment"]["status"] == "unresolved"
    obligation = body["domain_assessment"]["obligations"][0]
    assert obligation["predicate"] == "nonzero"
    assert obligation["expression"]["value"] == "b"
    assert obligation["predicate_evaluation"]["outcome"] == "unknown"
    assert obligation["predicate_evaluation"]["scope"] == "unresolved_expression"
    contracts = {contract["name"]: contract for contract in body["contracts"]}
    assert contracts["b"]["constraints"] == []
    assert contracts["x"]["scope"] == "section-2"


def test_formula_parse_exposes_constant_domain_failure_without_promoting_it(monkeypatch) -> None:
    response = _client(monkeypatch).post(
        "/v1/formulas/parse",
        headers=_headers(),
        json={"latex": r"\frac{1}{0}"},
    )

    assert response.status_code == 200
    assessment = response.json()["domain_assessment"]
    assert assessment["status"] == "unresolved"
    assert assessment["obligations"][0]["status"] == "unresolved"
    assert assessment["obligations"][0]["predicate_evaluation"]["outcome"] == "false"


def test_formula_parse_keeps_log_base_one_unresolved(monkeypatch) -> None:
    response = _client(monkeypatch).post(
        "/v1/formulas/parse",
        headers=_headers(),
        json={"latex": r"\log_{1}{8}"},
    )

    assert response.status_code == 200
    assessment = response.json()["domain_assessment"]
    assert assessment["status"] == "unresolved"
    base_obligation = next(
        item
        for item in assessment["obligations"]
        if item["location"].endswith("log_base_not_one")
    )
    assert base_obligation["predicate"] == "not_one"
    assert base_obligation["predicate_evaluation"]["outcome"] == "false"
    assert base_obligation["predicate_evaluation"]["evaluator_version"] == (
        "domain-predicate-evaluator.v2"
    )


def test_high_confidence_never_returns_human_confirmation(monkeypatch) -> None:
    client = _client(monkeypatch)
    response = client.post(
        "/v1/formulas/parse",
        headers=_headers(),
        json={
            "latex": r"\mathbf{W}+\mathbf{V}",
            "extraction_confidence": 1.0,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["requires_review"] is False
    assert all("confirmed" not in contract for contract in body["contracts"])
    assert all(contract["inference_confidence"] == 1 for contract in body["contracts"])


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
        json={"formula_a": r"\sum_{i=1}^{n}x_i", "formula_b": r"\sum_{j=1}^{n}x_j"},
    )
    assert response.status_code == 200
    assert response.json()["structurally_equal"] is True
    assert response.json()["syntax_hash_version"] == "syntax-hash.v8"


def test_formula_compare_does_not_assume_unknown_products_commute(monkeypatch) -> None:
    client = _client(monkeypatch)
    response = client.post(
        "/v1/formulas/compare",
        headers=_headers(),
        json={"formula_a": "a*b", "formula_b": "b*a"},
    )
    assert response.status_code == 200
    assert response.json()["structurally_equal"] is False
    assert response.json()["sympy_equivalent"] is None
    assert response.json()["verification"]["symbolic"] == "unknown"


def test_compare_does_not_erase_domain_holes(monkeypatch):
    response = _client(monkeypatch).post(
        "/v1/formulas/compare", headers=_headers(),
        json={"formula_a": "x/x", "formula_b": "1"},
    )
    assert response.status_code == 200
    assert response.json()["sympy_equivalent"] is None
    assert response.json()["verification"]["domain"] == "unresolved"


def test_formula_parse_rejects_inline_confirmation_or_approval(monkeypatch) -> None:
    client = _client(monkeypatch)
    response = client.post(
        "/v1/formulas/parse",
        headers=_headers(),
        json={
            "latex": "x",
            "confirmations": [{"name": "x", "approved": True}],
        },
    )
    assert response.status_code == 422


def test_formula_parse_rejects_top_level_approved_field(monkeypatch) -> None:
    client = _client(monkeypatch)
    response = client.post(
        "/v1/formulas/parse",
        headers=_headers(),
        json={
            "latex": "x",
            "approved": True,
        },
    )
    assert response.status_code == 422
