from __future__ import annotations

import hashlib
import json as _json_mod
from dataclasses import dataclass, replace
from typing import Literal

from lxml import etree

FormulaFormat = Literal["latex", "mathml"]
NodeKind = Literal[
    "number",
    "symbol",
    "add",
    "multiply",
    "divide",
    "power",
    "negate",
    "call",
    "distribution",
    "subscript",
    "equals",
    "sum",
    "product",
    "integral",
    "sequence",
    "style",
]

MAX_FORMULA_CHARACTERS = 20_000
MAX_AST_NODES = 2_048
MAX_PARSE_DEPTH = 64


class FormulaParseError(ValueError):
    def __init__(self, code: str, message: str, position: int | None = None):
        super().__init__(message)
        self.code = code
        self.position = position


@dataclass(frozen=True)
class AstNode:
    kind: NodeKind
    value: str | None = None
    children: tuple[AstNode, ...] = ()
    attributes: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"kind": self.kind}
        if self.value is not None:
            result["value"] = self.value
        if self.children:
            result["children"] = [child.to_dict() for child in self.children]
        if self.attributes:
            result["attributes"] = dict(self.attributes)
        return result

    def attribute(self, name: str) -> str | None:
        return dict(self.attributes).get(name)


@dataclass(frozen=True)
class ParsedSymbol:
    name: str
    category: Literal[
        "scalar",
        "vector",
        "matrix",
        "tensor",
        "function",
        "distribution",
        "index",
    ]
    indices: tuple[str, ...] = ()
    style: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "category": self.category,
            "indices": list(self.indices),
            "style": self.style,
        }


@dataclass(frozen=True)
class ParsedFormula:
    root: AstNode
    free_variables: tuple[str, ...]
    bound_variables: tuple[str, ...]
    symbols: tuple[ParsedSymbol, ...]
    source_format: FormulaFormat
    canonical_hash: str = ""


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str
    position: int


_SPACING_COMMANDS = {",", ";", ":", "!", "quad", "qquad", " "}
_OPERATOR_COMMANDS = {"cdot": "*", "times": "*", "div": "/"}
_FUNCTION_COMMANDS = {
    "sin",
    "cos",
    "tan",
    "exp",
    "log",
    "ln",
    "max",
    "min",
    "softmax",
    "det",
    "tr",
}
_STRUCTURAL_COMMANDS = {
    "frac",
    "sqrt",
    "sum",
    "prod",
    "int",
    "operatorname",
    "mathrm",
    "mathbf",
    "boldsymbol",
    "vec",
    "mathcal",
}
_CONSTANTS = {"pi", "infty", "infinity"}


def parse_formula(source: str, source_format: FormulaFormat = "latex") -> ParsedFormula:
    if not isinstance(source, str) or not source.strip():
        raise FormulaParseError("EMPTY_FORMULA", "A non-empty formula is required.")
    if source_format not in {"latex", "mathml"}:
        raise FormulaParseError("UNSUPPORTED_FORMAT", "Formula format must be latex or mathml.")
    if len(source) > MAX_FORMULA_CHARACTERS:
        raise FormulaParseError("FORMULA_TOO_LARGE", "The formula exceeds the parser limit.")
    latex = _mathml_to_latex(source) if source_format == "mathml" else source
    tokens = _tokenize_latex(latex)
    root = _FormulaParser(tokens).parse()
    free, bound, symbols = _analyze_symbols(root)
    c_hash = compute_canonical_hash(root)
    return ParsedFormula(
        root=root,
        free_variables=tuple(sorted(free)),
        bound_variables=tuple(sorted(bound)),
        symbols=tuple(symbols),
        source_format=source_format,
        canonical_hash=c_hash,
    )


