"""Comprehensive unit & Hypothesis PBT suite for the 4 Cross-Paper Standards + Dual-Track."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from app.context_disambiguator import (
    DISAMBIGUATOR_VERSION,
    disambiguate_equation_context,
    synthesize_exploratory_reviews,
)
from app.egraph_engine import egraph_check_equivalence
from app.evidence import analyze_equation
from app.extractor import extract_paper
from app.formula_ast import SYNTAX_HASH_VERSION, parse_formula
from app.mcp_server import create_mcp_server
from app.models import ExtractedEquation, QuickSymbolicRequest
from app.sandbox import validate_worker_result
from app.slt_opt import (
    SLT_HASH_VERSION,
    W3C_INTENT_VERSION,
    annotate_w3c_intent,
    build_slt,
    slt_similarity,
)
from app.symbol_contracts import (
    ContractReview,
    ReviewedContractValue,
    assess_domain_obligations,
    discharge_domain_obligations_from_reviews,
    infer_contracts,
    infer_domain_obligations,
)
from app.verification import _run_worker_payload, run_symbolic_check


def _review(
    symbol: str,
    *,
    category: str = "scalar",
    domain: str = "real",
    shape: tuple[int | str, ...] | None = (),
    constraints: list[str] | None = None,
    scope: str = "eq-scope",
) -> ContractReview:
    return ContractReview(
        review_id=f"rev_{symbol}_{category}_{domain}",
        symbol_name=symbol,
        reviewer_id="researcher-1",
        reviewer_role="researcher",
        decision="accepted",
        scope=scope,
        reviewed_contract=ReviewedContractValue(
            name=symbol,
            category=category,  # type: ignore[arg-type]
            shape=shape,
            domain=domain,  # type: ignore[arg-type]
            constraints=constraints or [],
            scope=scope,
        ),
        reviewed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


# ── Standard 1: LaTeXML MathML Ingestion ───────────────────────────────────


def test_extractor_preserves_presentation_and_content_mathml():
    html = """
    <html>
      <div id="watermark-tr">arXiv:2609.00001v1 [cs.LG] 01 Sep 2026</div>
      <section id="S1"><h2>Method</h2>
        <div id="S1.E1" class="ltx_equation">
          <math display="block" alttext="v = \\mu + \\Delta_g">
            <semantics>
              <mrow>
                <mi>v</mi><mo>=</mo><mi>μ</mi><mo>+</mo><msub><mi>Δ</mi><mi>g</mi></msub>
              </mrow>
              <annotation encoding="application/x-tex">v = \\mu + \\Delta_g</annotation>
              <annotation-xml encoding="MathML-Content">
                <apply><eq/><ci>v</ci><apply><plus/><ci>mu</ci><ci>Delta_g</ci></apply></apply>
              </annotation-xml>
            </semantics>
          </math>
        </div>
      </section>
    </html>
    """
    paper = extract_paper(html, "https://arxiv.org/html/2609.00001v1")
    assert len(paper.equations) == 1
    eq = paper.equations[0]
    assert eq.latex == r"v = \mu + \Delta_g"
    assert eq.presentation_mathml is not None
    assert "<annotation" not in eq.presentation_mathml
    assert "<msub>" in eq.presentation_mathml
    assert eq.content_mathml is not None
    assert "<apply>" in eq.content_mathml


# ── Standard 2: SLT + OPT + W3C MathML 4 intent ────────────────────────────


def test_slt_achieves_100_percent_coverage_even_on_unsupported_opt():
    unsupported = ExtractedEquation(
        equation_id="eq-qg",
        anchor="S4.E1",
        latex=r"\begin{matrix} \delta\langle T_{\mu\nu}\rangle & 0 \end{matrix}",
        extraction_method="alttext",
        confidence=0.9,
    )
    analysis, parsed, contracts = analyze_equation(unsupported)
    assert parsed is None
    assert contracts == []
    assert analysis["status"] == "unsupported"
    assert "slt" in analysis
    assert analysis["slt"]["slt_hash_version"] == SLT_HASH_VERSION  # type: ignore[index]
    assert len(analysis["slt"]["slt_hash"]) == 64  # type: ignore[index]


@settings(max_examples=80, deadline=None)
@given(raw_latex=st.text(min_size=0, max_size=120))
def test_pbt_slt_is_total_and_deterministic_for_arbitrary_strings(raw_latex: str):
    a = build_slt(raw_latex)
    b = build_slt(raw_latex)
    assert a.slt_hash == b.slt_hash
    assert a.slt_hash_version == SLT_HASH_VERSION
    assert a.node_count >= 1
    assert 0.0 <= slt_similarity(a, b) <= 1.0


def test_slt_similarity_and_content_mathml_fallback_bridge():
    eq_a = build_slt(r"Q K^T")
    eq_b = build_slt(r"A B^T")
    eq_c = build_slt(r"\sum_{i=1}^{n} \frac{x_i}{y_i}")
    assert slt_similarity(eq_a, eq_a) == 1.0
    assert eq_a.source_kind == "latex_layout"
    assert 0.0 <= slt_similarity(eq_a, eq_b) <= 1.0
    assert 0.0 <= slt_similarity(eq_a, eq_c) < 1.0

    cml = (
        "<math><apply><eq/><ci>y</ci>"
        "<apply><divide/><ci>a</ci><apply><plus/><ci>b</ci><cn>1</cn></apply></apply>"
        "</apply></math>"
    )
    parsed = parse_formula(cml, source_format="mathml")
    assert parsed.root.kind == "equals"
    assert parsed.root.children[1].kind == "divide"


def test_landmark_vaswani_3dgs_and_quantum_gravity_pi_discharge():
    s3_ex1 = parse_formula(
        r"\displaystyle\mathrm{MultiHead}(Q,K,V) \displaystyle="
        r"\mathrm{Concat}(\mathrm{head_{1}},...,\mathrm{head_{h}})W^{O}"
    )
    assert s3_ex1.syntax_hash_version == SYNTAX_HASH_VERSION
    assert "ellipsis" not in s3_ex1.free_variables
    s3_ex2 = parse_formula(
        r"\displaystyle\text{where}~\mathrm{head_{i}} \displaystyle="
        r"\mathrm{Attention}(QW^{Q}_{i},KW^{K}_{i},VW^{V}_{i})"
    )
    assert s3_ex2.root.kind == "equals"
    assert "where" not in s3_ex2.free_variables

    s3_e13 = parse_formula(
        r"q_{\hat{v}}=q_{\mu}+\left\lfloor(k_{v}\rho_{v})/2^{\tau_{v}}\right\rceil,"
    )
    assert s3_e13.root.kind == "equals"
    a1_e14 = parse_formula(
        r"\mathcal{R}(a,d)=\operatorname{sgn}(a)\left\lfloor\frac{|a|}{d}+\frac{1}{2}\right\rfloor."
    )
    assert a1_e14.root.kind == "equals"

    intents = annotate_w3c_intent(s3_ex2, infer_contracts(s3_ex2))
    assert intents["intent_version"] == W3C_INTENT_VERSION
    assert "matrix(Q)" in intents["root_intent"]

    qg = parse_formula(r"H = -\frac{\kappa}{2\pi}\frac{A}{4 G_N}.")
    obs = infer_domain_obligations(infer_contracts(qg), qg)
    discharged = discharge_domain_obligations_from_reviews(obs, [])
    assessed = assess_domain_obligations(obs, discharged)
    pi_ob = next(
        o
        for o in assessed.obligations
        if "denominator" in o.location and "2" in str(o.expression)
    )
    assert pi_ob.status == "discharged"


# ── Standard 3: LaCASt Prose Context Disambiguator ────────────────────────


def test_lacast_context_disambiguator_on_real_3dgs_and_attention_prose():
    gs_eq = ExtractedEquation(
        equation_id="eq_gs_s3e4",
        anchor="S3.E4",
        latex=r"\hat{v}=\mu+\Delta_{g}k_{v}",
        preceding_text=(
            r"For a scalar variable v in group g, the predicted mean \mu and a "
            r"learned quantization step \Delta_{g}>0 determine its integer residual k_v."
        ),
        extraction_method="tex_annotation",
        confidence=0.99,
    )
    analysis, parsed, _ = analyze_equation(gs_eq)
    assert parsed is not None
    disambig = analysis["context_disambiguation"]
    assert disambig["disambiguator_version"] == DISAMBIGUATOR_VERSION  # type: ignore[index]
    hints = disambig["hints"]  # type: ignore[index]
    assert hints["Delta_g"]["domain"] == "positive"
    assert hints["Delta_g"]["constraints"] == ["Delta_g > 0"]
    assert hints["Delta_g"]["status"] == "inferred_draft"
    assert hints["v"]["category"] == "scalar"
    assert hints["mu"]["category"] == "scalar"

    att = parse_formula(r"y = \sum_{i=1}^{n} \frac{x_i}{d_k}")
    ctx = disambiguate_equation_context(
        att,
        following_text=r"where d_{k} is the dimension and x \in \mathbb{R}^{n}.",
    )
    assert ctx.hints["d_k"].domain == "positive"
    assert ctx.hints["d_k"].constraints == ("d_k > 0",)
    assert len(ctx.meom_operators) == 1
    assert ctx.meom_operators[0]["operator"] == "sum"
    assert ctx.meom_operators[0]["binder"] == "i"


# ── Standard 4: E-Graph & Equality Saturation Engine ───────────────────────


def test_egraph_preserves_non_commutativity_and_proves_transpose_identities():
    ab = parse_formula(r"\mathbf{A} \mathbf{B}").root
    ba = parse_formula(r"\mathbf{B} \mathbf{A}").root
    mat_assumptions = {
        "A": {"category": "matrix", "domain": "real"},
        "B": {"category": "matrix", "domain": "real"},
        "C": {"category": "matrix", "domain": "real"},
    }
    assert egraph_check_equivalence(ab, ba, symbol_assumptions=mat_assumptions) is None

    t_ab = parse_formula(r"(\mathbf{A}\mathbf{B})^T").root
    bt_at = parse_formula(r"\mathbf{B}^T \mathbf{A}^T").root
    res_t = egraph_check_equivalence(t_ab, bt_at, symbol_assumptions=mat_assumptions)
    assert res_t is not None
    assert res_t["outcome"] == "supported"
    assert res_t["witness"]["method"] == "egraph_equality_saturation"  # type: ignore[index]

    lhs = parse_formula(r"\mathbf{A}(\mathbf{B} + \mathbf{C})").root
    rhs = parse_formula(r"\mathbf{A}\mathbf{B} + \mathbf{A}\mathbf{C}").root
    assert egraph_check_equivalence(lhs, rhs, symbol_assumptions=mat_assumptions) is not None

    cipr_eq = parse_formula(
        r"\displaystyle\mathbf{Q}_{j} \displaystyle=\mathbf{X}\mathbf{W}^{Q}_{j}"
    ).root
    cipr_eq_t = parse_formula(r"\mathbf{Q}_j^T = (\mathbf{W}_j^Q)^T \mathbf{X}^T").root
    cipr_eq_bad = parse_formula(r"\mathbf{Q}_j^T = \mathbf{X}^T (\mathbf{W}_j^Q)^T").root
    cipr_assumptions = {
        "Q": {"category": "matrix", "domain": "real"},
        "W": {"category": "matrix", "domain": "real"},
        "X": {"category": "matrix", "domain": "real"},
        "j": {"category": "index", "domain": "integer"},
    }
    res_eq_t = egraph_check_equivalence(
        cipr_eq, cipr_eq_t, symbol_assumptions=cipr_assumptions
    )
    assert res_eq_t is not None
    assert res_eq_t["outcome"] == "supported"
    assert "eq_transpose_both_sides" in res_eq_t["witness"]["rules_applied"]  # type: ignore[index]
    assert (
        egraph_check_equivalence(
            cipr_eq, cipr_eq_bad, symbol_assumptions=cipr_assumptions
        )
        is None
    )


def test_egraph_enforces_guarded_division_log_and_3dgs_equation_rescaling():
    x_div_x = parse_formula(r"\frac{x}{x}").root
    one = parse_formula("1").root
    real_x = {"x": {"category": "scalar", "domain": "real"}}
    assert egraph_check_equivalence(x_div_x, one, symbol_assumptions=real_x) is None
    nz_x = {"x": {"category": "scalar", "domain": "real", "nonzero": True}}
    assert egraph_check_equivalence(x_div_x, one, symbol_assumptions=nz_x) is not None

    log_a = parse_formula(r"\log\frac{a}{b}").root
    log_b = parse_formula(r"\log(a) - \log(b)").root
    assert (
        egraph_check_equivalence(
            log_a,
            log_b,
            symbol_assumptions={
                "a": {"category": "scalar", "domain": "real"},
                "b": {"category": "scalar", "domain": "real"},
            },
        )
        is None
    )
    assert (
        egraph_check_equivalence(
            log_a,
            log_b,
            symbol_assumptions={
                "a": {"category": "scalar", "domain": "positive"},
                "b": {"category": "scalar", "domain": "positive"},
            },
        )
        is not None
    )

    eq1 = parse_formula(r"v = \mu + \Delta_g k_v").root
    eq2 = parse_formula(r"\frac{v - \mu}{\Delta_g} = k_v").root
    gs_unproven = {
        "v": {"category": "scalar", "domain": "real"},
        "mu": {"category": "scalar", "domain": "real"},
        "Delta_g": {"category": "scalar", "domain": "real"},
        "k_v": {"category": "scalar", "domain": "integer"},
    }
    assert egraph_check_equivalence(eq1, eq2, symbol_assumptions=gs_unproven) is None

    gs_proven = {
        **gs_unproven,
        "Delta_g": {"category": "scalar", "domain": "positive"},
    }
    res_gs = egraph_check_equivalence(eq1, eq2, symbol_assumptions=gs_proven)
    assert res_gs is not None
    assert "nonzero_factor_rescale" in res_gs["witness"]["rules_applied"]  # type: ignore[index]


def test_egraph_sandbox_validator_and_local_worker_integration():
    reviews = [
        _review("v", scope="eq-3dgs"),
        _review("mu", scope="eq-3dgs"),
        _review("Delta", domain="positive", constraints=["Delta > 0"], scope="eq-3dgs"),
        _review("k", domain="integer", scope="eq-3dgs"),
    ]
    check = run_symbolic_check(
        r"v = \mu + \Delta k",
        r"\frac{v - \mu}{\Delta} = k",
        contract_reviews=reviews,
        contract_review_scope="eq-3dgs",
        worker_runner=_run_worker_payload,
    )
    assert check.outcome == "supported"
    assert check.witness is not None
    assert check.witness["method"] == "egraph_equality_saturation"
    validated = validate_worker_result(
        json.dumps({"outcome": "supported", "witness": check.witness}).encode("utf-8")
    )
    assert validated["outcome"] == "supported"

    tampered = json.dumps(
        {"outcome": "supported", "witness": {**check.witness, "extra": "bad"}}
    ).encode("utf-8")
    assert validate_worker_result(tampered)["outcome"] == "error"


# ── Dual-Track Epistemic Architecture & MCP Integration ───────────────────


def test_dual_track_exploratory_constraints_and_mcp_tools():
    with pytest.raises(ValidationError, match="Lean 4 export requires human-certified"):
        QuickSymbolicRequest(
            workspace_id="ws",
            target_uuid=uuid4(),
            formula_b="x + 1",
            include_lean_export=True,
            allow_auto_inferred=True,
        )

    parsed_list = [
        parse_formula(r"v = \mu + \Delta k"),
        parse_formula(r"\frac{v - \mu}{\Delta} = k"),
    ]
    human_mu = _review("mu", domain="real", scope="eq-1")
    merged, meta = synthesize_exploratory_reviews(
        parsed_list,
        preceding_text=r"Quantization step \Delta > 0 and integer residual k.",
        existing_accepted=[human_mu],
        scope="eq-1",
    )
    by_name = {r.symbol_name: r for r in merged}
    assert by_name["mu"].reviewer_id == "researcher-1"
    assert by_name["Delta"].reviewer_id == "system:auto-inferred-exploratory"
    assert by_name["Delta"].reviewed_contract is not None
    assert by_name["Delta"].reviewed_contract.category == "scalar"
    assert by_name["Delta"].reviewed_contract.domain == "positive"
    assert any(
        item["symbol"] == "Delta" and item["source"] == "lacast_prose_context" for item in meta
    )

    mcp = create_mcp_server(allow_local_subprocess=True)
    tools = mcp._tool_manager._tools  # type: ignore[attr-defined]
    analyzed = json.loads(
        tools["fgl_analyze_formula"].fn(
            formula=r"\hat{v} = \mu + \Delta_g k_v",
            preceding_text=r"quantization step \Delta_{g}>0",
        )
    )
    assert analyzed["slt"]["slt_hash_version"] == SLT_HASH_VERSION
    assert analyzed["w3c_intents"]["intent_version"] == W3C_INTENT_VERSION
    assert analyzed["context_disambiguation"]["hints"]["Delta_g"]["domain"] == "positive"

    blocked_lean = json.loads(
        tools["fgl_verify_derivation"].fn(
            source_latex=r"v = \mu + \Delta k",
            target_latex=r"\frac{v - \mu}{\Delta} = k",
            allow_auto_inferred=True,
            include_lean_export=True,
        )
    )
    assert blocked_lean["error_code"] == "LEAN_REQUIRES_CERTIFIED_CONTRACTS"

    exploratory = json.loads(
        tools["fgl_verify_derivation"].fn(
            source_latex=r"v = \mu + \Delta k",
            target_latex=r"\frac{v - \mu}{\Delta} = k",
            allow_auto_inferred=True,
            preceding_text=r"quantization step \Delta > 0, scalar v, mean \mu, integer k",
        )
    )
    assert exploratory["outcome"] == "supported"
    assert exploratory["epistemic_track"] == "auto_inferred_exploratory"
    assert exploratory["contract_provenance"] == "lacast_auto_inferred_exploratory"
