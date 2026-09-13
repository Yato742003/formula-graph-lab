from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import NAMESPACE_URL, uuid5

from app.analysis_versions import make_analysis_version
from app.episodes import ResearchEpisode, build_paper_episodes
from app.formula_ast import AstNode, FormulaParseError, ParsedFormula, parse_formula
from app.models import ExtractedEquation, ExtractedPaper
from app.ontology import RelationContract
from app.semantic_identity import CORE_OPERATOR_VERSIONS, build_semantic_identity
from app.symbol_contracts import (
    ReviewedContractValue,
    SymbolContract,
    assess_domain_obligations,
    infer_contracts,
    infer_domain_obligations,
    infer_expression_shape,
)


def _uuid(*parts: object) -> str:
    return str(uuid5(NAMESPACE_URL, json.dumps(parts, ensure_ascii=False)))


def _json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class EvidenceGraph:
    import_uuid: str
    group_id: str
    paper_id: str
    version: int | None
    source_sha256: str
    paper_version_uuid: str
    episodes: list[ResearchEpisode]
    nodes: list[dict]
    edges: list[dict]
    analyses: list[dict]


def build_evidence_graph(
    paper: ExtractedPaper,
    *,
    workspace_id: str,
    reference_time: datetime | None = None,
    reviewed_contracts: Mapping[str, Iterable[ReviewedContractValue]] | None = None,
) -> EvidenceGraph:
    episodes = build_paper_episodes(paper, workspace_id=workspace_id, reference_time=reference_time)
    root = episodes[0]
    nodes: list[dict] = []
    edges: list[dict] = []
    analyses: list[dict] = []
    symbol_nodes: dict[tuple[str, str], str] = {}

    def node(kind: str, logical_id: str, payload: dict, episode_uuid: str) -> str:
        # Paper is shared across its revisions; every source snapshot remains immutable.
        namespace = root.group_id if kind == "Paper" else root.uuid
        node_uuid = _uuid(namespace, kind, logical_id)
        nodes.append({
            "uuid": node_uuid, "kind": kind, "logical_id": logical_id,
            "payload": _json(payload), "episode_uuid": episode_uuid,
            "paper_id": paper.paper_id,
            "paper_version": paper.version if kind != "Paper" else None,
        })
        return node_uuid

    def edge(
        source_uuid: str, target_uuid: str, source_type: str, target_type: str,
        relation: str, episode_uuid: str, anchor: str = "",
    ) -> None:
        RelationContract(
            source_type=source_type, target_type=target_type, relation=relation,
            source_anchor=anchor, confidence=1,
        )
        edges.append({
            "uuid": _uuid(root.group_id, source_uuid, relation, target_uuid),
            "source_uuid": source_uuid, "target_uuid": target_uuid,
            "relation": relation, "episode_uuids": [episode_uuid], "source_anchor": anchor,
        })

    paper_uuid = node("Paper", paper.paper_id, {"arxiv_id": paper.paper_id}, root.uuid)
    version_uuid = node("PaperVersion", f"{paper.paper_id}v{paper.version}", {
        **paper.model_dump(mode="json", exclude={"sections", "equations"}),
    }, root.uuid)
    edge(paper_uuid, version_uuid, "Paper", "PaperVersion", "has_version", root.uuid)

    ordered_sections = sorted(paper.sections, key=lambda s: s.order)
    section_nodes = {}
    section_episodes = {}
    for section, episode in zip(ordered_sections, episodes[1:], strict=True):
        section_nodes[section.section_id] = node(
            "Section", section.section_id, section.model_dump(mode="json"), episode.uuid,
        )
        section_episodes[section.section_id] = episode.uuid
    for section in ordered_sections:
        section_uuid = section_nodes[section.section_id]
        episode_uuid = section_episodes[section.section_id]
        anchor = section.anchor if section.anchor_is_source else ""
        edge(
            version_uuid,
            section_uuid,
            "PaperVersion",
            "Section",
            "contains",
            episode_uuid,
            anchor,
        )
        if section.parent_section_id:
            if section.parent_section_id not in section_nodes:
                raise ValueError("A section references an unknown parent.")
            parent = next(s for s in ordered_sections if s.section_id == section.parent_section_id)
            if parent.order >= section.order:
                raise ValueError("A parent section must precede its child.")
            edge(section_nodes[parent.section_id], section_uuid, "Section", "Section",
                 "contains", episode_uuid, anchor)
    for equation in paper.equations:
        episode_uuid = section_episodes.get(equation.section_id, root.uuid)
        eq_reviews = (
            reviewed_contracts.get(equation.equation_id, ())
            if reviewed_contracts
            else ()
        )
        analysis, parsed, contracts = analyze_equation(
            equation, reviewed_contracts=eq_reviews,
        )
        equation_payload = equation.model_dump(mode="json")
        equation_uuid = node(
            "Equation", equation.equation_id, equation_payload, episode_uuid,
        )
        analyses.append(make_analysis_version(equation_uuid, _json(equation_payload), analysis))
        source_uuid = section_nodes.get(equation.section_id, version_uuid)
        source_type = "Section" if equation.section_id else "PaperVersion"
        edge(source_uuid, equation_uuid, source_type, "Equation", "contains", episode_uuid,
             equation.anchor if equation.anchor_is_source else "")
        if parsed is None:
            continue
        defined_names = _defined_symbol_names(parsed.root)
        scope = equation.section_id or f"{paper.paper_id}v{paper.version}"
        for contract in contracts:
            symbol_key = (scope, contract.name)
            symbol_uuid = symbol_nodes.get(symbol_key)
            if symbol_uuid is None:
                symbol_uuid = node(
                    "Symbol",
                    f"{scope}:{contract.name}",
                    {
                        "notation": contract.name,
                        "semantic_name": None,
                        "mathematical_type": contract.category,
                        "shape": list(contract.shape) if contract.shape is not None else None,
                        "domain": contract.domain,
                        "constraints": contract.constraints,
                        "scope": contract.scope,
                        "inference_confidence": contract.inference_confidence,
                        "review_required": contract.review_required,
                    },
                    episode_uuid,
                )
                symbol_nodes[symbol_key] = symbol_uuid
            relation = "defines" if contract.name in defined_names else "uses"
            edge(
                equation_uuid,
                symbol_uuid,
                "Equation",
                "Symbol",
                relation,
                episode_uuid,
                equation.anchor if equation.anchor_is_source else "",
            )
    return EvidenceGraph(
        import_uuid=root.uuid, group_id=root.group_id, paper_id=paper.paper_id,
        version=paper.version, source_sha256=paper.source_sha256,
        paper_version_uuid=version_uuid, episodes=episodes, nodes=nodes, edges=edges,
        analyses=analyses,
    )


