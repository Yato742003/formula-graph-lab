"""FGL-403: Symbol contracts — shape, domain, shadowing, and confidence tests."""

from __future__ import annotations

from app.formula_ast import parse_formula
from app.symbol_contracts import (
    CONFIDENCE_THRESHOLD,
    SymbolContract,
    apply_contract_confirmations,
    check_denominator_domain,
    check_shape_compatibility,
    detect_symbol_shadowing,
    infer_contracts,
    infer_expression_shape,
)

# ───────────────────────────────────────────────────────────────────
# Contract inference
# ───────────────────────────────────────────────────────────────────


class TestInferContracts:
    def _contract(self, contracts: list[SymbolContract], name: str) -> SymbolContract | None:
        return next((c for c in contracts if c.name == name), None)

    def test_scalar_shape(self):
        f = parse_formula("x + y")
        contracts = infer_contracts(f)
        c = self._contract(contracts, "x")
        assert c is not None
        assert c.shape == ()
        assert c.category == "scalar"

    def test_vector_shape(self):
        f = parse_formula("x_i")
        contracts = infer_contracts(f)
        c = self._contract(contracts, "x")
        assert c is not None
        assert c.shape == ("?",)
        assert c.category == "vector"

    def test_matrix_shape(self):
        f = parse_formula(r"\mathbf{W}")
        contracts = infer_contracts(f)
        c = self._contract(contracts, "W")
        assert c is not None
        assert c.category == "matrix"
        assert c.shape == ("?", "?")

    def test_tensor_shape(self):
        f = parse_formula(r"\mathcal{T}")
        contracts = infer_contracts(f)
        c = self._contract(contracts, "T")
        assert c is not None
        assert c.category == "tensor"
        assert c.shape == ("?", "?", "?")

    def test_function_shape_is_none(self):
        f = parse_formula(r"\sin(x)")
        contracts = infer_contracts(f)
        c = self._contract(contracts, "sin")
        assert c is not None
        assert c.shape is None

    def test_index_shape(self):
        f = parse_formula(r"\sum_{i=1}^{n} x_i")
        contracts = infer_contracts(f)
        c = self._contract(contracts, "i")
        assert c is not None
        assert c.shape == ()
        assert c.category == "index"

    def test_scope_passed_through(self):
        f = parse_formula("x + y")
        contracts = infer_contracts(f, section_id="sec-3")
        for c in contracts:
            assert c.scope == "sec-3"

    def test_denominator_constraint(self):
        f = parse_formula(r"\frac{a}{b}")
        contracts = infer_contracts(f)
        c = self._contract(contracts, "b")
        assert c is not None
        assert "!= 0" in c.constraints

    def test_numerator_no_constraint(self):
        f = parse_formula(r"\frac{a}{b}")
        contracts = infer_contracts(f)
        c = self._contract(contracts, "a")
        assert c is not None
        assert "!= 0" not in c.constraints


# ───────────────────────────────────────────────────────────────────
# Confidence threshold
# ───────────────────────────────────────────────────────────────────


class TestConfidence:
    def test_high_confidence_confirmed(self):
        f = parse_formula("x + y")
        contracts = infer_contracts(f, extraction_confidence=0.9)
        for c in contracts:
            assert c.confidence >= CONFIDENCE_THRESHOLD
            assert c.confirmed is True

    def test_low_confidence_unconfirmed(self):
        f = parse_formula("x + y")
        contracts = infer_contracts(f, extraction_confidence=0.3)
        for c in contracts:
            assert c.confidence < CONFIDENCE_THRESHOLD
            assert c.confirmed is False

    def test_threshold_boundary(self):
        f = parse_formula("x + y")
        contracts = infer_contracts(f, extraction_confidence=0.6)
        for c in contracts:
            assert c.confirmed is True

    def test_style_boosts_confidence(self):
        f = parse_formula(r"\mathbf{x}")
        contracts = infer_contracts(f, extraction_confidence=0.55)
        c = next(c for c in contracts if c.name == "x")
        # Bold style should add 0.1 → 0.65 ≥ 0.6.
        assert c.confidence >= CONFIDENCE_THRESHOLD
        assert c.confirmed is True

    def test_human_confirmation_replaces_low_confidence_contract(self):
        inferred = infer_contracts(parse_formula("x"), extraction_confidence=0.2)
        confirmed = apply_contract_confirmations(
            inferred,
            [
                SymbolContract(
                    name="x",
                    category="vector",
                    shape=(128,),
                    domain="real",
                    confidence=1,
                    confirmed=True,
                )
            ],
        )
        assert confirmed[0].shape == (128,)
        assert confirmed[0].confirmed is True
        assert confirmed[0].confidence == 1


