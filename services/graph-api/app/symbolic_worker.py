"""Fixed-input subprocess worker for bounded symbolic comparisons."""

from __future__ import annotations

import json
import sys

import sympy

from app.formula_ast import FormulaParseError, ast_to_sympy, parse_formula


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(192 * 1024 + 1)
        if len(raw) > 192 * 1024:
            raise ValueError("oversized payload")
        payload = json.loads(raw)
        if not isinstance(payload, dict) or set(payload) != {"formula_a", "formula_b", "format"}:
            raise ValueError("invalid payload")
        if not isinstance(payload["formula_a"], str) or not isinstance(payload["formula_b"], str):
            raise ValueError("invalid formula")
        if payload["format"] not in {"latex", "mathml"}:
            raise ValueError("invalid format")
        left = ast_to_sympy(parse_formula(payload["formula_a"], payload["format"]).root)
        right = ast_to_sympy(parse_formula(payload["formula_b"], payload["format"]).root)
        difference = sympy.simplify(left - right)
        if difference == 0:
            result = {
                "outcome": "supported",
                "witness": {"method": "sympy_simplify", "difference": "0"},
            }
        elif bool(difference.is_number) and bool(difference.is_finite):
            result = {
                "outcome": "refuted",
                "counterexample": {
                    "method": "exact_constant_difference",
                    "difference": str(difference),
                },
            }
        else:
            result = {
                "outcome": "unknown",
                "witness": {"method": "sympy_inconclusive"},
            }
    except FormulaParseError as exc:
        result = {"outcome": "unsupported", "error_code": exc.code}
    except Exception:
        result = {"outcome": "error", "error_code": "WORKER_EXCEPTION"}
    sys.stdout.write(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
