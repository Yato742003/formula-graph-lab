from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.episodes import workspace_group_id
from app.models import EvidenceSearchRequest
from app.search import (
    EvidenceCandidate,
    EvidenceSearchService,
    InvalidSearchCursor,
    SearchCursorCodec,
    build_lucene_query,
)


def candidate(
    uuid: str,
    *,
    episode: str,
    latex: str,
    graph_distance: int | None = None,
) -> EvidenceCandidate:
    return EvidenceCandidate(
        uuid=uuid,
        kind="Equation",
        logical_id=f"equation-{uuid}",
        payload=f'{{"latex":"{latex}"}}',
        paper_id="2402.08954",
        paper_version=1,
        valid_at=datetime(2024, 2, 14, tzinfo=UTC),
        verification_status="reported",
        episode_uuids=(episode,),
        graph_distance=graph_distance,
    )


class MemoryRepository:
    def __init__(self):
        self.lexical: list[EvidenceCandidate] = []
        self.semantic: list[EvidenceCandidate] = []
        self.neighbors: list[EvidenceCandidate] = []
        self.filters = []

    async def lexical_search(self, query, filters, limit):
        self.filters.append(filters)
        return self.lexical[:limit]

    async def evidence_for_episodes(self, episode_uuids, filters, limit):
        self.filters.append(filters)
        return self.semantic[:limit]

    async def graph_neighbors(self, center_node_uuid, filters, limit):
        self.filters.append(filters)
        return self.neighbors[:limit]


class MemorySemanticSearch:
    def __init__(self, episode_uuids: list[str]):
        self.episode_uuids = episode_uuids
        self.calls = []

    async def search_episode_uuids(self, query, group_id, limit):
        self.calls.append((query, group_id, limit))
        return self.episode_uuids


def request(**overrides) -> EvidenceSearchRequest:
    values = {
        "query": "scaled similarity",
        "workspace_id": "site-scoped-user",
        "limit": 20,
    }
    values.update(overrides)
    return EvidenceSearchRequest(**values)


@pytest.mark.asyncio
async def test_paraphrase_maps_graphiti_episode_back_to_exact_evidence():
    repository = MemoryRepository()
    repository.semantic = [candidate("semantic-hit", episode="ep-method", latex="QK^T")]
    semantic = MemorySemanticSearch(["ep-method"])
    service = EvidenceSearchService(
        repository,
        SearchCursorCodec("s" * 32),
        semantic,
    )

    result = await service.search(request(query="normalize query and key similarity"))

    assert result.semantic_available is True
    assert result.hits[0].uuid == "semantic-hit"
    assert result.hits[0].match_sources == ["semantic"]
    assert result.hits[0].payload["latex"] == "QK^T"
    assert semantic.calls[0][1] == workspace_group_id("site-scoped-user")


@pytest.mark.asyncio
async def test_rrf_graph_boost_and_signed_keyset_pagination_are_deterministic():
    repository = MemoryRepository()
    first = candidate("00000000-0000-0000-0000-000000000001", episode="ep-1", latex="a")
    boosted = candidate(
        "00000000-0000-0000-0000-000000000002",
        episode="ep-2",
        latex="b",
    )
    boosted_neighbor = candidate(
        boosted.uuid,
        episode="ep-2",
        latex="b",
        graph_distance=1,
    )
    third = candidate("00000000-0000-0000-0000-000000000003", episode="ep-3", latex="c")
    repository.lexical = [first, boosted, third]
    repository.neighbors = [boosted_neighbor]
    service = EvidenceSearchService(repository, SearchCursorCodec("p" * 32))
    center = "11111111-1111-1111-1111-111111111111"

    page_one = await service.search(request(limit=1, center_node_uuid=center))
    page_two = await service.search(
        request(limit=1, center_node_uuid=center, cursor=page_one.next_cursor),
    )

    assert page_one.hits[0].uuid == boosted.uuid
    assert page_one.hits[0].match_sources == ["lexical", "graph"]
    assert page_one.hits[0].score_components.graph_distance == 1
    assert page_two.hits[0].uuid == first.uuid
    assert page_one.next_cursor is not None
    assert page_two.next_cursor is not None

    with pytest.raises(InvalidSearchCursor):
        await service.search(
            request(query="different query", cursor=page_one.next_cursor),
        )
    with pytest.raises(InvalidSearchCursor):
        await service.search(
            request(cursor=page_one.next_cursor[:-1] + "A"),
        )


@pytest.mark.asyncio
async def test_filters_are_scoped_to_hashed_workspace_and_preserved():
    repository = MemoryRepository()
    service = EvidenceSearchService(repository, SearchCursorCodec("f" * 32))
    as_of = datetime(2024, 2, 14, 12, tzinfo=UTC)

    await service.search(
        request(
            paper_id="2402.08954",
            version=1,
            entity_types=["Equation"],
            verification_statuses=["reported"],
            as_of=as_of,
        ),
    )

    filters = repository.filters[0]
    assert filters.group_id == workspace_group_id("site-scoped-user")
    assert filters.paper_id == "2402.08954"
    assert filters.version == 1
    assert filters.entity_types == ("Equation",)
    assert filters.verification_statuses == ("reported",)
    assert filters.as_of == as_of


def test_lucene_query_is_partitioned_and_rejects_operator_only_input():
    group_id = workspace_group_id("tenant-a")
    query = build_lucene_query(group_id, 'QK^T + sqrt(d_k) OR group_id:"other"')
    assert query.startswith(f'group_id:"{group_id}" AND (')
    assert 'group_id:"other"' not in query
    assert '"qk"' in query
    assert '"sqrt"' in query
    with pytest.raises(ValueError, match="word"):
        build_lucene_query(group_id, "\\ + =")


def test_request_rejects_naive_time_duplicate_filters_and_non_uuid_center():
    with pytest.raises(ValidationError):
        request(as_of=datetime(2024, 2, 14))
    with pytest.raises(ValidationError):
        request(entity_types=["Equation", "Equation"])
    with pytest.raises(ValidationError):
        request(center_node_uuid="not-a-uuid")