def analyze_equation(
    equation: ExtractedEquation,
    reviewed_contracts: Iterable[ReviewedContractValue] = (),
    *,
    operator_versions: Mapping[str, str] | None = None,
) -> tuple[dict[str, object], ParsedFormula | None, list[SymbolContract]]:
    """Return bounded, deterministic analysis without rejecting an entire import."""
    try:
        parsed = parse_formula(equation.latex)
    except FormulaParseError as exc:
        return (
            {
                "status": "unsupported",
                "error": {
                    "code": exc.code,
                    "message": str(exc),
                    "position": exc.position,
                },
            },
            None,
            [],
        )

    contracts = infer_contracts(
        parsed,
        section_id=equation.section_id,
        extraction_confidence=equation.confidence,
    )
    result_shape, shape_errors = infer_expression_shape(contracts, parsed)
    domain_assessment = assess_domain_obligations(
        infer_domain_obligations(contracts, parsed)
    )
    requires_review = any(contract.review_required for contract in contracts)
    scope_id = equation.section_id or equation.equation_id
    active_operators = (
        dict(operator_versions)
        if operator_versions is not None
        else CORE_OPERATOR_VERSIONS
    )
    semantic_identity_payload = None
    try:
        sem_id = build_semantic_identity(
            parsed,
            reviewed_contracts,
            scope_id=scope_id,
            operator_versions=active_operators,
        )
        semantic_identity_payload = {
            "semantic_hash": sem_id.semantic_hash,
            "semantic_hash_version": sem_id.semantic_hash_version,
            "canonicalizer_version": sem_id.canonicalizer_version,
            "scoped_symbol_version": sem_id.scoped_symbol_version,
            "complete_contracts": sem_id.complete_contracts,
            "unresolved_symbols": list(sem_id.unresolved_symbols),
            "typed_ir": sem_id.typed_ir.to_dict(),
        }
    except Exception:
        semantic_identity_payload = None
    status = (
        "invalid"
        if shape_errors or domain_assessment.status == "contradictory"
        else "needs_review"
        if requires_review
        else "needs_domain_assumptions"
        if domain_assessment.status == "unresolved"
        else "analyzed"
    )
    return (
        {
            "status": status,
            "ast": parsed.root.to_dict(),
            "syntax_hash": parsed.syntax_hash,
            "syntax_hash_version": parsed.syntax_hash_version,
            "canonicalizer_version": parsed.canonicalizer_version,
            "canonical_hash": parsed.canonical_hash,
            "semantic_identity": semantic_identity_payload,
            "extraction_assessment": {
                "confidence": equation.confidence,
                "source": "equation_extraction",
            },
            "free_variables": list(parsed.free_variables),
            "bound_variables": list(parsed.bound_variables),
            "symbols": [symbol.to_dict() for symbol in parsed.symbols],
            "contracts": [contract.model_dump(mode="json") for contract in contracts],
            "result_shape": list(result_shape) if result_shape is not None else None,
            "shape_errors": [
                {
                    "message": error.message,
                    "location": error.node_path,
                    "symbols": list(error.symbols),
                }
                for error in shape_errors
            ],
            "domain_assessment": domain_assessment.model_dump(mode="json"),
            "requires_review": requires_review,
        },
        parsed,
        contracts,
    )