def _tokenize_latex(source: str) -> list[_Token]:
    text = source.strip()
    for left, right in (("\\[", "\\]"), ("$$", "$$"), ("$", "$")):
        if text.startswith(left) and text.endswith(right) and len(text) >= len(left) + len(right):
            text = text[len(left) : len(text) - len(right)].strip()
            break
    tokens: list[_Token] = []
    index = 0
    while index < len(text):
        character = text[index]
        if character.isspace():
            index += 1
            continue
        if character == "\\":
            start = index
            index += 1
            if index >= len(text):
                raise FormulaParseError("MALFORMED_COMMAND", "Trailing backslash.", start)
            if text[index].isalpha():
                end = index + 1
                while end < len(text) and text[end].isalpha():
                    end += 1
                command = text[index:end]
                index = end
            else:
                command = text[index]
                index += 1
            if command in _SPACING_COMMANDS or command in {"left", "right"}:
                continue
            if command in _OPERATOR_COMMANDS:
                tokens.append(_Token("operator", _OPERATOR_COMMANDS[command], start))
            elif command in _FUNCTION_COMMANDS:
                tokens.append(_Token("function", command, start))
            elif command in _STRUCTURAL_COMMANDS:
                tokens.append(_Token("command", command, start))
            elif command in {"begin", "end"}:
                raise FormulaParseError(
                    "UNSUPPORTED_ENVIRONMENT",
                    "LaTeX environments are outside the supported formula subset.",
                    start,
                )
            else:
                tokens.append(_Token("identifier", command, start))
            continue
        if character.isdigit() or (
            character == "." and index + 1 < len(text) and text[index + 1].isdigit()
        ):
            end = index + 1
            decimal_seen = character == "."
            while end < len(text):
                if text[end].isdigit():
                    end += 1
                    continue
                if text[end] == "." and not decimal_seen:
                    decimal_seen = True
                    end += 1
                    continue
                break
            tokens.append(_Token("number", text[index:end], index))
            index = end
            continue
        if character.isalpha() or ord(character) > 127:
            end = index + 1
            while end < len(text) and (text[end].isalnum() or ord(text[end]) > 127):
                end += 1
            identifier = text[index:end]
            if len(identifier) > 1 and identifier.isascii() and identifier.isupper():
                tokens.extend(
                    _Token("identifier", value, index + offset)
                    for offset, value in enumerate(identifier)
                )
            else:
                tokens.append(_Token("identifier", identifier, index))
            index = end
            continue
        if character in "{}()[]_^+-*/=,":
            kind = "operator" if character in "^_+-*/=," else "delimiter"
            tokens.append(_Token(kind, character, index))
            index += 1
            continue
        raise FormulaParseError(
            "UNSUPPORTED_TOKEN",
            f"Unsupported formula token {character!r}.",
            index,
        )
    tokens.append(_Token("eof", "", len(text)))
    return tokens


