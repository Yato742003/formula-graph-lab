"""FGL-403: Symbol contracts — shape, domain, and scope validation.

Each parsed formula can be annotated with inferred contracts describing the
mathematical role of every symbol (shape, domain, constraints).  Validation
functions detect shape-incompatible operations, unsafe denominators, and
cross-section symbol shadowing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from app.formula_ast import AstNode, ParsedFormula, ParsedSymbol

# The threshold below which a contract is flagged as *unconfirmed* and cannot
# participate in verified transformations without human review.
CONFIDENCE_THRESHOLD = 0.6

SymbolCategory = Literal[
    "scalar", "vector", "matrix", "tensor", "function", "distribution", "index",
]
SymbolDomain = Literal["real", "positive", "non_negative", "complex", "integer"]


class SymbolContract(BaseModel):
    """A typed contract describing a mathematical symbol's properties."""

    name: str
    category: SymbolCategory
    shape: tuple[int | str, ...] | None = None
    domain: SymbolDomain = "real"
    constraints: list[str] = Field(default_factory=list)
    scope: str | None = None  # section_id
    confidence: float = Field(ge=0, le=1, default=1.0)
    confirmed: bool = True

    model_config = {"frozen": True}


def apply_contract_confirmations(
    inferred: list[SymbolContract],
    confirmations: list[SymbolContract],
) -> list[SymbolContract]:
    """Apply explicit human-reviewed contracts to an inferred contract set."""
    inferred_names = {contract.name for contract in inferred}
    confirmation_names = [contract.name for contract in confirmations]
    if len(confirmation_names) != len(set(confirmation_names)):
        raise ValueError("Contract confirmations must have unique symbol names.")
    unknown = sorted(set(confirmation_names) - inferred_names)
    if unknown:
        raise ValueError(f"Contract confirmation references unknown symbol: {unknown[0]}")
    by_name = {contract.name: contract for contract in confirmations}
    return [
        by_name[contract.name].model_copy(
            update={
                "scope": by_name[contract.name].scope or contract.scope,
                "confidence": 1.0,
                "confirmed": True,
            }
        )
        if contract.name in by_name
        else contract
        for contract in inferred
    ]


# ── Validation result types ──────────────────────────────────────


@dataclass(frozen=True)
class ShapeError:
    """Raised when tensor dimensions are incompatible."""

    message: str
    node_path: str
    symbols: tuple[str, ...]


@dataclass(frozen=True)
class DomainError:
    """Raised when a symbol in a denominator can be zero."""

    message: str
    symbol: str
    location: str


@dataclass(frozen=True)
class ShadowWarning:
    """Raised when the same symbol name has conflicting contracts."""

    symbol: str
    section_a: str | None
    section_b: str | None
    contract_a: SymbolContract
    contract_b: SymbolContract
    reason: str


# ── Inference ────────────────────────────────────────────────────


def infer_contracts(
    formula: ParsedFormula,
    *,
    section_id: str | None = None,
    extraction_confidence: float = 1.0,
) -> list[SymbolContract]:
    """Convert a formula's ``ParsedSymbol`` list into typed contracts.

    Symbols classified as ``function`` or ``distribution`` are assigned
    ``shape=None`` (not applicable).  Scalars get shape ``()``, vectors get a
    single unknown dimension, matrices two, and tensors three.
    """
    contracts: list[SymbolContract] = []
    denominator_names = _denominator_symbols(formula.root)

    for sym in formula.symbols:
        shape = _infer_shape(sym)
        domain = _infer_domain(sym, denominator_names)
        constraints = _infer_constraints(sym, denominator_names)
        confidence = _compute_confidence(sym, extraction_confidence)
        contracts.append(SymbolContract(
            name=sym.name,
            category=sym.category,
            shape=shape,
            domain=domain,
            constraints=constraints,
            scope=section_id,
            confidence=confidence,
            confirmed=confidence >= CONFIDENCE_THRESHOLD,
        ))
    return contracts


def _infer_shape(sym: ParsedSymbol) -> tuple[int | str, ...] | None:
    if sym.category in {"function", "distribution"}:
        return None
    if sym.category in {"scalar", "index"}:
        return ()
    index_count = len(sym.indices)
    inferred_rank = {
        "vector": 1,
        "matrix": 2,
        "tensor": max(3, index_count),
    }[sym.category]
    return tuple("?" for _ in range(max(inferred_rank, index_count)))


def _infer_domain(sym: ParsedSymbol, denominator_names: set[str]) -> SymbolDomain:
    if sym.category in {"function", "distribution"}:
        return "real"
    if sym.name in denominator_names:
        return "real"  # Could be constrained but we default conservatively.
    return "real"


def _infer_constraints(sym: ParsedSymbol, denominator_names: set[str]) -> list[str]:
    constraints: list[str] = []
    if sym.name in denominator_names:
        constraints.append("!= 0")
    return constraints


