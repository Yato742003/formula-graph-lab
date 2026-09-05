import inspect
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from graphiti_core import Graphiti
from graphiti_core.errors import NodeNotFoundError
from graphiti_core.helpers import validate_group_id
from graphiti_core.nodes import EpisodicNode, EpisodeType
from graphiti_core.utils.ontology_utils.entity_types_utils import validate_entity_types

from app.episodes import build_paper_episodes, workspace_group_id
from app.extractor import extract_paper
from app.graph_store import GraphitiResearchStore
from app.graphiti_client import NativeGraphitiClient

FIXTURE = Path(__file__).parent / "fixtures" / "arxiv_sample.html"


def sample():
    return extract_paper(FIXTURE.read_text(encoding="utf-8"), "https://arxiv.org/html/1706.03762v7")


class ContractGraphiti:
    def __init__(self):
        self.calls = []
        self.prepared = {}
        self.initialized = self.closed = False

    async def prepare_episode(self, episode):
        self.prepared[episode.uuid] = episode

    async def add_episode(self, **kwargs):
        # Validate against the installed SDK, not a permissive **kwargs-only mock.
        inspect.signature(Graphiti.add_episode).bind(self, **kwargs)
        validate_group_id(kwargs["group_id"])
        validate_entity_types(kwargs["entity_types"])
        assert kwargs["uuid"] in self.prepared
        self.calls.append(kwargs)
        return {"uuid": kwargs["uuid"]}

    async def build_indices_and_constraints(self):
        self.initialized = True

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_ingests_ordered_source_episodes_with_sdk_compatible_contract():
    client = ContractGraphiti()
    store = GraphitiResearchStore(client)
    await store.initialize()
    results = await store.ingest_paper_version(workspace_id="lab-1", paper=sample())
    await store.close()

    assert len(results) == len(client.calls) == 2
    assert client.initialized and client.closed
    metadata, section = client.calls
    assert metadata["source"] is EpisodeType.json
    assert metadata["group_id"] == workspace_group_id("lab-1")
    assert metadata["reference_time"] == datetime(2023, 8, 2, tzinfo=UTC)
    assert metadata["previous_episode_uuids"] == []
    assert section["previous_episode_uuids"] == [metadata["uuid"]]
    assert section["saga_previous_episode_uuid"] == metadata["uuid"]
    assert metadata["saga"] == section["saga"]
    assert metadata["excluded_entity_types"] == ["Hypothesis"]
    assert json.loads(section["episode_body"])["equations"][0]["anchor"] == "S3.E1"


@pytest.mark.asyncio
async def test_stable_identity_does_not_collide_between_workspaces():
    client = ContractGraphiti()
    store = GraphitiResearchStore(client)
    await store.ingest_paper_version(workspace_id="a", paper=sample())
    await store.ingest_paper_version(workspace_id="b", paper=sample())
    assert {c["uuid"] for c in client.calls[:2]}.isdisjoint(
        {c["uuid"] for c in client.calls[2:]}
    )


@pytest.mark.asyncio
async def test_native_adapter_seeds_missing_uuid_before_sdk_ingestion(monkeypatch):
    driver = object()
    sdk = SimpleNamespace(driver=driver, add_episode=AsyncMock(return_value="ok"))
    adapter = NativeGraphitiClient(sdk)
    episode = build_paper_episodes(sample(), workspace_id="a")[0]
    monkeypatch.setattr(EpisodicNode, "get_by_uuid", AsyncMock(side_effect=NodeNotFoundError(episode.uuid)))
    saved = []

    async def save(node, target_driver):
        assert target_driver is driver
        saved.append(node)

    monkeypatch.setattr(EpisodicNode, "save", save)
    await adapter.prepare_episode(episode)
    assert saved[0].uuid == episode.uuid
    assert saved[0].content == episode.body
    assert saved[0].group_id == episode.group_id
    assert saved[0].valid_at == episode.reference_time


@pytest.mark.asyncio
async def test_native_adapter_rejects_existing_cross_workspace_episode(monkeypatch):
    episode = build_paper_episodes(sample(), workspace_id="a")[0]
    sdk = SimpleNamespace(driver=object())
    monkeypatch.setattr(EpisodicNode, "get_by_uuid", AsyncMock(return_value=SimpleNamespace(
        group_id="another_workspace", content=episode.body,
    )))
    with pytest.raises(ValueError, match="conflicts"):
        await NativeGraphitiClient(sdk).prepare_episode(episode)