class _FormulaParser:
    def __init__(self, tokens: list[_Token]):
        self.tokens = tokens
        self.index = 0
        self.node_count = 0
        self.depth = 0

    @property
    def current(self) -> _Token:
        return self.tokens[self.index]

    def parse(self) -> AstNode:
        expression = self._expression(0, frozenset())
        if self.current.kind != "eof":
            raise FormulaParseError(
                "TRAILING_INPUT",
                "Unexpected input after the formula.",
                self.current.position,
            )
        return expression

    def _node(
        self,
        kind: NodeKind,
        value: str | None = None,
        children: tuple[AstNode, ...] = (),
        attributes: tuple[tuple[str, str], ...] = (),
    ) -> AstNode:
        self.node_count += 1
        if self.node_count > MAX_AST_NODES:
            raise FormulaParseError("AST_TOO_LARGE", "The formula AST exceeds its node limit.")
        return AstNode(kind, value, children, attributes)

    def _expression(self, minimum_binding: int, stops: frozenset[str]) -> AstNode:
        self.depth += 1
        if self.depth > MAX_PARSE_DEPTH:
            raise FormulaParseError("AST_TOO_DEEP", "The formula nesting limit was exceeded.")
        try:
            left = self._prefix()
            while self.current.value not in stops and self.current.kind != "eof":
                if self.current.value in {"_", "^"}:
                    operator = self._advance().value
                    right = self._script_argument()
                    attributes = (
                        (("operation", "transpose"),)
                        if operator == "^"
                        and right.kind == "symbol"
                        and right.value in {"T", "top"}
                        else ()
                    )
                    left = self._node(
                        "subscript" if operator == "_" else "power",
                        children=(left, right),
                        attributes=attributes,
                    )
                    continue
                if self.current.value == "(" and left.kind in {"symbol", "style"}:
                    left = self._call(left)
                    continue
                explicit = self.current.value
                if explicit in {"=", "+", "-", "*", "/"}:
                    left_binding, right_binding = {
                        "=": (3, 4),
                        "+": (10, 11),
                        "-": (10, 11),
                        "*": (20, 21),
                        "/": (20, 21),
                    }[explicit]
                    if left_binding < minimum_binding:
                        break
                    self._advance()
                    right = self._expression(right_binding, stops)
                    left = self._infix(explicit, left, right)
                    continue
                if self._starts_atom(self.current):
                    if minimum_binding > 20:
                        break
                    right = self._expression(21, stops)
                    left = self._associative("multiply", left, right)
                    continue
                break
            return left
        finally:
            self.depth -= 1

    def _prefix(self) -> AstNode:
        token = self._advance()
        if token.kind == "number":
            return self._node("number", token.value)
        if token.kind in {"identifier", "function"}:
            return self._node("symbol", token.value, attributes=(("role", token.kind),))
        if token.value == "-":
            return self._node("negate", children=(self._expression(25, frozenset()),))
        if token.value in {"(", "{"}:
            return self._group(token.value)
        if token.kind == "command":
            return self._command(token)
        raise FormulaParseError(
            "EXPECTED_EXPRESSION", "Expected a formula expression.", token.position
        )

    def _command(self, token: _Token) -> AstNode:
        command = token.value
        if command == "frac":
            numerator = self._required_group()
            denominator = self._required_group()
            return self._node("divide", children=(numerator, denominator))
        if command == "sqrt":
            argument = self._required_group()
            function = self._node("symbol", "sqrt", attributes=(("role", "function"),))
            return self._node("call", children=(function, argument))
        if command in {"sum", "prod", "int"}:
            return self._binder(command)
        if command in {"operatorname", "mathrm"}:
            name = self._required_group_text()
            role = (
                "function"
                if command == "operatorname" or name in _FUNCTION_COMMANDS
                else "identifier"
            )
            return self._node("symbol", name, attributes=(("role", role),))
        if command in {"mathbf", "boldsymbol", "vec", "mathcal"}:
            child = self._required_group()
            style = {
                "mathbf": "bold",
                "boldsymbol": "bold",
                "vec": "vector",
                "mathcal": "calligraphy",
            }[command]
            if child.kind == "symbol":
                return replace(child, attributes=((*child.attributes, ("style", style))))
            return self._node("style", children=(child,), attributes=(("style", style),))
        raise FormulaParseError(
            "UNSUPPORTED_COMMAND", f"Unsupported command \\{command}.", token.position
        )

    def _binder(self, command: str) -> AstNode:
        lower: AstNode | None = None
        upper: AstNode | None = None
        while self.current.value in {"_", "^"}:
            operator = self._advance().value
            argument = self._script_argument()
            if operator == "_":
                if lower is not None:
                    raise FormulaParseError("DUPLICATE_BOUND", "Duplicate lower bound.")
                lower = argument
            else:
                if upper is not None:
                    raise FormulaParseError("DUPLICATE_BOUND", "Duplicate upper bound.")
                upper = argument
        binder = _bound_name(lower)
        if self.current.kind == "eof" or self.current.value in {"}", ")", "]", ","}:
            raise FormulaParseError("MISSING_BINDER_BODY", "A binder requires a body.")
        body = self._expression(11, frozenset({",", ")", "}", "]"}))
        children = tuple(child for child in (lower, upper, body) if child is not None)
        attributes = (("binder", binder),) if binder else ()
        kind: NodeKind = {"sum": "sum", "prod": "product", "int": "integral"}[command]
        return self._node(kind, children=children, attributes=attributes)

    def _call(self, function: AstNode) -> AstNode:
        self._expect("(")
        arguments: list[AstNode] = []
        if self.current.value != ")":
            while True:
                arguments.append(self._expression(0, frozenset({",", ")"})))
                if self.current.value != ",":
                    break
                self._advance()
        self._expect(")")
        name = function.value if function.kind == "symbol" else None
        kind: NodeKind = (
            "distribution" if name and name.lower() in {"n", "normal", "bernoulli"} else "call"
        )
        return self._node(kind, children=(function, *arguments))

    def _group(self, opening: str) -> AstNode:
        closing = {"(": ")", "{": "}"}[opening]
        if self.current.value == closing:
            raise FormulaParseError("EMPTY_GROUP", "Empty formula groups are not supported.")
        values = [self._expression(0, frozenset({",", closing}))]
        while self.current.value == ",":
            self._advance()
            values.append(self._expression(0, frozenset({",", closing})))
        self._expect(closing)
        return values[0] if len(values) == 1 else self._node("sequence", children=tuple(values))

    def _required_group(self) -> AstNode:
        if self.current.value != "{":
            raise FormulaParseError(
                "EXPECTED_GROUP",
                "This LaTeX command requires a braced group.",
                self.current.position,
            )
        self._advance()
        return self._group("{")

    def _required_group_text(self) -> str:
        if self.current.value != "{":
            raise FormulaParseError(
                "EXPECTED_GROUP",
                "This LaTeX command requires a braced name.",
                self.current.position,
            )
        self._advance()
        parts: list[str] = []
        depth = 1
        while depth:
            token = self._advance()
            if token.kind == "eof":
                raise FormulaParseError("UNCLOSED_GROUP", "A formula group was not closed.")
            if token.value == "{":
                depth += 1
            elif token.value == "}":
                depth -= 1
            elif depth == 1:
                parts.append(token.value)
        name = "".join(parts).strip()
        if not name:
            raise FormulaParseError("EMPTY_NAME", "A non-empty operator name is required.")
        return name

    def _script_argument(self) -> AstNode:
        if self.current.value == "{":
            self._advance()
            return self._group("{")
        return self._prefix()

    def _infix(self, operator: str, left: AstNode, right: AstNode) -> AstNode:
        if operator == "+":
            return self._associative("add", left, right)
        if operator == "-":
            return self._associative("add", left, self._node("negate", children=(right,)))
        if operator == "*":
            return self._associative("multiply", left, right)
        if operator == "/":
            return self._node("divide", children=(left, right))
        return self._node("equals", children=(left, right))

    def _associative(
        self,
        kind: Literal["add", "multiply"],
        left: AstNode,
        right: AstNode,
    ) -> AstNode:
        children: list[AstNode] = []
        children.extend(left.children if left.kind == kind else (left,))
        children.extend(right.children if right.kind == kind else (right,))
        return self._node(kind, children=tuple(children))

    def _advance(self) -> _Token:
        token = self.current
        if token.kind != "eof":
            self.index += 1
        return token

    def _expect(self, value: str) -> None:
        if self.current.value != value:
            raise FormulaParseError(
                "UNCLOSED_GROUP", f"Expected {value!r}.", self.current.position
            )
        self._advance()

    @staticmethod
    def _starts_atom(token: _Token) -> bool:
        return token.kind in {"number", "identifier", "function", "command"} or token.value in {
            "(",
            "{",
        }


