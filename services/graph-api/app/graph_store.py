from __future__ import annotations

from datetime import datetime
from typing import Protocol

from graphiti_core import Graphiti
from graphiti_core.nodes import EpisodeType

from app.episodes import build_paper_episode, paper_episode_uuid
from app.models import ExtractedPaper
from app.ontology import EDGE_TYPES, ENTITY_TYPES


class EpisodeClient(Protocol):
    async def add_episode(self, **kwargs: object) -> object: ...

    async def build_indices_and_constraints(self) -> object: ...

    async def close(self) -> object: ...


EDGE_TYPE_MAP = {
    ("Paper", "PaperVersion"): ["TypedRelation"],
    ("PaperVersion", "Section"): ["TypedRelation"],
    ("Section", "Equation"): ["TypedRelation"],
    ("Equation", "Symbol"): ["TypedRelation"],
    ("Equation", "Assumption"): ["TypedRelation"],
    ("PaperVersion", "Claim"): ["TypedRelation"],
    ("Claim", "Concept"): ["TypedRelation"],
    ("Equation", "Equation"): ["TypedRelation"],
}

EXTRACTION_INSTRUCTIONS = """
Treat the JSON as source-bound mathematical evidence.
Never claim two equations are equivalent unless the episode states the exact
conditions. Represent a paper's comparison or conclusion as a Claim scoped to
that PaperVersion. Do not invalidate or overwrite facts from other papers.
Preserve equation_id, source_url, source_sha256, and anchor exactly.
""".strip()


class GraphitiResearchStore:
    def __init__(self, client: EpisodeClient) -> None:
        self._client = client

    @classmethod
    def connect(cls, uri: str, user: str, password: str) -> "GraphitiResearchStore":
        return cls(Graphiti(uri, user, password))

    async def initialize(self) -> None:
        await self._client.build_indices_and_constraints()

    async def close(self) -> None:
        await self._client.close()

    async def ingest_paper_version(
        self,
        *,
        workspace_id: str,
        paper: ExtractedPaper,
        reference_time: datetime,
        previous_episode_uuids: list[str] | None = None,
    ) -> object:
        version_label = paper.version if paper.version is not None else "latest"
        return await self._client.add_episode(
            name=f"arxiv:{paper.paper_id}:v{version_label}",
            episode_body=build_paper_episode(paper),
            source_description=(
                f"Structured HTML/MathML extraction from {paper.source_url}"
            ),
            reference_time=reference_time,
            source=EpisodeType.json,
            group_id=f"workspace:{workspace_id}",
            uuid=paper_episode_uuid(paper),
            entity_types=ENTITY_TYPES,
            edge_types=EDGE_TYPES,
            edge_type_map=EDGE_TYPE_MAP,
            excluded_entity_types=["Hypothesis"],
            previous_episode_uuids=previous_episode_uuids,
            custom_extraction_instructions=EXTRACTION_INSTRUCTIONS,
        )
