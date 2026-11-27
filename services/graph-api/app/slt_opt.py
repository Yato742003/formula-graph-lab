"""Standard 2: Dual Tree Representation — Symbol Layout Tree (SLT) & W3C MathML 4 intent.

Provides:
- ``SymbolLayoutTree`` (SLT): 100%-coverage structural visual/layout tree parsed from
  LaTeXML Presentation MathML with a total deterministic raw-LaTeX layout scanner
  fallback. Computes Tangent-S style layout path tuples and canonical ``slt_hash``
  for cross-paper formula search and graph visualization even when algebraic CAS parsing
  cannot classify exotic notation.
- ``annotate_w3c_intent``: Generates W3C MathML 4 Working Draft ``intent`` semantic
  annotations over the Operator Tree (OPT / ``AstNode``), resolved symbol contracts, and
  optional LaCASt prose context hints.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from lxml import etree

from app.formula_ast import AstNode, ParsedFormula
from app.symbol_contracts import SymbolContract

SLT_HASH_VERSION = "slt-hash.v1"
W3C_INTENT_VERSION = "w3c-mathml4-intent.v1"
MAX_SLT_NODES = 2_048
MAX_SLT_DEPTH = 64
MAX_LAYOUT_PATHS = 128

_CMD_TOKEN_RE = re.compile(r"\\(?:[A-Za-z]+|[^A-Za-z\s])")
_SCRIPT_ARG_EMPTY = ()
_LAYOUT_ACCENT_CMDS = frozenset(
    {"hat", "widehat", "tilde", "widetilde", "bar", "overline", "vec"}
)
_LAYOUT_WRAPPER_CMDS = frozenset(
    {
        "text",
        "textrm",
        "operatorname",
        "mathrm",
        "mathbf",
        "boldsymbol",
        "bm",
        "mathbb",
        "mathcal",
        *_LAYOUT_ACCENT_CMDS,
    }
)


@dataclass(frozen=True)
class SltNode:
    """Canonical Symbol Layout Tree node (MathML-aligned structural representation)."""

    kind: str
    value: str | None = None
    children: tuple[SltNode, ...] = ()

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"kind": self.kind}
        if self.value is not None:
            payload["value"] = self.value
        if self.children:
            payload["children"] = [child.to_dict() for child in self.children]
        return payload


@dataclass(frozen=True)
class SymbolLayoutTree:
    """Versioned SLT representation with Tangent-S structural paths and canonical hash."""

    root: SltNode
    slt_hash: str
    slt_hash_version: str
    source_kind: Literal["presentation_mathml", "latex_layout"]
    node_count: int
    symbol_tokens: tuple[str, ...]
    layout_paths: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "slt_hash": self.slt_hash,
            "slt_hash_version": self.slt_hash_version,
            "source_kind": self.source_kind,
            "node_count": self.node_count,
            "symbol_tokens": list(self.symbol_tokens),
            "layout_paths": list(self.layout_paths),
            "root": self.root.to_dict(),
        }


class _SltBuilder:
    def __init__(self) -> None:
        self.count = 0

    def node(
        self,
        kind: str,
        value: str | None = None,
        children: Sequence[SltNode] = _SCRIPT_ARG_EMPTY,
    ) -> SltNode:
        self.count += 1
        if self.count > MAX_SLT_NODES:
            return SltNode("truncated")
        return SltNode(kind=kind, value=value, children=tuple(children))


def _is_unsafe_xml(xml_text: str) -> bool:
    upper = xml_text.upper()
    return "<!DOCTYPE" in upper or "<!ENTITY" in upper


def _presentation_mathml_to_slt(presentation_mathml: str) -> SltNode | None:
    """Parse LaTeXML Presentation MathML safely into canonical ``SltNode`` layout."""
    if not presentation_mathml or _is_unsafe_xml(presentation_mathml):
        return None
    try:
        parser = etree.XMLParser(
            resolve_entities=False,
            no_network=True,
            recover=False,
            huge_tree=False,
        )
        root_el = etree.fromstring(presentation_mathml.encode("utf-8"), parser=parser)
    except (etree.XMLSyntaxError, ValueError):
        return None

    builder = _SltBuilder()

    def walk(el: etree._Element, depth: int) -> SltNode | None:
        if depth > MAX_SLT_DEPTH or builder.count >= MAX_SLT_NODES:
            return SltNode("truncated")
        tag = etree.QName(el).localname
        children = [c for c in el if isinstance(c.tag, str)]

        if tag in {"math", "mrow", "mstyle", "mpadded", "mphantom", "merror"}:
            converted = [n for c in children if (n := walk(c, depth + 1)) is not None]
            if not converted:
                text = "".join(el.itertext()).strip()
                return builder.node("row", text) if text else builder.node("row")
            if len(converted) == 1:
                return converted[0]
            return builder.node("row", children=converted)

        if tag == "semantics":
            if not children:
                return builder.node("row")
            return walk(children[0], depth + 1)

        if tag in {"annotation", "annotation-xml"}:
            return None

        if tag == "mi":
            text = "".join(el.itertext()).strip()
            return builder.node("id", text or "?")

        if tag == "mn":
            text = "".join(el.itertext()).strip()
            return builder.node("num", text or "0")

        if tag == "mo":
            text = "".join(el.itertext()).strip()
            if text in {"\u2061", "\u2062", "\u2063", ""}:
                return builder.node("op", text or "apply")
            return builder.node("op", text)

        if tag in {"mtext", "ms"}:
            text = "".join(el.itertext()).strip()
            return builder.node("text", text or "")

        if tag == "msub" and len(children) >= 2:
            base = walk(children[0], depth + 1) or builder.node("row")
            sub = walk(children[1], depth + 1) or builder.node("row")
            return builder.node("sub", children=(base, sub))

        if tag == "msup" and len(children) >= 2:
            base = walk(children[0], depth + 1) or builder.node("row")
            sup = walk(children[1], depth + 1) or builder.node("row")
            return builder.node("sup", children=(base, sup))

        if tag == "msubsup" and len(children) >= 3:
            base = walk(children[0], depth + 1) or builder.node("row")
            sub = walk(children[1], depth + 1) or builder.node("row")
            sup = walk(children[2], depth + 1) or builder.node("row")
            return builder.node("subsup", children=(base, sub, sup))

        if tag == "mfrac" and len(children) >= 2:
            num = walk(children[0], depth + 1) or builder.node("row")
            den = walk(children[1], depth + 1) or builder.node("row")
            return builder.node("frac", children=(num, den))

        if tag == "msqrt":
            converted = [n for c in children if (n := walk(c, depth + 1)) is not None]
            inner = (
                converted[0]
                if len(converted) == 1
                else builder.node("row", children=converted)
            )
            return builder.node("sqrt", children=(inner,))

        if tag == "mroot" and len(children) >= 2:
            rad = walk(children[0], depth + 1) or builder.node("row")
            deg = walk(children[1], depth + 1) or builder.node("row")
            return builder.node("sqrt", children=(rad, deg))

        if tag in {"munder", "mover"} and len(children) >= 2:
            kind = "under" if tag == "munder" else "over"
            base = walk(children[0], depth + 1) or builder.node("row")
            script = walk(children[1], depth + 1) or builder.node("row")
            return builder.node(kind, children=(base, script))

        if tag == "munderover" and len(children) >= 3:
            base = walk(children[0], depth + 1) or builder.node("row")
            sub = walk(children[1], depth + 1) or builder.node("row")
            sup = walk(children[2], depth + 1) or builder.node("row")
            return builder.node("underover", children=(base, sub, sup))

        if tag in {"mtable", "mtr", "mtd"}:
            kind = {"mtable": "table", "mtr": "row", "mtd": "row"}[tag]
            converted = [n for c in children if (n := walk(c, depth + 1)) is not None]
            return builder.node(kind, children=converted)

        if tag == "mfenced":
            converted = [n for c in children if (n := walk(c, depth + 1)) is not None]
            fence_val = (el.get("open", "(") or "(") + (el.get("close", ")") or ")")
            return builder.node("fence", fence_val, children=converted)

        converted = [n for c in children if (n := walk(c, depth + 1)) is not None]
        if converted:
            return builder.node(tag, children=converted)
        text = "".join(el.itertext()).strip()
        return builder.node(tag, text or None)

    result = walk(root_el, 0)
    return result if result is not None else builder.node("row")


def _latex_to_slt(latex: str) -> SltNode:
    """Total, deterministic raw-LaTeX visual layout parser (never raises)."""
    src = (latex or "").strip()
    builder = _SltBuilder()
    length = len(src)

    def parse_seq(pos: int, stop_tokens: set[str], depth: int) -> tuple[SltNode, int]:
        if depth > MAX_SLT_DEPTH or builder.count >= MAX_SLT_NODES:
            return SltNode("truncated"), length
        items: list[SltNode] = []
        cur = pos
        while cur < length and builder.count < MAX_SLT_NODES:
            if src[cur].isspace():
                cur += 1
                continue
            if src[cur] in stop_tokens:
                break
            if any(len(st) > 1 and src.startswith(st, cur) for st in stop_tokens):
                break
            atom, cur = parse_atom_with_scripts(cur, depth + 1)
            items.append(atom)
        if not items:
            return builder.node("row"), cur
        if len(items) == 1:
            return items[0], cur
        return builder.node("row", children=items), cur

    def parse_group_or_atom(pos: int, depth: int) -> tuple[SltNode, int]:
        while pos < length and src[pos].isspace():
            pos += 1
        if pos >= length:
            return builder.node("row"), pos
        if src[pos] == "{":
            inner, end = parse_seq(pos + 1, {"}"}, depth + 1)
            if end < length and src[end] == "}":
                end += 1
            return inner, end
        return parse_atom(pos, depth + 1)

    def parse_atom_with_scripts(pos: int, depth: int) -> tuple[SltNode, int]:
        base, cur = parse_atom(pos, depth + 1)
        sub: SltNode | None = None
        sup: SltNode | None = None
        while cur < length and builder.count < MAX_SLT_NODES:
            while cur < length and src[cur].isspace():
                cur += 1
            if cur >= length or src[cur] not in {"_", "^"}:
                break
            op = src[cur]
            script, cur = parse_group_or_atom(cur + 1, depth + 1)
            if op == "_" and sub is None:
                sub = script
            elif op == "^" and sup is None:
                sup = script
            else:
                if sub is not None and sup is not None:
                    base = builder.node("subsup", children=(base, sub, sup))
                    sub = None
                    sup = None
                elif sub is not None:
                    base = builder.node("sub", children=(base, sub))
                    sub = None
                elif sup is not None:
                    base = builder.node("sup", children=(base, sup))
                    sup = None
                if op == "_":
                    sub = script
                else:
                    sup = script
        if sub is not None and sup is not None:
            return builder.node("subsup", children=(base, sub, sup)), cur
        if sub is not None:
            return builder.node("sub", children=(base, sub)), cur
        if sup is not None:
            return builder.node("sup", children=(base, sup)), cur
        return base, cur

    def parse_atom(pos: int, depth: int) -> tuple[SltNode, int]:
        if depth > MAX_SLT_DEPTH or builder.count >= MAX_SLT_NODES:
            return SltNode("truncated"), length
        while pos < length and src[pos].isspace():
            pos += 1
        if pos >= length:
            return builder.node("row"), pos
        ch = src[pos]
        if ch == "{":
            inner, end = parse_seq(pos + 1, {"}"}, depth + 1)
            if end < length and src[end] == "}":
                end += 1
            return inner, end
        if ch == "\\":
            if src.startswith("\\\\", pos):
                return builder.node("op", "\\\\"), pos + 2
            m = _CMD_TOKEN_RE.match(src, pos)
            cmd = m.group(0)[1:] if m else ""
            cur = m.end() if m else pos + 1
            if cmd in {"frac", "dfrac", "tfrac", "binom"}:
                num, cur = parse_group_or_atom(cur, depth + 1)
                den, cur = parse_group_or_atom(cur, depth + 1)
                kind = "frac" if cmd != "binom" else "table"
                return builder.node(kind, children=(num, den)), cur
            if cmd == "sqrt":
                while cur < length and src[cur].isspace():
                    cur += 1
                deg: SltNode | None = None
                if cur < length and src[cur] == "[":
                    deg, cur = parse_seq(cur + 1, {"]"}, depth + 1)
                    if cur < length and src[cur] == "]":
                        cur += 1
                rad, cur = parse_group_or_atom(cur, depth + 1)
                return builder.node(
                    "sqrt",
                    children=(rad, deg) if deg is not None else (rad,),
                ), cur
            if cmd == "begin":
                env_node, cur = parse_group_or_atom(cur, depth + 1)
                env_name = env_node.value or "env"
                body, cur = parse_seq(cur, {r"\end"}, depth + 1)
                if src.startswith(r"\end", cur):
                    _, cur = parse_group_or_atom(cur + 4, depth + 1)
                return builder.node("table", env_name, children=(body,)), cur
            if cmd in {"left", "right"}:
                if cur < length and src[cur] == "\\":
                    m2 = _CMD_TOKEN_RE.match(src, cur)
                    delim = m2.group(0) if m2 else src[cur]
                    cur = m2.end() if m2 else cur + 1
                elif cur < length:
                    delim = src[cur]
                    cur += 1
                else:
                    delim = ""
                return builder.node("fence", delim or cmd), cur
            if cmd in _LAYOUT_WRAPPER_CMDS:
                arg, cur = parse_group_or_atom(cur, depth + 1)
                kind = (
                    "text"
                    if cmd in {"text", "textrm"}
                    else "over"
                    if cmd in _LAYOUT_ACCENT_CMDS
                    else "id"
                )
                return builder.node(kind, cmd, children=(arg,)), cur
            return builder.node("id" if cmd.isalpha() else "op", f"\\{cmd}" if cmd else "\\"), cur
        if ch.isdigit():
            end = pos + 1
            while end < length and (
                src[end].isdigit()
                or (src[end] == "." and end + 1 < length and src[end + 1].isdigit())
            ):
                end += 1
            return builder.node("num", src[pos:end]), end
        if ch.isalpha() or ord(ch) > 127:
            return builder.node("id", ch), pos + 1
        return builder.node("op", ch), pos + 1

    root, _ = parse_seq(0, set(), 0)
    return root


def _node_token_label(node: SltNode) -> str:
    if node.value:
        return node.value
    if node.kind == "row" and node.children:
        return _node_token_label(node.children[0])
    return node.kind


def _collect_slt_stats(root: SltNode) -> tuple[int, tuple[str, ...], tuple[str, ...]]:
    count = 0
    tokens: list[str] = []
    seen_tokens: set[str] = set()
    paths: set[str] = set()

    def walk(node: SltNode, depth: int) -> None:
        nonlocal count
        count += 1
        if node.kind in {"id", "num", "op"} and node.value and node.value not in seen_tokens:
            seen_tokens.add(node.value)
            tokens.append(node.value)
        if depth > MAX_SLT_DEPTH:
            return
        if node.kind in {"sub", "sup", "subsup"} and len(node.children) >= 2:
            base_tok = _node_token_label(node.children[0])
            sub_tok = _node_token_label(node.children[1])
            paths.add(f"{node.kind}:base:{base_tok}")
            paths.add(f"{node.kind}:sub:{base_tok}->{sub_tok}")
            if len(node.children) >= 3:
                sup_tok = _node_token_label(node.children[2])
                paths.add(f"{node.kind}:sup:{base_tok}->{sup_tok}")
        elif node.kind == "frac" and len(node.children) >= 2:
            paths.add(f"frac:num:{_node_token_label(node.children[0])}")
            paths.add(f"frac:den:{_node_token_label(node.children[1])}")
        elif node.kind == "row" and len(node.children) >= 2:
            for left, right in zip(node.children[:-1], node.children[1:], strict=True):
                paths.add(f"next:{_node_token_label(left)}->{_node_token_label(right)}")
                if len(paths) >= MAX_LAYOUT_PATHS:
                    break
        for child in node.children:
            if len(paths) >= MAX_LAYOUT_PATHS and count >= MAX_SLT_NODES:
                break
            walk(child, depth + 1)

    walk(root, 0)
    return count, tuple(tokens[:MAX_LAYOUT_PATHS]), tuple(sorted(paths)[:MAX_LAYOUT_PATHS])


def build_slt(
    latex: str,
    *,
    presentation_mathml: str | None = None,
) -> SymbolLayoutTree:
    """Build a 100%-coverage canonical Symbol Layout Tree from Presentation MathML or LaTeX."""
    slt_root = _presentation_mathml_to_slt(presentation_mathml) if presentation_mathml else None
    source_kind: Literal["presentation_mathml", "latex_layout"]
    if slt_root is not None:
        source_kind = "presentation_mathml"
    else:
        slt_root = _latex_to_slt(latex)
        source_kind = "latex_layout"

    node_count, symbol_tokens, layout_paths = _collect_slt_stats(slt_root)
    digest_payload = {
        "slt_hash_version": SLT_HASH_VERSION,
        "root": slt_root.to_dict(),
    }
    slt_hash = hashlib.sha256(
        json.dumps(
            digest_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return SymbolLayoutTree(
        root=slt_root,
        slt_hash=slt_hash,
        slt_hash_version=SLT_HASH_VERSION,
        source_kind=source_kind,
        node_count=node_count,
        symbol_tokens=symbol_tokens,
        layout_paths=layout_paths,
    )


def slt_similarity(left: SymbolLayoutTree, right: SymbolLayoutTree) -> float:
    """Compute Tangent-S style Jaccard similarity in ``[0.0, 1.0]`` over layout paths/tokens."""
    if left.slt_hash == right.slt_hash:
        return 1.0
    use_paths = bool(left.layout_paths or right.layout_paths)
    set_l = set(left.layout_paths) if use_paths else set(left.symbol_tokens)
    set_r = set(right.layout_paths) if use_paths else set(right.symbol_tokens)
    union = set_l | set_r
    if not union:
        return 0.0
    return len(set_l & set_r) / len(union)


def _join_subscript_label(node: AstNode) -> str | None:
    if node.kind == "symbol" and node.value:
        return node.value
    if node.kind == "number" and node.value:
        return node.value
    if node.kind == "style" and len(node.children) == 1:
        return _join_subscript_label(node.children[0])
    if node.kind == "subscript" and len(node.children) == 2:
        left = _join_subscript_label(node.children[0])
        right = _join_subscript_label(node.children[1])
        return f"{left}_{right}" if left and right else None
    if node.kind == "sequence" and node.children:
        parts = [p for c in node.children if (p := _join_subscript_label(c)) is not None]
        return "_".join(parts) if parts else None
    return None


def annotate_w3c_intent(
    parsed: ParsedFormula,
    contracts: Sequence[SymbolContract] = (),
    context_hints: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Generate W3C MathML 4 Working Draft ``intent`` semantic annotations over the OPT."""
    contract_by_name = {c.name: c for c in contracts}
    hints_map = context_hints or {}
    symbol_intents: dict[str, str] = {}
    node_intents: list[dict[str, str]] = []

    for sym in parsed.symbols:
        hint = hints_map.get(sym.name)
        hint_intent = getattr(hint, "w3c_intent", None)
        if hint_intent is None and isinstance(hint, Mapping):
            hint_intent = hint.get("w3c_intent")
        if isinstance(hint_intent, str) and hint_intent:
            symbol_intents[sym.name] = hint_intent
            continue
        contract = contract_by_name.get(sym.name)
        category = contract.category if contract is not None else sym.category
        domain = contract.domain if contract is not None else "real"
        if category == "matrix":
            symbol_intents[sym.name] = f"matrix({sym.name})"
        elif category == "vector":
            symbol_intents[sym.name] = f"vector({sym.name})"
        elif category == "tensor":
            symbol_intents[sym.name] = f"tensor({sym.name})"
        elif category == "function":
            symbol_intents[sym.name] = f"function({sym.name})"
        elif category == "distribution":
            symbol_intents[sym.name] = f"probability-distribution({sym.name})"
        elif category == "index":
            symbol_intents[sym.name] = f"index-variable({sym.name})"
        elif domain == "positive":
            symbol_intents[sym.name] = f"positive-scalar({sym.name})"
        elif domain == "integer":
            symbol_intents[sym.name] = f"integer-scalar({sym.name})"
        else:
            symbol_intents[sym.name] = f"scalar({sym.name})"

    def render_intent(node: AstNode, path: str, *, collect: bool = True) -> str:
        if node.kind == "number":
            return node.value or "0"
        if node.kind == "symbol":
            name = node.value or "x"
            return symbol_intents.get(name, name)
        if node.kind == "style" and node.children:
            return render_intent(node.children[0], path, collect=collect)
        if node.kind == "negate" and node.children:
            return f"minus({render_intent(node.children[0], f'{path}.0', collect=collect)})"
        if node.kind == "add":
            rendered = [
                render_intent(c, f"{path}.{i}", collect=collect)
                for i, c in enumerate(node.children)
            ]
            return f"plus({', '.join(rendered)})"
        if node.kind == "multiply":
            has_matrix = any(
                c.kind == "symbol"
                and (c_obj := contract_by_name.get(c.value or "")) is not None
                and c_obj.category == "matrix"
                for c in node.children
            )
            op = "matrix-product" if has_matrix or node.value == "times" else "times"
            rendered = [
                render_intent(c, f"{path}.{i}", collect=collect)
                for i, c in enumerate(node.children)
            ]
            return f"{op}({', '.join(rendered)})"
        if node.kind == "divide" and len(node.children) == 2:
            num = render_intent(node.children[0], f"{path}.0", collect=collect)
            den = render_intent(node.children[1], f"{path}.1", collect=collect)
            intent_str = f"fraction({num},{den})"
            if collect and len(node_intents) < 64:
                node_intents.append({"path": path, "kind": "divide", "intent": intent_str})
            return intent_str
        if node.kind == "power" and len(node.children) == 2:
            base_node, exp_node = node.children
            base_i = render_intent(base_node, f"{path}.0", collect=collect)
            if node.attribute("operation") == "transpose":
                intent_str = f"transpose({base_i})"
                if collect and len(node_intents) < 64:
                    node_intents.append({"path": path, "kind": "transpose", "intent": intent_str})
                return intent_str
            base_name = _join_subscript_label(base_node) or ""
            b_contract = contract_by_name.get(base_name)
            base_cat = b_contract.category if b_contract is not None else None
            if (
                base_cat in {"matrix", "vector", "tensor"}
                and exp_node.kind == "symbol"
                and exp_node.value
                and exp_node.value[:1].isupper()
            ):
                intent_str = f"matrix-projection({base_i},{exp_node.value})"
                if collect and len(node_intents) < 64:
                    node_intents.append({"path": path, "kind": "matrix_tag", "intent": intent_str})
                return intent_str
            exp_i = render_intent(exp_node, f"{path}.1", collect=collect)
            intent_str = f"power({base_i},{exp_i})"
            if collect and len(node_intents) < 64:
                node_intents.append({"path": path, "kind": "power", "intent": intent_str})
            return intent_str
        if node.kind == "subscript" and len(node.children) == 2:
            base_i = render_intent(node.children[0], f"{path}.0", collect=collect)
            idx_i = render_intent(node.children[1], f"{path}.1", collect=collect)
            intent_str = f"indexed-by({base_i},{idx_i})"
            joined = _join_subscript_label(node)
            if joined and joined not in symbol_intents:
                hint = hints_map.get(joined)
                hint_intent = getattr(hint, "w3c_intent", None)
                symbol_intents[joined] = (
                    hint_intent if isinstance(hint_intent, str) and hint_intent else intent_str
                )
            return intent_str
        if node.kind == "call" and node.children:
            fn = node.children[0]
            fn_name = (
                fn.value
                if fn.kind == "symbol" and fn.value
                else render_intent(fn, f"{path}.0", collect=False)
            )
            intent_fn = {
                "log": "logarithm",
                "ln": "natural-logarithm",
                "exp": "exponential",
                "sqrt": "square-root",
                "abs": "absolute-value",
                "norm": "norm",
                "floor": "floor",
                "ceil": "ceiling",
                "round": "round-to-nearest-integer",
                "softmax": "softmax",
            }.get(fn_name, fn_name)
            rendered = [
                render_intent(c, f"{path}.{i + 1}", collect=collect)
                for i, c in enumerate(node.children[1:])
            ]
            intent_str = f"{intent_fn}({','.join(rendered)})"
            if collect and len(node_intents) < 64:
                node_intents.append({"path": path, "kind": "call", "intent": intent_str})
            return intent_str
        if node.kind in {"sum", "product", "integral"} and node.children:
            binder = node.attribute("binder") or "i"
            op_map = {"sum": "summation", "product": "product", "integral": "definite-integral"}
            op = op_map[node.kind]
            body = render_intent(node.children[-1], f"{path}.body", collect=collect)
            intent_str = f"{op}({binder},{body})"
            if collect and len(node_intents) < 64:
                node_intents.append({"path": path, "kind": node.kind, "intent": intent_str})
            return intent_str
        if node.kind == "piecewise":
            rendered = [
                render_intent(c, f"{path}.{i}", collect=collect)
                for i, c in enumerate(node.children)
            ]
            intent_str = f"piecewise({','.join(rendered)})"
            if collect and len(node_intents) < 64:
                node_intents.append({"path": path, "kind": "piecewise", "intent": intent_str})
            return intent_str
        if node.kind == "equals" and len(node.children) == 2:
            lhs = render_intent(node.children[0], f"{path}.0", collect=collect)
            rhs = render_intent(node.children[1], f"{path}.1", collect=collect)
            return f"equation({lhs},{rhs})"
        if node.children:
            rendered = [
                render_intent(c, f"{path}.{i}", collect=collect)
                for i, c in enumerate(node.children)
            ]
            return f"{node.kind}({','.join(rendered)})"
        return node.kind

    root_intent = render_intent(parsed.root, "root")
    return {
        "intent_version": W3C_INTENT_VERSION,
        "root_intent": root_intent,
        "symbol_intents": symbol_intents,
        "node_intents": node_intents,
    }