def _bound_name(lower: AstNode | None) -> str | None:
    if lower is None:
        return None
    candidate = lower.children[0] if lower.kind == "equals" else lower
    while candidate.kind == "subscript" and candidate.children:
        candidate = candidate.children[0]
    return candidate.value if candidate.kind == "symbol" else None


def _analyze_symbols(
    root: AstNode,
) -> tuple[set[str], set[str], list[ParsedSymbol]]:
    free: set[str] = set()
    bound: set[str] = set()
    collected: dict[str, ParsedSymbol] = {}
    category_rank = {
        "scalar": 0,
        "index": 1,
        "vector": 2,
        "matrix": 3,
        "tensor": 4,
        "function": 5,
        "distribution": 6,
    }

    def collect(symbol: ParsedSymbol) -> None:
        existing = collected.get(symbol.name)
        if existing is None or category_rank[symbol.category] > category_rank[existing.category]:
            collected[symbol.name] = symbol

    def visit(node: AstNode, scope: frozenset[str], context: str = "value") -> None:
        if node.kind in {"sum", "product", "integral"}:
            binder = node.attribute("binder")
            body = node.children[-1] if node.children else None
            local_scope = scope | ({binder} if binder else set())
            if binder:
                bound.add(binder)
                collect(ParsedSymbol(binder, "index"))
            for child in node.children[:-1]:
                visit(child, local_scope)
            if body is not None:
                visit(body, local_scope)
            return
        if node.kind in {"call", "distribution"} and node.children:
            function = node.children[0]
            if function.kind == "symbol" and function.value:
                category = "distribution" if node.kind == "distribution" else "function"
                collect(
                    ParsedSymbol(
                        function.value,
                        category,
                        style=function.attribute("style"),
                    )
                )
            else:
                visit(function, scope, "function")
            for argument in node.children[1:]:
                visit(argument, scope)
            return
        if node.kind == "subscript" and len(node.children) == 2:
            base, index = node.children
            base_name = _symbol_name(base)
            indices = tuple(sorted(_symbol_names(index)))
            if base_name:
                category = _indexed_category(base, len(indices))
                collect(
                    ParsedSymbol(
                        base_name,
                        category,
                        indices=indices,
                        style=base.attribute("style"),
                    )
                )
                if base_name not in scope and base_name not in _CONSTANTS:
                    free.add(base_name)
            else:
                visit(base, scope)
            visit(index, scope, "index")
            return
        if node.kind == "power" and node.attribute("operation") == "transpose":
            if node.children:
                visit(node.children[0], scope, context)
            return
        if node.kind == "symbol" and node.value:
            if node.value in _CONSTANTS:
                return
            if context == "function":
                collect(ParsedSymbol(node.value, "function", style=node.attribute("style")))
                return
            category = "index" if context in {"index", "bound"} else _plain_category(node)
            collect(ParsedSymbol(node.value, category, style=node.attribute("style")))
            if node.value not in scope:
                free.add(node.value)
            return
        for child in node.children:
            visit(child, scope, context)

    visit(root, frozenset())
    ordered = [collected[name] for name in sorted(collected)]
    return free, bound, ordered


