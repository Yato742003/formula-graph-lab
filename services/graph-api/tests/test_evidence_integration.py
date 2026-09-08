import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from graphiti_core.driver.neo4j_driver import Neo4jDriver
from graphiti_core.nodes import EpisodicNode

from app.evidence import build_evidence_graph, build_reported_claim
from app.evidence_store import Neo4jEvidenceStore
from app.extractor import extract_paper
from app.models import EvidenceGraphSnapshotRequest, EvidenceSearchRequest
from app.search import EvidenceSearchService, SearchCursorCodec

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


async def assert_all_evidence_relations_resolve_to_episodes(store, group_id: str):
    records, _, _ = await store.driver.execute_query(
        """
        MATCH ()-[r:EVIDENCE_RELATION {group_id:$group}]->()
        WITH collect(r) AS relations
        WITH relations, size(relations) AS relation_count
        UNWIND relations AS relation
        UNWIND coalesce(relation.episode_uuids, []) AS episode_uuid
        OPTIONAL MATCH (episode:Episodic {uuid:episode_uuid, group_id:$group})
        RETURN relation_count, count(episode_uuid) AS source_count,
               count(episode) AS resolved_count
        """,
        group=group_id,
    )
    assert records[0]["source_count"] >= records[0]["relation_count"]
    assert records[0]["resolved_count"] == records[0]["source_count"]


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
        await assert_all_evidence_relations_resolve_to_episodes(store, graph.group_id)
        result, _, _ = await store.driver.execute_query(
            "MATCH (n:Evidence {group_id:$group, kind:'Equation'}) RETURN n.payload AS payload",
            group=graph.group_id,
        )
        assert {json.loads(r["payload"])["latex"] for r in result} == {
            e.latex for e in paper.equations
        }
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


def revision(paper_id: str, version: int, formula: str):
    return extract_paper(
        f"<html><div id='watermark-tr'>arXiv:{paper_id}v{version} [cs.AI] "
        f"0{version} Feb 2024</div><section id='S1'><h2>1 Result</h2>"
        f"<math id='S1.E1' display='block' alttext='{formula}'></math></section></html>",
        f"https://arxiv.org/html/{paper_id}v{version}",
    )


async def test_out_of_order_revisions_history_and_cross_paper_disagreement():
    from datetime import UTC, datetime

    store = Neo4jEvidenceStore.connect(*connection())
    workspace = "test_history_" + uuid4().hex
    v1 = build_evidence_graph(revision("2402.08954", 1, "x=1"), workspace_id=workspace)
    v2 = build_evidence_graph(revision("2402.08954", 2, "x=2"), workspace_id=workspace)
    other = build_evidence_graph(revision("2402.08955", 1, "x=-1"), workspace_id=workspace)
    try:
        await store.initialize()
        # Deliberately ingest out of order; the direct chain must still be v2 -> v1.
        await store.ingest(v2)
        await store.ingest(v1)
        await store.ingest(other)
        records, _, _ = await store.driver.execute_query(
            """
            MATCH (newer:Evidence)-[r:EVIDENCE_RELATION {relation:'supersedes'}]->
                  (older:Evidence)
            WHERE r.group_id=$group
            RETURN newer.paper_id AS newer_paper, newer.paper_version AS newer_version,
                   older.paper_id AS older_paper, older.paper_version AS older_version
            """,
            group=v1.group_id,
        )
        assert [dict(record) for record in records] == [{
            "newer_paper": "2402.08954", "newer_version": 2,
            "older_paper": "2402.08954", "older_version": 1,
        }]
        assert await store.version_at(
            workspace_id=workspace, paper_id="2402.08954",
            as_of=datetime(2024, 1, 31, 23, 59, tzinfo=UTC),
        ) is None
        historical = await store.version_at(
            workspace_id=workspace, paper_id="2402.08954",
            as_of=datetime(2024, 2, 1, 12, tzinfo=UTC),
        )
        current = await store.version_at(
            workspace_id=workspace, paper_id="2402.08954",
            as_of=datetime(2024, 2, 2, 12, tzinfo=UTC),
        )
        assert historical and historical.version == 1
        assert current and current.version == 2

        old_claim = build_reported_claim(
            v1, logical_id="convergence", statement="The objective converges.",
            source_anchor="S1.E1",
        )
        other_claim = build_reported_claim(
            other, logical_id="convergence", statement="The objective diverges.",
            source_anchor="S1.E1",
        )
        await store.record_claim(old_claim)
        await store.record_claim(other_claim, disagrees_with_uuid=old_claim.uuid)
        records, _, _ = await store.driver.execute_query(
            """
            MATCH (c:Evidence {group_id:$group, kind:'Claim'})
            OPTIONAL MATCH (c)-[d:EVIDENCE_RELATION {relation:'disagrees_with'}]->(:Evidence)
            RETURN c.paper_id AS paper_id, c.payload AS payload,
                   c.invalid_at AS invalid_at, count(d) AS disagreements
            ORDER BY c.paper_id
            """,
            group=v1.group_id,
        )
        claims = [dict(record) for record in records]
        assert [json.loads(item["payload"])["status"] for item in claims] == [
            "reported", "reported",
        ]
        assert all(item["invalid_at"] is None for item in claims)
        assert sum(item["disagreements"] for item in claims) == 1
        await assert_all_evidence_relations_resolve_to_episodes(store, v1.group_id)
    finally:
        await store.close()


