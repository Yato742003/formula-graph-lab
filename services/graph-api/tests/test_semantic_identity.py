"""FGL-H3 typed canonicalization and scoped identity regressions."""

from __future__ import annotations

from app.formula_ast import AstNode, ast_to_sympy, canonicalize, parse_formula
from app.semantic_identity import build_semantic_identity
from app.symbol_contracts import ReviewedContractValue


def _contract(
    name: str,
    *,
    category: str = "scalar",
    shape: tuple[int | str, ...] = (),
    domain: str = "real",
    scope: str = "equation:S1.E1",
) -> ReviewedContractValue:
    return ReviewedContractValue.model_validate(
        {
            "name": name,
            "category": category,
            "shape": shape,
            "domain": domain,
            "scope": scope,
        }
    )


def _identity(source: str, contracts: list[ReviewedContractValue]):
    return build_semantic_identity(
        parse_formula(source),
        contracts,
        scope_id="equation:S1.E1",
        operator_versions={"multiply": "core.multiply.v1", "add": "core.add.v1"},
    )


def test_unknown_product_order_is_not_collapsed() -> None:
    left = _identity("a*b", [])
    right = _identity("b*a", [])
    assert left.semantic_hash != right.semantic_hash
    assert left.complete_contracts is False
    assert left.unresolved_symbols == ("a", "b")


def test_reviewed_matrix_product_order_is_not_collapsed() -> None:
    contracts = [
        _contract("a", category="matrix", shape=("m", "n")),
        _contract("b", category="matrix", shape=("n", "p")),
    ]
    assert _identity("a*b", contracts).semantic_hash != _identity(
        "b*a", contracts
    ).semantic_hash


def test_reviewed_scalar_product_deduplicates() -> None:
    contracts = [_contract("a"), _contract("b")]
    left = _identity("a*b", contracts)
    right = _identity("b*a", contracts)
    assert left.semantic_hash == right.semantic_hash
    assert left.complete_contracts is True


def test_contract_domain_changes_semantic_hash() -> None:
    real = _identity("x", [_contract("x", domain="real")])
    positive = _identity("x", [_contract("x", domain="positive")])
    assert real.semantic_hash != positive.semantic_hash


def test_operator_version_changes_semantic_hash() -> None:
    parsed = parse_formula("x")
    contract = [_contract("x")]
    first = build_semantic_identity(
        parsed,
        contract,
        scope_id="equation:S1.E1",
        operator_versions={"identity": "v1"},
    )
    second = build_semantic_identity(
        parsed,
        contract,
        scope_id="equation:S1.E1",
        operator_versions={"identity": "v2"},
    )
    assert first.semantic_hash != second.semantic_hash


def test_scope_changes_symbol_ids_and_semantic_hash() -> None:
    parsed = parse_formula("x")
    first = build_semantic_identity(parsed, [], scope_id="equation:S1.E1")
    second = build_semantic_identity(parsed, [], scope_id="equation:S2.E1")
    assert first.semantic_hash != second.semantic_hash
    assert first.scoped_symbols[0].symbol_id != second.scoped_symbols[0].symbol_id


def test_nested_binders_have_distinct_lexical_ids() -> None:
    identity = _identity(
        r"\sum_{i=1}^{n} \sum_{i=1}^{m} x_i",
        [_contract("x")],
    )
    bound_ids = {
        occurrence.symbol_id
        for occurrence in identity.scoped_symbols
        if occurrence.binding == "bound"
    }
    assert len(bound_ids) == 2
    assert all(":bound:" in symbol_id for symbol_id in bound_ids)


def test_alpha_renaming_is_stable_across_sibling_scopes() -> None:
    left = parse_formula(
        r"\sum_{i=1}^{n} x_i + \sum_{j=1}^{m} y_j"
    ).syntax_hash
    right = parse_formula(
        r"\sum_{k=1}^{n} x_k + \sum_{q=1}^{m} y_q"
    ).syntax_hash
    assert left == right


def test_alpha_renaming_avoids_free_name_capture() -> None:
    free_name = "__fgl_bound_0"
    lower = AstNode(
        "equals",
        children=(AstNode("symbol", "i"), AstNode("number", "1")),
    )
    body = AstNode(
        "add",
        children=(AstNode("symbol", free_name), AstNode("symbol", "i")),
    )
    root = AstNode(
        "sum",
        children=(lower, AstNode("symbol", "n"), body),
        attributes=(("binder", "i"),),
    )
    normalized = canonicalize(root)
    assert normalized.attribute("binder") == "__fgl_bound_0_1"
    assert normalized.children[-1].children[0].value == free_name
    assert normalized.children[-1].children[1].value == "__fgl_bound_0_1"


def test_duplicate_or_cross_scope_reviewed_contract_is_rejected() -> None:
    parsed = parse_formula("x")
    try:
        build_semantic_identity(
            parsed,
            [_contract("x"), _contract("x")],
            scope_id="equation:S1.E1",
        )
    except ValueError as exc:
        assert "Duplicate" in str(exc)
    else:
        raise AssertionError("duplicate contract should be rejected")

    try:
        build_semantic_identity(
            parsed,
            [_contract("x", scope="equation:other")],
            scope_id="equation:S1.E1",
        )
    except ValueError as exc:
        assert "scope mismatch" in str(exc)
    else:
        raise AssertionError("cross-scope contract should be rejected")


