"""FGL-403: Symbol contracts — shape, domain, shadowing, and confidence tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.formula_ast import parse_formula
from app.symbol_contracts import (
    CONFIDENCE_THRESHOLD,
    ContractReview,
    ReviewedContractValue,
    SymbolContract,
    _make_predicate_evaluation,
    _predicate_input_hash,
    apply_contract_reviews,
    assess_domain_obligations,
    check_shape_compatibility,
    detect_symbol_shadowing,
    domain_assumption,
    evaluate_domain_predicate,
    infer_contracts,
    infer_domain_obligations,
    infer_expression_shape,
    latest_contract_reviews,
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

    def test_latest_review_per_symbol_is_deterministic_and_rejection_supersedes_acceptance(self):
        reviewed_at = datetime(2026, 1, 1, tzinfo=UTC)
        accepted = ContractReview(
            review_id="review-accepted",
            symbol_name="x",
            reviewer_id="human-1",
            reviewer_role="reviewer",
            decision="accepted",
            scope="eq-1",
            reviewed_contract=ReviewedContractValue(name="x", category="scalar", shape=()),
            reviewed_at=reviewed_at,
        )
        rejected = accepted.model_copy(update={
            "review_id": "review-rejected",
            "decision": "rejected",
            "reviewed_contract": None,
            "reviewed_at": reviewed_at.replace(day=2),
        })
        accepted_y = accepted.model_copy(update={
            "review_id": "review-y",
            "symbol_name": "y",
        })

        current = latest_contract_reviews([accepted, rejected, accepted_y])

        assert [(review.symbol_name, review.decision) for review in current] == [
            ("x", "rejected"),
            ("y", "accepted"),
        ]
        assert current == latest_contract_reviews([accepted_y, rejected, accepted])
        tied_rejection = rejected.model_copy(update={"reviewed_at": reviewed_at})
        assert latest_contract_reviews([accepted, tied_rejection])[0].decision == "rejected"


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

    def test_sample_assignment_finds_a_plus_b_zero_without_proving_it(self):
        formula = parse_formula(r"\frac{1}{a+b}")
        obligation = infer_domain_obligations(infer_contracts(formula), formula)[0]
        expression = formula.root.children[1]

        sample = evaluate_domain_predicate(
            obligation.predicate,
            expression,
            bindings={"a": 1, "b": -1},
        )

        assert sample.outcome == "false"
        assert sample.scope == "sample_assignment"
        assert sample.witness.model_dump(exclude_none=True) == {
            "value": "0",
            "bindings": {"a": "1", "b": "-1"},
        }
        assert obligation.predicate_evaluation.outcome == "unknown"
        assert assess_domain_obligations([obligation]).status == "unresolved"

        sample_true = evaluate_domain_predicate(
            obligation.predicate,
            expression,
            bindings={"a": 2, "b": 1},
        )
        assert sample_true.outcome == "true"
        assert sample_true.scope == "sample_assignment"
        with pytest.raises(ValueError, match="Pointwise predicate results"):
            type(obligation).model_validate(
                obligation.model_dump(mode="python")
                | {"predicate_evaluation": sample_true}
            )

        unicode_formula = parse_formula(r"\frac{1}{α}")
        unicode_obligation = infer_domain_obligations(
            infer_contracts(unicode_formula), unicode_formula
        )[0]
        unicode_sample = evaluate_domain_predicate(
            unicode_obligation.predicate,
            unicode_formula.root.children[1],
            bindings={"α": 1},
        )
        assert unicode_sample.outcome == "true"
        assert unicode_sample.scope == "sample_assignment"

    def test_exact_constant_predicates_are_separate_from_obligation_state(self):
        valid = parse_formula(r"\frac{1}{2}")
        valid_obligation = infer_domain_obligations(infer_contracts(valid), valid)[0]
        assessed_valid = assess_domain_obligations([valid_obligation])
        assert valid_obligation.predicate_evaluation.outcome == "true"
        assert valid_obligation.predicate_evaluation.scope == "exact_constant"
        assert assessed_valid.obligations[0].status == "discharged"
        assert assessed_valid.obligations[0].discharged_by == [
            valid_obligation.predicate_evaluation.evaluation_id
        ]
        assert valid_obligation.predicate_evaluation.evaluator_version == (
            "domain-predicate-evaluator.v2"
        )

        invalid = parse_formula(r"\frac{1}{0}")
        invalid_obligation = infer_domain_obligations(infer_contracts(invalid), invalid)[0]
        assessed_invalid = assess_domain_obligations([invalid_obligation])
        assert invalid_obligation.predicate_evaluation.outcome == "false"
        assert assessed_invalid.status == "unresolved"
        assert assessed_invalid.obligations[0].status == "unresolved"

        nested = parse_formula(r"\frac{1}{\frac{1}{2}}")
        nested_assessment = assess_domain_obligations(
            infer_domain_obligations(infer_contracts(nested), nested)
        )
        assert nested_assessment.status == "discharged"
        assert len(nested_assessment.obligations) == 2
        assert all(
            item.predicate_evaluation.outcome == "true"
            for item in nested_assessment.obligations
        )

        legacy_payload = valid_obligation.model_dump(mode="python")
        legacy_payload.pop("predicate_evaluation")
        assert type(valid_obligation).model_validate(
            legacy_payload
        ).predicate_evaluation is None

        v1 = "domain-predicate-evaluator.v1"
        legacy_evaluation = _make_predicate_evaluation(
            _predicate_input_hash(
                valid_obligation.predicate,
                valid_obligation.expression,
                {},
                evaluator_version=v1,
            ),
            outcome="true",
            scope="exact_constant",
            witness={"value": "2"},
            evaluator_version=v1,
        )
        legacy_payload = valid_obligation.model_dump(mode="python")
        legacy_payload["predicate_evaluation"] = legacy_evaluation.model_dump(mode="python")
        legacy_payload["discharged_by"] = [legacy_evaluation.evaluation_id]
        loaded = type(valid_obligation).model_validate(legacy_payload)
        assert loaded.predicate_evaluation.evaluator_version == v1

    def test_zero_to_zero_is_not_used_to_discharge_domain_obligations(self):
        formula = parse_formula(r"\frac{1}{0^0}")
        assessment = assess_domain_obligations(
            infer_domain_obligations(infer_contracts(formula), formula)
        )

        assert assessment.status == "unsupported"
        assert assessment.obligations[0].status == "unsupported"
        assert assessment.obligations[0].predicate_evaluation.outcome == "unsupported"

    def test_log_base_obligations_include_positive_and_not_one(self):
        formula = parse_formula(r"\log_{2}{8}")
        assessment = assess_domain_obligations(
            infer_domain_obligations(infer_contracts(formula), formula)
        )
        obligations = {item.location.rsplit(".", 1)[-1]: item for item in assessment.obligations}

        assert assessment.status == "discharged"
        assert obligations["log_argument"].predicate == "positive"
        assert obligations["log_base_positive"].predicate == "positive"
        assert obligations["log_base_not_one"].predicate == "not_one"

        invalid_base = parse_formula("log(8,1)")
        invalid_assessment = assess_domain_obligations(
            infer_domain_obligations(infer_contracts(invalid_base), invalid_base)
        )
        base_not_one = next(
            item
            for item in invalid_assessment.obligations
            if item.location.endswith("log_base_not_one")
        )
        assert base_not_one.predicate_evaluation.outcome == "false"
        assert base_not_one.status == "unresolved"
        assert invalid_assessment.status == "unresolved"

        complex_base = parse_formula("log(x,b)")
        contracts = [
            item.model_copy(update={"domain": "complex"})
            if item.name == "b"
            else item
            for item in infer_contracts(complex_base)
        ]
        complex_obligations = infer_domain_obligations(contracts, complex_base)
        assert {
            item.predicate
            for item in complex_obligations
            if item.location.endswith("log_base_positive")
        } == {"nonzero"}
        assert {
            item.predicate
            for item in complex_obligations
            if item.location.endswith("log_base_not_one")
        } == {"not_one"}

    def test_log_and_sqrt_literal_domain_counterexamples_are_visible(self):
        for source, outcome in ((r"\log(0)", "false"), (r"\sqrt{-1}", "false")):
            formula = parse_formula(source)
            obligation = infer_domain_obligations(infer_contracts(formula), formula)[0]
            assert obligation.predicate_evaluation.outcome == outcome

    def test_log_and_sqrt_obligations_follow_declared_real_or_complex_domain(self):
        log_formula = parse_formula(r"\log(x)")
        log_contracts = infer_contracts(log_formula)
        real_log = infer_domain_obligations(log_contracts, log_formula)
        complex_log_contracts = [
            item.model_copy(update={"domain": "complex"}) for item in log_contracts
        ]
        complex_log = infer_domain_obligations(
            complex_log_contracts,
            log_formula,
        )

        assert [(item.predicate, item.predicate_evaluation.outcome) for item in real_log] == [
            ("positive", "unknown"),
        ]
        assert [(item.predicate, item.predicate_evaluation.outcome) for item in complex_log] == [
            ("nonzero", "unknown"),
        ]

        sqrt_formula = parse_formula(r"\sqrt{x}")
        sqrt_contracts = infer_contracts(sqrt_formula)
        real_sqrt = infer_domain_obligations(sqrt_contracts, sqrt_formula)
        complex_sqrt_contracts = [
            item.model_copy(update={"domain": "complex"}) for item in sqrt_contracts
        ]
        complex_sqrt = infer_domain_obligations(
            complex_sqrt_contracts,
            sqrt_formula,
        )

        assert [(item.predicate, item.predicate_evaluation.outcome) for item in real_sqrt] == [
            ("non_negative", "unknown"),
        ]
        assert complex_sqrt == []

    def test_predicate_evaluator_keeps_unsupported_and_invalid_inputs_distinct(self):
        summation = parse_formula(r"\sum_{i=1}^{n} i")
        unsupported = evaluate_domain_predicate("nonzero", summation.root)
        assert unsupported.outcome == "unsupported"

        large_power = parse_formula(r"\frac{1}{2^{65}}")
        power_obligation = infer_domain_obligations(
            infer_contracts(large_power), large_power
        )[0]
        assert power_obligation.predicate_evaluation.outcome == "unsupported"
        assert power_obligation.status == "unsupported"
        assert assess_domain_obligations([power_obligation]).status == "unsupported"

        symbolic = parse_formula("x")
        invalid_binding = evaluate_domain_predicate(
            "nonzero", symbolic.root, bindings={"x": True}
        )
        assert invalid_binding.outcome == "error"

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

    def test_three_way_domain_contradiction_does_not_create_vacuous_success(self):
        formula = parse_formula("1/x")
        obligations = infer_domain_obligations(infer_contracts(formula), formula)
        expression = formula.root.children[1]
        assumptions = [
            domain_assumption(
                assumption_id="non-negative",
                predicate="non_negative",
                expression=expression,
                origin="user_accepted",
                acceptance="accepted",
                accepted_by="human",
            ),
            domain_assumption(
                assumption_id="non-positive",
                predicate="non_positive",
                expression=expression,
                origin="user_accepted",
                acceptance="accepted",
                accepted_by="human",
            ),
            domain_assumption(
                assumption_id="nonzero",
                predicate="nonzero",
                expression=expression,
                origin="user_accepted",
                acceptance="accepted",
                accepted_by="human",
            ),
        ]

        assessment = assess_domain_obligations(obligations, assumptions)

        assert assessment.status == "contradictory"
        assert assessment.obligations[0].status == "contradictory"
        assert assessment.contradictions == [
            ("non-negative", "non-positive", "nonzero")
        ]

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
