"""Fixed-input subprocess worker for bounded symbolic comparisons."""

from __future__ import annotations

import json
import sys

from app.egraph_engine import egraph_check_equivalence
from app.formula_ast import AstNode, FormulaParseError, ast_to_sympy, parse_formula

_ALLOWED_CATEGORIES = {
    "scalar",
    "vector",
    "matrix",
    "tensor",
    "function",
    "distribution",
    "index",
}
_ALLOWED_DOMAINS = {"real", "positive", "non_negative", "complex", "integer"}


def _validate_assumptions(raw: object) -> dict[str, dict[str, object]] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or len(raw) > 64:
        raise ValueError("invalid symbol_assumptions")
    validated: dict[str, dict[str, object]] = {}
    for name, spec in raw.items():
        if not isinstance(name, str) or not name or len(name) > 64 or not isinstance(spec, dict):
            raise ValueError("invalid assumption entry")
        if not set(spec).issubset(
            {"category", "domain", "nonzero", "negative", "positive", "nonnegative"}
        ):
            raise ValueError("unexpected assumption keys")
        category = spec.get("category")
        domain = spec.get("domain")
        if category not in _ALLOWED_CATEGORIES or domain not in _ALLOWED_DOMAINS:
            raise ValueError("invalid assumption category or domain")
        entry: dict[str, object] = {"category": category, "domain": domain}
        for flag in ("nonzero", "negative", "positive", "nonnegative"):
            if flag in spec:
                if type(spec[flag]) is not bool:
                    raise ValueError("invalid assumption boolean")
                entry[flag] = spec[flag]
        validated[name] = entry
    return validated


def _has_matrix_or_transpose(
    node: AstNode,
    assumptions: dict[str, dict[str, object]] | None,
) -> bool:
    if assumptions and any(
        spec.get("category") in {"matrix", "vector", "tensor"} for spec in assumptions.values()
    ):
        return True
    if node.kind == "power" and node.attribute("operation") == "transpose":
        return True
    return any(_has_matrix_or_transpose(child, None) for child in node.children)


def _simplify_difference(expr, *, use_domain_rules: bool):  # noqa: ANN001, ANN202
    """Simplify symbolically without force=True so branch cuts remain sound."""
    import sympy

    if expr.has(sympy.Piecewise):
        expr = sympy.piecewise_fold(expr)
    diff = sympy.simplify(expr)
    if diff == 0 or not use_domain_rules:
        return diff
    transforms = []
    if diff.has(sympy.log):
        transforms.append(lambda e: sympy.simplify(sympy.expand_log(e, force=False)))
    if diff.has(sympy.Pow):
        transforms.append(lambda e: sympy.simplify(sympy.powdenest(e, force=False)))
    if diff.has(sympy.sin, sympy.cos, sympy.tan):
        transforms.append(lambda e: sympy.simplify(sympy.trigsimp(e)))
    if diff.has(sympy.Add) or diff.has(sympy.Mul):
        transforms.append(lambda e: sympy.cancel(sympy.together(e)))
    for transform in transforms:
        try:
            candidate = transform(diff)
            if candidate == 0:
                return candidate
        except Exception:
            continue
    return diff


