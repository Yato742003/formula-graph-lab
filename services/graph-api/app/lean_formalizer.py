"""Deterministic Lean 4 / Mathlib statement generator and fail-closed five-bin auditor.

Implements the formalization and audit contracts from ``lean4`` and
``lean-statement-audit``:

1. Translates verified scalar equations and multi-step derivation chains
   (``E_0 -> E_1 -> ... -> E_k``) into Mathlib-idiomatic Lean 4 ``DRAFT_SORRY``
   theorem skeletons bound to the exact pinned source formula hash and human
   ``ContractReview`` records.
2. Independently reconstructs expected free variables, carrier types, domain
   premises, and target equality from the source/target ASTs and runs the
   fail-closed five-bin binder audit (``SOURCE``, ``STANDING``, ``TYPING``,
   ``RULED``, ``EXCESS``) plus draft allowlist and axiom checks.
3. Never promotes a ``DRAFT_SORRY`` or non-kernel-checked declaration to
   ``PROVED``; unverified drafts always report ``PROOF_STATUS: FROZEN_UNPROVED``.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from typing import Literal

from pydantic import BaseModel, Field

from app.formula_ast import _CONSTANTS, AstNode, FormulaParseError, parse_formula
from app.symbol_contracts import (
    ContractReview,
    DomainObligation,
    _parse_constraint_flags,
    latest_contract_reviews,
)

AuditBin = Literal["SOURCE", "STANDING", "TYPING", "RULED", "EXCESS"]
ProofStatus = Literal["FROZEN_UNPROVED", "PROVED", "AUDIT_FAILED"]

_ALLOWED_STANDARD_AXIOMS = frozenset({"propext", "Classical.choice", "Quot.sound"})
_GREEK_TO_LEAN = {
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "delta": "δ",
    "epsilon": "ε",
    "varepsilon": "ε",
    "theta": "θ",
    "lambda": "λ",
    "mu": "μ",
    "nu": "ν",
    "sigma": "σ",
    "tau": "τ",
    "phi": "φ",
    "varphi": "φ",
    "psi": "ψ",
    "omega": "ω",
    "Gamma": "Γ",
    "Delta": "Δ",
    "Theta": "Θ",
    "Lambda": "Λ",
    "Sigma": "Σ",
    "Phi": "Φ",
    "Psi": "Ψ",
    "Omega": "Ω",
}
_LEAN_KEYWORDS = frozenset(
    {
        "theorem",
        "lemma",
        "def",
        "by",
        "sorry",
        "admit",
        "axiom",
        "let",
        "in",
        "fun",
        "match",
        "with",
        "if",
        "then",
        "else",
        "where",
        "import",
        "open",
        "namespace",
        "end",
        "calc",
        "True",
        "False",
    }
)
_FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("admit", re.compile(r"\badmit\b")),
    ("axiom", re.compile(r"\baxiom\b")),
    ("native_decide", re.compile(r"\bnative_decide\b")),
    ("unsafe", re.compile(r"\bunsafe\b")),
    ("implemented_by", re.compile(r"\bimplemented_by\b")),
    ("sorryAx", re.compile(r"\bsorryAx\b")),
)


class LeanFormalizationError(ValueError):
    """Raised when an equation or contract cannot be faithfully translated to Lean 4."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class BinderAuditRow(BaseModel):
    name: str
    type_expr: str
    bin: AuditBin
    citation: str

    model_config = {"frozen": True, "extra": "forbid"}


class LeanStatementAuditReport(BaseModel):
    declaration_fidelity: Literal["PASS", "FAIL"]
    draft_allowlist: Literal["PASS", "FAIL"]
    proof_status: ProofStatus
    kernel_checked: bool = False
    sorry_count: int = Field(ge=0)
    forbidden_tokens: list[str] = Field(default_factory=list)
    unauthorized_axioms: list[str] = Field(default_factory=list)
    binder_table: list[BinderAuditRow] = Field(default_factory=list)
    bin_counts: dict[AuditBin, int] = Field(default_factory=dict)
    missing_binders: list[str] = Field(default_factory=list)
    carrier_mismatches: list[str] = Field(default_factory=list)
    conclusion_match: bool = False
    failure_reasons: list[str] = Field(default_factory=list)

    model_config = {"frozen": True, "extra": "forbid"}


