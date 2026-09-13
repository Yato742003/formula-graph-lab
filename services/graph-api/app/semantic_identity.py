"""FGL-H3: typed canonicalization and scope-aware semantic identity."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.formula_ast import AstNode, ParsedFormula, _is_binder_declaration, canonicalize
from app.symbol_contracts import DomainAssumption, ReviewedContractValue

SEMANTIC_HASH_VERSION = "semantic-hash.v1"
TYPED_CANONICALIZER_VERSION = "typed-canonicalizer.v2"
SCOPED_SYMBOL_VERSION = "scoped-symbol.v1"
CORE_OPERATOR_VERSIONS: dict[str, str] = {
    "add": "core.add.v1",
    "multiply": "core.multiply.v1",
}


@dataclass(frozen=True)
class ScopedSymbolOccurrence:
    printed_name: str
    symbol_id: str
    binding: str
    node_path: str

    def to_dict(self) -> dict[str, str]:
        return {
            "printed_name": self.printed_name,
            "symbol_id": self.symbol_id,
            "binding": self.binding,
            "node_path": self.node_path,
        }


@dataclass(frozen=True)
class SemanticIdentity:
    semantic_hash: str
    semantic_hash_version: str
    canonicalizer_version: str
    scoped_symbol_version: str
    typed_ir: AstNode
    scoped_symbols: tuple[ScopedSymbolOccurrence, ...]
    complete_contracts: bool
    unresolved_symbols: tuple[str, ...]


def build_semantic_identity(
    parsed: ParsedFormula,
    reviewed_contracts: Iterable[ReviewedContractValue],
    *,
    scope_id: str,
    assumptions: Iterable[DomainAssumption] = (),
    operator_versions: Mapping[str, str] | None = None,
) -> SemanticIdentity:
    """Build a deterministic identity without treating inferred types as proof.

    Partial reviewed contracts are allowed. Their unresolved symbols remain in
    the result and multiplication/addition order is preserved wherever scalar
    commutativity has not been established.
    """
    if not scope_id.strip():
        raise ValueError("Semantic identity requires a non-empty scope_id.")

    contracts = list(reviewed_contracts)
    by_name: dict[str, ReviewedContractValue] = {}
    for contract in contracts:
        if contract.name in by_name:
            raise ValueError(f"Duplicate reviewed contract: {contract.name}")
        if contract.scope is not None and contract.scope != scope_id:
            raise ValueError(f"Reviewed contract scope mismatch: {contract.name}")
        by_name[contract.name] = contract

    alpha_root = canonicalize(parsed.root)
    versions = dict(sorted((operator_versions or {}).items()))
    typed_root = _canonicalize_typed(alpha_root, by_name, versions)
    scoped_root, occurrences = _scope_symbols(typed_root, scope_id)

    required = set(parsed.free_variables)
    unresolved = tuple(sorted(required - set(by_name)))
    normalized_contracts = sorted(
        (contract.model_dump(mode="json") for contract in contracts),
        key=lambda value: str(value["name"]),
    )
    normalized_assumptions = sorted(
        (assumption.model_dump(mode="json") for assumption in assumptions),
        key=lambda value: str(value["assumption_id"]),
    )
    payload = {
        "semantic_hash_version": SEMANTIC_HASH_VERSION,
        "canonicalizer_version": TYPED_CANONICALIZER_VERSION,
        "scoped_symbol_version": SCOPED_SYMBOL_VERSION,
        "scope_id": scope_id,
        "typed_ir": scoped_root.to_dict(),
        "reviewed_contracts": normalized_contracts,
        "assumptions": normalized_assumptions,
        "operator_versions": versions,
        "unresolved_symbols": unresolved,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return SemanticIdentity(
        semantic_hash=hashlib.sha256(encoded).hexdigest(),
        semantic_hash_version=SEMANTIC_HASH_VERSION,
        canonicalizer_version=TYPED_CANONICALIZER_VERSION,
        scoped_symbol_version=SCOPED_SYMBOL_VERSION,
        typed_ir=scoped_root,
        scoped_symbols=tuple(occurrences),
        complete_contracts=not unresolved,
        unresolved_symbols=unresolved,
    )


def _canonicalize_typed(
    node: AstNode,
    contracts: Mapping[str, ReviewedContractValue],
    operator_versions: Mapping[str, str],
) -> AstNode:
    children = tuple(
        _canonicalize_typed(child, contracts, operator_versions) for child in node.children
    )
    scalar_commutative = {
        "add": "core.add.v1", "multiply": "core.multiply.v1",
    }
    known_operator = (
        node.kind in scalar_commutative
        and operator_versions.get(node.kind) == scalar_commutative[node.kind]
    )
    if known_operator and children and all(
        _is_reviewed_scalar(child, contracts) for child in children
    ):
        children = tuple(sorted(children, key=_stable_ast_json))
    return AstNode(node.kind, node.value, children, node.attributes)


def _is_reviewed_scalar(
    node: AstNode,
    contracts: Mapping[str, ReviewedContractValue],
) -> bool:
    if node.kind == "number":
        return True
    if node.kind == "symbol" and node.value:
        if node.attribute("binding") == "bound_index":
            return True
        contract = contracts.get(node.value)
        return bool(
            contract
            and contract.category in {"scalar", "index"}
            and contract.shape == ()
        )
    if node.kind in {"negate", "add", "multiply", "divide", "power"}:
        return bool(node.children) and all(
            _is_reviewed_scalar(child, contracts) for child in node.children
        )
    if node.kind == "style" and len(node.children) == 1:
        return _is_reviewed_scalar(node.children[0], contracts)
    return False


def _scope_symbols(
    root: AstNode,
    scope_id: str,
) -> tuple[AstNode, list[ScopedSymbolOccurrence]]:
    occurrences: list[ScopedSymbolOccurrence] = []

    def visit(
        node: AstNode,
        path: tuple[int, ...],
        bound_ids: Mapping[str, str],
    ) -> AstNode:
        local_bound_ids = dict(bound_ids)
        if node.kind in {"sum", "product", "integral"}:
            binder = node.attribute("binder")
            if binder:
                local_bound_ids[binder] = (
                    f"{SCOPED_SYMBOL_VERSION}:{scope_id}:bound:{_path(path)}"
                )
        if node.kind == "symbol" and node.value:
            symbol_id = local_bound_ids.get(
                node.value,
                f"{SCOPED_SYMBOL_VERSION}:{scope_id}:free:{node.value}",
            )
            binding = "bound" if node.value in local_bound_ids else "free"
            occurrences.append(
                ScopedSymbolOccurrence(node.value, symbol_id, binding, _path(path))
            )
            attributes = tuple(
                (key, value) for key, value in node.attributes if key != "symbol_id"
            ) + (("symbol_id", symbol_id),)
            return AstNode(node.kind, node.value, node.children, attributes)

        children = []
        is_binder = node.kind in {"sum", "product", "integral"} and node.attribute("binder")
        binder = node.attribute("binder") if is_binder else None
        for index, child in enumerate(node.children):
            child_path = (*path, index)
            if not is_binder or index == len(node.children) - 1:
                scoped = visit(child, child_path, local_bound_ids)
            elif index == 0:
                if child.kind == "equals" and len(child.children) == 2:
                    scoped = AstNode(child.kind, child.value, (
                        visit(child.children[0], (*child_path, 0), local_bound_ids),
                        visit(child.children[1], (*child_path, 1), bound_ids),
                    ), child.attributes)
                elif binder and _is_binder_declaration(child, binder):
                    scoped = visit(child, child_path, local_bound_ids)
                else:
                    scoped = visit(child, child_path, bound_ids)
            else:
                scoped = visit(child, child_path, bound_ids)
            children.append(scoped)
        return AstNode(node.kind, node.value, tuple(children), node.attributes)

    return visit(root, (), {}), occurrences


def _path(path: tuple[int, ...]) -> str:
    return "root" if not path else ".".join(str(index) for index in path)


def _stable_ast_json(node: AstNode) -> str:
    return json.dumps(
        node.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