# ───────────────────────────────────────────────────────────────────
# Shape compatibility
# ───────────────────────────────────────────────────────────────────


class TestShapeCompatibility:
    def test_scalar_multiplication_ok(self):
        f = parse_formula("a * b")
        contracts = infer_contracts(f)
        errors = check_shape_compatibility(contracts, f)
        assert errors == []

    def test_matrix_vector_compatible(self):
        """Matching inner dimensions pass."""
        contracts = [
            SymbolContract(name="M", category="matrix", shape=(3, 4)),
            SymbolContract(name="v", category="vector", shape=(4,)),
        ]
        # We need an AST with M * v.
        f = parse_formula("M * v")
        errors = check_shape_compatibility(contracts, f)
        # Shapes: M(3,4) * v(4,) → inner 4==4 → OK.
        assert errors == []

    def test_matrix_vector_incompatible(self):
        """Mismatched inner dimensions produce a ShapeError."""
        contracts = [
            SymbolContract(name="M", category="matrix", shape=(3, 4)),
            SymbolContract(name="v", category="vector", shape=(5,)),
        ]
        f = parse_formula("M * v")
        errors = check_shape_compatibility(contracts, f)
        assert len(errors) == 1
        assert "mismatch" in errors[0].message.lower()

    def test_unknown_dimensions_pass(self):
        """Unknown (?) dimensions are assumed compatible."""
        contracts = [
            SymbolContract(name="M", category="matrix", shape=("?", "?")),
            SymbolContract(name="v", category="vector", shape=("?",)),
        ]
        f = parse_formula("M * v")
        errors = check_shape_compatibility(contracts, f)
        assert errors == []

    def test_scalar_plus_vector_rejected(self):
        """No implicit broadcasting: scalar + vector is an error."""
        contracts = [
            SymbolContract(name="a", category="scalar", shape=()),
            SymbolContract(name="v", category="vector", shape=("?",)),
        ]
        f = parse_formula("a + v")
        errors = check_shape_compatibility(contracts, f)
        assert len(errors) == 1
        assert "broadcast" in errors[0].message.lower()

    def test_tensor_matrix_multiplication_infers_contracted_shape(self):
        contracts = [
            SymbolContract(name="A", category="tensor", shape=(2, 3, 4)),
            SymbolContract(name="B", category="matrix", shape=(4, 5)),
        ]
        formula = parse_formula("A * B")
        shape, errors = infer_expression_shape(contracts, formula)
        assert errors == []
        assert shape == (2, 3, 5)

    def test_same_rank_addition_dimension_mismatch_rejected(self):
        contracts = [
            SymbolContract(name="a", category="vector", shape=(3,)),
            SymbolContract(name="b", category="vector", shape=(4,)),
        ]
        errors = check_shape_compatibility(contracts, parse_formula("a + b"))
        assert len(errors) == 1
        assert "broadcast" in errors[0].message.lower()


# ───────────────────────────────────────────────────────────────────
# Denominator domain
# ───────────────────────────────────────────────────────────────────


