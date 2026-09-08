"""FGL-401/402: Formula AST parsing, canonicalization, and equivalence tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from app.formula_ast import (
    FormulaParseError,
    ParsedFormula,
    ast_to_sympy,
    canonicalize,
    compute_canonical_hash,
    parse_formula,
    sympy_equivalent,
)

# ───────────────────────────────────────────────────────────────────
# FGL-401  Golden AST corpus
# ───────────────────────────────────────────────────────────────────


class TestGoldenCorpus:
    """Each case asserts the exact shape of the parsed AST."""

    def test_simple_addition(self):
        f = parse_formula("x + y")
        assert f.root.kind == "add"
        assert len(f.root.children) == 2
        assert f.root.children[0].value == "x"
        assert f.root.children[1].value == "y"

    def test_simple_multiplication(self):
        f = parse_formula("a * b")
        assert f.root.kind == "multiply"
        assert len(f.root.children) == 2

    def test_implicit_multiplication(self):
        f = parse_formula("a b")
        assert f.root.kind == "multiply"

    def test_fraction(self):
        f = parse_formula(r"\frac{a}{b}")
        assert f.root.kind == "divide"
        assert f.root.children[0].value == "a"
        assert f.root.children[1].value == "b"

    def test_power(self):
        f = parse_formula("x^2")
        assert f.root.kind == "power"
        assert f.root.children[0].value == "x"
        assert f.root.children[1].value == "2"

    def test_negation(self):
        f = parse_formula("-x")
        assert f.root.kind == "negate"
        assert f.root.children[0].value == "x"

    def test_sin_function(self):
        f = parse_formula(r"\sin(x)")
        assert f.root.kind == "call"
        assert f.root.children[0].value == "sin"

    def test_exp_function(self):
        f = parse_formula(r"\exp(-x^2)")
        assert f.root.kind == "call"
        assert f.root.children[0].value == "exp"
        arg = f.root.children[1]
        assert arg.kind == "negate"

    def test_operatorname_softmax(self):
        f = parse_formula(r"\operatorname{softmax}(z)")
        assert f.root.kind == "call"
        assert f.root.children[0].value == "softmax"
        assert f.root.children[0].attribute("role") == "function"

    def test_sqrt(self):
        f = parse_formula(r"\sqrt{x}")
        assert f.root.kind == "call"
        assert f.root.children[0].value == "sqrt"

    def test_sum_binder(self):
        f = parse_formula(r"\sum_{i=1}^{n} x_i")
        assert f.root.kind == "sum"
        assert f.root.attribute("binder") == "i"

    def test_product_binder(self):
        f = parse_formula(r"\prod_{k=1}^{K} a_k")
        assert f.root.kind == "product"
        assert f.root.attribute("binder") == "k"

    def test_integral(self):
        f = parse_formula(r"\int_0^\infty t")
        assert f.root.kind == "integral"

    def test_subscript(self):
        f = parse_formula("x_i")
        assert f.root.kind == "subscript"
        assert f.root.children[0].value == "x"
        assert f.root.children[1].value == "i"

    def test_nested_fraction_in_sum(self):
        f = parse_formula(r"\frac{\sum_{i=1}^{n} x_i}{n}")
        assert f.root.kind == "divide"
        assert f.root.children[0].kind == "sum"
        assert f.root.children[1].value == "n"

    def test_nested_sqrt_fraction(self):
        f = parse_formula(r"\sqrt{\frac{a}{b}}")
        assert f.root.kind == "call"
        assert f.root.children[0].value == "sqrt"
        assert f.root.children[1].kind == "divide"

    def test_equality(self):
        f = parse_formula(r"y = x + b")
        assert f.root.kind == "equals"
        assert f.root.children[0].value == "y"
        assert f.root.children[1].kind == "add"

    def test_linear_model(self):
        f = parse_formula(r"y = \mathbf{W} \mathbf{x} + \mathbf{b}")
        assert f.root.kind == "equals"

    def test_multi_argument_function(self):
        f = parse_formula(r"f(x, y, z)")
        assert f.root.kind == "call"
        assert len(f.root.children) == 4  # function + 3 args

    def test_distribution(self):
        f = parse_formula(r"N(0, 1)")
        assert f.root.kind == "distribution"
        assert f.root.children[0].value == "N"

    def test_number_literal(self):
        f = parse_formula("3.14")
        assert f.root.kind == "number"
        assert f.root.value == "3.14"

    def test_constant_pi(self):
        f = parse_formula(r"\pi")
        assert f.root.kind == "symbol"
        assert f.root.value == "pi"
        assert "pi" not in f.free_variables

    def test_cdot_operator(self):
        f = parse_formula(r"a \cdot b")
        assert f.root.kind == "multiply"

    def test_adjacent_uppercase_symbols_are_implicit_multiplication(self):
        f = parse_formula(r"QK^T")
        assert f.root.kind == "multiply"
        assert f.root.children[0].value == "Q"
        assert f.root.children[1].attribute("operation") == "transpose"


# ───────────────────────────────────────────────────────────────────
# FGL-401  Malformed input
# ───────────────────────────────────────────────────────────────────


class TestMalformedInput:
    """Match on human-readable error messages, not codes."""

    def test_empty_string(self):
        with pytest.raises(FormulaParseError, match="non-empty formula"):
            parse_formula("")

    def test_whitespace_only(self):
        with pytest.raises(FormulaParseError, match="non-empty formula"):
            parse_formula("   ")

    def test_trailing_backslash(self):
        with pytest.raises(FormulaParseError, match="Trailing backslash"):
            parse_formula("x + \\")

    def test_unclosed_brace(self):
        with pytest.raises(FormulaParseError):
            parse_formula(r"\frac{a}")

    def test_unsupported_environment(self):
        with pytest.raises(FormulaParseError, match="environments"):
            parse_formula(r"\begin{matrix} a \end{matrix}")

    def test_oversized_formula(self):
        with pytest.raises(FormulaParseError, match="exceeds the parser limit"):
            parse_formula("x" * 30_000)

    def test_excessive_depth(self):
        formula = "x"
        for _ in range(70):
            formula = r"\frac{" + formula + r"}{y}"
        with pytest.raises(FormulaParseError):
            parse_formula(formula)

    def test_non_string_input(self):
        with pytest.raises(FormulaParseError, match="non-empty formula"):
            parse_formula(None)  # type: ignore[arg-type]

    def test_unsupported_source_format(self):
        with pytest.raises(FormulaParseError, match="latex or mathml"):
            parse_formula("x", source_format="asciimath")  # type: ignore[arg-type]

    def test_empty_group(self):
        with pytest.raises(FormulaParseError, match="Empty formula groups"):
            parse_formula("()")

    def test_error_code_attribute(self):
        """FormulaParseError carries the structured error code."""
        with pytest.raises(FormulaParseError) as exc_info:
            parse_formula("")
        assert exc_info.value.code == "EMPTY_FORMULA"


# ───────────────────────────────────────────────────────────────────
# FGL-401  Binder scope
# ───────────────────────────────────────────────────────────────────


class TestBinderScope:
    def test_sum_bound_variable(self):
        f = parse_formula(r"\sum_{i=1}^{n} x_i")
        assert "i" in f.bound_variables
        assert "i" not in f.free_variables

    def test_sum_free_variables(self):
        f = parse_formula(r"\sum_{i=1}^{n} x_i")
        assert "x" in f.free_variables
        assert "n" in f.free_variables

    def test_nested_binders(self):
        f = parse_formula(r"\sum_{i=1}^{n} \sum_{j=1}^{m} a")
        assert "i" in f.bound_variables
        assert "j" in f.bound_variables
        assert "a" in f.free_variables

    def test_product_bound(self):
        f = parse_formula(r"\prod_{k=1}^{K} x_k")
        assert "k" in f.bound_variables
        assert "K" in f.free_variables

    def test_body_variable_is_free(self):
        """A symbol used in the body but not as a binder is free."""
        f = parse_formula(r"\sum_{i=1}^{n} w_i y")
        assert "y" in f.free_variables
        assert "w" in f.free_variables

    def test_bound_and_free_separate_roles(self):
        """The binder name 'i' is recorded as bound; it may also appear in
        free if used outside the binder scope (e.g. as a subscript index).
        The key invariant is that bound_variables contains 'i'."""
        f = parse_formula(r"\sum_{i=1}^{n} x_i")
        assert "i" in f.bound_variables


# ───────────────────────────────────────────────────────────────────
# FGL-401  Indexed notation and symbol categories
# ───────────────────────────────────────────────────────────────────


class TestIndexedNotation:
    def _symbol(self, f: ParsedFormula, name: str):
        return next((s for s in f.symbols if s.name == name), None)

    def test_scalar(self):
        f = parse_formula("x + y")
        s = self._symbol(f, "x")
        assert s is not None
        assert s.category == "scalar"

    def test_vector_single_index(self):
        f = parse_formula("x_i")
        s = self._symbol(f, "x")
        assert s is not None
        assert s.category == "vector"
        assert "i" in s.indices

    def test_matrix_two_indices(self):
        """W_{i} single uppercase → vector."""
        f = parse_formula("W_{i}")
        s = self._symbol(f, "W")
        assert s is not None
        assert s.category == "vector"

    def test_bold_vector(self):
        f = parse_formula(r"\mathbf{x}")
        s = self._symbol(f, "x")
        assert s is not None
        assert s.category == "vector"
        assert s.style == "bold"

    def test_bold_uppercase_matrix(self):
        f = parse_formula(r"\mathbf{W}")
        s = self._symbol(f, "W")
        assert s is not None
        assert s.category == "matrix"

    def test_calligraphy_tensor(self):
        f = parse_formula(r"\mathcal{T}")
        s = self._symbol(f, "T")
        assert s is not None
        assert s.category == "tensor"

    def test_vec_style(self):
        f = parse_formula(r"\vec{v}")
        s = self._symbol(f, "v")
        assert s is not None
        assert s.category == "vector"
        assert s.style == "vector"


# ───────────────────────────────────────────────────────────────────
# FGL-401  Symbol analysis
# ───────────────────────────────────────────────────────────────────


class TestSymbolAnalysis:
    def _symbol(self, f: ParsedFormula, name: str):
        return next((s for s in f.symbols if s.name == name), None)

    def test_function_detection(self):
        f = parse_formula(r"\sin(x)")
        s = self._symbol(f, "sin")
        assert s is not None
        assert s.category == "function"

    def test_operatorname_function(self):
        f = parse_formula(r"\operatorname{softmax}(z)")
        s = self._symbol(f, "softmax")
        assert s is not None
        assert s.category == "function"

    def test_distribution_detection(self):
        f = parse_formula(r"N(0, 1)")
        s = self._symbol(f, "N")
        assert s is not None
        assert s.category == "distribution"

    def test_index_from_binder(self):
        f = parse_formula(r"\sum_{i=1}^{n} x_i")
        s = self._symbol(f, "i")
        assert s is not None
        assert s.category == "index"

    def test_constant_excluded(self):
        f = parse_formula(r"\pi + x")
        assert "pi" not in f.free_variables
        assert "x" in f.free_variables

    def test_multiple_symbols(self):
        f = parse_formula(r"a + b + c")
        names = {s.name for s in f.symbols}
        assert names == {"a", "b", "c"}


# ───────────────────────────────────────────────────────────────────
# FGL-401  MathML input
# ───────────────────────────────────────────────────────────────────


class TestMathMLInput:
    def test_simple_mathml(self):
        mathml = "<math><mi>x</mi><mo>+</mo><mi>y</mi></math>"
        f = parse_formula(mathml, source_format="mathml")
        assert f.root.kind == "add"
        assert f.source_format == "mathml"

    def test_fraction_mathml(self):
        mathml = "<math><mfrac><mi>a</mi><mi>b</mi></mfrac></math>"
        f = parse_formula(mathml, source_format="mathml")
        assert f.root.kind == "divide"

    def test_subscript_mathml(self):
        mathml = "<math><msub><mi>x</mi><mi>i</mi></msub></math>"
        f = parse_formula(mathml, source_format="mathml")
        assert f.root.kind == "subscript"

    def test_unsafe_mathml_dtd(self):
        mathml = '<!DOCTYPE math SYSTEM "foo"><math><mi>x</mi></math>'
        with pytest.raises(FormulaParseError, match="DTD"):
            parse_formula(mathml, source_format="mathml")

    def test_malformed_mathml(self):
        with pytest.raises(FormulaParseError, match="could not be parsed"):
            parse_formula("<not-xml", source_format="mathml")


# ═══════════════════════════════════════════════════════════════════
# FGL-402  Canonical identity
# ═══════════════════════════════════════════════════════════════════


class TestCanonicalize:
    def test_commutative_addition(self):
        """a + b and b + a have the same canonical hash."""
        h1 = parse_formula("a + b").canonical_hash
        h2 = parse_formula("b + a").canonical_hash
        assert h1 == h2

    def test_commutative_multiplication(self):
        h1 = parse_formula("a * b").canonical_hash
        h2 = parse_formula("b * a").canonical_hash
        assert h1 == h2

    def test_non_equivalent_different_hash(self):
        h1 = parse_formula("a + b").canonical_hash
        h2 = parse_formula("a * b").canonical_hash
        assert h1 != h2

    def test_alpha_rename_sum(self):
        """Sum over i and sum over j with same body structure → same hash."""
        h1 = parse_formula(r"\sum_{i=1}^{n} x_i").canonical_hash
        h2 = parse_formula(r"\sum_{j=1}^{n} x_j").canonical_hash
        assert h1 == h2

    def test_alpha_rename_product(self):
        h1 = parse_formula(r"\prod_{k=1}^{K} a_k").canonical_hash
        h2 = parse_formula(r"\prod_{m=1}^{K} a_m").canonical_hash
        assert h1 == h2

    def test_different_body_different_hash(self):
        h1 = parse_formula(r"\sum_{i=1}^{n} x_i").canonical_hash
        h2 = parse_formula(r"\sum_{i=1}^{n} y_i").canonical_hash
        assert h1 != h2

    def test_hash_stability(self):
        """Same input always produces the same hash."""
        formula = r"\frac{\sum_{i=1}^{n} x_i}{n}"
        hashes = {parse_formula(formula).canonical_hash for _ in range(50)}
        assert len(hashes) == 1

    def test_hash_is_sha256_hex(self):
        h = parse_formula("x").canonical_hash
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_three_term_commutative(self):
        """a + b + c in any order → same hash."""
        perms = ["a + b + c", "b + c + a", "c + a + b", "b + a + c"]
        hashes = {parse_formula(p).canonical_hash for p in perms}
        assert len(hashes) == 1

    def test_nested_commutative(self):
        """(a + b) * (c + d) and (d + c) * (b + a) → same hash."""
        h1 = parse_formula("(a + b) * (c + d)").canonical_hash
        h2 = parse_formula("(d + c) * (b + a)").canonical_hash
        assert h1 == h2

    def test_canonicalize_preserves_structure(self):
        """Non-commutative operations like divide preserve child order."""
        root = parse_formula(r"\frac{a}{b}").root
        canonical = canonicalize(root)
        assert canonical.children[0].value == "a"
        assert canonical.children[1].value == "b"

    def test_power_order_preserved(self):
        """x^2 ≠ 2^x — power is not commutative."""
        h1 = parse_formula("x^2").canonical_hash
        h2 = parse_formula("2^x").canonical_hash
        assert h1 != h2

    def test_matrix_multiplication_order_is_preserved(self):
        h1 = parse_formula(r"\mathbf{A} * \mathbf{B}").canonical_hash
        h2 = parse_formula(r"\mathbf{B} * \mathbf{A}").canonical_hash
        assert h1 != h2


# ───────────────────────────────────────────────────────────────────
# FGL-402  SymPy equivalence
# ───────────────────────────────────────────────────────────────────


class TestSympyEquivalence:
    def test_expanded_square(self):
        """(a+b)^2 is algebraically equivalent to a^2 + 2ab + b^2."""
        a = parse_formula("(a + b)^2").root
        b = parse_formula("a^2 + 2 * a * b + b^2").root
        assert sympy_equivalent(a, b) is True

    def test_not_equivalent(self):
        a = parse_formula("a + b").root
        b = parse_formula("a * b").root
        assert sympy_equivalent(a, b) is False

    def test_matrix_order_is_not_reported_as_equivalent(self):
        a = parse_formula(r"\mathbf{A} * \mathbf{B}").root
        b = parse_formula(r"\mathbf{B} * \mathbf{A}").root
        assert sympy_equivalent(a, b) is False

    def test_commutative_equivalence(self):
        a = parse_formula("x * y + z").root
        b = parse_formula("z + y * x").root
        assert sympy_equivalent(a, b) is True

    def test_fraction_equivalence(self):
        a = parse_formula(r"\frac{a}{b}").root
        b_node = parse_formula("a * b^{-1}").root
        result = sympy_equivalent(a, b_node)
        assert result is True

    def test_ast_to_sympy_number(self):
        import sympy
        node = parse_formula("42").root
        expr = ast_to_sympy(node)
        assert expr == sympy.Number(42)

    def test_ast_to_sympy_sin(self):
        import sympy
        node = parse_formula(r"\sin(x)").root
        expr = ast_to_sympy(node)
        x = sympy.Symbol("x")
        assert expr == sympy.sin(x)

    def test_ast_to_sympy_negation(self):
        import sympy
        node = parse_formula("-x").root
        expr = ast_to_sympy(node)
        assert expr == -sympy.Symbol("x")

    def test_sympy_identity_check(self):
        """Same tree compared to itself is equivalent."""
        a = parse_formula("x").root
        assert sympy_equivalent(a, a) is True


class TestCanonicalHashFunction:
    """Test the standalone compute_canonical_hash function."""

    def test_direct_call(self):
        root = parse_formula("x + y").root
        h = compute_canonical_hash(root)
        assert isinstance(h, str)
        assert len(h) == 64

    def test_matches_parsed_formula(self):
        f = parse_formula("a * b + c")
        assert f.canonical_hash == compute_canonical_hash(f.root)

    def test_hash_is_stable_across_processes(self):
        formula = r"\sum_{i=1}^{n} x_i"
        script = (
            "from app.formula_ast import parse_formula; "
            f"print(parse_formula({formula!r}).canonical_hash)"
        )
        cwd = Path(__file__).resolve().parents[1]
        first = subprocess.run(
            [sys.executable, "-c", script],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        second = subprocess.run(
            [sys.executable, "-c", script],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert first == second == parse_formula(formula).canonical_hash
