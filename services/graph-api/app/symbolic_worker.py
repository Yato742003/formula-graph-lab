"""Fixed-input subprocess worker for bounded symbolic comparisons."""

from __future__ import annotations

import json
import sys

import sympy

from app.formula_ast import FormulaParseError, ast_to_sympy, parse_formula

_ALLOWED_CATEGORIES = {
    "scalar", "vector", "matrix", "tensor", "function", "distribution", "index",
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


def _simplify_difference(expr, *, use_domain_rules: bool):  # noqa: ANN001, ANN202
    """Simplify symbolically without force=True so branch cuts remain sound."""
    if expr.has(sympy.Piecewise):
        expr = sympy.piecewise_fold(expr)
    diff = sympy.simplify(expr)
    if diff == 0 or not use_domain_rules:
        return diff
    for transform in (
        lambda e: sympy.simplify(sympy.expand_log(e, force=False)),
        lambda e: sympy.simplify(sympy.powdenest(e, force=False)),
        lambda e: sympy.simplify(sympy.trigsimp(e)),
        lambda e: sympy.simplify(sympy.cancel(sympy.together(e))),
    ):
        try:
            candidate = transform(diff)
            if candidate == 0:
                return candidate
        except Exception:
            continue
    return diff


def _compare_expressions(left, right, *, use_domain_rules: bool) -> dict[str, object]:  # noqa: ANN001
    method = "sympy_assumptions_simplify" if use_domain_rules else "sympy_simplify"
    if isinstance(left, sympy.Eq) and isinstance(right, sympy.Eq):
        lhs_diff = _simplify_difference(left.lhs - right.lhs, use_domain_rules=use_domain_rules)
        rhs_diff = _simplify_difference(left.rhs - right.rhs, use_domain_rules=use_domain_rules)
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
        left = ast_to_sympy(
            parse_formula(payload["formula_a"], payload["format"]).root,
            symbol_assumptions=assumptions,
        )
        right = ast_to_sympy(
            parse_formula(payload["formula_b"], payload["format"]).root,
            symbol_assumptions=assumptions,
        )
        result = _compare_expressions(left, right, use_domain_rules=bool(assumptions))
    except FormulaParseError as exc:
        result = {"outcome": "unsupported", "error_code": exc.code}
    except Exception:
        result = {"outcome": "error", "error_code": "WORKER_EXCEPTION"}
    sys.stdout.write(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