def _defined_symbol_names(root: AstNode) -> set[str]:
    if root.kind != "equals" or not root.children:
        return set()
    return _ast_symbol_names(root.children[0])


def _ast_symbol_names(node: AstNode) -> set[str]:
    names = {node.value} if node.kind == "symbol" and node.value else set()
    for child in node.children:
        names.update(_ast_symbol_names(child))
    return names


@dataclass(frozen=True)
class ReportedClaim:
    uuid: str
    group_id: str
    paper_id: str
    paper_version: int | None
    paper_version_uuid: str
    episode_uuid: str
    logical_id: str
    statement: str
    source_anchor: str
    payload: str


def build_reported_claim(
    graph: EvidenceGraph,
    *,
    logical_id: str,
    statement: str,
    source_anchor: str,
) -> ReportedClaim:
    logical_id = logical_id.strip()
    statement = statement.strip()
    source_anchor = source_anchor.strip()
    if not logical_id or len(logical_id) > 200:
        raise ValueError("A bounded claim ID is required.")
    if not statement or len(statement) > 20_000:
        raise ValueError("A bounded claim statement is required.")
    anchored_nodes = []
    for item in graph.nodes:
        if item["kind"] not in {"Section", "Equation"}:
            continue
        payload = json.loads(item["payload"])
        if payload.get("anchor_is_source") and payload.get("anchor") == source_anchor:
            anchored_nodes.append(item)
    if not anchored_nodes:
        raise ValueError("A claim must resolve to a source section or equation anchor.")
    episode_ids = {item["episode_uuid"] for item in anchored_nodes}
    if len(episode_ids) != 1:
        raise ValueError("A claim anchor resolves ambiguously across source episodes.")
    episode_uuid = next(iter(episode_ids))
    claim_uuid = _uuid(graph.import_uuid, "Claim", logical_id)
    payload = _json({
        "claim_id": logical_id,
        "statement": statement,
        "scope_paper_id": graph.paper_id,
        "paper_version": graph.version,
        "status": "reported",
        "source_anchor": source_anchor,
    })
    return ReportedClaim(
        uuid=claim_uuid,
        group_id=graph.group_id,
        paper_id=graph.paper_id,
        paper_version=graph.version,
        paper_version_uuid=graph.paper_version_uuid,
        episode_uuid=episode_uuid,
        logical_id=logical_id,
        statement=statement,
        source_anchor=source_anchor,
        payload=payload,
    )
