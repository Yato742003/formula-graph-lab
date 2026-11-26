"""Playbook 1 regression suite: SymPy assumptions, domain discharge, chains, and Lean 4 audit."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from app.lean_formalizer import audit_lean4_declaration, generate_lean4_export
from app.main import app, require_evidence_store, require_research_service_actor
from app.research_jobs import validate_symbolic_check_payload
from app.symbol_contracts import ContractReview, ReviewedContractValue
from app.verification import _run_worker_payload, run_symbolic_check
from tests.test_check_api import CheckStore, _client, _headers

CHECK_TIMEOUT_MS = 10_000


def _review(
    symbol: str,
    *,
    scope: str = "eq-1",
    category: str = "scalar",
    domain: str = "real",
    shape: tuple[int | str, ...] | None = (),
    constraints: list[str] | None = None,
    review_id: str | None = None,
) -> ContractReview:
    return ContractReview(
        review_id=review_id or f"rev-{symbol}-{domain}",
        symbol_name=symbol,
        reviewer_id="researcher-1",
        reviewer_role="reviewer",
        decision="accepted",
        scope=scope,
        reviewed_contract=ReviewedContractValue(
            name=symbol,
            category=category,  # type: ignore[arg-type]
            shape=shape,
            domain=domain,  # type: ignore[arg-type]
            constraints=constraints or [],
            scope="E1",
        ),
        reviewed_at=datetime.now(UTC),
    )


# ── 1. SymPy assumptions & domain obligation discharge ─────────────────────


def test_positive_scalar_review_discharges_sqrt_and_proves_identity() -> None:
    reviews = [_review("x", domain="positive")]
    result = run_symbolic_check(
        r"\sqrt{x^2}",
        "x",
        timeout_ms=CHECK_TIMEOUT_MS,
        contract_reviews=reviews,
        contract_review_scope="eq-1",
    )
    assert result.outcome == "supported"
    assert result.vector.domain == "discharged"
    assert result.witness == {"method": "sympy_assumptions_simplify", "difference": "0"}
    assert "x:scalar:positive" in result.assumptions


def test_real_scalar_review_discharges_sqrt_x_sq_domain_but_never_collapses_abs() -> None:
    reviews = [_review("x", domain="real")]
    result = run_symbolic_check(
        r"\sqrt{x^2}",
        "x",
        timeout_ms=CHECK_TIMEOUT_MS,
        contract_reviews=reviews,
        contract_review_scope="eq-1",
    )
    # x^2 >= 0 holds for all real x, so domain is discharged,
    # but sqrt(x^2) = |x| != x for negative reals, so symbolic must stay unknown!
    assert result.vector.domain == "discharged"
    assert result.outcome == "unknown"


def test_positive_scalar_review_discharges_log_and_fraction_identities() -> None:
    reviews = [_review("x", domain="positive")]
    log_res = run_symbolic_check(
        r"\log(x^2)",
        r"2 * \log(x)",
        timeout_ms=CHECK_TIMEOUT_MS,
        contract_reviews=reviews,
        contract_review_scope="eq-1",
    )
    assert log_res.outcome == "supported"
    assert log_res.vector.domain == "discharged"

    frac_res = run_symbolic_check(
        "x/x",
        "1",
        timeout_ms=CHECK_TIMEOUT_MS,
        contract_reviews=reviews,
        contract_review_scope="eq-1",
    )
    assert frac_res.outcome == "supported"
    assert frac_res.vector.domain == "discharged"


def test_real_only_review_does_not_discharge_log_or_zero_risk_denominator() -> None:
    reviews = [_review("x", domain="real")]
    log_res = run_symbolic_check(
        r"\log(x)",
        r"\log(x)",
        timeout_ms=CHECK_TIMEOUT_MS,
        contract_reviews=reviews,
        contract_review_scope="eq-1",
    )
    assert log_res.outcome == "unknown"
    assert log_res.vector.domain == "unresolved"
    assert log_res.error_code == "UNRESOLVED_DOMAIN"

    frac_res = run_symbolic_check(
        "x/x",
        "1",
        timeout_ms=CHECK_TIMEOUT_MS,
        contract_reviews=reviews,
        contract_review_scope="eq-1",
    )
    assert frac_res.outcome == "unknown"
    assert frac_res.vector.domain == "unresolved"


def test_positive_sum_discharges_denominator_while_nonzero_sum_stays_unresolved() -> None:
    pos_reviews = [_review("a", domain="positive"), _review("b", domain="positive")]
    pos_res = run_symbolic_check(
        r"\frac{a + b}{a + b}",
        "1",
        timeout_ms=CHECK_TIMEOUT_MS,
        contract_reviews=pos_reviews,
        contract_review_scope="eq-1",
    )
    assert pos_res.outcome == "supported"
    assert pos_res.vector.domain == "discharged"

    nz_reviews = [
        _review("a", domain="real", constraints=["nonzero"]),
        _review("b", domain="real", constraints=["nonzero"]),
    ]
    nz_res = run_symbolic_check(
        r"\frac{a + b}{a + b}",
        "1",
        timeout_ms=CHECK_TIMEOUT_MS,
        contract_reviews=nz_reviews,
        contract_review_scope="eq-1",
    )
    assert nz_res.outcome == "unknown"
    assert nz_res.vector.domain == "unresolved"


def test_negative_constraint_on_log_argument_reports_contradictory_domain() -> None:
    reviews = [_review("x", domain="real", constraints=["negative"])]
    res = run_symbolic_check(
        r"\log(x)",
        r"\log(x)",
        timeout_ms=CHECK_TIMEOUT_MS,
        contract_reviews=reviews,
        contract_review_scope="eq-1",
    )
    assert res.outcome == "unknown"
    assert res.vector.domain == "contradictory"
    assert res.error_code == "CONTRADICTORY_DOMAIN"


def test_reviewed_matrices_remain_strictly_noncommutative() -> None:
    reviews = [
        _review("A", category="matrix", shape=(2, 2), domain="real"),
        _review("B", category="matrix", shape=(2, 2), domain="real"),
    ]
    res = run_symbolic_check(
        "A * B",
        "B * A",
        timeout_ms=CHECK_TIMEOUT_MS,
        contract_reviews=reviews,
        contract_review_scope="eq-1",
    )
    assert res.vector.type == "well_typed"
    assert res.outcome == "unknown"


def test_equation_equality_comparison_handles_lhs_and_rhs() -> None:
    reviews = [_review("y"), _review("a"), _review("b")]
    supported = run_symbolic_check(
        "y = (a + b) - b",
        "y = a",
        timeout_ms=CHECK_TIMEOUT_MS,
        contract_reviews=reviews,
        contract_review_scope="eq-1",
    )
    assert supported.outcome == "supported"

    refuted = run_symbolic_check(
        "y = a",
        "y = a + 1",
        timeout_ms=CHECK_TIMEOUT_MS,
        contract_reviews=reviews,
        contract_review_scope="eq-1",
    )
    assert refuted.outcome == "refuted"
    assert refuted.counterexample == {
        "method": "exact_constant_difference",
        "difference": "-1",
    }


# ── 2. Multi-step derivation chain endpoint (/v1/checks/symbolic/quick) ────


def test_quick_derivation_chain_verifies_all_steps_and_exports_audited_lean(monkeypatch) -> None:
    target_uuid = str(uuid4())
    reviews = [
        _review("x", scope=target_uuid, domain="positive"),
        _review("a", scope=target_uuid, domain="real"),
        _review("b", scope=target_uuid, domain="real"),
    ]
    store = CheckStore(source_latex=r"\sqrt{x^2} + (a + b) - b", contract_reviews=reviews)
    client: TestClient = _client(monkeypatch, store)
    from types import SimpleNamespace

    class ValidatingQueue:
        def __init__(self, *_args):
            pass

        async def admit(self, *_args, **kwargs):
            # Enforce the real ResearchQueue payload and exact-source invariants!
            validate_symbolic_check_payload(kwargs["payload"])
            assert kwargs["payload"]["formula_a"] == store.source_latex
            return SimpleNamespace(
                envelope=SimpleNamespace(
                    job_id="chain-job-1",
                    execution_profile=kwargs.get("execution_profile"),
                ),
                result=None,
            )

        async def claim(self, *_args):
            pass

        async def finish(self, *_args):
            pass

    monkeypatch.setattr("app.main.ResearchQueue", ValidatingQueue)
    monkeypatch.setattr(
        "app.main.run_sandbox",
        lambda payload, **_kwargs: _run_worker_payload(payload, timeout_ms=CHECK_TIMEOUT_MS),
    )
    app.dependency_overrides[require_research_service_actor] = lambda: (None, "workspace-1")
    try:
        response = client.post(
            "/v1/checks/symbolic/quick",
            headers=_headers(),
            json={
                "workspace_id": "workspace-1",
                "target_uuid": target_uuid,
                "steps": ["x + (a + b) - b", "x + a"],
                "include_lean_export": True,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "ad_hoc_check_not_frozen_campaign"
    assert body["outcome"] == "supported"
    assert body["failed_step_index"] is None
    assert len(body["steps"]) == 2
    assert all(step["outcome"] == "supported" for step in body["steps"])
    assert any(ob["status"] == "discharged" for ob in body["domain_obligations"])
    lean = body["lean_export"]
    assert lean["status"] == "audited_draft"
    assert "calc" in lean["lean_code"]
    assert lean["audit"]["declaration_fidelity"] == "PASS"
    assert lean["audit"]["draft_allowlist"] == "PASS"
    assert lean["audit"]["proof_status"] == "FROZEN_UNPROVED"


def test_quick_derivation_chain_pinpoints_exact_failing_step(monkeypatch) -> None:
    target_uuid = str(uuid4())
    reviews = [
        _review("a", scope=target_uuid, domain="real"),
        _review("b", scope=target_uuid, domain="real"),
    ]
    store = CheckStore(source_latex="(a + b) - b", contract_reviews=reviews)
    client: TestClient = _client(monkeypatch, store)
    monkeypatch.setattr(
        "app.main.run_sandbox",
        lambda payload, **_kwargs: _run_worker_payload(payload, timeout_ms=CHECK_TIMEOUT_MS),
    )
    app.dependency_overrides[require_research_service_actor] = lambda: (None, "workspace-1")
    try:
        response = client.post(
            "/v1/checks/symbolic/quick",
            headers=_headers(),
            json={
                "workspace_id": "workspace-1",
                "target_uuid": target_uuid,
                "steps": ["a + (b - b)", "a + 1", "a + 2"],
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "refuted"
    assert body["failed_step_index"] == 2
    assert len(body["steps"]) == 2  # Short-circuited at failing step 2
    assert body["steps"][0]["outcome"] == "supported"
    assert body["steps"][1]["outcome"] == "refuted"
    assert body["steps"][1]["counterexample"] == {
        "method": "exact_constant_difference",
        "difference": "-1",
    }


def test_quick_derivation_chain_fails_closed_on_unreviewed_symbol_and_validation(
    monkeypatch,
) -> None:
    target_uuid = str(uuid4())
    reviews = [_review("a", scope=target_uuid, domain="real")]
    store = CheckStore(source_latex="a", contract_reviews=reviews)
    client: TestClient = _client(monkeypatch, store)
    app.dependency_overrides[require_research_service_actor] = lambda: (None, "workspace-1")
    try:
        missing = client.post(
            "/v1/checks/symbolic/quick",
            headers=_headers(),
            json={
                "workspace_id": "workspace-1",
                "target_uuid": target_uuid,
                "steps": ["a", "a + z - z"],
            },
        )
        assert missing.status_code == 200
        assert missing.json()["reason_code"] == "UNRESOLVED_SHAPE"
        assert missing.json()["missing_symbols"] == ["z"]

        too_many = client.post(
            "/v1/checks/symbolic/quick",
            headers=_headers(),
            json={
                "workspace_id": "workspace-1",
                "target_uuid": target_uuid,
                "steps": ["a"] * 9,
            },
        )
        assert too_many.status_code == 422

        mismatched = client.post(
            "/v1/checks/symbolic/quick",
            headers=_headers(),
            json={
                "workspace_id": "workspace-1",
                "target_uuid": target_uuid,
                "formula_b": "a + 1",
                "steps": ["a"],
            },
        )
        assert mismatched.status_code == 422
    finally:
        app.dependency_overrides.pop(require_research_service_actor, None)
        app.dependency_overrides.pop(require_evidence_store, None)


# ── 3. Lean 4 formalizer & fail-closed five-bin statement audit ────────────


def test_lean4_export_generates_mathlib_draft_passing_five_bin_audit() -> None:
    reviews = [_review("x", domain="positive")]
    export = generate_lean4_export(
        source_formula=r"\sqrt{x^2}",
        target_formula="x",
        contract_reviews=reviews,
        source_equation_uuid="eq-1",
        scope="eq-1",
    )
    assert export.status == "audited_draft"
    assert export.lean_code is not None
    assert "import Mathlib.Data.Real.Basic" in export.lean_code
    assert "import Mathlib.Data.Real.Sqrt" in export.lean_code
    assert all(len(line) <= 100 for line in export.lean_code.splitlines())
    assert export.audit is not None
    assert export.audit.declaration_fidelity == "PASS"
    assert export.audit.draft_allowlist == "PASS"
    assert export.audit.proof_status == "FROZEN_UNPROVED"
    assert export.audit.kernel_checked is False
    assert export.audit.bin_counts == {
        "SOURCE": 1,
        "STANDING": 0,
        "TYPING": 1,
        "RULED": 0,
        "EXCESS": 0,
    }


def test_lean_audit_negative_regression_excess_hypothesis_fails() -> None:
    """Negative fixture 1: extra EXCESS hypothesis MUST fail DECLARATION_FIDELITY."""
    reviews = [_review("x", domain="positive")]
    bad_lean = (
        "import Mathlib.Data.Real.Basic\n"
        "import Mathlib.Data.Real.Sqrt\n"
        "theorem bad_extra (x : ℝ) (h_x_pos : 0 < x) (h_cheat : x = 1) :\n"
        "    Real.sqrt (x ^ 2) = x := by\n"
        "  sorry\n"
    )
    report = audit_lean4_declaration(
        lean_code=bad_lean,
        source_formula=r"\sqrt{x^2}",
        target_formula="x",
        contract_reviews=reviews,
        scope="eq-1",
    )
    assert report.declaration_fidelity == "FAIL"
    assert report.bin_counts["EXCESS"] == 1
    assert report.proof_status == "AUDIT_FAILED"


def test_lean_audit_negative_regression_carrier_mismatch_and_weakened_conclusion_fail() -> None:
    """Negative fixture 2 & 3: wrong carrier or trivialized conclusion MUST fail."""
    reviews = [_review("x", domain="real")]
    wrong_carrier = (
        "theorem bad_carrier (x : ℤ) : x + x = 2 * x := by\n"
        "  sorry\n"
    )
    report_carrier = audit_lean4_declaration(
        lean_code=wrong_carrier,
        source_formula="x + x",
        target_formula="2 * x",
        contract_reviews=reviews,
        scope="eq-1",
    )
    assert report_carrier.declaration_fidelity == "FAIL"
    assert report_carrier.carrier_mismatches

    weakened = (
        "theorem bad_weak (x : ℝ) : True := by\n"
        "  trivial\n"
    )
    report_weak = audit_lean4_declaration(
        lean_code=weakened,
        source_formula="x + x",
        target_formula="2 * x",
        contract_reviews=reviews,
        scope="eq-1",
        kernel_checked=True,
    )
    assert report_weak.declaration_fidelity == "FAIL"
    assert report_weak.conclusion_match is False
    assert report_weak.proof_status == "AUDIT_FAILED"


def test_lean_audit_negative_regression_forbidden_escape_hatches_and_axioms_fail() -> None:
    """Negative fixture 4: admit / axiom / native_decide / custom axiom MUST fail."""
    reviews = [_review("x", domain="real")]
    for escape in ("admit", "native_decide", "axiom cheat : False"):
        bad_code = (
            f"theorem bad_escape (x : ℝ) : x + x = 2 * x := by\n"
            f"  {escape}\n"
        )
        report = audit_lean4_declaration(
            lean_code=bad_code,
            source_formula="x + x",
            target_formula="2 * x",
            contract_reviews=reviews,
            scope="eq-1",
        )
        assert report.draft_allowlist == "FAIL"
        assert report.proof_status == "AUDIT_FAILED"

    clean_sealed = "theorem ok_sealed (x : ℝ) : x + x = 2 * x := by\n  ring\n"
    bad_axiom = audit_lean4_declaration(
        lean_code=clean_sealed,
        source_formula="x + x",
        target_formula="2 * x",
        contract_reviews=reviews,
        scope="eq-1",
        kernel_checked=True,
        kernel_axioms=["propext", "my_custom_axiom"],
    )
    assert bad_axiom.draft_allowlist == "FAIL"
    assert bad_axiom.unauthorized_axioms == ["my_custom_axiom"]
    assert bad_axiom.proof_status == "AUDIT_FAILED"

    good_sealed = audit_lean4_declaration(
        lean_code=clean_sealed,
        source_formula="x + x",
        target_formula="2 * x",
        contract_reviews=reviews,
        scope="eq-1",
        kernel_checked=True,
        kernel_axioms=["propext", "Classical.choice", "Quot.sound"],
    )
    assert good_sealed.declaration_fidelity == "PASS"
    assert good_sealed.draft_allowlist == "PASS"
    assert good_sealed.proof_status == "PROVED"
    assert good_sealed.kernel_checked is True


def test_lean4_export_fails_closed_for_unsupported_matrix_carrier() -> None:
    reviews = [_review("A", category="matrix", shape=(2, 2), domain="real")]
    export = generate_lean4_export(
        source_formula="A",
        target_formula="A",
        contract_reviews=reviews,
        scope="eq-1",
    )
    assert export.status == "unsupported"
    assert export.error_code == "LEAN_UNSUPPORTED_CARRIER"
    assert export.lean_code is None