def _compare_expressions(left, right, *, use_domain_rules: bool) -> dict[str, object]:  # noqa: ANN001
    import sympy

    method = "sympy_assumptions_simplify" if use_domain_rules else "sympy_simplify"
    if isinstance(left, sympy.Eq) and isinstance(right, sympy.Eq):
        lhs_diff = sympy.simplify(left.lhs - right.lhs)
        rhs_diff = sympy.simplify(left.rhs - right.rhs)
        if lhs_diff == 0 and rhs_diff == 0:
            return {"outcome": "supported", "witness": {"method": method, "difference": "0"}}
        if lhs_diff == 0 and bool(rhs_diff.is_number) and bool(rhs_diff.is_finite):
            return {
                "outcome": "refuted",
                "counterexample": {
                    "method": "exact_constant_difference",
                    "difference": str(rhs_diff),
                },
            }
        left_res = left.lhs - left.rhs
        right_res = right.lhs - right.rhs
        for combined in (left_res - right_res, left_res + right_res):
            rearranged = _simplify_difference(combined, use_domain_rules=use_domain_rules)
            if rearranged == 0:
                return {"outcome": "supported", "witness": {"method": method, "difference": "0"}}
        return {"outcome": "unknown", "witness": {"method": "sympy_inconclusive"}}

    if isinstance(left, sympy.Eq) or isinstance(right, sympy.Eq):
        return {"outcome": "unknown", "witness": {"method": "sympy_inconclusive"}}

    difference = _simplify_difference(left - right, use_domain_rules=use_domain_rules)
    if difference == 0:
        return {
            "outcome": "supported",
            "witness": {"method": method, "difference": "0"},
        }
    if bool(difference.is_number) and bool(difference.is_finite):
        return {
            "outcome": "refuted",
            "counterexample": {
                "method": "exact_constant_difference",
                "difference": str(difference),
            },
        }
    return {
        "outcome": "unknown",
        "witness": {"method": "sympy_inconclusive"},
    }


def _should_prefer_egraph(
    left: AstNode,
    right: AstNode,
    assumptions: dict[str, dict[str, object]] | None,
) -> bool:
    """Use E-Graph equality saturation first on equations and non-commutative matrix/transpose ASTs.

    Plain algebraic expressions defer to SymPy first (preserving exact ``sympy_simplify``
    witnesses) and escalate to E-Graph when SymPy returns ``unknown`` or ``SYMPY_UNSUPPORTED``.
    """
    if left.kind == "equals" and right.kind == "equals":
        return True
    return _has_matrix_or_transpose(left, assumptions) or _has_matrix_or_transpose(
        right, assumptions
    )


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(192 * 1024 + 1)
        if len(raw) > 192 * 1024:
            raise ValueError("oversized payload")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("invalid payload")
        keys = set(payload)
        if not {"formula_a", "formula_b", "format"}.issubset(keys) or not keys.issubset(
            {"formula_a", "formula_b", "format", "symbol_assumptions"}
        ):
            raise ValueError("invalid payload")
        if not isinstance(payload["formula_a"], str) or not isinstance(payload["formula_b"], str):
            raise ValueError("invalid formula")
        if payload["format"] not in {"latex", "mathml"}:
            raise ValueError("invalid format")
        assumptions = _validate_assumptions(payload.get("symbol_assumptions"))
        parsed_a = parse_formula(payload["formula_a"], payload["format"])
        parsed_b = parse_formula(payload["formula_b"], payload["format"])

        result: dict[str, object] | None = None
        if _should_prefer_egraph(parsed_a.root, parsed_b.root, assumptions):
            eg_result = egraph_check_equivalence(
                parsed_a.root,
                parsed_b.root,
                symbol_assumptions=assumptions,
            )
            if eg_result is not None and eg_result.get("outcome") == "supported":
                result = eg_result

        if result is None:
            sympy_error: FormulaParseError | None = None
            try:
                left = ast_to_sympy(parsed_a.root, symbol_assumptions=assumptions)
                right = ast_to_sympy(parsed_b.root, symbol_assumptions=assumptions)
                result = _compare_expressions(left, right, use_domain_rules=bool(assumptions))
            except FormulaParseError as exc:
                sympy_error = exc
                result = {"outcome": "unknown", "witness": {"method": "sympy_inconclusive"}}

            if result.get("outcome") not in {"supported", "refuted"}:
                eg_result = egraph_check_equivalence(
                    parsed_a.root,
                    parsed_b.root,
                    symbol_assumptions=assumptions,
                )
                if eg_result is not None and eg_result.get("outcome") == "supported":
                    result = eg_result
                elif sympy_error is not None:
                    raise sympy_error
    except FormulaParseError as exc:
        result = {"outcome": "unsupported", "error_code": exc.code}
    except Exception:
        result = {"outcome": "error", "error_code": "WORKER_EXCEPTION"}
    sys.stdout.write(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
