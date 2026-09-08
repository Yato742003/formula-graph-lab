from __future__ import annotations

from dataclasses import dataclass, field, replace
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
    if len(source) > MAX_FORMULA_CHARACTERS:
        raise FormulaParseError("FORMULA_TOO_LARGE", "The formula exceeds the parser limit.")
    latex = _mathml_to_latex(source) if source_format == "mathml" else source
    tokens = _tokenize_latex(latex)
    root = _FormulaParser(tokens).parse()
    free, bound, symbols = _analyze_symbols(root)
    return ParsedFormula(
        root=root,
        free_variables=tuple(sorted(free)),
        bound_variables=tuple(sorted(bound)),
        symbols=tuple(symbols),
        source_format=source_format,
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
            tokens.append(_Token("identifier", text[index:end], index))
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
                    left = self._node(
                        "subscript" if operator == "_" else "power",
                        children=(left, right),
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
                    if 20 < minimum_binding:
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
        raise FormulaParseError("EXPECTED_EXPRESSION", "Expected a formula expression.", token.position)

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
            role = "function" if command == "operatorname" or name in _FUNCTION_COMMANDS else "identifier"
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
        raise FormulaParseError("UNSUPPORTED_COMMAND", f"Unsupported command \\{command}.", token.position)

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
                "EXPECTED_GROUP", "This LaTeX command requires a braced group.", self.current.position
            )
        self._advance()
        return self._group("{")

    def _required_group_text(self) -> str:
        if self.current.value != "{":
            raise FormulaParseError(
                "EXPECTED_GROUP", "This LaTeX command requires a braced name.", self.current.position
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

    def _associative(self, kind: Literal["add", "multiply"], left: AstNode, right: AstNode) -> AstNode:
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
            for child in node.children[:-1]:
                visit(child, scope, "bound")
            if binder:
                bound.add(binder)
                collect(ParsedSymbol(binder, "index"))
            if body is not None:
                visit(body, scope | ({binder} if binder else set()))
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
        parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=False, huge_tree=False)
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
            return f"\\frac{{{convert(children[0], depth + 1)}}}{{{convert(children[1], depth + 1)}}}"
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
            return opening + separator.join(convert(child, depth + 1) for child in children) + closing
        if tag == "annotation":
            return ""
        raise FormulaParseError("UNSUPPORTED_MATHML", f"Unsupported MathML element <{tag}>.")

    latex = convert(root).strip()
    if not latex:
        raise FormulaParseError("EMPTY_FORMULA", "MathML does not contain a formula.")
    return latex