class TestDenominatorDomain:
    def test_denominator_without_constraint(self):
        """A symbol in the denominator without != 0 is flagged."""
        contracts = [
            SymbolContract(name="a", category="scalar", shape=()),
            SymbolContract(name="b", category="scalar", shape=(), constraints=[]),
        ]
        f = parse_formula(r"\frac{a}{b}")
        errors = check_denominator_domain(contracts, f)
        assert len(errors) == 1
        assert errors[0].symbol == "b"

    def test_denominator_with_constraint_ok(self):
        """A symbol with != 0 constraint is not flagged."""
        contracts = [
            SymbolContract(name="a", category="scalar", shape=()),
            SymbolContract(name="b", category="scalar", shape=(), constraints=["!= 0"]),
        ]
        f = parse_formula(r"\frac{a}{b}")
        errors = check_denominator_domain(contracts, f)
        assert errors == []

    def test_no_division_no_errors(self):
        contracts = [
            SymbolContract(name="a", category="scalar", shape=()),
            SymbolContract(name="b", category="scalar", shape=()),
        ]
        f = parse_formula("a + b")
        errors = check_denominator_domain(contracts, f)
        assert errors == []

    def test_index_is_not_constrained_as_a_denominator(self):
        contracts = infer_contracts(parse_formula(r"\frac{a}{d_k}"))
        by_name = {contract.name: contract for contract in contracts}
        assert "!= 0" in by_name["d"].constraints
        assert "!= 0" not in by_name["k"].constraints


# ───────────────────────────────────────────────────────────────────
# Symbol shadowing
# ───────────────────────────────────────────────────────────────────


class TestSymbolShadowing:
    def test_same_category_no_warning(self):
        contracts_a = [SymbolContract(name="x", category="scalar", shape=())]
        contracts_b = [SymbolContract(name="x", category="scalar", shape=())]
        warnings = detect_symbol_shadowing({"s1": contracts_a, "s2": contracts_b})
        assert warnings == []

    def test_different_category_warning(self):
        contracts_a = [SymbolContract(name="x", category="scalar", shape=())]
        contracts_b = [SymbolContract(name="x", category="vector", shape=("?",))]
        warnings = detect_symbol_shadowing({"s1": contracts_a, "s2": contracts_b})
        assert len(warnings) == 1
        assert warnings[0].symbol == "x"
        assert "category" in warnings[0].reason

    def test_different_shape_warning(self):
        contracts_a = [SymbolContract(name="M", category="matrix", shape=(3, 3))]
        contracts_b = [SymbolContract(name="M", category="matrix", shape=(4, 4))]
        warnings = detect_symbol_shadowing({"s1": contracts_a, "s2": contracts_b})
        assert len(warnings) == 1
        assert "shape" in warnings[0].reason

    def test_different_domain_warning(self):
        contracts_a = [SymbolContract(name="x", category="scalar", shape=(), domain="real")]
        contracts_b = [SymbolContract(name="x", category="scalar", shape=(), domain="complex")]
        warnings = detect_symbol_shadowing({"s1": contracts_a, "s2": contracts_b})
        assert len(warnings) == 1
        assert "domain" in warnings[0].reason

    def test_unique_names_no_warning(self):
        contracts_a = [SymbolContract(name="x", category="scalar", shape=())]
        contracts_b = [SymbolContract(name="y", category="vector", shape=("?",))]
        warnings = detect_symbol_shadowing({"s1": contracts_a, "s2": contracts_b})
        assert warnings == []


# ───────────────────────────────────────────────────────────────────
# Contract serialization
# ───────────────────────────────────────────────────────────────────


class TestContractSerialization:
    def test_round_trip_json(self):
        c = SymbolContract(
            name="W",
            category="matrix",
            shape=(3, 4),
            domain="real",
            constraints=["!= 0"],
            scope="sec-1",
            confidence=0.85,
            confirmed=True,
        )
        data = c.model_dump(mode="json")
        restored = SymbolContract.model_validate(data)
        assert restored.name == c.name
        assert restored.shape == c.shape
        assert restored.constraints == c.constraints
        assert restored.confidence == c.confidence