def _symbol_name(node: AstNode) -> str | None:
    if node.kind == "symbol":
        return node.value
    if node.kind == "style" and len(node.children) == 1:
        return _symbol_name(node.children[0])
    return None


def _symbol_names(node: AstNode) -> set[str]:
    names: set[str] = set()
    if node.kind == "symbol" and node.value:
        names.add(node.value)
    for child in node.children:
        names.update(_symbol_names(child))
    return names


def _plain_category(node: AstNode) -> Literal["scalar", "vector", "matrix", "tensor"]:
    style = node.attribute("style")
    if style == "calligraphy":
        return "tensor"
    if style == "vector":
        return "vector"
    if style == "bold":
        return "matrix" if node.value and node.value[:1].isupper() else "vector"
    return "matrix" if node.value and node.value[:1].isupper() else "scalar"


def _indexed_category(
    base: AstNode, index_count: int
) -> Literal["scalar", "vector", "matrix", "tensor"]:
    style = base.attribute("style")
    if style == "calligraphy" or index_count >= 3:
        return "tensor"
    if style == "bold" and base.value and base.value[:1].isupper():
        return "matrix"
    if index_count >= 2:
        return "matrix"
    return "vector" if index_count == 1 or style in {"bold", "vector"} else "scalar"


def _mathml_to_latex(source: str) -> str:
    if "<!DOCTYPE" in source.upper() or "<!ENTITY" in source.upper():
        raise FormulaParseError("UNSAFE_MATHML", "DTD and entity declarations are not allowed.")
    try:
        parser = etree.XMLParser(
            resolve_entities=False,
            no_network=True,
            recover=False,
            huge_tree=False,
        )
        root = etree.fromstring(source.encode("utf-8"), parser=parser)
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise FormulaParseError("MALFORMED_MATHML", "MathML could not be parsed.") from exc
    count = 0

    def convert(element: etree._Element, depth: int = 0) -> str:
        nonlocal count
        count += 1
        if count > MAX_AST_NODES or depth > MAX_PARSE_DEPTH:
            raise FormulaParseError("MATHML_TOO_COMPLEX", "MathML exceeds parser limits.")
        tag = etree.QName(element).localname
        children = [child for child in element if isinstance(child.tag, str)]
        if tag in {"math", "mrow", "semantics"}:
            usable = children[:1] if tag == "semantics" else children
            return " ".join(convert(child, depth + 1) for child in usable)
        if tag in {"mi", "mn"}:
            value = "".join(element.itertext()).strip()
            if not value:
                raise FormulaParseError("EMPTY_MATHML_TOKEN", "MathML token is empty.")
            return value
        if tag == "mo":
            value = "".join(element.itertext()).strip()
            return {"×": "*", "·": "*", "−": "-", "⁢": "*"}.get(value, value)
        if tag == "mfrac" and len(children) == 2:
            numerator = convert(children[0], depth + 1)
            denominator = convert(children[1], depth + 1)
            return f"\\frac{{{numerator}}}{{{denominator}}}"
        if tag == "msqrt" and children:
            return f"\\sqrt{{{' '.join(convert(child, depth + 1) for child in children)}}}"
        if tag == "msub" and len(children) == 2:
            return f"{{{convert(children[0], depth + 1)}}}_{{{convert(children[1], depth + 1)}}}"
        if tag == "msup" and len(children) == 2:
            return f"{{{convert(children[0], depth + 1)}}}^{{{convert(children[1], depth + 1)}}}"
        if tag == "msubsup" and len(children) == 3:
            return (
                f"{{{convert(children[0], depth + 1)}}}_{{{convert(children[1], depth + 1)}}}"
                f"^{{{convert(children[2], depth + 1)}}}"
            )
        if tag == "mfenced":
            opening = element.get("open", "(")
            closing = element.get("close", ")")
            separator = element.get("separators", ",")[:1] or ","
            content = separator.join(convert(child, depth + 1) for child in children)
            return opening + content + closing
        if tag == "annotation":
            return ""
        raise FormulaParseError("UNSUPPORTED_MATHML", f"Unsupported MathML element <{tag}>.")

    latex = convert(root).strip()
    if not latex:
        raise FormulaParseError("EMPTY_FORMULA", "MathML does not contain a formula.")
    return latex