def _compute_confidence(sym: ParsedSymbol, extraction_confidence: float) -> float:
    base = extraction_confidence
    # Style annotations increase confidence.
    if sym.style is not None:
        base = min(base + 0.1, 1.0)
    # Index-only symbols are high confidence since they are structurally determined.
    if sym.category == "index":
        return min(base + 0.1, 1.0)
    # Functions detected via \operatorname or known commands are high confidence.
    if sym.category in {"function", "distribution"}:
        return min(base + 0.05, 1.0)
    return base


def _denominator_symbols(node: AstNode) -> set[str]:
    """Collect symbol names that appear in the denominator of a division."""
    names: set[str] = set()
    _walk_denominators(node, names, in_denominator=False)
    return names


def _walk_denominators(node: AstNode, out: set[str], *, in_denominator: bool) -> None:
    if node.kind == "divide" and len(node.children) == 2:
        _walk_denominators(node.children[0], out, in_denominator=in_denominator)
        _walk_denominators(node.children[1], out, in_denominator=True)
        return
    if node.kind == "subscript" and node.children and in_denominator:
        base = node.children[0]
        if base.kind == "symbol" and base.value:
            out.add(base.value)
        return
    if node.kind in {"call", "distribution"} and node.children:
        for child in node.children[1:]:
            _walk_denominators(child, out, in_denominator=in_denominator)
        return
    if node.kind == "symbol" and node.value and in_denominator:
        out.add(node.value)
    for child in node.children:
        _walk_denominators(child, out, in_denominator=in_denominator)


# ── Shape compatibility ─────────────────────────────────────────


def check_shape_compatibility(
    contracts: list[SymbolContract],
    formula: ParsedFormula,
) -> list[ShapeError]:
    """Return shape errors without permitting implicit broadcasting."""
    _, errors = infer_expression_shape(contracts, formula)
    return errors


def infer_expression_shape(
    contracts: list[SymbolContract],
    formula: ParsedFormula,
) -> tuple[tuple[int | str, ...] | None, list[ShapeError]]:
    """Infer the root shape using ordered tensor contraction for multiplication."""
    contract_map = {c.name: c for c in contracts}
    errors: list[ShapeError] = []
    shape = _infer_node_shape(formula.root, contract_map, errors, path="root")
    return shape, errors


def _infer_node_shape(
    node: AstNode,
    contracts: dict[str, SymbolContract],
    errors: list[ShapeError],
    path: str,
) -> tuple[int | str, ...] | None:
    if node.kind == "symbol" and node.value:
        c = contracts.get(node.value)
        return c.shape if c else None
    if node.kind == "number":
        return ()
    if node.kind == "subscript" and node.children:
        base = node.children[0]
        c = contracts.get(base.value) if base.kind == "symbol" and base.value else None
        if c is None or c.shape is None:
            return None
        index_count = _index_count(node.children[1]) if len(node.children) > 1 else 0
        return c.shape[:-index_count] if index_count and len(c.shape) >= index_count else c.shape
    if node.kind == "style" and node.children:
        return _infer_node_shape(node.children[0], contracts, errors, f"{path}.style")
    if node.kind == "negate" and node.children:
        return _infer_node_shape(node.children[0], contracts, errors, f"{path}.negate")
    if node.kind == "multiply":
        child_shapes = [
            _infer_node_shape(child, contracts, errors, f"{path}.multiply[{index}]")
            for index, child in enumerate(node.children)
        ]
        if not child_shapes:
            return None
        result = child_shapes[0]
        for index, next_shape in enumerate(child_shapes[1:], start=1):
            result = _contract_shapes(
                result,
                next_shape,
                node.children[index - 1],
                node.children[index],
                errors,
                f"{path}.multiply[{index - 1}:{index}]",
            )
        return result

    if node.kind == "add":
        shapes = [
            _infer_node_shape(child, contracts, errors, f"{path}.add[{index}]")
            for index, child in enumerate(node.children)
        ]
        known = [(i, s) for i, s in enumerate(shapes) if s is not None]
        if known:
            base_index, base_shape = known[0]
            for other_index, other_shape in known[1:]:
                if _shapes_compatible(base_shape, other_shape):
                    continue
                names = _child_symbol_names(
                    node.children[base_index], node.children[other_index]
                )
                errors.append(ShapeError(
                    message=f"No implicit broadcasting: {base_shape} + {other_shape}",
                    node_path=f"{path}.add[{base_index}:{other_index}]",
                    symbols=names,
                ))
            return base_shape
        return None

    if node.kind == "divide" and len(node.children) == 2:
        numerator = _infer_node_shape(node.children[0], contracts, errors, f"{path}.numerator")
        denominator = _infer_node_shape(
            node.children[1], contracts, errors, f"{path}.denominator"
        )
        if denominator is not None and not _is_scalar(denominator):
            errors.append(ShapeError(
                message="Division denominator must be scalar.",
                node_path=f"{path}.denominator",
                symbols=_child_symbol_names(node.children[1]),
            ))
        return numerator

    if node.kind == "power" and len(node.children) == 2:
        base = _infer_node_shape(node.children[0], contracts, errors, f"{path}.base")
        if node.attribute("operation") == "transpose" and base is not None and len(base) >= 2:
            return (*base[:-2], base[-1], base[-2])
        return base

    if node.kind == "equals" and len(node.children) == 2:
        left = _infer_node_shape(node.children[0], contracts, errors, f"{path}.left")
        right = _infer_node_shape(node.children[1], contracts, errors, f"{path}.right")
        if left is not None and right is not None and not _shapes_compatible(left, right):
            errors.append(ShapeError(
                message=f"Equality shape mismatch: {left} vs {right}",
                node_path=path,
                symbols=_child_symbol_names(*node.children),
            ))
        return left

    for index, child in enumerate(node.children):
        _infer_node_shape(child, contracts, errors, f"{path}[{index}]")
    return None