def test_sum_bounds_use_outer_scope_and_body_uses_local_scope():
    parsed = parse_formula(r"\sum_{i=i}^{i} x_i")
    assert "i" in parsed.free_variables
    normalized = canonicalize(parsed.root)
    assert normalized.children[0].children[0].value == "__fgl_bound_0"
    assert normalized.children[0].children[1].value == "i"
    assert normalized.children[1].value == "i"
    assert normalized.children[-1].children[1].value == "__fgl_bound_0"
    identity = _identity(r"\sum_{i=i}^{i} x_i", [])
    free_i = [o for o in identity.scoped_symbols if o.printed_name == "i"]
    assert len(free_i) == 2
    assert all(o.binding == "free" for o in free_i)
    assert "i" in identity.unresolved_symbols


def test_nested_shadowing_resolves_bounds_to_outer_index():
    left = parse_formula(r"\sum_{i=1}^{n}\sum_{i=i}^{m}x_i")
    right = parse_formula(r"\sum_{j=1}^{n}\sum_{k=j}^{m}x_k")
    assert left.syntax_hash == right.syntax_hash
    normalized = canonicalize(left.root)
    nested = normalized.children[-1]
    assert nested.children[0].children[1].value == normalized.attribute("binder")
    assert nested.children[-1].children[1].value == nested.attribute("binder")
    assert canonicalize(normalized) == normalized


def test_changed_free_upper_bound_is_not_alpha_equivalent():
    left = parse_formula(r"\sum_{i=1}^{i}x_i")
    right = parse_formula(r"\sum_{j=1}^{j}x_j")
    assert left.syntax_hash != right.syntax_hash


def test_free_canonical_looking_name_is_not_a_scalar_proof():
    free = AstNode("symbol", "__fgl_bound_0")
    assert ast_to_sympy(free).is_commutative is False


def test_scalar_contracts_do_not_authorize_unknown_operator_semantics():
    contracts = [_contract("a"), _contract("b")]
    for versions in ({}, {"multiply": "unreviewed.multiply.v999"}):
        left = build_semantic_identity(
            parse_formula("a*b"), contracts, scope_id="equation:S1.E1",
            operator_versions=versions,
        )
        right = build_semantic_identity(
            parse_formula("b*a"), contracts, scope_id="equation:S1.E1",
            operator_versions=versions,
        )
        assert left.semantic_hash != right.semantic_hash


def test_single_symbol_binder_declaration_is_bound_and_alpha_equivalent():
    p1 = parse_formula(r"\sum_{i} x_i")
    p2 = parse_formula(r"\sum_{j} x_j")
    assert p1.free_variables == ("x",)
    assert p1.bound_variables == ("i",)
    assert p2.free_variables == ("x",)
    assert p2.bound_variables == ("j",)
    assert p1.syntax_hash == p2.syntax_hash

    id1 = build_semantic_identity(p1, [], scope_id="equation:S1.E1")
    id2 = build_semantic_identity(p2, [], scope_id="equation:S1.E1")
    assert id1.semantic_hash == id2.semantic_hash
    assert id1.unresolved_symbols == ("x",)
    assert any(
        s.binding == "bound" and s.printed_name == "__fgl_bound_0" for s in id1.scoped_symbols
    )


def test_production_analyze_equation_connects_reviewed_contracts():
    from app.evidence import analyze_equation
    from app.models import ExtractedEquation

    eq1 = ExtractedEquation(
        equation_id="E1",
        latex="a*b",
        section_id="S1",
        anchor="S1.E1",
        extraction_method="tex_annotation",
        confidence=0.9,
    )
    eq2 = ExtractedEquation(
        equation_id="E2",
        latex="b*a",
        section_id="S1",
        anchor="S1.E2",
        extraction_method="tex_annotation",
        confidence=0.9,
    )

    # Without reviewed contracts: unresolved symbols, no commutativity
    analysis1, _, _ = analyze_equation(eq1)
    analysis2, _, _ = analyze_equation(eq2)
    assert analysis1["semantic_identity"]["complete_contracts"] is False
    assert set(analysis1["semantic_identity"]["unresolved_symbols"]) == {"a", "b"}
    hash1 = analysis1["semantic_identity"]["semantic_hash"]
    hash2 = analysis2["semantic_identity"]["semantic_hash"]
    assert hash1 != hash2

    # With reviewed contracts: complete contracts, canonical commutativity under core operators
    rev_a = _contract("a", scope="S1")
    rev_b = _contract("b", scope="S1")
    reviewed_analysis1, _, _ = analyze_equation(eq1, reviewed_contracts=[rev_a, rev_b])
    reviewed_analysis2, _, _ = analyze_equation(eq2, reviewed_contracts=[rev_a, rev_b])
    assert reviewed_analysis1["semantic_identity"]["complete_contracts"] is True
    assert reviewed_analysis1["semantic_identity"]["unresolved_symbols"] == []
    rev_hash1 = reviewed_analysis1["semantic_identity"]["semantic_hash"]
    rev_hash2 = reviewed_analysis2["semantic_identity"]["semantic_hash"]
    assert rev_hash1 == rev_hash2

