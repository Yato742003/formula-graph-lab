"""Standard 3: LaCASt-style Context-Aware Mathematical Disambiguator (TACAS 2022).

Deterministically extracts mathematical types, dimensions, domain bounds, and
semantic roles from prose context (``preceding_text``, ``following_text``, inline
LaTeX/MathML spans) and combines them with Operator Tree (OPT / ``AstNode``) MEOM
metadata.

Epistemic invariant:
All hints produced here carry ``source='lacast_prose_context'`` and
``status='inferred_draft'``. Exploratory reviews synthesized for Dual-Track
auto-checking carry ``reviewer_id='system:auto-inferred-exploratory'`` and must
never be persisted as human-certified ``ContractReview`` or ``CheckResult``
authority records.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from app.formula_ast import _CONSTANTS, AstNode, ParsedFormula, ParsedSymbol
from app.symbol_contracts import (
    ContractReview,
    InferredCategory,
    ReviewedContractValue,
    SymbolCategory,
    SymbolContract,
    SymbolDomain,
    infer_contracts,
)

DISAMBIGUATOR_VERSION = "lacast-context.v1"
_AUTO_INFERRED_TIME = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
_BUILTIN_MATH_FUNCTIONS = frozenset(
    {
        "sqrt",
        "log",
        "ln",
        "exp",
        "sin",
        "cos",
        "tan",
        "sinh",
        "cosh",
        "tanh",
        "abs",
        "floor",
        "ceil",
        "round",
    }
)

_UNICODE_GREEK = {
    "α": "alpha",
    "β": "beta",
    "γ": "gamma",
    "δ": "delta",
    "Δ": "Delta",
    "ε": "epsilon",
    "ϵ": "epsilon",
    "ζ": "zeta",
    "η": "eta",
    "θ": "theta",
    "Θ": "Theta",
    "ι": "iota",
    "κ": "kappa",
    "λ": "lambda",
    "Λ": "Lambda",
    "μ": "mu",
    "ν": "nu",
    "ξ": "xi",
    "Ξ": "Xi",
    "π": "pi",
    "Π": "Pi",
    "ρ": "rho",
    "σ": "sigma",
    "Σ": "Sigma",
    "τ": "tau",
    "υ": "upsilon",
    "ϕ": "phi",
    "φ": "phi",
    "Φ": "Phi",
    "χ": "chi",
    "ψ": "psi",
    "Ψ": "Psi",
    "ω": "omega",
    "Ω": "Omega",
    "ℓ": "ell",
}

_UNICODE_SYMBOLS = {
    "ℝ": "\\mathbb{R}",
    "ℤ": "\\mathbb{Z}",
    "ℕ": "\\mathbb{N}",
    "ℂ": "\\mathbb{C}",
    "∈": " \\in ",
    "∉": " \\notin ",
    "≥": " >= ",
    "≤": " <= ",
    "≠": " != ",
    "×": " x ",
    "·": " * ",
    "−": "-",
    "\u2061": "",
    "\u2062": "",
    "\u2063": "",
}

_CLAUSE_SPLIT_RE = re.compile(r"[.,;]|\b(?:and|while|where|whereas)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ContextSymbolHint:
    """Auditable draft symbol contract extracted from surrounding prose & OPT context."""

    symbol: str
    category: InferredCategory | None = None
    domain: SymbolDomain | None = None
    shape: tuple[int | str, ...] | None = None
    constraints: tuple[str, ...] = ()
    semantic_role: str | None = None
    w3c_intent: str | None = None
    evidence_span: str = ""
    confidence: float = 0.75
    source: str = "lacast_prose_context"
    status: str = "inferred_draft"

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "category": self.category,
            "domain": self.domain,
            "shape": list(self.shape) if self.shape is not None else None,
            "constraints": list(self.constraints),
            "semantic_role": self.semantic_role,
            "w3c_intent": self.w3c_intent,
            "evidence_span": self.evidence_span,
            "confidence": self.confidence,
            "source": self.source,
            "status": self.status,
        }


@dataclass(frozen=True)
class ContextDisambiguationResult:
    """Structured output of LaCASt-style prose disambiguation + MEOM extraction."""

    disambiguator_version: str
    hints: dict[str, ContextSymbolHint]
    meom_operators: list[dict[str, object]]

    def to_dict(self) -> dict[str, object]:
        return {
            "disambiguator_version": self.disambiguator_version,
            "hints": {k: self.hints[k].to_dict() for k in sorted(self.hints)},
            "meom_operators": list(self.meom_operators),
        }


def _normalize_prose_for_matching(text: str) -> str:
    """Normalize LaTeXML plain+LaTeX text spans into uniform ASCII/LaTeX-friendly text."""
    out: list[str] = []
    for ch in text:
        if ch in _UNICODE_GREEK:
            out.append(f" {_UNICODE_GREEK[ch]} ")
        elif ch in _UNICODE_SYMBOLS:
            out.append(_UNICODE_SYMBOLS[ch])
        else:
            out.append(ch)
    norm = "".join(out)
    norm = re.sub(
        r"\\(?:mathbf|boldsymbol|bm|mathrm|mathcal|vec|hat|widehat|tilde|widetilde|bar)\{([^{}]+)\}",
        r"\1",
        norm,
    )
    norm = re.sub(r"\\([A-Za-z]+)_\{([A-Za-z0-9]+)\}", r"\1_\2", norm)
    norm = re.sub(r"\b([A-Za-z]+)_\{([A-Za-z0-9]+)\}", r"\1_\2", norm)
    norm = re.sub(r"\\([A-Za-z]+)", lambda m: f"\\{m.group(1)}", norm)
    norm = norm.replace("$", " ")
    return re.sub(r"\s+", " ", norm).strip()


def _collect_target_symbols(
    parsed: ParsedFormula,
) -> tuple[dict[str, ParsedSymbol], dict[str, str]]:
    """Map every symbol token (including joined subscripts like ``d_k``) to ParsedSymbol."""
    by_name: dict[str, ParsedSymbol] = {sym.name: sym for sym in parsed.symbols}
    alias_to_canonical: dict[str, str] = {sym.name: sym.name for sym in parsed.symbols}

    def visit(node: AstNode) -> None:
        if node.kind == "subscript" and len(node.children) == 2:
            base = node.children[0]
            while base.kind == "style" and len(base.children) == 1:
                base = base.children[0]
            idx = node.children[1]
            while idx.kind == "style" and len(idx.children) == 1:
                idx = idx.children[0]
            if (
                base.kind == "symbol"
                and base.value
                and idx.kind in {"symbol", "number"}
                and idx.value
            ):
                joined = f"{base.value}_{idx.value}"
                canonical = base.value if base.value in by_name else joined
                alias_to_canonical.setdefault(joined, canonical)
                if joined not in by_name and base.value in by_name:
                    by_name[joined] = by_name[base.value]
        for child in node.children:
            visit(child)

    visit(parsed.root)
    return by_name, alias_to_canonical


def _extract_meom_operators(node: AstNode) -> list[dict[str, object]]:
    """Extract Mathematical Expression & Operator Metadata (bound ranges on binders)."""
    operators: list[dict[str, object]] = []

    def render_short(el: AstNode | None) -> str | None:
        if el is None:
            return None
        if el.kind == "number":
            return el.value
        if el.kind == "symbol":
            return el.value
        if el.kind == "style" and len(el.children) == 1:
            return render_short(el.children[0])
        if el.kind == "equals" and len(el.children) == 2:
            return f"{render_short(el.children[0])}={render_short(el.children[1])}"
        if el.kind == "relation" and el.value and len(el.children) == 2:
            return f"{render_short(el.children[0])} {el.value} {render_short(el.children[1])}"
        return el.kind

    def walk(el: AstNode, path: str) -> None:
        if el.kind in {"sum", "product", "integral"} and el.children:
            binder = el.attribute("binder")
            lower = el.children[0] if len(el.children) >= 2 else None
            upper = el.children[1] if len(el.children) >= 3 else None
            operators.append(
                {
                    "operator": el.kind,
                    "binder": binder,
                    "lower": render_short(lower),
                    "upper": render_short(upper),
                    "has_explicit_bounds": len(el.children) >= 3,
                    "location": path,
                }
            )
        for idx, child in enumerate(el.children):
            walk(child, f"{path}.{el.kind}[{idx}]")

    walk(node, "root")
    return operators


def _symbol_match_regex(sym: str) -> re.Pattern[str]:
    """Build a prose regex matching LaTeX ``\\Delta_{g}``, plain ``Delta g``, or plain ``d_k``."""
    if "_" in sym:
        base, sub = sym.split("_", 1)
        base_esc = re.escape(base)
        sub_esc = re.escape(sub)
        return re.compile(
            rf"(?:\\?{base_esc}\s*(?:_\{{\s*{sub_esc}\s*\}}|_\s*{sub_esc}|\s+{sub_esc})\b)"
        )
    esc = re.escape(sym)
    return re.compile(rf"(?:\\?{esc})(?![A-Za-z0-9_])")


def _parse_dim_tokens(raw_dims: str) -> tuple[int | str, ...]:
    cleaned = raw_dims.strip()
    if cleaned.startswith("{") and cleaned.endswith("}"):
        cleaned = cleaned[1:-1].strip()
    cleaned = re.sub(r"\\times\b", " x ", cleaned)
    parts = [
        p.strip(" {}()")
        for p in re.split(r"\s+(?:x|\\times)\s+|\*", cleaned)
        if p.strip(" {}()")
    ]
    return tuple(int(part) if part.isdigit() else part for part in parts)


def _containing_clause(text: str, start: int, end: int) -> tuple[str, int]:
    """Return the immediate grammatical clause surrounding ``[start, end)`` and local offset."""
    left_bound = max(0, start - 96)
    for boundary in _CLAUSE_SPLIT_RE.finditer(text, left_bound, start):
        left_bound = boundary.end()
    right_bound = min(len(text), end + 96)
    nxt = _CLAUSE_SPLIT_RE.search(text, end, right_bound)
    if nxt is not None:
        right_bound = nxt.start()
    clause = text[left_bound:right_bound].strip()
    return clause, max(0, start - left_bound)


def _merge_hint(
    hints: dict[str, ContextSymbolHint],
    sym: str,
    *,
    category: InferredCategory | None = None,
    domain: SymbolDomain | None = None,
    shape: tuple[int | str, ...] | None = None,
    constraints: Sequence[str] = (),
    semantic_role: str | None = None,
    w3c_intent: str | None = None,
    evidence_span: str = "",
    confidence: float = 0.75,
) -> None:
    existing = hints.get(sym)
    if existing is None:
        hints[sym] = ContextSymbolHint(
            symbol=sym,
            category=category,
            domain=domain,
            shape=shape,
            constraints=tuple(dict.fromkeys(constraints)),
            semantic_role=semantic_role,
            w3c_intent=w3c_intent,
            evidence_span=evidence_span[:180],
            confidence=confidence,
        )
        return

    merged_constraints = tuple(dict.fromkeys([*existing.constraints, *constraints]))
    domain_priority = {"positive": 4, "non_negative": 3, "integer": 2, "complex": 2, "real": 1}
    chosen_domain = existing.domain
    if domain is not None and (
        chosen_domain is None
        or domain_priority.get(domain, 0) > domain_priority.get(chosen_domain, 0)
    ):
        chosen_domain = domain

    has_known_shape = shape is not None and (existing.shape is None or "?" in existing.shape)
    chosen_shape = shape if has_known_shape else existing.shape
    chosen_cat = category if existing.category in {None, "unknown"} else existing.category
    chosen_conf = max(existing.confidence, confidence)
    hints[sym] = ContextSymbolHint(
        symbol=sym,
        category=chosen_cat,
        domain=chosen_domain,
        shape=chosen_shape,
        constraints=merged_constraints,
        semantic_role=existing.semantic_role or semantic_role,
        w3c_intent=existing.w3c_intent or w3c_intent,
        evidence_span=existing.evidence_span or evidence_span[:180],
        confidence=chosen_conf,
    )


_PROSE_ROLE_PATTERNS: tuple[
    tuple[re.Pattern[str], InferredCategory | None, SymbolDomain, str, str, bool],
    ...,
] = (
    (
        re.compile(
            r"\b(?:quantization step|step size|temperature|standard deviation|variance|"
            r"learning rate|scale factor|scaling factor|bandwidth|positive scalar)\b",
            re.IGNORECASE,
        ),
        "scalar",
        "positive",
        "positive_hyperparameter",
        "positive-scalar",
        True,
    ),
    (
        re.compile(
            r"\b(?:dimension|hidden size|model size|sequence length|context length|"
            r"batch size|vocab(?:ulary)? size|number of)\b",
            re.IGNORECASE,
        ),
        "scalar",
        "positive",
        "dimension_size",
        "dimension",
        True,
    ),
    (
        re.compile(
            r"\b(?:weight matrix|projection matrix|attention matrix|"
            r"covariance matrix|matrix|matrices)\b",
            re.IGNORECASE,
        ),
        "matrix",
        "real",
        "matrix_operator",
        "matrix",
        False,
    ),
    (
        re.compile(
            r"\b(?:feature vector|latent vector|embedding vector|column vector|"
            r"row vector|vector|embeddings?|latents?)\b",
            re.IGNORECASE,
        ),
        "vector",
        "real",
        "vector_representation",
        "vector",
        False,
    ),
    (
        re.compile(
            r"\b(?:integer residual|integer index|integer count|integer)\b",
            re.IGNORECASE,
        ),
        "scalar",
        "integer",
        "integer_value",
        "integer",
        False,
    ),
    (
        re.compile(
            r"\b(?:probability|coding cost|cross[- ]entropy|entropy|"
            r"kl divergence|non-negative)\b",
            re.IGNORECASE,
        ),
        None,
        "non_negative",
        "probabilistic_quantity",
        "nonnegative-quantity",
        False,
    ),
    (
        re.compile(
            r"\b(?:scalar variable|scalar|predicted mean|mean|bias|offset|threshold)\b",
            re.IGNORECASE,
        ),
        "scalar",
        "real",
        "scalar_parameter",
        "scalar",
        False,
    ),
)


def disambiguate_equation_context(
    parsed: ParsedFormula,
    *,
    preceding_text: str | None = None,
    following_text: str | None = None,
    equation_latex: str = "",
) -> ContextDisambiguationResult:
    """Infer auditable draft symbol hints from prose context and OPT MEOM operators."""
    targets_by_name, alias_to_canonical = _collect_target_symbols(parsed)
    meom_operators = _extract_meom_operators(parsed.root)
    hints: dict[str, ContextSymbolHint] = {}

    combined_raw = " ".join(part for part in (preceding_text, following_text) if part).strip()
    norm_text = _normalize_prose_for_matching(combined_raw) if combined_raw else ""

    for contract in infer_contracts(parsed):
        if (
            contract.shape is not None
            and "?" not in contract.shape
            and (contract.domain != "real" or contract.shape != ())
        ):
            _merge_hint(
                hints,
                contract.name,
                category=contract.category,
                domain=contract.domain,
                shape=contract.shape,
                constraints=contract.constraints,
                semantic_role="inline_domain_clause",
                evidence_span=equation_latex[:120],
                confidence=0.92,
            )

    if not norm_text:
        return ContextDisambiguationResult(
            disambiguator_version=DISAMBIGUATOR_VERSION,
            hints=hints,
            meom_operators=meom_operators,
        )

    candidate_symbols = sorted(
        (
            name
            for name in targets_by_name
            if name not in _CONSTANTS and name not in _BUILTIN_MATH_FUNCTIONS
        ),
        key=lambda item: (-len(item), item),
    )

    for sym in candidate_symbols:
        sym_obj = targets_by_name[sym]
        if sym_obj.category == "distribution":
            continue
        matches = list(_symbol_match_regex(sym).finditer(norm_text))
        if not matches:
            continue

        target_keys = [sym]
        canonical = alias_to_canonical.get(sym)
        if (
            canonical
            and canonical != sym
            and canonical in targets_by_name
            and not any(
                other.startswith(f"{canonical}_") and other != sym
                for other in candidate_symbols
            )
        ):
            target_keys.append(canonical)

        for match in matches:
            clause, rel_pos = _containing_clause(norm_text, match.start(), match.end())
            after_window = norm_text[match.end() : min(len(norm_text), match.end() + 48)]
            before_window = norm_text[max(0, match.start() - 56) : match.start()]

            set_match = re.match(
                r"\s*\\in\s*\\mathbb\{([RZNC])\}"
                r"(?:_\{?(>0|\+|>=0|\\ge0)\}?)?"
                r"(?:\^(\{[^{}]+\}|[A-Za-z0-9_]+))?",
                after_window,
            )
            if set_match:
                space, sign_sub, raw_exp = set_match.groups()
                base_dom: SymbolDomain = {
                    "R": "real",
                    "Z": "integer",
                    "N": "positive",
                    "C": "complex",
                }[space]
                constraints: list[str] = []
                if sign_sub in {">0", "+"}:
                    base_dom = "positive"
                    constraints.append(f"{sym} > 0")
                elif sign_sub in {">=0", "\\ge0"}:
                    base_dom = "non_negative"
                    constraints.append(f"{sym} >= 0")
                dims: tuple[int | str, ...] = _parse_dim_tokens(raw_exp) if raw_exp else ()
                cat: InferredCategory = (
                    "scalar"
                    if len(dims) == 0
                    else "vector"
                    if len(dims) == 1
                    else "matrix"
                    if len(dims) == 2
                    else "tensor"
                )
                for key in target_keys:
                    c_list = [c.replace(sym, key, 1) if key != sym else c for c in constraints]
                    key_cat = "function" if targets_by_name[key].category == "function" else cat
                    _merge_hint(
                        hints,
                        key,
                        category=key_cat,
                        domain=base_dom,
                        shape=dims,
                        constraints=c_list,
                        semantic_role=f"member_of_{space}",
                        evidence_span=clause,
                        confidence=0.95,
                    )

            interval_match = re.match(r"\s*\\in\s*(\[|\()\s*0\s*,\s*1\s*(\]|\))", after_window)
            if interval_match:
                open_br = interval_match.group(1)
                dom: SymbolDomain = "positive" if open_br == "(" else "non_negative"
                c_str = f"{sym} > 0" if dom == "positive" else f"{sym} >= 0"
                for key in target_keys:
                    c_val = c_str.replace(sym, key, 1) if key != sym else c_str
                    _merge_hint(
                        hints,
                        key,
                        category="scalar",
                        domain=dom,
                        shape=(),
                        constraints=(c_val,),
                        semantic_role="bounded_unit_interval",
                        evidence_span=clause,
                        confidence=0.92,
                    )

            # Pattern B: Explicit inequality (`sym > 0`, `0 < sym`, etc.) proves scalar unless bold.
            ineq_after = re.match(
                r"\s*(>=|<=|>|<|!=|\\neq|\\geq|\\ge|\\leq|\\le)\s*0(?![0-9.])",
                after_window,
            )
            if ineq_after:
                op = ineq_after.group(1)
                dom_c: tuple[SymbolDomain, str, str] | None = None
                if op == ">":
                    dom_c = ("positive", f"{sym} > 0", "strictly_positive")
                elif op in {">=", "\\geq", "\\ge"}:
                    dom_c = ("non_negative", f"{sym} >= 0", "non_negative")
                elif op in {"!=", "\\neq"}:
                    dom_c = ("real", f"{sym} != 0", "nonzero")
                elif op == "<":
                    dom_c = ("real", f"{sym} < 0", "strictly_negative")
                if dom_c is not None:
                    d_val, c_tmpl, role = dom_c
                    for key in target_keys:
                        key_sym = targets_by_name[key]
                        is_bold_vec_or_mat = (
                            key_sym.style == "bold" and key_sym.category in {"matrix", "vector"}
                        )
                        inferred_cat: InferredCategory = (
                            key_sym.category if is_bold_vec_or_mat else "scalar"
                        )
                        c_str = c_tmpl.replace(sym, key, 1) if key != sym else c_tmpl
                        _merge_hint(
                            hints,
                            key,
                            category=inferred_cat,
                            domain=d_val,
                            shape=() if inferred_cat in {"scalar", "index"} else None,
                            constraints=(c_str,),
                            semantic_role=role,
                            evidence_span=clause,
                            confidence=0.94,
                        )

            ineq_before = re.search(r"(?<![0-9.])0\s*(<|<=)\s*\Z", before_window)
            if ineq_before:
                op = ineq_before.group(1)
                d_val = "positive" if op == "<" else "non_negative"
                c_tmpl = f"{sym} > 0" if op == "<" else f"{sym} >= 0"
                for key in target_keys:
                    c_str = c_tmpl.replace(sym, key, 1) if key != sym else c_tmpl
                    _merge_hint(
                        hints,
                        key,
                        category="scalar",
                        domain=d_val,
                        shape=(),
                        constraints=(c_str,),
                        semantic_role="lower_bounded_scalar",
                        evidence_span=clause,
                        confidence=0.92,
                    )

            # Pattern C: Find closest noun-phrase role strictly inside this symbol's clause.
            best_role: tuple[int, InferredCategory | None, SymbolDomain, str, str, bool] | None = (
                None
            )
            for phrase_re, p_cat, p_dom, p_role, p_intent, add_pos in _PROSE_ROLE_PATTERNS:
                for p_match in phrase_re.finditer(clause):
                    dist = min(abs(p_match.end() - rel_pos), abs(p_match.start() - rel_pos))
                    if best_role is None or dist < best_role[0]:
                        best_role = (dist, p_cat, p_dom, p_role, p_intent, add_pos)
            if best_role is not None:
                _, p_cat, p_dom, p_role, p_intent, add_pos = best_role
                for key in target_keys:
                    key_sym = targets_by_name[key]
                    if key_sym.category == "function":
                        chosen_cat: InferredCategory | None = "function"
                    elif key_sym.style == "bold" and key_sym.category in {
                        "matrix",
                        "vector",
                        "tensor",
                    }:
                        chosen_cat = key_sym.category
                    else:
                        chosen_cat = p_cat
                    chosen_shape: tuple[int | str, ...] | None = (
                        () if chosen_cat in {"scalar", "index"} else None
                    )
                    c_list = (
                        (f"{key} > 0",) if add_pos and chosen_cat in {"scalar", "index"} else ()
                    )
                    _merge_hint(
                        hints,
                        key,
                        category=chosen_cat,
                        domain=p_dom,
                        shape=chosen_shape,
                        constraints=c_list,
                        semantic_role=p_role,
                        w3c_intent=f"{p_intent}({key})",
                        evidence_span=clause,
                        confidence=0.80,
                    )

    return ContextDisambiguationResult(
        disambiguator_version=DISAMBIGUATOR_VERSION,
        hints=hints,
        meom_operators=meom_operators,
    )


def _subscript_joined_symbols(root: AstNode) -> dict[str, tuple[str, str]]:
    """Map subscripted leaf names (e.g. ``d_k`` -> ``('d', 'k')``) in the formula."""
    out: dict[str, tuple[str, str]] = {}

    def walk(node: AstNode) -> None:
        if node.kind == "subscript" and len(node.children) == 2:
            base = node.children[0]
            while base.kind == "style" and len(base.children) == 1:
                base = base.children[0]
            idx = node.children[1]
            while idx.kind == "style" and len(idx.children) == 1:
                idx = idx.children[0]
            if (
                base.kind == "symbol"
                and base.value
                and idx.kind in {"symbol", "number"}
                and idx.value
            ):
                out[f"{base.value}_{idx.value}"] = (base.value, idx.value)
        for child in node.children:
            walk(child)

    walk(root)
    return out


def synthesize_exploratory_reviews(
    parsed_formulas: Sequence[ParsedFormula],
    *,
    preceding_text: str | None = None,
    following_text: str | None = None,
    existing_accepted: Sequence[ContractReview] = (),
    scope: str,
) -> tuple[list[ContractReview], list[dict[str, object]]]:
    """Synthesize ephemeral exploratory contracts for Dual-Track Auto-Check execution.

    Existing human ``accepted`` reviews are preserved unmodified. For every remaining
    missing symbol (or ``unknown``/``?`` contract), this builds an in-memory review
    labeled ``system:auto-inferred-exploratory`` informed by LaCASt prose hints and
    OPT structural inference. Never persisted to Neo4j.
    """
    preserved = [
        r for r in existing_accepted if r.decision == "accepted" and r.reviewed_contract is not None
    ]
    reviews_by_symbol: dict[str, ContractReview] = {r.symbol_name: r for r in preserved}
    inferred_metadata: list[dict[str, object]] = []

    if not parsed_formulas:
        return preserved, inferred_metadata

    combined_hints: dict[str, ContextSymbolHint] = {}
    all_contracts: dict[str, SymbolContract] = {}
    joined_subs: dict[str, tuple[str, str]] = {}

    for parsed in parsed_formulas:
        disambig = disambiguate_equation_context(
            parsed,
            preceding_text=preceding_text,
            following_text=following_text,
        )
        for sym_name, hint in disambig.hints.items():
            best = combined_hints.get(sym_name)
            if best is None or hint.confidence > best.confidence:
                combined_hints[sym_name] = hint
        for contract in infer_contracts(parsed):
            if contract.category == "function" and contract.name in _BUILTIN_MATH_FUNCTIONS:
                continue
            existing = all_contracts.get(contract.name)
            if existing is None or (
                existing.category == "unknown" and contract.category != "unknown"
            ):
                all_contracts[contract.name] = contract
        joined_subs.update(_subscript_joined_symbols(parsed.root))

    for sym_name in sorted(all_contracts):
        if sym_name in reviews_by_symbol:
            continue
        draft = all_contracts[sym_name]
        hint = combined_hints.get(sym_name)
        if hint is None:
            for joined_name, (base_name, _idx) in sorted(joined_subs.items()):
                if base_name == sym_name and joined_name in combined_hints:
                    hint = combined_hints[joined_name]
                    break

        category: SymbolCategory
        raw_cat = (
            hint.category
            if (hint and hint.category and hint.category != "unknown")
            else draft.category
        )
        category = "scalar" if raw_cat == "unknown" else raw_cat

        domain: SymbolDomain = (
            hint.domain
            if (hint and hint.domain is not None)
            else ("integer" if category == "index" else draft.domain)
        )

        shape: tuple[int | str, ...] | None
        if hint and hint.shape is not None and "?" not in hint.shape:
            shape = hint.shape
        elif category in {"scalar", "index"}:
            shape = ()
        elif category == "vector":
            shape = draft.shape if (draft.shape and "?" not in draft.shape) else ("d",)
        elif category == "matrix":
            shape = draft.shape if (draft.shape and "?" not in draft.shape) else ("N", "N")
        elif category == "tensor":
            shape = draft.shape if (draft.shape and "?" not in draft.shape) else ("B", "N", "d")
        else:
            shape = None

        constraints = (
            list(hint.constraints) if (hint and hint.constraints) else list(draft.constraints)
        )
        if hint and hint.symbol != sym_name:
            constraints = [
                re.sub(rf"\b{re.escape(hint.symbol)}\b", sym_name, c) for c in constraints
            ]

        source_label = "lacast_prose_context" if hint is not None else "opt_default_heuristic"
        evidence_span = hint.evidence_span if hint is not None else None

        reviewed_val = ReviewedContractValue(
            name=sym_name,
            category=category,
            shape=shape,
            domain=domain,
            constraints=constraints,
            scope=scope,
        )
        digest = hashlib.sha256(
            f"auto_inferred:{scope}:{sym_name}:{category}:{domain}:{shape}:{constraints}".encode()
        ).hexdigest()
        synthesized = ContractReview(
            review_id=f"auto_inferred_{digest[:16]}",
            symbol_name=sym_name,
            reviewer_id="system:auto-inferred-exploratory",
            reviewer_role="researcher",
            decision="accepted",
            scope=scope,
            reviewed_contract=reviewed_val,
            evidence=[
                "epistemic_track:auto_inferred_exploratory",
                f"source:{source_label}",
            ],
            reviewed_at=_AUTO_INFERRED_TIME,
        )
        reviews_by_symbol[sym_name] = synthesized
        inferred_metadata.append(
            {
                "symbol": sym_name,
                "category": category,
                "domain": domain,
                "shape": list(shape) if shape is not None else None,
                "constraints": constraints,
                "source": source_label,
                "evidence_span": evidence_span,
                "status": "inferred_draft",
            }
        )

    merged = [reviews_by_symbol[name] for name in sorted(reviews_by_symbol)]
    return merged, inferred_metadata
