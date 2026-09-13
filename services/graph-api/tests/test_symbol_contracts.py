"""FGL-403: Symbol contracts — shape, domain, shadowing, and confidence tests."""

from __future__ import annotations

from datetime import UTC, datetime

from app.formula_ast import parse_formula
from app.symbol_contracts import (
    CONFIDENCE_THRESHOLD,
    ContractReview,
    ReviewedContractValue,
    SymbolContract,
    apply_contract_reviews,
    assess_domain_obligations,
    check_shape_compatibility,
    detect_symbol_shadowing,
    domain_assumption,
    infer_contracts,
    infer_domain_obligations,
    infer_expression_shape,
)

# ───────────────────────────────────────────────────────────────────
# Contract inference
# ───────────────────────────────────────────────────────────────────


class TestInferContracts:
    def _contract(self, contracts: list[SymbolContract], name: str) -> SymbolContract | None:
        return next((c for c in contracts if c.name == name), None)

    def test_scalar_shape(self):
        """Under FGL-H3, unstyled lowercase is unknown; bound index is scalar shape."""
        f = parse_formula("x + y")
        contracts = infer_contracts(f)
        c = self._contract(contracts, "x")
        assert c is not None
        assert c.shape is None
        assert c.category == "unknown"

        f_sum = parse_formula(r"\sum_{i=1}^n x_i")
        contracts_sum = infer_contracts(f_sum)
        c_i = self._contract(contracts_sum, "i")
        assert c_i is not None
        assert c_i.shape == ()
        assert c_i.category == "index"

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
        assert c.constraints == []
        obligations = infer_domain_obligations(contracts, f)
        assert len(obligations) == 1
        assert obligations[0].predicate == "nonzero"
        assert obligations[0].expression["value"] == "b"

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
    def test_high_confidence_does_not_create_human_confirmation(self):
        f = parse_formula(r"\mathbf{W} + \mathbf{V}")
        contracts = infer_contracts(f, extraction_confidence=0.9)
        for c in contracts:
            assert c.inference_confidence >= CONFIDENCE_THRESHOLD
            assert c.review_required is False
            assert "confirmed" not in c.model_dump()

    def test_low_confidence_requests_review(self):
        f = parse_formula("x + y")
        contracts = infer_contracts(f, extraction_confidence=0.3)
        for c in contracts:
            assert c.inference_confidence < CONFIDENCE_THRESHOLD
            assert c.review_required is True

    def test_threshold_boundary(self):
        f = parse_formula(r"\mathbf{W} + \mathbf{V}")
        contracts = infer_contracts(f, extraction_confidence=0.6)
        for c in contracts:
            assert c.review_required is False

    def test_style_boosts_confidence(self):
        f = parse_formula(r"\mathbf{x}")
        contracts = infer_contracts(f, extraction_confidence=0.55)
        c = next(c for c in contracts if c.name == "x")
        # Bold style should add 0.1 → 0.65 ≥ 0.6.
        assert c.inference_confidence >= CONFIDENCE_THRESHOLD
        assert c.review_required is False

    def test_human_review_is_a_separate_immutable_record(self):
        inferred = infer_contracts(
            parse_formula("x"), section_id="sec-1", extraction_confidence=0.2
        )
        reviewed_contract = ReviewedContractValue(
            name="x",
            category="vector",
            shape=(128,),
            domain="real",
            scope="sec-1",
        )
        review = ContractReview(
            review_id="review-1",
            symbol_name="x",
            reviewer_id="human-1",
            reviewer_role="reviewer",
            decision="accepted",
            scope="sec-1",
            reviewed_contract=reviewed_contract,
            evidence=["source-span-1"],
            reviewed_at=datetime.now(UTC),
        )
        resolved = apply_contract_reviews(
            inferred,
            [review],
            scope="sec-1",
        )
        assert resolved[0].shape == (128,)
        assert inferred[0].shape is None
        assert review.decision == "accepted"


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
    def test_denominator_tracks_the_full_expression(self):
        formula = parse_formula(r"\frac{1}{a+b}")
        obligations = infer_domain_obligations(infer_contracts(formula), formula)
        assert len(obligations) == 1
        assert obligations[0].predicate == "nonzero"
        assert obligations[0].expression["kind"] == "add"
        assert assess_domain_obligations(obligations).status == "unresolved"

    def test_symbol_nonzero_assumptions_do_not_discharge_sum(self):
        formula = parse_formula(r"\frac{1}{a+b}")
        obligations = infer_domain_obligations(infer_contracts(formula), formula)
        a, b = formula.root.children[1].children
        assumptions = [
            domain_assumption(
                assumption_id="a-nonzero",
                predicate="nonzero",
                expression=a,
                origin="user_accepted",
                acceptance="accepted",
                accepted_by="human",
            ),
            domain_assumption(
                assumption_id="b-nonzero",
                predicate="nonzero",
                expression=b,
                origin="user_accepted",
                acceptance="accepted",
                accepted_by="human",
            ),
        ]
        assert assess_domain_obligations(obligations, assumptions).status == "unresolved"

    def test_ai_proposed_assumption_cannot_discharge_itself(self):
        formula = parse_formula("x/x")
        obligations = infer_domain_obligations(infer_contracts(formula), formula)
        proposed = domain_assumption(
            assumption_id="model-proposal",
            predicate="nonzero",
            expression=formula.root.children[1],
            origin="ai_proposed",
            acceptance="proposed",
        )
        assessment = assess_domain_obligations(obligations, [proposed])
        assert assessment.status == "unresolved"
        assert assessment.obligations[0].discharged_by == []

    def test_user_assumption_is_conditional_not_proof(self):
        formula = parse_formula("x/x")
        obligations = infer_domain_obligations(infer_contracts(formula), formula)
        accepted = domain_assumption(
            assumption_id="human-condition",
            predicate="nonzero",
            expression=formula.root.children[1],
            origin="user_accepted",
            acceptance="accepted",
            accepted_by="human",
        )
        assessment = assess_domain_obligations(obligations, [accepted])
        assert assessment.status == "conditional"
        assert assessment.obligations[0].conditions == ["human-condition"]

    def test_checker_supported_assumption_discharges_obligation(self):
        formula = parse_formula("x/x")
        obligations = infer_domain_obligations(infer_contracts(formula), formula)
        checked = domain_assumption(
            assumption_id="checker-result",
            predicate="positive",
            expression=formula.root.children[1],
            origin="checker_supported",
            acceptance="accepted",
        )
        assessment = assess_domain_obligations(obligations, [checked])
        assert assessment.status == "discharged"
        assert assessment.obligations[0].discharged_by == ["checker-result"]

    def test_log_and_sqrt_obligations_respect_real_or_complex_contract(self):
        real_log = parse_formula(r"\log(x)")
        real_obligations = infer_domain_obligations(infer_contracts(real_log), real_log)
        assert [item.predicate for item in real_obligations] == ["positive"]

        complex_contracts = [
            SymbolContract(name="log", category="function", shape=None),
            SymbolContract(name="x", category="scalar", shape=(), domain="complex"),
        ]
        complex_obligations = infer_domain_obligations(complex_contracts, real_log)
        assert [item.predicate for item in complex_obligations] == ["nonzero"]

        real_sqrt = parse_formula(r"\sqrt{x}")
        sqrt_obligations = infer_domain_obligations(
            infer_contracts(real_sqrt), real_sqrt
        )
        assert [item.predicate for item in sqrt_obligations] == ["non_negative"]

    def test_contradictory_assumptions_do_not_create_vacuous_success(self):
        formula = parse_formula("1/x")
        obligations = infer_domain_obligations(infer_contracts(formula), formula)
        expression = formula.root.children[1]
        assumptions = [
            domain_assumption(
                assumption_id="positive",
                predicate="positive",
                expression=expression,
                origin="user_accepted",
                acceptance="accepted",
                accepted_by="human",
            ),
            domain_assumption(
                assumption_id="negative",
                predicate="negative",
                expression=expression,
                origin="user_accepted",
                acceptance="accepted",
                accepted_by="human",
            ),
        ]
        assessment = assess_domain_obligations(obligations, assumptions)
        assert assessment.status == "contradictory"
        assert assessment.contradictions == [("negative", "positive")]

    def test_index_is_not_constrained_as_a_denominator(self):
        formula = parse_formula(r"\frac{a}{d_k}")
        contracts = infer_contracts(formula)
        by_name = {contract.name: contract for contract in contracts}
        assert by_name["d"].constraints == []
        assert by_name["k"].constraints == []
        obligations = infer_domain_obligations(contracts, formula)
        assert obligations[0].expression["kind"] == "subscript"


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
            inference_confidence=0.85,
            review_required=False,
        )
        data = c.model_dump(mode="json")
        restored = SymbolContract.model_validate(data)
        assert restored.name == c.name
        assert restored.shape == c.shape
        assert restored.constraints == c.constraints
        assert restored.inference_confidence == c.inference_confidence