# ---------------------------------------------------------------------------
# FGL-402  Canonical identity
# ---------------------------------------------------------------------------


def canonicalize(root: AstNode) -> AstNode:
    """Alpha-rename bound variables and sort commutative children.

    Returns a new tree that can be compared structurally for equivalence.
    """
    return _canonicalize_node(root, {}, _BoundCounter())


class _BoundCounter:
    """Mutable counter for generating canonical bound variable names."""

    def __init__(self) -> None:
        self.n = 0

    def next(self) -> str:
        name = f"_b{self.n}"
        self.n += 1
        return name


def _canonicalize_node(
    node: AstNode,
    renames: dict[str, str],
    counter: _BoundCounter,
) -> AstNode:
    if node.kind in {"sum", "product", "integral"}:
        binder = node.attribute("binder")
        local_renames = dict(renames)
        new_binder: str | None = None
        if binder:
            new_binder = counter.next()
            local_renames[binder] = new_binder

        new_children: list[AstNode] = []
        for child in node.children:
            new_children.append(_canonicalize_node(child, local_renames, counter))

        new_attrs = tuple(
            ("binder", new_binder) if k == "binder" and new_binder else (k, v)
            for k, v in node.attributes
        )
        return AstNode(node.kind, node.value, tuple(new_children), new_attrs)

    if node.kind == "symbol" and node.value:
        renamed = renames.get(node.value, node.value)
        return AstNode(node.kind, renamed, node.children, node.attributes)

    new_children = [_canonicalize_node(c, renames, counter) for c in node.children]

    # Addition is commutative for supported numeric/tensor values. Multiplication
    # is reordered only when every operand is provably scalar; matrix/tensor order
    # must remain significant.
    if node.kind == "add" or (
        node.kind == "multiply" and all(_is_provably_scalar(child) for child in new_children)
    ):
        new_children.sort(key=_ast_to_json)

    return AstNode(node.kind, node.value, tuple(new_children), node.attributes)


