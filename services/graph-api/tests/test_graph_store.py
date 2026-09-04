from datetime import UTC, datetime
from pathlib import Path

import pytest
from graphiti_core.nodes import EpisodeType

from app.extractor import extract_paper
from app.graph_store import GraphitiResearchStore


FIXTURE = Path(__file__).parent / "fixtures" / "arxiv_sample.html"


class FakeGraphiti:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.initialized = False
        self.closed = False

    async def add_episode(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return {"status": "ok"}

    async def build_indices_and_constraints(self) -> None:
        self.initialized = True

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_ingests_structured_source_bound_episode() -> None:
    client = FakeGraphiti()
    store = GraphitiResearchStore(client)
    paper = extract_paper(
        FIXTURE.read_text(encoding="utf-8"),
        "https://arxiv.org/html/1706.03762v7",
    )
    reference_time = datetime(2023, 8, 2, tzinfo=UTC)

    await store.initialize()
    result = await store.ingest_paper_version(
        workspace_id="lab-1",
        paper=paper,
        reference_time=reference_time,
        previous_episode_uuids=["previous-version"],
    )
    await store.close()

    assert result == {"status": "ok"}
    assert client.initialized is True
    assert client.closed is True
    call = client.calls[0]
    assert call["source"] is EpisodeType.json
    assert call["group_id"] == "workspace:lab-1"
    assert call["reference_time"] == reference_time
    assert call["previous_episode_uuids"] == ["previous-version"]
    assert call["excluded_entity_types"] == ["Hypothesis"]
    assert '"source_sha256"' in str(call["episode_body"])
    assert '"anchor":"S3.E1"' in str(call["episode_body"])
