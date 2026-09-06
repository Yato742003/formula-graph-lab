from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from uuid import NAMESPACE_URL, uuid5

from app.episodes import ResearchEpisode, build_paper_episodes
from app.models import ExtractedPaper
from app.ontology import RelationContract


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


def build_evidence_graph(
    paper: ExtractedPaper, *, workspace_id: str, reference_time: datetime | None = None,
) -> EvidenceGraph:
    episodes = build_paper_episodes(paper, workspace_id=workspace_id, reference_time=reference_time)
    root = episodes[0]
    nodes: list[dict] = []
    edges: list[dict] = []

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
        equation_uuid = node(
            "Equation", equation.equation_id, equation.model_dump(mode="json"), episode_uuid,
        )
        source_uuid = section_nodes.get(equation.section_id, version_uuid)
        source_type = "Section" if equation.section_id else "PaperVersion"
        edge(source_uuid, equation_uuid, source_type, "Equation", "contains", episode_uuid,
             equation.anchor if equation.anchor_is_source else "")
    return EvidenceGraph(
        import_uuid=root.uuid, group_id=root.group_id, paper_id=paper.paper_id,
        version=paper.version, source_sha256=paper.source_sha256,
        paper_version_uuid=version_uuid, episodes=episodes, nodes=nodes, edges=edges,
    )


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