class LeanFormalizationExport(BaseModel):
    status: Literal["audited_draft", "unsupported"]
    theorem_name: str | None = None
    lean_code: str | None = None
    imports: list[str] = Field(default_factory=list)
    audit: LeanStatementAuditReport | None = None
    error_code: str | None = None
    error_message: str | None = None

    model_config = {"frozen": True, "extra": "forbid"}


def _lean_ident(raw_name: str) -> str:
    """Map a LaTeX/AST symbol name deterministically to a safe Lean 4 identifier."""
    cleaned = raw_name.strip().lstrip("\\")
    if cleaned in _GREEK_TO_LEAN:
        return _GREEK_TO_LEAN[cleaned]
    if "_" in cleaned:
        base, *rest = cleaned.split("_")
        mapped_base = _GREEK_TO_LEAN.get(base, base)
        joined = "_".join([mapped_base, *rest])
    else:
        joined = cleaned
    sanitized = re.sub(r"[^A-Za-z0-9_α-ωΑ-Ω]", "_", joined)
    if not sanitized or sanitized[0].isdigit() or sanitized in _LEAN_KEYWORDS:
        sanitized = f"v_{sanitized}"
    return sanitized


def _carrier_for_review(review: ContractReview) -> tuple[str, set[str]]:
    """Map an accepted scalar/index ContractReview to its exact Mathlib carrier type."""
    contract = review.reviewed_contract
    if contract is None or review.decision != "accepted":
        raise LeanFormalizationError(
            "LEAN_UNREVIEWED_SYMBOL",
            f"Symbol '{review.symbol_name}' lacks an accepted human ContractReview.",
        )
    if contract.category not in {"scalar", "index"}:
        raise LeanFormalizationError(
            "LEAN_UNSUPPORTED_CARRIER",
            f"Lean 4 export currently supports scalar/index carriers; got {contract.category!r} "
            f"for '{review.symbol_name}'.",
        )
    if contract.domain == "integer":
        return "ℤ", {"Mathlib.Data.Int.Basic"}
    if contract.domain == "complex":
        return "ℂ", {"Mathlib.Data.Complex.Basic"}
    return "ℝ", {"Mathlib.Data.Real.Basic"}


def _premises_for_review(review: ContractReview) -> list[tuple[str, str, str]]:
    """Return ``(hyp_name, lean_prop, citation)`` premises implied strictly by a ContractReview."""
    contract = review.reviewed_contract
    if contract is None or review.decision != "accepted":
        return []
    if contract.category not in {"scalar", "index"}:
        return []
    ident = _lean_ident(review.symbol_name)
    flags = _parse_constraint_flags(review.symbol_name, contract.constraints)
    domain = contract.domain
    if "positive" in flags and domain in {"real", "non_negative"}:
        domain = "positive"
    elif "non_negative" in flags and domain == "real":
        domain = "non_negative"

    cite = f"review:{review.review_id}:{review.symbol_name}"
    premises: list[tuple[str, str, str]] = []
    if domain == "positive":
        premises.append((f"h_{ident}_pos", f"0 < {ident}", f"{cite}:positive"))
    elif domain == "non_negative":
        if "nonzero" in flags:
            premises.append((f"h_{ident}_pos", f"0 < {ident}", f"{cite}:positive"))
        else:
            premises.append((f"h_{ident}_nonneg", f"0 ≤ {ident}", f"{cite}:non_negative"))
    else:
        if "negative" in flags and domain in {"real", "integer"}:
            premises.append((f"h_{ident}_neg", f"{ident} < 0", f"{cite}:negative"))
        elif "nonzero" in flags:
            premises.append((f"h_{ident}_ne_zero", f"{ident} ≠ 0", f"{cite}:nonzero"))
    if "not_one" in flags:
        premises.append((f"h_{ident}_ne_one", f"{ident} ≠ 1", f"{cite}:not_one"))
    return premises


