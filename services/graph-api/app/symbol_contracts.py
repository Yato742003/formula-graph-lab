"""FGL-403: Symbol contracts — shape, domain, and scope validation.

Each parsed formula can be annotated with inferred contracts describing the
mathematical role of every symbol (shape, domain, constraints).  Validation
functions detect shape-incompatible operations, unsafe denominators, and
cross-section symbol shadowing.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.formula_ast import AstNode, ParsedFormula, ParsedSymbol

# The threshold is a review-routing hint only. It never creates a human review
# or proves that a contract is mathematically valid.
CONFIDENCE_THRESHOLD = 0.6

SymbolCategory = Literal[
    "scalar", "vector", "matrix", "tensor", "function", "distribution", "index",
]
InferredCategory = Literal[
    "scalar", "vector", "matrix", "tensor", "function", "distribution", "index",
    "unknown",
]
SymbolDomain = Literal["real", "positive", "non_negative", "complex", "integer"]
DomainPredicateKind = Literal[
    "nonzero", "zero", "positive", "negative", "non_negative", "non_positive",
]
DomainStatus = Literal[
    "discharged", "conditional", "unresolved", "contradictory", "unsupported",
]


class SymbolContract(BaseModel):
    """An inferred, immutable mathematical contract.

    Human review is intentionally absent. A review is a separate
    :class:`ContractReview` record so extraction or inference confidence cannot
    impersonate an approval.
    """

    name: str
    category: InferredCategory
    shape: tuple[int | str, ...] | None = None
    domain: SymbolDomain = "real"
    constraints: list[str] = Field(default_factory=list)
    scope: str | None = None  # section_id
    inference_confidence: float = Field(ge=0, le=1, default=1.0)
    review_required: bool = False

    model_config = {"frozen": True, "extra": "forbid"}


class ReviewedContractValue(BaseModel):
    """The mathematical values accepted by a reviewer, without confidence."""

    name: str
    category: SymbolCategory
    shape: tuple[int | str, ...] | None = None
    domain: SymbolDomain = "real"
    constraints: list[str] = Field(default_factory=list)
    scope: str | None = None

    model_config = {"frozen": True, "extra": "forbid"}


class ContractReview(BaseModel):
    """A human-authored decision stored independently from inference."""

    review_id: str = Field(min_length=1, max_length=200)
    symbol_name: str = Field(min_length=1, max_length=200)
    reviewer_id: str = Field(min_length=1, max_length=200)
    reviewer_role: Literal["researcher", "reviewer", "admin"]
    decision: Literal["accepted", "rejected"]
    scope: str = Field(min_length=1, max_length=500)
    reviewed_contract: ReviewedContractValue | None = None
    evidence: list[str] = Field(default_factory=list, max_length=50)
    schema_version: Literal["contract-review.v1"] = "contract-review.v1"
    reviewed_at: datetime

    model_config = {"frozen": True, "extra": "forbid"}

    @field_validator("reviewed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Contract review time must be timezone-aware.")
        return value


class DomainObligation(BaseModel):
    """A predicate over an expression required for a formula to be defined."""

    obligation_id: str
    predicate: DomainPredicateKind
    expression: dict[str, object]
    expression_hash: str
    location: str
    origin: Literal["inferred"] = "inferred"
    status: DomainStatus = "unresolved"
    discharged_by: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)

    model_config = {"frozen": True, "extra": "forbid"}

    @model_validator(mode="after")
    def validate_expression_hash(self):
        if self.expression_hash != _expression_dict_hash(self.expression):
            raise ValueError("Domain obligation expression hash does not match its AST.")
        return self


class DomainAssumption(BaseModel):
    """A source, user, model, or checker predicate considered by assessment."""

    assumption_id: str = Field(min_length=1, max_length=200)
    predicate: DomainPredicateKind
    expression: dict[str, object]
    expression_hash: str
    origin: Literal[
        "source_reported", "user_accepted", "ai_proposed", "checker_supported",
    ]
    acceptance: Literal["proposed", "accepted", "rejected"]
    accepted_by: str | None = Field(default=None, min_length=1, max_length=200)
    evidence: list[str] = Field(default_factory=list, max_length=50)

    model_config = {"frozen": True, "extra": "forbid"}

    @model_validator(mode="after")
    def validate_acceptance(self):
        if self.expression_hash != _expression_dict_hash(self.expression):
            raise ValueError("Domain assumption expression hash does not match its AST.")
        if self.origin == "checker_supported" and self.acceptance != "accepted":
            raise ValueError("Checker-supported assumptions must be accepted.")
        if (
            self.origin == "ai_proposed"
            and self.acceptance == "accepted"
            and self.accepted_by is None
        ):
            raise ValueError("An AI-proposed assumption needs a human acceptance record.")
        return self


class DomainAssessment(BaseModel):
    status: DomainStatus
    obligations: list[DomainObligation]
    contradictions: list[tuple[str, str]] = Field(default_factory=list)

    model_config = {"frozen": True, "extra": "forbid"}


def apply_contract_reviews(
    inferred: list[SymbolContract],
    reviews: list[ContractReview],
    *,
    scope: str,
) -> list[SymbolContract]:
    """Resolve accepted human reviews for trusted internal computation.

    The review records remain the authority and must be persisted separately.
    This helper never accepts review-shaped client data and is not exposed by
    the formula parse endpoint.
    """
    inferred_names = {contract.name for contract in inferred}
    reviewed_names = [review.symbol_name for review in reviews]
    if len(reviewed_names) != len(set(reviewed_names)):
        raise ValueError("Contract reviews must have unique symbol names.")
    unknown = sorted(set(reviewed_names) - inferred_names)
    if unknown:
        raise ValueError(f"Contract review references unknown symbol: {unknown[0]}")
    if any(review.scope != scope for review in reviews):
        raise ValueError("Contract review scope does not match the formula scope.")
    rejected = next((review for review in reviews if review.decision == "rejected"), None)
    if rejected is not None:
        raise ValueError(f"Contract review rejected symbol: {rejected.symbol_name}")
    by_name = {review.symbol_name: review for review in reviews}
    resolved: list[SymbolContract] = []
    for contract in inferred:
        review = by_name.get(contract.name)
        reviewed = review.reviewed_contract if review else None
        if reviewed is None:
            resolved.append(contract)
            continue
        resolved.append(contract.model_copy(update={
            "category": reviewed.category,
            "shape": reviewed.shape,
            "domain": reviewed.domain,
            "constraints": reviewed.constraints,
            "scope": reviewed.scope or contract.scope,
            "review_required": False,
        }))
    return resolved


# ── Validation result types ──────────────────────────────────────


@dataclass(frozen=True)
class ShapeError:
    """Raised when tensor dimensions are incompatible."""

    message: str
    node_path: str
    symbols: tuple[str, ...]


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
    for sym in formula.symbols:
        shape = _infer_shape(sym)
        domain = _infer_domain(sym)
        confidence = _compute_confidence(sym, extraction_confidence)
        contracts.append(SymbolContract(
            name=sym.name,
            category=sym.category,
            shape=shape,
            domain=domain,
            constraints=[],
            scope=section_id,
            inference_confidence=confidence,
            review_required=confidence < CONFIDENCE_THRESHOLD,
        ))
    return contracts


def _infer_shape(sym: ParsedSymbol) -> tuple[int | str, ...] | None:
    if sym.category in {"function", "distribution", "unknown"}:
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


def _infer_domain(sym: ParsedSymbol) -> SymbolDomain:
    if sym.category in {"function", "distribution"}:
        return "real"
    return "real"


def _compute_confidence(sym: ParsedSymbol, extraction_confidence: float) -> float:
    base = extraction_confidence
    # Unknown category has no structural evidence — always low confidence.
    if sym.category == "unknown":
        return min(base, CONFIDENCE_THRESHOLD - 0.1)
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


# ── Domain obligations ───────────────────────────────────────────


def infer_domain_obligations(
    contracts: list[SymbolContract],
    formula: ParsedFormula,
) -> list[DomainObligation]:
    """Infer whole-expression definedness predicates without discharging them."""
    contract_map = {contract.name: contract for contract in contracts}
    obligations: list[DomainObligation] = []

    def walk(node: AstNode, path: str) -> None:
        if node.kind == "divide" and len(node.children) == 2:
            obligations.append(_domain_obligation(
                "nonzero", node.children[1], f"{path}.denominator"
            ))
        if node.kind == "call" and len(node.children) >= 2:
            function = node.children[0]
            argument = node.children[1]
            function_name = function.value if function.kind == "symbol" else None
            domain = _expression_domain(argument, contract_map)
            if function_name == "log":
                predicate: DomainPredicateKind = (
                    "nonzero" if domain == "complex" else "positive"
                )
                obligations.append(_domain_obligation(
                    predicate, argument, f"{path}.log_argument"
                ))
            elif function_name == "sqrt" and domain != "complex":
                obligations.append(_domain_obligation(
                    "non_negative", argument, f"{path}.sqrt_argument"
                ))
        for index, child in enumerate(node.children):
            walk(child, f"{path}.{node.kind}[{index}]")

    walk(formula.root, "root")
    unique = {obligation.obligation_id: obligation for obligation in obligations}
    return [unique[key] for key in sorted(unique)]


def domain_assumption(
    *,
    assumption_id: str,
    predicate: DomainPredicateKind,
    expression: AstNode,
    origin: Literal[
        "source_reported", "user_accepted", "ai_proposed", "checker_supported",
    ],
    acceptance: Literal["proposed", "accepted", "rejected"],
    accepted_by: str | None = None,
    evidence: list[str] | None = None,
) -> DomainAssumption:
    """Build a stable assumption record from an AST expression."""
    expression_dict, expression_hash = _expression_identity(expression)
    return DomainAssumption(
        assumption_id=assumption_id,
        predicate=predicate,
        expression=expression_dict,
        expression_hash=expression_hash,
        origin=origin,
        acceptance=acceptance,
        accepted_by=accepted_by,
        evidence=evidence or [],
    )


def assess_domain_obligations(
    obligations: list[DomainObligation],
    assumptions: list[DomainAssumption] | None = None,
) -> DomainAssessment:
    """Assess obligations while preserving conditional and contradictory states."""
    considered = [
        assumption
        for assumption in (assumptions or [])
        if assumption.acceptance == "accepted"
    ]
    contradictions = _domain_contradictions(considered)
    contradiction_ids = {item for pair in contradictions for item in pair}
    assessed: list[DomainObligation] = []
    for obligation in obligations:
        matching = [
            assumption
            for assumption in considered
            if assumption.expression_hash == obligation.expression_hash
            and _predicate_implies(assumption.predicate, obligation.predicate)
        ]
        conflicting = [
            assumption.assumption_id
            for assumption in considered
            if assumption.expression_hash == obligation.expression_hash
            and assumption.assumption_id in contradiction_ids
        ]
        if conflicting:
            assessed.append(obligation.model_copy(update={
                "status": "contradictory",
                "conditions": sorted(conflicting),
            }))
            continue
        checker_ids = sorted(
            assumption.assumption_id
            for assumption in matching
            if assumption.origin == "checker_supported"
        )
        conditional_ids = sorted(
            assumption.assumption_id
            for assumption in matching
            if assumption.origin != "checker_supported"
        )
        if checker_ids:
            assessed.append(obligation.model_copy(update={
                "status": "discharged",
                "discharged_by": checker_ids,
            }))
        elif conditional_ids:
            assessed.append(obligation.model_copy(update={
                "status": "conditional",
                "conditions": conditional_ids,
            }))
        else:
            assessed.append(obligation)

    if contradictions:
        status: DomainStatus = "contradictory"
    elif any(item.status == "unresolved" for item in assessed):
        status = "unresolved"
    elif any(item.status == "conditional" for item in assessed):
        status = "conditional"
    else:
        status = "discharged"
    return DomainAssessment(
        status=status,
        obligations=assessed,
        contradictions=contradictions,
    )


def _domain_obligation(
    predicate: DomainPredicateKind,
    expression: AstNode,
    location: str,
) -> DomainObligation:
    expression_dict, expression_hash = _expression_identity(expression)
    obligation_id = hashlib.sha256(
        f"domain-obligation.v1:{predicate}:{expression_hash}:{location}".encode()
    ).hexdigest()
    return DomainObligation(
        obligation_id=obligation_id,
        predicate=predicate,
        expression=expression_dict,
        expression_hash=expression_hash,
        location=location,
    )


def _expression_identity(expression: AstNode) -> tuple[dict[str, object], str]:
    expression_dict = expression.to_dict()
    return expression_dict, _expression_dict_hash(expression_dict)


def _expression_dict_hash(expression: dict[str, object]) -> str:
    serialized = json.dumps(
        expression, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode()).hexdigest()


def _expression_domain(
    expression: AstNode,
    contracts: dict[str, SymbolContract],
) -> SymbolDomain:
    names = _all_symbol_names(expression)
    return "complex" if any(
        contracts[name].domain == "complex" for name in names if name in contracts
    ) else "real"


def _all_symbol_names(node: AstNode) -> set[str]:
    names = {node.value} if node.kind == "symbol" and node.value else set()
    for child in node.children:
        names.update(_all_symbol_names(child))
    return names


def _predicate_implies(
    actual: DomainPredicateKind,
    required: DomainPredicateKind,
) -> bool:
    if actual == required:
        return True
    implications = {
        "positive": {"nonzero", "non_negative"},
        "negative": {"nonzero", "non_positive"},
        "zero": {"non_negative", "non_positive"},
    }
    return required in implications.get(actual, set())


def _domain_contradictions(
    assumptions: list[DomainAssumption],
) -> list[tuple[str, str]]:
    contradictions: list[tuple[str, str]] = []
    incompatible = {
        frozenset(("positive", "negative")),
        frozenset(("positive", "non_positive")),
        frozenset(("negative", "non_negative")),
        frozenset(("zero", "nonzero")),
        frozenset(("zero", "positive")),
        frozenset(("zero", "negative")),
    }
    for index, left in enumerate(assumptions):
        for right in assumptions[index + 1:]:
            if left.expression_hash != right.expression_hash:
                continue
            if frozenset((left.predicate, right.predicate)) in incompatible:
                contradictions.append(tuple(sorted((
                    left.assumption_id,
                    right.assumption_id,
                ))))
    return sorted(set(contradictions))


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