def _ast_to_json(node: AstNode) -> str:
    """Deterministic JSON representation used for sorting and hashing."""
    return _json_mod.dumps(
        node.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _is_provably_scalar(node: AstNode) -> bool:
    if node.kind == "number":
        return True
    if node.kind == "symbol":
        return (
            node.attribute("style") is None
            and node.attribute("role") != "function"
            and bool(node.value)
            and node.value[:1].islower()
        )
    if node.kind in {"negate", "add", "multiply", "divide", "power"}:
        return bool(node.children) and all(_is_provably_scalar(child) for child in node.children)
    return False


def ast_node_count(root: AstNode) -> int:
    """Return the number of nodes without recursion-dependent global state."""
    return 1 + sum(ast_node_count(child) for child in root.children)


def compute_canonical_hash(root: AstNode) -> str:
    """Canonical SHA-256 hash of the alpha-renamed, commutative-sorted tree."""
    canonical = canonicalize(root)
    json_bytes = _ast_to_json(canonical).encode("utf-8")
    return hashlib.sha256(json_bytes).hexdigest()


def ast_to_sympy(node: AstNode):  # noqa: ANN201 — returns sympy.Expr
    """Convert an ``AstNode`` tree to a SymPy expression.

    Raises ``FormulaParseError`` with code ``SYMPY_UNSUPPORTED`` for node kinds
    that cannot be represented in SymPy.
    """
    import sympy

    if node.kind == "number":
        return sympy.Number(node.value)

    if node.kind == "symbol":
        if node.value in _CONSTANTS:
            mapping = {"pi": sympy.pi, "infty": sympy.oo, "infinity": sympy.oo}
            return mapping.get(node.value, sympy.Symbol(node.value))
        return sympy.Symbol(node.value, commutative=_is_provably_scalar(node))

    if node.kind == "add":
        return sympy.Add(*(ast_to_sympy(c) for c in node.children))

    if node.kind == "multiply":
        return sympy.Mul(*(ast_to_sympy(c) for c in node.children))

    if node.kind == "divide":
        if len(node.children) != 2:
            raise FormulaParseError("SYMPY_UNSUPPORTED", "Division requires two operands.")
        return ast_to_sympy(node.children[0]) / ast_to_sympy(node.children[1])

    if node.kind == "power":
        if len(node.children) != 2:
            raise FormulaParseError("SYMPY_UNSUPPORTED", "Power requires two operands.")
        if node.attribute("operation") == "transpose":
            raise FormulaParseError(
                "SYMPY_UNSUPPORTED",
                "Symbolic transpose requires confirmed matrix contracts.",
            )
        return sympy.Pow(ast_to_sympy(node.children[0]), ast_to_sympy(node.children[1]))

    if node.kind == "negate":
        return -ast_to_sympy(node.children[0])

    if node.kind == "equals":
        if len(node.children) != 2:
            raise FormulaParseError("SYMPY_UNSUPPORTED", "Equality requires two sides.")
        lhs = ast_to_sympy(node.children[0])
        rhs = ast_to_sympy(node.children[1])
        return sympy.Eq(lhs, rhs)

    if node.kind == "call":
        if not node.children:
            raise FormulaParseError("SYMPY_UNSUPPORTED", "Function call with no children.")
        func_node = node.children[0]
        args = [ast_to_sympy(c) for c in node.children[1:]]
        name = func_node.value or "f"
        builtin = {
            "sin": sympy.sin,
            "cos": sympy.cos,
            "tan": sympy.tan,
            "exp": sympy.exp,
            "log": sympy.log,
            "ln": sympy.ln,
            "sqrt": sympy.sqrt,
            "det": sympy.Function("det"),
            "tr": sympy.Function("tr"),
        }
        fn = builtin.get(name)
        if fn is not None:
            if callable(fn) and not isinstance(fn, sympy.Function):
                return fn(*args)
            return fn(*args)
        return sympy.Function(name)(*args)

    if node.kind == "sum":
        binder = node.attribute("binder")
        if binder and len(node.children) >= 3:
            var = sympy.Symbol(binder)
            lower = ast_to_sympy(node.children[0])
            upper = ast_to_sympy(node.children[1])
            body = ast_to_sympy(node.children[2])
            # Extract the starting value from an equality like `i=1`.
            if isinstance(lower, sympy.Eq):
                lower = lower.rhs
            return sympy.Sum(body, (var, lower, upper))
        raise FormulaParseError(
            "SYMPY_UNSUPPORTED", "Sum binder requires lower, upper, and body.",
        )

    if node.kind == "product":
        binder = node.attribute("binder")
        if binder and len(node.children) >= 3:
            var = sympy.Symbol(binder)
            lower = ast_to_sympy(node.children[0])
            upper = ast_to_sympy(node.children[1])
            body = ast_to_sympy(node.children[2])
            if isinstance(lower, sympy.Eq):
                lower = lower.rhs
            return sympy.Product(body, (var, lower, upper))
        raise FormulaParseError(
            "SYMPY_UNSUPPORTED", "Product binder requires lower, upper, and body.",
        )

    if node.kind == "subscript":
        # Represent subscripted symbols as indexed SymPy Symbols.
        base = node.children[0] if node.children else node
        name = base.value or "x"
        idx_parts: list[str] = []
        if len(node.children) >= 2:
            _collect_index_names(node.children[1], idx_parts)
        indexed_name = f"{name}_{'_'.join(idx_parts)}" if idx_parts else name
        return sympy.Symbol(indexed_name)

    if node.kind == "sequence":
        # Return a tuple of converted children.
        return sympy.Tuple(*(ast_to_sympy(c) for c in node.children))

    if node.kind == "style":
        if node.children:
            return ast_to_sympy(node.children[0])
        raise FormulaParseError("SYMPY_UNSUPPORTED", "Empty style node.")

    if node.kind == "distribution":
        # Treat distributions as named functions.
        if node.children:
            name = node.children[0].value or "D"
            args = [ast_to_sympy(c) for c in node.children[1:]]
            return sympy.Function(name)(*args)
        raise FormulaParseError("SYMPY_UNSUPPORTED", "Empty distribution node.")

    if node.kind == "integral":
        binder = node.attribute("binder")
        if binder and len(node.children) >= 3:
            var = sympy.Symbol(binder)
            lower = ast_to_sympy(node.children[0])
            upper = ast_to_sympy(node.children[1])
            body = ast_to_sympy(node.children[2])
            return sympy.Integral(body, (var, lower, upper))
        raise FormulaParseError(
            "SYMPY_UNSUPPORTED", "Integral binder requires lower, upper, and body.",
        )

    raise FormulaParseError(
        "SYMPY_UNSUPPORTED", f"Cannot convert {node.kind!r} to SymPy.",
    )


def _collect_index_names(node: AstNode, out: list[str]) -> None:
    """Gather index symbol names from a subscript index node."""
    if node.kind == "symbol" and node.value:
        out.append(node.value)
    elif node.kind == "sequence":
        for child in node.children:
            _collect_index_names(child, out)
    elif node.kind == "number" and node.value:
        out.append(node.value)
    else:
        for child in node.children:
            _collect_index_names(child, out)


def sympy_equivalent(a: AstNode, b: AstNode) -> bool | None:
    """Check algebraic equivalence of two ASTs using SymPy.

    Returns ``True`` if equivalent, ``False`` if not, or ``None`` when the
    conversion to SymPy fails for either tree.
    """
    import sympy

    # Symbolic simplification can grow rapidly. Structural hashing remains
    # available for larger inputs, while the optional CAS check stays bounded.
    if ast_node_count(a) > 256 or ast_node_count(b) > 256:
        return None

    try:
        expr_a = ast_to_sympy(a)
        expr_b = ast_to_sympy(b)
    except FormulaParseError:
        return None

    # Handle Eq objects.
    if isinstance(expr_a, sympy.Eq) and isinstance(expr_b, sympy.Eq):
        return bool(
            sympy.simplify(expr_a.lhs - expr_b.lhs) == 0
            and sympy.simplify(expr_a.rhs - expr_b.rhs) == 0
        )

    if isinstance(expr_a, sympy.Eq) or isinstance(expr_b, sympy.Eq):
        return False

    try:
        return bool(sympy.simplify(expr_a - expr_b) == 0)
    except (TypeError, AttributeError):
        return None