_BUILTIN_LEAN_FUNCS = frozenset({"sqrt", "log", "ln", "exp", "sin", "cos", "tan", "abs"})


def _collect_ast_symbols(node: AstNode) -> set[str]:
    """Collect free symbol identifiers (skipping builtin math function operators)."""
    names: set[str] = set()
    if node.kind == "symbol" and node.value:
        if node.value not in _CONSTANTS:
            names.add(node.value)
        return names
    if node.kind == "call" and node.children:
        fn_node = node.children[0]
        if fn_node.kind == "symbol" and fn_node.value in _BUILTIN_LEAN_FUNCS:
            for arg in node.children[1:]:
                names.update(_collect_ast_symbols(arg))
            return names
    if node.kind == "subscript" and len(node.children) == 2 and node.children[0].kind == "symbol":
        base = node.children[0].value
        idx = node.children[1]
        if base and idx.kind in {"symbol", "number"} and idx.value:
            names.add(f"{base}_{idx.value}")
            return names
    for child in node.children:
        names.update(_collect_ast_symbols(child))
    return names


def ast_to_lean4(node: AstNode) -> tuple[str, set[str]]:
    """Translate an ``AstNode`` deterministically to Lean 4 syntax + required Mathlib imports.

    Fails closed with ``LeanFormalizationError`` on unsupported constructs so no
    fallback value or off-source semantics can ever be synthesized.
    """
    imports: set[str] = set()

    def render(cur: AstNode, parent_prec: int = 0) -> str:
        if cur.kind == "number" and cur.value is not None:
            return cur.value

        if cur.kind == "symbol" and cur.value:
            if cur.value == "pi":
                imports.add("Mathlib.Analysis.SpecialFunctions.Trigonometric.Basic")
                return "Real.pi"
            if cur.value in {"infty", "infinity"}:
                raise LeanFormalizationError(
                    "LEAN_UNSUPPORTED_CONSTRUCT",
                    "Extended real infinity requires explicit EReal carrier.",
                )
            return _lean_ident(cur.value)

        if cur.kind == "subscript" and len(cur.children) == 2 and cur.children[0].kind == "symbol":
            base = cur.children[0].value
            idx = cur.children[1]
            if base and idx.kind in {"symbol", "number"} and idx.value:
                return _lean_ident(f"{base}_{idx.value}")
            raise LeanFormalizationError(
                "LEAN_UNSUPPORTED_CONSTRUCT",
                "Complex subscript expressions are not yet supported in Lean 4 export.",
            )

        if cur.kind == "style" and len(cur.children) == 1:
            return render(cur.children[0], parent_prec)

        if cur.kind == "negate" and len(cur.children) == 1:
            inner = render(cur.children[0], 35)
            expr = f"-{inner}"
            return f"({expr})" if parent_prec > 35 else expr

        if cur.kind == "add" and len(cur.children) >= 2:
            parts: list[str] = [render(cur.children[0], 10)]
            for child in cur.children[1:]:
                if child.kind == "negate" and len(child.children) == 1:
                    parts.append(f"- {render(child.children[0], 11)}")
                else:
                    parts.append(f"+ {render(child, 10)}")
            expr = " ".join(parts)
            return f"({expr})" if parent_prec > 10 else expr

        if cur.kind == "multiply" and len(cur.children) >= 2:
            expr = " * ".join(render(child, 20) for child in cur.children)
            return f"({expr})" if parent_prec > 20 else expr

        if cur.kind == "divide" and len(cur.children) == 2:
            left = render(cur.children[0], 20)
            right = render(cur.children[1], 21)
            expr = f"{left} / {right}"
            return f"({expr})" if parent_prec > 20 else expr

        if cur.kind == "power" and len(cur.children) == 2:
            if cur.attribute("operation") == "transpose":
                raise LeanFormalizationError(
                    "LEAN_UNSUPPORTED_CONSTRUCT",
                    "Matrix transpose requires confirmed matrix carrier formalization.",
                )
            base = render(cur.children[0], 31)
            exp_node = cur.children[1]
            if exp_node.kind == "number" and exp_node.value and exp_node.value.isdigit():
                expr = f"{base} ^ {exp_node.value}"
            else:
                imports.add("Mathlib.Analysis.SpecialFunctions.Pow.Real")
                exp_str = render(exp_node, 31)
                expr = f"{base} ^ ({exp_str})"
            return f"({expr})" if parent_prec > 30 else expr

        if cur.kind == "equals" and len(cur.children) == 2:
            left = render(cur.children[0], 5)
            right = render(cur.children[1], 5)
            expr = f"{left} = {right}"
            return f"({expr})" if parent_prec > 5 else expr

        if cur.kind == "call" and cur.children:
            fn = cur.children[0].value if cur.children[0].kind == "symbol" else None
            args = cur.children[1:]
            if fn == "sqrt" and len(args) == 1:
                imports.add("Mathlib.Data.Real.Sqrt")
                arg = render(args[0], 40)
                expr = f"Real.sqrt {arg}"
                return f"({expr})" if parent_prec >= 40 else expr
            if fn in {"log", "ln"} and len(args) == 1:
                imports.add("Mathlib.Analysis.SpecialFunctions.Log.Basic")
                arg = render(args[0], 40)
                expr = f"Real.log {arg}"
                return f"({expr})" if parent_prec >= 40 else expr
            if fn == "exp" and len(args) == 1:
                imports.add("Mathlib.Analysis.SpecialFunctions.ExpDeriv")
                arg = render(args[0], 40)
                expr = f"Real.exp {arg}"
                return f"({expr})" if parent_prec >= 40 else expr
            if fn in {"sin", "cos", "tan"} and len(args) == 1:
                imports.add("Mathlib.Analysis.SpecialFunctions.Trigonometric.Basic")
                arg = render(args[0], 40)
                expr = f"Real.{fn} {arg}"
                return f"({expr})" if parent_prec >= 40 else expr
            if fn == "abs" and len(args) == 1:
                arg = render(args[0], 0)
                return f"|{arg}|"
            raise LeanFormalizationError(
                "LEAN_UNSUPPORTED_CONSTRUCT",
                f"Function call '{fn}' cannot be translated without an audited Lean definition.",
            )

        raise LeanFormalizationError(
            "LEAN_UNSUPPORTED_CONSTRUCT",
            f"AST node kind {cur.kind!r} is not supported for fail-closed Lean 4 export.",
        )

    return render(node), imports


