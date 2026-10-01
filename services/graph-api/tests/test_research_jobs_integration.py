import asyncio
import json
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio

from app.evidence import build_evidence_graph
from app.evidence_store import Neo4jEvidenceStore
from app.extractor import extract_paper
from app.research_jobs import JobRejected, ResearchQueue, digest
from app.worker_auth import WorkerPrincipal

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
IMAGE = "sha256:" + "a" * 64


@pytest_asyncio.fixture
async def queue():
    assert os.getenv("TEST_NEO4J_PASSWORD"), "Isolated Neo4j required"
    store = Neo4jEvidenceStore.connect(os.environ["TEST_NEO4J_URI"], "neo4j",
                                       os.environ["TEST_NEO4J_PASSWORD"])
    workspace = "h6-" + uuid4().hex
    await store.initialize()
    paper = extract_paper(
        '<html><section id="S1"><h2>Math</h2>'
        '<math id="E1" display="block" alttext="1+1"/></section></html>',
        "https://arxiv.org/html/2402.08954v1",
    )
    graph = build_evidence_graph(paper, workspace_id=workspace, reference_time=datetime.now(UTC))
    await store.ingest(graph)
    target = next(n["uuid"] for n in graph.nodes if n["kind"] == "Equation")
    actor = WorkerPrincipal("v1", "verifier", frozenset({workspace}))
    try:
        yield ResearchQueue(store, b"q" * 40), actor, target
    finally:
        await store.driver.execute_query(
            "MATCH (j:ResearchJob {workspace:$workspace}) DELETE j",
            workspace=workspace, database_=store.database,
        )
        await store.close()


def payload(ms=2000):
    return {"formula_a": "1+1", "formula_b": "2", "format": "latex", "timeout_ms": ms}


async def admit(queue, actor, target, key, ms=2000):
    return await queue.admit(actor, workspace_id=next(iter(actor.workspaces)), target_uuid=target,
                             idempotency_key=key, request_hash=digest(payload(ms)),
                             payload=payload(ms), image=IMAGE)


async def test_atomic_quota_claim_and_persisted_replay(queue):
    q, actor, target = queue
    attempts = await asyncio.gather(
        *(admit(q, actor, target, str(i)) for i in range(5)), return_exceptions=True,
    )
    tickets = [t for t in attempts if not isinstance(t, Exception)]
    assert len(tickets) == 2
    assert all(isinstance(t, JobRejected) and t.status == 429
               for t in attempts if isinstance(t, Exception))
    ticket = tickets[0]
    claims = await asyncio.gather(q.claim(ticket, actor, payload(), IMAGE),
                                  q.claim(ticket, actor, payload(), IMAGE), return_exceptions=True)
    assert sum(isinstance(t, JobRejected) for t in claims) == 1
    await q.finish(ticket, '{"server_result":true}')
    # A fresh controller must return the stored result without claiming again.
    rows, _, _ = await q.store.driver.execute_query(
        "MATCH (j:ResearchJob {id:$id}) RETURN j.envelope AS envelope", id=ticket.envelope.job_id,
    )
    assert json.loads(rows[0]["envelope"])["workspace_id"] in actor.workspaces
    assert not any(secret in rows[0]["envelope"] for secret in ("<html", "formula_a", "qqqqqq"))


async def test_ops_metrics_and_recovery_cannot_read_or_mutate_another_workspace(queue):
    q, actor, target = queue
    own = next(iter(actor.workspaces))
    foreign = "other-" + uuid4().hex
    foreign_job = str(uuid4())
    ticket = await admit(q, actor, target, "recover-own")
    try:
        await q.store.driver.execute_query(
            "CREATE (:ResearchJob {id:$id, workspace:$workspace, state:'running', "
            "expires_at:1, reserved_ms:3000})",
            id=foreign_job, workspace=foreign, database_=q.store.database,
        )
        metrics = await q.job_health_metrics(own, now=ticket.envelope.expires_at + 1)
        assert metrics["total"] == 1 and metrics["stuck"] == 1
        report = await q.recover_stuck_jobs(own, now=ticket.envelope.expires_at + 1)
        assert report["recovered_job_ids"] == [ticket.envelope.job_id]
        again = await q.recover_stuck_jobs(own, now=ticket.envelope.expires_at + 2)
        assert again["recovered_count"] == 0
        rows, _, _ = await q.store.driver.execute_query(
            "MATCH (j:ResearchJob) WHERE j.id IN $ids "
            "RETURN j.id AS id, j.state AS state, j.reserved_ms AS reserved_ms",
            ids=[ticket.envelope.job_id, foreign_job], database_=q.store.database,
        )
        by_id = {r["id"]: r for r in rows}
        assert by_id[foreign_job]["state"] == "running"
        assert by_id[ticket.envelope.job_id]["state"] == "failed"
        assert by_id[ticket.envelope.job_id]["reserved_ms"] == ticket.envelope.reserved_ms
    finally:
        await q.store.driver.execute_query(
            "MATCH (j:ResearchJob {id:$id}) DELETE j", id=foreign_job, database_=q.store.database,
        )


async def test_hourly_budget_survives_completion_and_retry(queue):
    q, actor, target = queue
    for i in range(4):
        ticket = await admit(q, actor, target, str(i), 30000)
        await q.claim(ticket, actor, payload(30000), IMAGE)
        await q.finish(ticket, '{}')
        again = await admit(ResearchQueue(q.store, q.key), actor, target, str(i), 30000)
        assert again.result == '{}'
    with pytest.raises(JobRejected, match="QUOTA"):
        await admit(q, actor, target, "over-budget")
    with pytest.raises(JobRejected, match="CONFLICT"):
        await admit(q, actor, target, "0", 1000)


async def test_fabricated_target_and_result_tampering_rejected(queue):
    q, actor, target = queue
    with pytest.raises(ValueError):
        await admit(q, actor, "does-not-exist", "fabricated")
    ticket = await admit(q, actor, target, "real")
    await q.claim(ticket, actor, payload(), IMAGE)
    await q.finish(ticket, '{}')
    await q.store.driver.execute_query(
        "MATCH (j:ResearchJob {id:$id}) SET j.result=$result",
        id=ticket.envelope.job_id, result='{"fitness":1}',
    )
    with pytest.raises(JobRejected, match="RESULT_SIGNATURE"):
        await admit(q, actor, target, "real")