async def test_semantic_receipt_blocks_ambiguous_replay_until_reconciled():
    store = Neo4jEvidenceStore.connect(*connection())
    paper = revision("2402.08956", 1, "x=1")
    graph = build_evidence_graph(paper, workspace_id="test_receipt_" + uuid4().hex)
    episode = graph.episodes[0]
    first_attempt = str(uuid4())
    try:
        await store.initialize()
        await store.ingest(graph)
        first = await store.begin_enrichment(
            group_id=graph.group_id, import_uuid=graph.import_uuid,
            episode_uuid=episode.uuid, attempt_uuid=first_attempt,
        )
        assert first.action == "run"
        concurrent = await store.begin_enrichment(
            group_id=graph.group_id, import_uuid=graph.import_uuid,
            episode_uuid=episode.uuid, attempt_uuid=str(uuid4()),
        )
        assert concurrent.action == "needs_reconciliation"
        await store.mark_enrichment_uncertain(
            receipt_uuid=first.receipt_uuid, attempt_uuid=first_attempt,
            error_type="RuntimeError",
        )
        blocked = await store.begin_enrichment(
            group_id=graph.group_id, import_uuid=graph.import_uuid,
            episode_uuid=episode.uuid, attempt_uuid=str(uuid4()),
        )
        assert blocked.action == "needs_reconciliation"
        await store.allow_enrichment_retry(
            receipt_uuid=first.receipt_uuid, operator_id="integration-test",
            reason="Verified that no semantic nodes were committed.",
        )
        retry_attempt = str(uuid4())
        retry = await store.begin_enrichment(
            group_id=graph.group_id, import_uuid=graph.import_uuid,
            episode_uuid=episode.uuid, attempt_uuid=retry_attempt,
        )
        assert retry.action == "run"
        await store.complete_enrichment(
            receipt_uuid=retry.receipt_uuid, attempt_uuid=retry_attempt,
        )
        replay = await store.begin_enrichment(
            group_id=graph.group_id, import_uuid=graph.import_uuid,
            episode_uuid=episode.uuid, attempt_uuid=str(uuid4()),
        )
        assert replay.action == "completed"
        records, _, _ = await store.driver.execute_query(
            "MATCH (r:SemanticEnrichment {uuid:$uuid}) "
            "RETURN r.status AS status, r.attempt_count AS attempts, "
            "r.error_type AS error_type",
            uuid=first.receipt_uuid,
        )
        assert dict(records[0]) == {
            "status": "completed", "attempts": 2, "error_type": None,
        }
    finally:
        await store.close()