def _format_binder_lines(binders: list[str], indent: str = "    ", width: int = 100) -> list[str]:
    """Pack Lean binders cleanly respecting Mathlib's 100-character line width."""
    lines: list[str] = []
    current = indent
    for binder in binders:
        sep = "" if current == indent else " "
        if len(current) + len(sep) + len(binder) <= width or current == indent:
            current = f"{current}{sep}{binder}"
        else:
            lines.append(current)
            current = f"{indent}{binder}"
    if current != indent:
        lines.append(current)
    return lines


def generate_lean4_export(
    *,
    source_formula: str,
    target_formula: str,
    derivation_steps: list[str] | None = None,
    contract_reviews: Iterable[ContractReview] = (),
    domain_obligations: Iterable[DomainObligation] = (),
    source_equation_uuid: str = "unknown",
    contract_review_hash: str | None = None,
    scope: str | None = None,
) -> LeanFormalizationExport:
    """Generate a pinned Lean 4 ``DRAFT_SORRY`` theorem and run the independent five-bin audit."""
    try:
        parsed_source = parse_formula(source_formula)
        parsed_target = parse_formula(target_formula)
        chain_formulas = derivation_steps if derivation_steps else [target_formula]
        parsed_chain = [parsed_source, *(parse_formula(step) for step in chain_formulas)]

        accepted_by_sym = {
            review.symbol_name: review
            for review in latest_contract_reviews(list(contract_reviews))
            if review.decision == "accepted"
            and review.reviewed_contract is not None
            and (scope is None or review.scope == scope)
        }

        all_symbols: set[str] = set()
        for parsed in parsed_chain:
            all_symbols.update(_collect_ast_symbols(parsed.root))

        imports: set[str] = set()
        binders: list[str] = []
        for sym in sorted(all_symbols):
            if sym not in accepted_by_sym:
                raise LeanFormalizationError(
                    "LEAN_UNREVIEWED_SYMBOL",
                    f"Symbol '{sym}' must have an accepted human ContractReview for Lean export.",
                )
            review = accepted_by_sym[sym]
            carrier, carrier_imports = _carrier_for_review(review)
            imports.update(carrier_imports)
            binders.append(f"({_lean_ident(sym)} : {carrier})")
            for hyp_name, prop, _cite in _premises_for_review(review):
                binders.append(f"({hyp_name} : {prop})")

        rendered_steps: list[str] = []
        for parsed in parsed_chain:
            expr_str, expr_imports = ast_to_lean4(parsed.root)
            imports.update(expr_imports)
            rendered_steps.append(expr_str)

        lhs_str = rendered_steps[0]
        rhs_str = rendered_steps[-1]
        if parsed_source.root.kind == "equals" and parsed_target.root.kind == "equals":
            conclusion = f"({lhs_str}) ↔ ({rhs_str})"
        elif parsed_source.root.kind == "equals" or parsed_target.root.kind == "equals":
            raise LeanFormalizationError(
                "LEAN_MISMATCHED_STATEMENT_KIND",
                "Cannot equate a proposition (`=`) with a bare expression in Lean 4.",
            )
        else:
            conclusion = f"{lhs_str} = {rhs_str}"

        if not imports:
            imports.add("Mathlib.Data.Real.Basic")
        sorted_imports = sorted(imports)

        src_sha = hashlib.sha256(source_formula.encode("utf-8")).hexdigest()
        stmt_sha = hashlib.sha256(
            f"{source_formula}\0{target_formula}\0{contract_review_hash or ''}".encode()
        ).hexdigest()
        prefix = "fgl_chain" if len(rendered_steps) > 2 else "fgl_equiv"
        theorem_name = f"{prefix}_{stmt_sha[:10]}"

        lines: list[str] = [f"import {mod}" for mod in sorted_imports]
        lines.append("")
        lines.append("/-- Auto-generated FormulaGraph Lab draft theorem (`DRAFT_SORRY`).")
        lines.append(f"    source_equation_uuid: {source_equation_uuid}")
        lines.append(f"    source_latex_sha256: {src_sha}")
        if contract_review_hash:
            lines.append(f"    contract_review_hash: {contract_review_hash}")
        lines.append("    scope: ad_hoc_check_not_frozen_campaign")
        lines.append("    proof_status: FROZEN_UNPROVED (kernel verification not executed) -/")

        if binders:
            lines.append(f"theorem {theorem_name}")
            lines.extend(_format_binder_lines(binders))
            lines.append(f"    : {conclusion} := by")
        else:
            header = f"theorem {theorem_name} : {conclusion} := by"
            if len(header) <= 100:
                lines.append(header)
            else:
                lines.append(f"theorem {theorem_name} :")
                lines.append(f"    {conclusion} := by")

        if len(rendered_steps) > 2 and parsed_source.root.kind != "equals":
            lines.append("  calc")
            lines.append(f"    {rendered_steps[0]} = {rendered_steps[1]} := by sorry")
            for step_expr in rendered_steps[2:]:
                lines.append(f"    _ = {step_expr} := by sorry")
        else:
            lines.append("  sorry")

        lean_code = "\n".join(lines) + "\n"
        audit = audit_lean4_declaration(
            lean_code=lean_code,
            source_formula=source_formula,
            target_formula=target_formula,
            contract_reviews=list(contract_reviews),
            domain_obligations=list(domain_obligations),
            scope=scope,
            kernel_checked=False,
        )
        return LeanFormalizationExport(
            status="audited_draft",
            theorem_name=theorem_name,
            lean_code=lean_code,
            imports=sorted_imports,
            audit=audit,
        )
    except (FormulaParseError, LeanFormalizationError) as exc:
        code = getattr(exc, "code", "LEAN_FORMALIZATION_FAILED")
        return LeanFormalizationExport(
            status="unsupported",
            error_code=code,
            error_message=str(exc),
        )