def _contract_shapes(
    left: tuple[int | str, ...] | None,
    right: tuple[int | str, ...] | None,
    left_node: AstNode,
    right_node: AstNode,
    errors: list[ShapeError],
    path: str,
) -> tuple[int | str, ...] | None:
    if left is None or right is None:
        return None
    if _is_scalar(left):
        return right
    if _is_scalar(right):
        return left
    inner_left, inner_right = left[-1], right[0]
    if not _dimensions_compatible(inner_left, inner_right):
        errors.append(ShapeError(
            message=f"Inner dimension mismatch: {inner_left} vs {inner_right}",
            node_path=path,
            symbols=_child_symbol_names(left_node, right_node),
        ))
        return None
    return (*left[:-1], *right[1:])


def _dimensions_compatible(left: int | str, right: int | str) -> bool:
    return left == "?" or right == "?" or left == right


def _shapes_compatible(
    left: tuple[int | str, ...], right: tuple[int | str, ...]
) -> bool:
    return len(left) == len(right) and all(
        _dimensions_compatible(left_dim, right_dim)
        for left_dim, right_dim in zip(left, right, strict=True)
    )


def _index_count(node: AstNode) -> int:
    return len(node.children) if node.kind == "sequence" else 1


def _is_scalar(shape: tuple[int | str, ...]) -> bool:
    return len(shape) == 0


def _child_symbol_names(*nodes: AstNode) -> tuple[str, ...]:
    names: list[str] = []
    for n in nodes:
        if n.kind == "symbol" and n.value:
            names.append(n.value)
        elif n.kind == "subscript" and n.children and n.children[0].kind == "symbol":
            names.append(n.children[0].value or "?")
        elif n.kind == "style" and n.children:
            names.extend(_child_symbol_names(n.children[0]))
    return tuple(names)


# ── Denominator domain ───────────────────────────────────────────


def check_denominator_domain(
    contracts: list[SymbolContract],
    formula: ParsedFormula,
) -> list[DomainError]:
    """Flag symbols that appear in a denominator without a ``!= 0`` constraint."""
    contract_map = {c.name: c for c in contracts}
    denom_names = _denominator_symbols(formula.root)
    errors: list[DomainError] = []
    for name in sorted(denom_names):
        c = contract_map.get(name)
        if c is None:
            continue
        if "!= 0" not in c.constraints:
            errors.append(DomainError(
                message=f"Symbol '{name}' appears in a denominator without a '!= 0' constraint.",
                symbol=name,
                location="denominator",
            ))
    return errors


# ── Symbol shadowing ─────────────────────────────────────────────


def detect_symbol_shadowing(
    contract_groups: dict[str | None, list[SymbolContract]],
) -> list[ShadowWarning]:
    """Compare contracts across sections and flag the same name with different types.

    ``contract_groups`` maps ``section_id`` (or ``None`` for the global scope)
    to the list of contracts inferred from that section.
    """
    by_name: dict[str, list[tuple[str | None, SymbolContract]]] = {}
    for section_id, contracts in contract_groups.items():
        for c in contracts:
            by_name.setdefault(c.name, []).append((section_id, c))

    warnings: list[ShadowWarning] = []
    for name, entries in sorted(by_name.items()):
        if len(entries) < 2:
            continue
        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                sec_a, c_a = entries[i]
                sec_b, c_b = entries[j]
                reasons: list[str] = []
                if c_a.category != c_b.category:
                    reasons.append(f"category: {c_a.category} vs {c_b.category}")
                if c_a.shape != c_b.shape:
                    reasons.append(f"shape: {c_a.shape} vs {c_b.shape}")
                if c_a.domain != c_b.domain:
                    reasons.append(f"domain: {c_a.domain} vs {c_b.domain}")
                if reasons:
                    warnings.append(ShadowWarning(
                        symbol=name,
                        section_a=sec_a,
                        section_b=sec_b,
                        contract_a=c_a,
                        contract_b=c_b,
                        reason="; ".join(reasons),
                    ))
    return warnings
