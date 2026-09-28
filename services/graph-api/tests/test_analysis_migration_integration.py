import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.analysis_migration import AnalysisMigration
from app.evidence import build_evidence_graph
from app.evidence_store import Neo4jEvidenceStore
from app.extractor import extract_paper
from app.models import EvidenceGraphSnapshotRequest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _store():
    if not os.getenv("TEST_NEO4J_PASSWORD"):
        pytest.fail("Dedicated test Neo4j configuration is required.")
    return Neo4jEvidenceStore.connect(
        os.getenv("TEST_NEO4J_URI", "bolt://127.0.0.1:17687"),
        "neo4j", os.environ["TEST_NEO4J_PASSWORD"],
    )


def _graphs(workspace):
    paper = extract_paper(
        "<html><section id='S1'><h2>Result</h2>"
        "<math id='S1.E1' display='block' alttext='x/x'/></section></html>",
        "https://arxiv.org/html/2402.08954v1",
    )
    ref_time = datetime(2024, 2, 1, 0, 0, tzinfo=UTC)
    current = build_evidence_graph(paper, workspace_id=workspace, reference_time=ref_time)
    nodes = []
    for node in current.nodes:
        if node["kind"] == "Equation":
            payload = {**json.loads(node["payload"]), "formula_analysis": {
                "status": "well_typed", "canonical_hash": "b" * 64,
            }}
            nodes.append({**node, "payload": json.dumps(payload)})
        else:
            nodes.append(node)
    return current, replace(current, nodes=nodes, analyses=[])


async def test_migrate_resume_replay_history_rollback_preserve_exact_source():
    store = _store()
    workspace = "migration_" + uuid4().hex
    current, legacy = _graphs(workspace)
    equation = next(n for n in current.nodes if n["kind"] == "Equation")
    try:
        await store.initialize()
        await store.ingest(legacy)
        migration = AnalysisMigration(store)
        before = await migration.inventory(workspace)
        assert before["legacy_embedded"] == 1
        first = await migration.migrate_batch(workspace, run_id="migration-test", limit=1)
        assert first["status"] == "running"
        last = await migration.migrate_batch(workspace, run_id="migration-test", limit=1)
        assert last["status"] == "completed"
        assert await migration.migrate_batch(workspace, run_id="migration-test") == last
        history = await store.analysis_history(
            workspace_id=workspace, equation_uuid=equation["uuid"],
        )
        assert len(history) == 2
        assert {a["analyzer_version"] for a in history} == {
            "legacy-embedded.v0", "formula-analyzer.v7",
        }
        assert any(a["payload"].get("domain_assessment", {}).get("status") == "unresolved"
                   for a in history)
        assert await migration.inventory(workspace) == before
        assert (await migration.verify_receipt(
            workspace, receipt_id=last["receipt_id"],
        ))["verified_sources"] == 1
        snapshot = await store.graph_snapshot(EvidenceGraphSnapshotRequest(
            workspace_id=workspace, paper_id=current.paper_id, version=1,
        ))
        selected = next(n for n in snapshot.nodes if n.uuid == equation["uuid"])
        assert selected.payload["formula_analysis"]["domain_assessment"]["status"] == "unresolved"
        assert await store.analysis_history(
            workspace_id="other-workspace", equation_uuid=equation["uuid"],
        ) == []
        with pytest.raises(ValueError, match="absent"):
            await migration.rollback("other-workspace", receipt_id=last["receipt_id"])
        assert await migration.rollback(workspace, receipt_id=last["receipt_id"]) == 2
        assert await migration.rollback(workspace, receipt_id=last["receipt_id"]) == 0
        assert await migration.inventory(workspace) == before
        assert await store.analysis_history(
            workspace_id=workspace, equation_uuid=equation["uuid"],
        ) == []
        assert len(await store.analysis_history(
            workspace_id=workspace, equation_uuid=equation["uuid"], include_retired=True,
        )) == 2
        with pytest.raises(ValueError, match="new run ID"):
            await migration.migrate_batch(workspace, run_id="migration-test")
        restored = await migration.migrate_batch(workspace, run_id="migration-restored")
        assert restored["status"] == "completed"
        assert len(await store.analysis_history(
            workspace_id=workspace, equation_uuid=equation["uuid"],
        )) == 2
        assert await migration.rollback(workspace, receipt_id=last["receipt_id"]) == 0
        assert await migration.rollback(workspace, receipt_id=restored["receipt_id"]) == 2
    finally:
        await store.close()


async def test_partial_transaction_failure_resumes_without_orphans():
    class FailingMigration(AnalysisMigration):
        @staticmethod
        async def _batch(tx, group, receipt_id, limit):
            await AnalysisMigration._batch(tx, group, receipt_id, limit)
            raise ValueError("injected after writes")

    store = _store()
    workspace = "migration_failure_" + uuid4().hex
    current, legacy = _graphs(workspace)
    try:
        await store.initialize()
        await store.ingest(legacy)
        before = await AnalysisMigration(store).inventory(workspace)
        with pytest.raises(ValueError, match="injected"):
            await FailingMigration(store).migrate_batch(workspace, run_id="resume-test")
        rows, _, _ = await store.driver.execute_query(
            "MATCH (a:FormulaAnalysisVersion {group_id:$group}) RETURN count(a) AS count",
            group=current.group_id,
        )
        assert rows[0]["count"] == 0
        result = await AnalysisMigration(store).migrate_batch(workspace, run_id="resume-test")
        assert result["processed"] == 1
        assert await AnalysisMigration(store).inventory(workspace) == before
    finally:
        await store.close()


async def test_reimport_and_migration_do_not_rewrite_or_retire_preexisting_analysis():
    store = _store()
    workspace = "migration_reimport_" + uuid4().hex
    current, legacy = _graphs(workspace)
    try:
        await store.initialize()
        await store.ingest(legacy)
        migration = AnalysisMigration(store)
        before = await migration.inventory(workspace)
        assert (await store.ingest(current)).replayed is True
        receipt = await migration.migrate_batch(workspace, run_id="after-reimport")
        assert await migration.rollback(workspace, receipt_id=receipt["receipt_id"]) == 1
        equation = next(n for n in current.nodes if n["kind"] == "Equation")
        history = await store.analysis_history(
            workspace_id=workspace, equation_uuid=equation["uuid"],
        )
        assert len(history) == 1
        assert history[0]["analyzer_version"] == "formula-analyzer.v7"
        assert await migration.inventory(workspace) == before
        bad = replace(current, analyses=[{**current.analyses[0], "source_hash": "0" * 64}])
        with pytest.raises(ValueError, match="immutable source"):
            await store.ingest(bad)
    finally:
        await store.close()