def _strip_lean_comments(code: str) -> str:
    """Strip Lean block (`/- ... -/`) and line (`-- ...`) comments cleanly."""
    without_block = re.sub(r"/-.*?-/", "", code, flags=re.S)
    return re.sub(r"--[^\n]*", "", without_block)


def _normalize_lean_expr(expr: str) -> str:
    collapsed = " ".join(expr.strip().split())
    while collapsed.startswith("(") and collapsed.endswith(")"):
        depth = 0
        balanced = True
        for idx, ch in enumerate(collapsed):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0 and idx < len(collapsed) - 1:
                    balanced = False
                    break
        if balanced and depth == 0:
            collapsed = collapsed[1:-1].strip()
        else:
            break
    return collapsed


def _extract_declaration_parts(code_no_comments: str) -> tuple[str, str, str, str]:
    """Parse ``theorem <name> <binders> : <conclusion> := <body>`` strictly."""
    match = re.search(r"\b(?:theorem|lemma)\s+([A-Za-z0-9_']+)\b", code_no_comments)
    if not match:
        raise ValueError("No theorem/lemma declaration found.")
    theorem_name = match.group(1)
    rest = code_no_comments[match.end() :]
    assign_idx = rest.find(":=")
    if assign_idx == -1:
        raise ValueError("Theorem declaration is missing ':=' proof separator.")
    signature = rest[:assign_idx].strip()
    proof_body = rest[assign_idx + 2 :].strip()

    # Locate top-level ':' separating binders from the conclusion type.
    depth = 0
    colon_idx = -1
    for idx, ch in enumerate(signature):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif ch == ":" and depth == 0:
            colon_idx = idx
            break
    if colon_idx == -1:
        raise ValueError("Theorem signature is missing top-level ':' type separator.")
    binder_section = signature[:colon_idx].strip()
    conclusion = signature[colon_idx + 1 :].strip()
    return theorem_name, binder_section, conclusion, proof_body


