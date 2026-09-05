import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from graphiti_core.driver.neo4j_driver import Neo4jDriver
from graphiti_core.nodes import EpisodicNode

from app.evidence import build_evidence_graph
from app.evidence_store import Neo4jEvidenceStore
from app.extractor import extract_paper

FIXTURE = Path(__file__).parent / "fixtures" / "arxiv_sample.html"
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def connection():
    if not os.getenv("TEST_NEO4J_PASSWORD"):
        pytest.fail("Integration tests require the dedicated test Neo4j configuration.")
    return (
        os.getenv("TEST_NEO4J_URI", "bolt://127.0.0.1:17687"),
        "neo4j",
        os.environ["TEST_NEO4J_PASSWORD"],
    )


async def test_atomic_import_concurrent_replay_and_native_graphiti_read():
    uri, user, password = connection()
    store = Neo4jEvidenceStore.connect(uri, user, password)
    paper = extract_paper(FIXTURE.read_text(encoding="utf-8"), "https://arxiv.org/html/1706.03762v7")
    graph = build_evidence_graph(paper, workspace_id="test_" + uuid4().hex)
    await store.initialize()
    try:
        receipts = await asyncio.gather(store.ingest(graph), store.ingest(graph))
        assert sorted(r.replayed for r in receipts) == [False, True]
        result, _, _ = await store.driver.execute_query(
            "MATCH (n:Evidence {group_id:$group}) RETURN count(n) AS n",
            group=graph.group_id,
        )
        assert result[0]["n"] == len(graph.nodes)
        result, _, _ = await store.driver.execute_query(
            "MATCH ()-[r:EVIDENCE_RELATION {group_id:$group}]->() "
            "RETURN count(r) AS n, collect(r.episode_uuids) AS sources",
            group=graph.group_id,
        )
        assert result[0]["n"] == len(graph.edges)
        assert all(result[0]["sources"])
        result, _, _ = await store.driver.execute_query(
            "MATCH (n:Evidence {group_id:$group, kind:'Equation'}) RETURN n.payload AS payload",
            group=graph.group_id,
        )
        assert {json.loads(r["payload"])["latex"] for r in result} == {e.latex for e in paper.equations}
        assert all("verification_status" not in json.loads(r["payload"]) for r in result)

        # Read persisted episodes with Graphiti's real SDK, no model calls.
        native_driver = Neo4jDriver(uri, user, password)
        try:
            for expected in graph.episodes:
                actual = await EpisodicNode.get_by_uuid(native_driver, expected.uuid)
                assert actual.content == expected.body
                assert actual.group_id == expected.group_id
        finally:
            await native_driver.close()
    finally:
        await store.close()


async def test_workspaces_do_not_share_equations_or_episode_ids():
    store = Neo4jEvidenceStore.connect(*connection())
    paper = extract_paper(FIXTURE.read_text(encoding="utf-8"), "https://arxiv.org/html/1706.03762v7")
    a = build_evidence_graph(paper, workspace_id="test_a_" + uuid4().hex)
    b = build_evidence_graph(paper, workspace_id="test_b_" + uuid4().hex)
    try:
        await store.initialize()
        await store.ingest(a)
        await store.ingest(b)
        result, _, _ = await store.driver.execute_query(
            "MATCH (a:Evidence)-[r:EVIDENCE_RELATION]->(b:Evidence) "
            "WHERE r.group_id IN $groups AND a.group_id <> b.group_id RETURN count(r) AS n",
            groups=[a.group_id, b.group_id],
        )
        assert result[0]["n"] == 0
        assert {e.uuid for e in a.episodes}.isdisjoint({e.uuid for e in b.episodes})
    finally:
        await store.close()


async def test_failure_rolls_back_nodes_episodes_and_receipt():
    class FailingStore(Neo4jEvidenceStore):
        @staticmethod
        async def _write(tx, graph):
            await Neo4jEvidenceStore._write(tx, graph)
            raise ValueError("injected failure before commit")

    store = FailingStore.connect(*connection())
    paper = extract_paper(FIXTURE.read_text(encoding="utf-8"), "https://arxiv.org/html/1706.03762v7")
    graph = build_evidence_graph(paper, workspace_id="test_rollback_" + uuid4().hex)
    try:
        await store.initialize()
        with pytest.raises(ValueError, match="injected failure"):
            await store.ingest(graph)
        records, _, _ = await store.driver.execute_query(
            "MATCH (n {group_id:$group}) RETURN count(n) AS n", group=graph.group_id,
        )
        assert records[0]["n"] == 0
    finally:
        await store.close()
