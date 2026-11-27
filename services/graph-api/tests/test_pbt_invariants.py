"""Property-Based Testing (PBT) invariants for Python L3/L4 mathematical and ingestion engines.

Uses Hypothesis with constrained strategies, pinned edge cases, and algebraic invariants
covering ``tex_extractor``, ``formula_ast``, ``symbol_contracts``, and ``lean_formalizer``.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime

from hypothesis import example, given, settings
from hypothesis import strategies as st

from app.evidence import build_evidence_graph
from app.formula_ast import (
    MAX_AST_NODES,
    FormulaParseError,
    ast_node_count,
    canonicalize,
    compute_canonical_hash,
    parse_formula,
)
from app.lean_formalizer import audit_lean4_declaration, generate_lean4_export
from app.symbol_contracts import (
    ContractReview,
    ReviewedContractValue,
    SymbolContract,
    assess_domain_obligations,
    discharge_domain_obligations_from_reviews,
    infer_domain_obligations,
)
from app.tex_extractor import (
    MAX_EXPANDED_EQUATION_CHARS,
    LatexMacro,
    expand_tex_macros,
    extract_tex,
    extract_tex_macros,
)

# ── Shared Strategies ───────────────────────────────────────────

_SAFE_VAR_NAMES = ("a", "b", "c", "u", "v", "w", "x", "y", "z", "alpha", "beta", "gamma")
_SAFE_INDEX_NAMES = ("i", "j", "k", "m", "p", "q", "r", "t")
_MACRO_POOL = ("ma", "mb", "mc", "md", "me", "mf", "mg", "mh")
_PROTECTED_SAMPLE = ("frac", "input", "include", "begin", "end", "newcommand", "def", "catcode")


def _var_latex(name: str) -> str:
    return rf"\{name}" if len(name) > 1 else name


@st.composite
def valid_scalar_latex(draw: st.DrawFn, max_depth: int = 3) -> str:
    """Generate structurally valid scalar LaTeX expressions parseable by ``formula_ast``."""
    leaf = st.one_of(
        st.sampled_from(["a", "b", "c", "x", "y", "z", r"\alpha", r"\beta"]),
        st.integers(min_value=1, max_value=19).map(str),
    )

    def _extend(children: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
        return st.one_of(
            st.tuples(children, children).map(lambda ab: f"({ab[0]} + {ab[1]})"),
            st.tuples(children, children).map(lambda ab: f"({ab[0]} - {ab[1]})"),
            st.tuples(children, children).map(lambda ab: f"({ab[0]} \\cdot {ab[1]})"),
            st.tuples(children, children).map(lambda ab: rf"\frac{{{ab[0]}}}{{{ab[1]}}}"),
            children.map(lambda e: rf"\sqrt{{{e}}}"),
            children.map(lambda e: rf"\log({e})"),
            st.tuples(children, st.integers(min_value=2, max_value=4)).map(
                lambda ep: f"({ep[0]})^{{{ep[1]}}}"
            ),
        )

    expr_strategy = st.recursive(leaf, _extend, max_leaves=2**max_depth)
    return draw(expr_strategy)


@st.composite
def tex_document_with_macros(draw: st.DrawFn) -> tuple[str, list[str]]:
    """Generate a valid TeX document with comments, sections, safe macros, and equations."""
    title = draw(
        st.text(
            alphabet=st.characters(
                whitelist_categories=("Lu", "Ll", "Nd"),
                whitelist_characters=" -",
            ),
            min_size=1,
            max_size=24,
        )
        .map(str.strip)
        .filter(bool)
    )
    macro_names = draw(st.lists(st.sampled_from(_MACRO_POOL), min_size=0, max_size=4, unique=True))
    lines = [rf"\title{{{title}}}"]
    for idx, name in enumerate(macro_names):
        if idx % 2 == 0:
            lines.append(rf"\newcommand{{\{name}}}{{(x + {idx + 1})}}")
        else:
            lines.append(rf"\DeclareMathOperator{{\{name}}}{{op{idx}}}")

    comment = draw(
        st.text(
            alphabet=st.characters(
                whitelist_categories=("Lu", "Ll", "Nd"),
                whitelist_characters=" ",
            ),
            min_size=0,
            max_size=24,
        )
    )
    lines.append(f"% {comment} $$ignored$$")
    lines.append(r"\section{Main}")

    eq_count = draw(st.integers(min_value=1, max_value=4))
    used_macros: list[str] = []
    for k in range(eq_count):
        if macro_names and draw(st.booleans()):
            chosen = draw(st.sampled_from(macro_names))
            used_macros.append(chosen)
            body = rf"y_{{{k}}} = \{chosen} + {k + 1}"
        else:
            body = rf"y_{{{k}}} = x + {k + 1}"
        lines.append(r"\begin{equation}")
        lines.append(body)
        lines.append(r"\end{equation}")
    return "\n".join(lines), used_macros


@st.composite
def adversarial_macro_graph(draw: st.DrawFn) -> tuple[dict[str, LatexMacro], str]:
    """Generate arbitrary macro graphs including direct/mutual cycles and duplicating chains."""
    names = draw(st.lists(st.sampled_from(_MACRO_POOL), min_size=1, max_size=6, unique=True))
    macros: dict[str, LatexMacro] = {}
    for name in names:
        arity = draw(st.integers(min_value=0, max_value=2))
        default_arg = draw(st.sampled_from([None, "x", "1"])) if arity >= 1 else None
        token_choices = [rf"\{other}" for other in names] + ["x", " + ", "1", r"\alpha"]
        if arity >= 1:
            token_choices.append("#1")
        if arity >= 2:
            token_choices.append("#2")
        parts = draw(st.lists(st.sampled_from(token_choices), min_size=1, max_size=6))
        macros[name] = LatexMacro(
            name=name,
            arity=arity,
            body="".join(parts),
            default_arg=default_arg,
        )

    eq_parts: list[str] = []
    for _ in range(draw(st.integers(min_value=1, max_value=5))):
        target = draw(st.sampled_from(names))
        macro = macros[target]
        if macro.arity == 0:
            eq_parts.append(rf"\{target}")
        elif macro.arity == 1:
            eq_parts.append(rf"\{target}{{x}}")
        else:
            eq_parts.append(rf"\{target}{{x}}{{y}}")
    return macros, " + ".join(eq_parts)


def _review(
    symbol: str,
    *,
    domain: str = "real",
    constraints: list[str] | None = None,
) -> ContractReview:
    return ContractReview(
        review_id=f"rev_{symbol}_{domain}",
        symbol_name=symbol,
        reviewer_id="pbt_reviewer",
        reviewer_role="researcher",
        decision="accepted",
        scope="E1",
        reviewed_contract=ReviewedContractValue(
            name=symbol,
            category="scalar",
            shape=(),
            domain=domain,  # type: ignore[arg-type]
            constraints=constraints or [],
        ),
        reviewed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _contract(symbol: str, *, domain: str = "real") -> SymbolContract:
    return SymbolContract(
        name=symbol,
        category="scalar",
        shape=(),
        domain=domain,  # type: ignore[arg-type]
    )


# ── 1. tex_extractor Invariants ─────────────────────────────────


@settings(max_examples=80, deadline=None)
@given(doc_and_macros=tex_document_with_macros())
def test_pbt_tex_extractor_determinism_and_raw_provenance(
    doc_and_macros: tuple[str, list[str]],
) -> None:
    source, used_macros = doc_and_macros
    paper_a = extract_tex(source)
    paper_b = extract_tex(source)
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()

    assert paper_a.model_dump() == paper_b.model_dump()
    assert paper_a.source_sha256 == digest
    assert paper_a.paper_id == f"tex_{digest[:12]}"
    assert paper_a.source_url == f"urn:fgl:local-tex:{digest}"
    assert len(paper_a.equations) >= 1

    # Raw fragments preserve unmodified source tokens even after macro expansion.
    all_raw = "\n".join(frag for eq in paper_a.equations for frag in eq.source_fragments)
    for mname in used_macros:
        assert rf"\{mname}" in all_raw

    graph_a = build_evidence_graph(paper_a, workspace_id="pbt")
    graph_b = build_evidence_graph(paper_b, workspace_id="pbt")
    assert graph_a.import_uuid == graph_b.import_uuid


@settings(max_examples=100, deadline=None)
@given(graph_and_eq=adversarial_macro_graph())
def test_pbt_macro_expansion_bounded_and_terminates(
    graph_and_eq: tuple[dict[str, LatexMacro], str],
) -> None:
    macros, eq = graph_and_eq
    expanded_a, warnings_a = expand_tex_macros(eq, macros)
    expanded_b, warnings_b = expand_tex_macros(eq, macros)

    assert expanded_a == expanded_b
    assert warnings_a == warnings_b
    assert len(expanded_a) <= max(MAX_EXPANDED_EQUATION_CHARS, len(eq))
    if len(eq) <= MAX_EXPANDED_EQUATION_CHARS:
        assert len(expanded_a) <= MAX_EXPANDED_EQUATION_CHARS


@settings(max_examples=60, deadline=None)
@given(
    protected=st.sampled_from(_PROTECTED_SAMPLE),
    body=st.text(
        alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd")),
        min_size=1,
        max_size=16,
    ),
)
def test_pbt_protected_commands_can_never_be_overridden(protected: str, body: str) -> None:
    tex = (
        rf"\newcommand{{\{protected}}}{{{body}}}"
        "\n"
        rf"\DeclareMathOperator{{\{protected}}}{{{body}}}"
    )
    macros, _ = extract_tex_macros(tex)
    assert protected not in macros


@settings(max_examples=120, deadline=None)
@example("")
@example("$$")
@example(r"\newcommand{\a}{\a}\begin{equation}\a\end{equation}")
@example(r"\section{unclosed")
@given(
    fuzz=st.lists(
        st.sampled_from(
            list("\\{}[]%$#0123456789abcxyz\n ")
            + [
                r"\newcommand",
                r"\DeclareMathOperator",
                r"\begin{equation}",
                r"\end{equation}",
                r"\[",
                r"\]",
                "$$",
                r"\section{",
                r"\label{",
            ]
        ),
        min_size=0,
        max_size=40,
    ).map("".join)
)
def test_pbt_tex_extractor_fail_closed_fuzz(fuzz: str) -> None:
    try:
        paper = extract_tex(fuzz)
    except ValueError:
        return
    assert len(paper.equations) >= 1
    assert paper.source_sha256 == hashlib.sha256(fuzz.encode("utf-8")).hexdigest()
    for eq in paper.equations:
        assert eq.latex.strip() != ""


# ── 2. formula_ast Invariants ───────────────────────────────────


@settings(max_examples=100, deadline=None)
@example("x + y")
@example(r"\frac{a + b}{c}")
@example(r"\sqrt{\log(x)}")
@given(expr=valid_scalar_latex())
def test_pbt_formula_ast_canonicalization_idempotence(expr: str) -> None:
    parsed = parse_formula(expr)
    canon_once = canonicalize(parsed.root)
    canon_twice = canonicalize(canon_once)

    assert canon_twice == canon_once
    assert compute_canonical_hash(canon_once) == compute_canonical_hash(parsed.root)
    assert compute_canonical_hash(canon_twice) == parsed.syntax_hash
    assert ast_node_count(parsed.root) <= MAX_AST_NODES


@settings(max_examples=80, deadline=None)
@given(
    indices=st.lists(st.sampled_from(_SAFE_INDEX_NAMES), min_size=2, max_size=2, unique=True),
    power=st.integers(min_value=1, max_value=5),
    coeff=st.integers(min_value=1, max_value=9),
)
def test_pbt_formula_ast_alpha_equivalence_bound_indices(
    indices: list[str],
    power: int,
    coeff: int,
) -> None:
    idx_a, idx_b = indices
    expr_a = rf"\sum_{{{idx_a}=1}}^{{n}} ({coeff} \cdot {idx_a}^{{{power}}} + x)"
    expr_b = rf"\sum_{{{idx_b}=1}}^{{n}} ({coeff} \cdot {idx_b}^{{{power}}} + x)"

    parsed_a = parse_formula(expr_a)
    parsed_b = parse_formula(expr_b)
    assert parsed_a.syntax_hash == parsed_b.syntax_hash
    assert parsed_a.free_variables == parsed_b.free_variables


@settings(max_examples=120, deadline=None)
@example("")
@example("{{{{{")
@example(r"\frac{1}{}")
@example(r"\sum_{i=1}^{n}")
@given(
    fuzz=st.lists(
        st.sampled_from(
            list("+-*/^_()[]{}0123456789xyz=<>.,| ")
            + [
                r"\frac",
                r"\sqrt",
                r"\sum",
                r"\prod",
                r"\int",
                r"\log",
                r"\alpha",
                r"\left(",
                r"\right)",
                r"\operatorname{f}",
            ]
        ),
        min_size=0,
        max_size=32,
    ).map("".join)
)
def test_pbt_formula_ast_parser_fail_closed_fuzz(fuzz: str) -> None:
    try:
        parsed = parse_formula(fuzz)
    except FormulaParseError:
        return
    assert ast_node_count(parsed.root) <= MAX_AST_NODES
    assert len(parsed.syntax_hash) == 64


# ── 3. symbol_contracts Domain Discharge Invariants ─────────────


@settings(max_examples=60, deadline=None)
@given(
    vars_list=st.lists(
        st.sampled_from(["a", "b", "c", "x", "y"]),
        min_size=1,
        max_size=3,
        unique=True,
    ),
    offset=st.integers(min_value=1, max_value=9),
)
def test_pbt_domain_discharge_soundness_positive_vs_real(
    vars_list: list[str],
    offset: int,
) -> None:
    pos_sum = " + ".join(vars_list) + f" + {offset}"
    parsed = parse_formula(rf"\frac{{1}}{{{pos_sum}}} + \log({pos_sum})")
    pos_reviews = [_review(v, domain="positive") for v in vars_list]
    pos_contracts = [_contract(v, domain="positive") for v in vars_list]
    obligations = infer_domain_obligations(pos_contracts, parsed)
    assert len(obligations) >= 2

    derived = discharge_domain_obligations_from_reviews(obligations, pos_reviews, scope="E1")
    assessment = assess_domain_obligations(obligations, derived)
    assert assessment.status == "discharged"

    # Under unconstrained real reviews, 1/v and log(v) MUST fail closed (never discharged).
    first = vars_list[0]
    unconstrained_parsed = parse_formula(rf"\frac{{1}}{{{first}}} + \log({first})")
    real_reviews = [_review(first, domain="real")]
    real_contracts = [_contract(first, domain="real")]
    unconstrained_obs = infer_domain_obligations(real_contracts, unconstrained_parsed)
    discharged = discharge_domain_obligations_from_reviews(
        unconstrained_obs, real_reviews, scope="E1"
    )
    assert discharged == []


# ── 4. lean_formalizer Five-Bin Audit Invariants ────────────────


@settings(max_examples=60, deadline=None)
@given(
    vars_pair=st.lists(st.sampled_from(_SAFE_VAR_NAMES[:8]), min_size=2, max_size=2, unique=True),
    domains=st.lists(
        st.sampled_from(["real", "positive", "non_negative", "integer"]),
        min_size=2,
        max_size=2,
    ),
    use_mul=st.booleans(),
)
def test_pbt_lean4_five_bin_audit_and_excess_binder_rejection(
    vars_pair: list[str],
    domains: list[str],
    use_mul: bool,
) -> None:
    v1, v2 = vars_pair
    d1, d2 = domains
    op = r"\cdot" if use_mul else "+"
    src = f"{_var_latex(v1)} {op} {_var_latex(v2)}"
    dst = f"{_var_latex(v2)} {op} {_var_latex(v1)}"
    reviews = [_review(v1, domain=d1), _review(v2, domain=d2)]

    exported = generate_lean4_export(
        source_formula=src,
        target_formula=dst,
        contract_reviews=reviews,
        source_equation_uuid="eq-pbt-1",
        scope="E1",
    )
    assert exported.status == "audited_draft"
    assert exported.lean_code is not None
    assert exported.audit is not None
    assert exported.audit.declaration_fidelity == "PASS"
    assert exported.audit.draft_allowlist == "PASS"
    assert exported.audit.proof_status == "FROZEN_UNPROVED"
    assert exported.audit.bin_counts["EXCESS"] == 0
    assert all(len(line) <= 100 for line in exported.lean_code.splitlines())

    # Mutation oracle: smuggling ANY extra hypothesis binder must fail closed as EXCESS.
    tampered = re.sub(
        r"(\n\s*:\s*)",
        "\n    (h_smuggled : (0 : ℝ) < smuggled_z)\\1",
        exported.lean_code,
        count=1,
    )
    assert tampered != exported.lean_code
    tampered_audit = audit_lean4_declaration(
        lean_code=tampered,
        source_formula=src,
        target_formula=dst,
        contract_reviews=reviews,
        scope="E1",
    )
    assert tampered_audit.declaration_fidelity == "FAIL"
    assert tampered_audit.bin_counts["EXCESS"] >= 1
    assert tampered_audit.proof_status == "AUDIT_FAILED"


# ── 5. CiPR Piecewise & Transcendental Domain PBT Invariants ───


@settings(max_examples=60, deadline=None)
@given(
    var=st.sampled_from(["a", "b", "x", "y", "z"]),
    c1=st.integers(min_value=1, max_value=9),
    c2=st.integers(min_value=1, max_value=9),
    op=st.sampled_from([">", "<", r"\geq", r"\leq", r"\neq"]),
    default_kw=st.sampled_from([r"\text{else}", r"\text{otherwise}"]),
)
def test_pbt_piecewise_cases_canonicalization_idempotence(
    var: str,
    c1: int,
    c2: int,
    op: str,
    default_kw: str,
) -> None:
    expr = (
        rf"f({var}) = \begin{{cases}} {c1} \cdot {var} & \text{{if }} {var} {op} 0 \\ "
        rf"{c2} & {default_kw} \end{{cases}}"
    )
    parsed = parse_formula(expr)
    canon_once = canonicalize(parsed.root)
    canon_twice = canonicalize(canon_once)
    assert canon_twice == canon_once
    assert compute_canonical_hash(canon_once) == parsed.syntax_hash
    assert parsed.root.children[1].kind == "piecewise"
    assert {"if", "else", "otherwise"}.isdisjoint(parsed.free_variables)


@settings(max_examples=50, deadline=None)
@given(
    vars_list=st.lists(
        st.sampled_from(["a", "b", "c", "s", "x"]),
        min_size=1,
        max_size=3,
        unique=True,
    ),
)
def test_pbt_exp_sum_domain_discharge_soundness(vars_list: list[str]) -> None:
    exp_sum = " + ".join(rf"\exp({v} / \tau)" for v in vars_list)
    parsed = parse_formula(rf"\log\frac{{\exp({vars_list[0]} / \tau)}}{{{exp_sum}}}")
    reviews = [_review(v, domain="real") for v in vars_list] + [_review("tau", domain="positive")]
    contracts = [_contract(v, domain="real") for v in vars_list] + [
        _contract("tau", domain="positive")
    ]
    obligations = infer_domain_obligations(contracts, parsed)
    assert obligations
    derived = discharge_domain_obligations_from_reviews(obligations, reviews, scope="E1")
    assert assess_domain_obligations(obligations, derived).status == "discharged"

    # Without positive tau, division by tau cannot be discharged (fail-closed).
    weak_reviews = [_review(v, domain="real") for v in vars_list] + [_review("tau", domain="real")]
    weak_derived = discharge_domain_obligations_from_reviews(obligations, weak_reviews, scope="E1")
    assert assess_domain_obligations(obligations, weak_derived).status != "discharged"