def _parse_binders(binder_section: str) -> list[tuple[str, str]]:
    """Extract ``(name, type_expr)`` pairs from explicit/implicit/instance Lean binders."""
    binders: list[tuple[str, str]] = []
    i = 0
    n = len(binder_section)
    open_to_close = {"(": ")", "{": "}", "[": "]"}
    while i < n:
        ch = binder_section[i]
        if ch not in open_to_close:
            i += 1
            continue
        close_ch = open_to_close[ch]
        depth = 0
        start = i + 1
        j = i
        while j < n:
            if binder_section[j] == ch:
                depth += 1
            elif binder_section[j] == close_ch:
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if depth != 0:
            raise ValueError("Unbalanced binder delimiters in Lean theorem signature.")
        inner = binder_section[start:j].strip()
        if ":" in inner:
            names_part, type_part = inner.split(":", 1)
            norm_type = _normalize_lean_expr(type_part)
            for name in names_part.strip().split():
                binders.append((name.strip(), norm_type))
        elif inner:
            binders.append(("_inst", _normalize_lean_expr(inner)))
        i = j + 1
    return binders


def audit_lean4_declaration(
    *,
    lean_code: str,
    source_formula: str,
    target_formula: str,
    contract_reviews: Iterable[ContractReview] = (),
    domain_obligations: Iterable[DomainObligation] = (),
    standing_assumptions: Mapping[str, str] | None = None,
    author_rulings: Mapping[str, str] | None = None,
    scope: str | None = None,
    kernel_checked: bool = False,
    kernel_axioms: Iterable[str] | None = None,
) -> LeanStatementAuditReport:
    """Independently audit a Lean 4 theorem against its pinned source and reviewed contracts.

    Follows the fail-closed ``lean-statement-audit`` protocol:
    - Any ``EXCESS`` binder fails ``DECLARATION_FIDELITY``.
    - Any forbidden escape hatch (``admit``, ``axiom``, ``native_decide``, ``unsafe``,
      ``sorryAx``) or header ``sorry`` fails ``DRAFT_ALLOWLIST``.
    - Any ``sorry`` or unexecuted kernel check keeps ``PROOF_STATUS = FROZEN_UNPROVED``.
    """
    _ = domain_obligations  # Reserved for future obligation-level proof-term composition audits.
    failure_reasons: list[str] = []
    bin_counts: dict[AuditBin, int] = {
        "SOURCE": 0,
        "STANDING": 0,
        "TYPING": 0,
        "RULED": 0,
        "EXCESS": 0,
    }

    code_clean = _strip_lean_comments(lean_code)
    forbidden_tokens = [
        label for label, pattern in _FORBIDDEN_PATTERNS if pattern.search(code_clean)
    ]

    try:
        _name, binder_section, actual_conclusion, proof_body = _extract_declaration_parts(
            code_clean
        )
        parsed_binders = _parse_binders(binder_section)
    except ValueError as exc:
        failure_reasons.append(str(exc))
        return LeanStatementAuditReport(
            declaration_fidelity="FAIL",
            draft_allowlist="FAIL" if forbidden_tokens else "PASS",
            proof_status="AUDIT_FAILED",
            kernel_checked=False,
            sorry_count=len(re.findall(r"\bsorry\b", code_clean)),
            forbidden_tokens=forbidden_tokens,
            bin_counts=bin_counts,
            failure_reasons=failure_reasons,
        )

    header_has_sorry = bool(re.search(r"\bsorry\b", f"{binder_section} : {actual_conclusion}"))
    if header_has_sorry:
        forbidden_tokens.append("header_sorry")
    sorry_count = len(re.findall(r"\bsorry\b", proof_body))

    # Reconstruct expected mathematics independently from source/target formulas + reviews.
    parsed_source = parse_formula(source_formula)
    parsed_target = parse_formula(target_formula)
    expected_symbols = _collect_ast_symbols(parsed_source.root) | _collect_ast_symbols(
        parsed_target.root
    )
    accepted_by_sym = {
        review.symbol_name: review
        for review in latest_contract_reviews(list(contract_reviews))
        if review.decision == "accepted"
        and review.reviewed_contract is not None
        and (scope is None or review.scope == scope)
    }

    expected_carriers: dict[str, tuple[str, str]] = {}
    expected_premises: dict[str, str] = {}
    for sym in sorted(expected_symbols):
        review = accepted_by_sym.get(sym)
        if review is None:
            failure_reasons.append(f"Unreviewed symbol '{sym}' cannot be audited as faithful.")
            continue
        carrier, _ = _carrier_for_review(review)
        expected_carriers[_lean_ident(sym)] = (carrier, f"review:{review.review_id}:{sym}")
        for _hname, prop, citation in _premises_for_review(review):
            expected_premises[_normalize_lean_expr(prop)] = citation

    standing_norm = {
        _normalize_lean_expr(prop): cite for prop, cite in (standing_assumptions or {}).items()
    }
    rulings_norm = {
        _normalize_lean_expr(prop): cite for prop, cite in (author_rulings or {}).items()
    }

    # Five-bin binder classification.
    binder_table: list[BinderAuditRow] = []
    seen_carriers: set[str] = set()
    seen_premises: set[str] = set()
    carrier_mismatches: list[str] = []

    for b_name, b_type in parsed_binders:
        if b_name in expected_carriers:
            expected_carrier, cite = expected_carriers[b_name]
            if b_type == expected_carrier and b_name not in seen_carriers:
                seen_carriers.add(b_name)
                bin_name: AuditBin = "TYPING"
                citation = cite
            else:
                bin_name = "EXCESS"
                citation = f"carrier_mismatch:expected_{expected_carrier}_got_{b_type}"
                carrier_mismatches.append(f"{b_name}: expected {expected_carrier}, got {b_type}")
        elif b_type in expected_premises and b_type not in seen_premises:
            seen_premises.add(b_type)
            bin_name = "SOURCE"
            citation = expected_premises[b_type]
        elif b_type in standing_norm:
            bin_name = "STANDING"
            citation = standing_norm[b_type]
        elif b_type in rulings_norm:
            bin_name = "RULED"
            citation = rulings_norm[b_type]
        else:
            bin_name = "EXCESS"
            citation = "unjustified_extra_binder_or_premise"

        bin_counts[bin_name] += 1
        binder_table.append(
            BinderAuditRow(name=b_name, type_expr=b_type, bin=bin_name, citation=citation)
        )

    missing_binders: list[str] = []
    for sym_ident, (carrier, _cite) in sorted(expected_carriers.items()):
        if sym_ident not in seen_carriers:
            missing_binders.append(f"({sym_ident} : {carrier})")
    for prop in sorted(expected_premises):
        if prop not in seen_premises:
            missing_binders.append(f"({prop})")

    # Independent conclusion reconstruction.
    lhs_lean, _ = ast_to_lean4(parsed_source.root)
    rhs_lean, _ = ast_to_lean4(parsed_target.root)
    if parsed_source.root.kind == "equals" and parsed_target.root.kind == "equals":
        expected_conclusion = _normalize_lean_expr(f"({lhs_lean}) ↔ ({rhs_lean})")
    else:
        expected_conclusion = _normalize_lean_expr(f"{lhs_lean} = {rhs_lean}")
    actual_norm = _normalize_lean_expr(actual_conclusion)
    conclusion_match = actual_norm == expected_conclusion
    if not conclusion_match:
        failure_reasons.append(
            f"Conclusion mismatch: expected '{expected_conclusion}', got '{actual_norm}'."
        )

    if bin_counts["EXCESS"] > 0:
        failure_reasons.append(
            f"Five-bin audit failed: {bin_counts['EXCESS']} EXCESS binder(s) detected."
        )
    if missing_binders:
        failure_reasons.append(f"Missing required source binder(s): {', '.join(missing_binders)}.")
    if carrier_mismatches:
        failure_reasons.append(f"Carrier mismatch(es): {', '.join(carrier_mismatches)}.")
    if forbidden_tokens:
        joined_tokens = ", ".join(sorted(set(forbidden_tokens)))
        failure_reasons.append(f"Draft allowlist failed: forbidden token(s) {joined_tokens}.")

    unauthorized_axioms = sorted(
        ax for ax in (kernel_axioms or []) if ax not in _ALLOWED_STANDARD_AXIOMS
    )
    if unauthorized_axioms:
        failure_reasons.append(
            f"Unauthorized axiom(s): {', '.join(unauthorized_axioms)}."
        )

    declaration_fidelity: Literal["PASS", "FAIL"] = (
        "PASS"
        if (
            bin_counts["EXCESS"] == 0
            and not missing_binders
            and not carrier_mismatches
            and conclusion_match
            and not any(r.startswith("Unreviewed symbol") for r in failure_reasons)
        )
        else "FAIL"
    )
    draft_allowlist: Literal["PASS", "FAIL"] = (
        "PASS" if not forbidden_tokens and not unauthorized_axioms else "FAIL"
    )

    if declaration_fidelity == "FAIL" or draft_allowlist == "FAIL":
        proof_status: ProofStatus = "AUDIT_FAILED"
    elif kernel_checked and sorry_count == 0 and not unauthorized_axioms:
        proof_status = "PROVED"
    else:
        proof_status = "FROZEN_UNPROVED"

    return LeanStatementAuditReport(
        declaration_fidelity=declaration_fidelity,
        draft_allowlist=draft_allowlist,
        proof_status=proof_status,
        kernel_checked=kernel_checked and proof_status == "PROVED",
        sorry_count=sorry_count,
        forbidden_tokens=sorted(set(forbidden_tokens)),
        unauthorized_axioms=unauthorized_axioms,
        binder_table=binder_table,
        bin_counts=bin_counts,
        missing_binders=missing_binders,
        carrier_mismatches=carrier_mismatches,
        conclusion_match=conclusion_match,
        failure_reasons=failure_reasons,
    )