async def test_search_filters_neighbors_pagination_and_tenant_isolation():
    from datetime import UTC, datetime

    store = Neo4jEvidenceStore.connect(*connection())
    workspace_a = "test_search_a_" + uuid4().hex
    workspace_b = "test_search_b_" + uuid4().hex
    a_v1 = build_evidence_graph(
        revision("2402.09101", 1, "spectralneedle=1"),
        workspace_id=workspace_a,
    )
    a_v2 = build_evidence_graph(
        revision("2402.09101", 2, "spectralneedle=2"),
        workspace_id=workspace_a,
    )
    b_v1 = build_evidence_graph(
        revision("2402.09101", 1, "spectralneedle=999"),
        workspace_id=workspace_b,
    )
    try:
        await store.initialize()
        await store.ingest(a_v2)
        await store.ingest(a_v1)
        await store.ingest(b_v1)
        search = EvidenceSearchService(store, SearchCursorCodec("i" * 32))
        base = {
            "query": "spectralneedle",
            "workspace_id": workspace_a,
            "paper_id": "2402.09101",
            "entity_types": ["Equation"],
            "verification_statuses": ["reported"],
            "limit": 1,
        }

        page_one = await search.search(EvidenceSearchRequest(**base))
        page_two = await search.search(
            EvidenceSearchRequest(**base, cursor=page_one.next_cursor),
        )
        a_equation_uuids = {
            node["uuid"]
            for graph in (a_v1, a_v2)
            for node in graph.nodes
            if node["kind"] == "Equation"
        }
        assert {page_one.hits[0].uuid, page_two.hits[0].uuid} == a_equation_uuids
        assert page_one.next_cursor is not None
        assert page_two.next_cursor is None
        assert all(hit.match_sources == ["lexical"] for hit in [
            page_one.hits[0],
            page_two.hits[0],
        ])

        version_one = await search.search(
            EvidenceSearchRequest(**{**base, "version": 1, "limit": 20}),
        )
        assert [hit.version for hit in version_one.hits] == [1]
        historical = await search.search(
            EvidenceSearchRequest(**{
                **base,
                "as_of": datetime(2024, 2, 1, 23, 59, tzinfo=UTC),
                "limit": 20,
            }),
        )
        assert [hit.version for hit in historical.hits] == [1]

        b_page = await search.search(
            EvidenceSearchRequest(**{
                **base,
                "workspace_id": workspace_b,
                "limit": 20,
            }),
        )
        b_equation_uuid = next(
            node["uuid"] for node in b_v1.nodes if node["kind"] == "Equation"
        )
        assert [hit.uuid for hit in b_page.hits] == [b_equation_uuid]
        assert b_equation_uuid not in a_equation_uuids

        graph_page = await search.search(
            EvidenceSearchRequest(
                query="term-that-does-not-exist",
                workspace_id=workspace_a,
                paper_id="2402.09101",
                version=2,
                entity_types=["Section"],
                center_node_uuid=a_v2.paper_version_uuid,
                limit=20,
            ),
        )
        assert len(graph_page.hits) == 1
        assert graph_page.hits[0].match_sources == ["graph"]
        assert graph_page.hits[0].score_components.graph_distance == 1

        snapshot = await store.graph_snapshot(EvidenceGraphSnapshotRequest(
            workspace_id=workspace_a,
            paper_id="2402.09101",
            version=2,
        ))
        expected_node_uuids = {node["uuid"] for node in a_v2.nodes}
        expected_node_uuids.add(a_v1.paper_version_uuid)
        assert {node.uuid for node in snapshot.nodes} == expected_node_uuids
        assert snapshot.truncated is False
        assert snapshot.edges
        assert all(
            edge.source_uuid in expected_node_uuids
            and edge.target_uuid in expected_node_uuids
            for edge in snapshot.edges
        )
        assert all(node.paper_id == "2402.09101" for node in snapshot.nodes)
        assert all(
            node.version in {None, 1, 2}
            for node in snapshot.nodes
        )
        assert any(
            edge.relation == "supersedes"
            and edge.source_uuid == a_v2.paper_version_uuid
            and edge.target_uuid == a_v1.paper_version_uuid
            for edge in snapshot.edges
        )
        assert not any("spectralneedle=999" in str(node.payload) for node in snapshot.nodes)
    finally:
        await store.close()
