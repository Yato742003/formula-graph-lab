from __future__ import annotations

from datetime import UTC, datetime

from graphiti_core import Graphiti
from graphiti_core.errors import NodeNotFoundError
from graphiti_core.nodes import EpisodeType, EpisodicNode
from graphiti_core.search.search_config import (
    EdgeReranker,
    EdgeSearchConfig,
    EdgeSearchMethod,
    EpisodeReranker,
    EpisodeSearchConfig,
    EpisodeSearchMethod,
    SearchConfig,
)

from app.episodes import ResearchEpisode


class NativeGraphitiClient:
    """Adapt Graphiti 0.30.1's existing-UUID contract to deterministic episodes."""

    def __init__(self, client: Graphiti) -> None:
        self.client = client

    async def prepare_episode(self, episode: ResearchEpisode) -> None:
        try:
            existing = await EpisodicNode.get_by_uuid(self.client.driver, episode.uuid)
        except NodeNotFoundError:
            await EpisodicNode(
                uuid=episode.uuid, name=episode.name, group_id=episode.group_id,
                source=EpisodeType.json, content=episode.body,
                source_description="Source-bound arXiv HTML extraction",
                created_at=datetime.now(UTC), valid_at=episode.reference_time,
            ).save(self.client.driver)
            return
        if existing.group_id != episode.group_id or existing.content != episode.body:
            raise ValueError("Existing episode identity conflicts with source/workspace.")

    async def add_episode(self, **kwargs: object) -> object:
        return await self.client.add_episode(**kwargs)

    async def build_indices_and_constraints(self) -> object:
        return await self.client.build_indices_and_constraints()

    async def search_episode_uuids(
        self,
        query: str,
        group_id: str,
        limit: int,
    ) -> list[str]:
        config = SearchConfig(
            edge_config=EdgeSearchConfig(
                search_methods=[
                    EdgeSearchMethod.bm25,
                    EdgeSearchMethod.cosine_similarity,
                ],
                reranker=EdgeReranker.rrf,
            ),
            episode_config=EpisodeSearchConfig(
                search_methods=[EpisodeSearchMethod.bm25],
                reranker=EpisodeReranker.rrf,
            ),
            limit=limit,
        )
        result = await self.client.search_(
            query,
            config,
            group_ids=[group_id],
        )
        episode_uuids = []
        for edge in result.edges:
            episode_uuids.extend(edge.episodes)
        episode_uuids.extend(episode.uuid for episode in result.episodes)
        return list(dict.fromkeys(episode_uuids))[:limit]

    async def close(self) -> object:
        return await self.client.close()
