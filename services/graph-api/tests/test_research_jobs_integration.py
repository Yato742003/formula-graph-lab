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


async def test_execution_profile_drift_and_profile_scoped_idempotency(queue, monkeypatch):
    q, actor, target = queue
    workspace_id = next(iter(actor.workspaces))
    ticket = await admit(q, actor, target, "profile-drift-key")
    assert ticket.envelope.schema_version == "research-job.v2"
    assert ticket.envelope.execution_profile is not None
    assert ticket.envelope.execution_profile.ram_bytes == 256 * 1024**2

    monkeypatch.setenv("FGL_WORKER_RAM_LIMIT_MB", "512")
    with pytest.raises(JobRejected, match="EXECUTION_PROFILE_DRIFT") as exc_info:
        await q.claim(ticket, actor, payload(), IMAGE)
    assert exc_info.value.status == 409

    with pytest.raises(JobRejected, match="JOB_IDEMPOTENCY_CONFLICT"):
        await admit(q, actor, target, "profile-drift-key")

    monkeypatch.delenv("FGL_WORKER_RAM_LIMIT_MB", raising=False)
    reviewed_payload = {
        **payload(),
        "contract_review_ids": ("rev-1",),
        "contract_review_hash": "a" * 64,
    }
    reviewed_ticket = await q.admit(
        actor,
        workspace_id=workspace_id,
        target_uuid=target,
        idempotency_key="reviewed-6-key",
        request_hash=digest(reviewed_payload),
        payload=reviewed_payload,
        image=IMAGE,
    )
    await q.claim(reviewed_ticket, actor, reviewed_payload, IMAGE)
    await q.finish(reviewed_ticket, '{"ok":true}')


async def test_double_delivery_quarantine_and_dead_letter_reconciliation(queue, caplog):
    import logging

    from app.verification import CheckResult, VerificationVector

    q, actor, target = queue
    own = next(iter(actor.workspaces))

    # 1. Double delivery with identical result is idempotent; conflicting payload fails 409.
    t_dup = await admit(q, actor, target, "double-delivery-key", ms=10_000)
    await q.claim(t_dup, actor, payload(10_000), IMAGE)
    await q.finish(t_dup, '{"ok":true}')
    await q.finish(t_dup, '{"ok": true}')
    with pytest.raises(JobRejected, match="JOB_RESULT_CONFLICT") as conflict_exc:
        await q.finish(t_dup, '{"ok":false}')
    assert conflict_exc.value.status == 409

    # 2. Late delivery commits quarantine metadata + dead_letter_alert and raises 409.
    t_late = await admit(q, actor, target, "late-delivery-key", ms=10_000)
    await q.claim(t_late, actor, payload(10_000), IMAGE)
    with caplog.at_level(logging.INFO, logger="fgl.telemetry"):
        await q.store.driver.execute_query(
            "MATCH (j:ResearchJob {id:$id}) SET j.expires_at=1",
            id=t_late.envelope.job_id,
            database_=q.store.database,
        )
        with pytest.raises(JobRejected, match="JOB_RESULT_REQUIRES_RECONCILIATION"):
            await q.finish(t_late, '{"late":true}')
    dlq_rows = await q.list_dead_letter_jobs(own)
    late_row = next(r for r in dlq_rows if r["job_id"] == t_late.envelope.job_id)
    assert late_row["reconciliation_status"] == "quarantined"
    assert late_row["prior_state"] == "running"
    assert late_row["retry_eligible"] is False
    assert late_row["reservation_retained"] is True
    assert any(
        json.loads(r.message).get("event") == "dead_letter_alert"
        and json.loads(r.message).get("job_id") == t_late.envelope.job_id
        for r in caplog.records
        if r.name == "fgl.telemetry"
    )

    # 3. Controller crash recovery distinguishes queued (retryable) vs running (uncertain).
    t_queued = await admit(q, actor, target, "crash-queued-key", ms=15_000)
    t_running = await admit(q, actor, target, "crash-running-key", ms=15_000)
    await q.claim(t_running, actor, payload(15_000), IMAGE)

    expired_now = max(t_queued.envelope.expires_at, t_running.envelope.expires_at) + 5
    metrics_before = await q.job_health_metrics(own, now=expired_now)
    assert metrics_before["stuck"] == 2

    report = await q.recover_stuck_jobs(own, now=expired_now)
    assert set(report["recovered_job_ids"]) == {
        t_queued.envelope.job_id,
        t_running.envelope.job_id,
    }
    metrics_after = await q.job_health_metrics(own, now=expired_now)
    assert metrics_after["stuck"] == 0
    assert metrics_after["dead_letter_pending"] == 2
    assert metrics_after["dead_letter_quarantined"] >= 1

    # Running job cannot be auto-retried.
    with pytest.raises(JobRejected, match="UNCERTAIN_JOB_CANNOT_AUTO_RETRY") as unc_exc:
        await q.reconcile_dead_letter_job(
            workspace_id=own,
            job_id=t_running.envelope.job_id,
            actor_id="operator-1",
            actor_role="researcher",
            decision="authorize_retry",
            notes="must fail closed on running",
            idempotency_key="rec-run-retry",
        )
    assert unc_exc.value.status == 409

    # Queued job CAN authorize_retry and keeps reserved_ms + rejects foreign workspace.
    queued_rec = await q.reconcile_dead_letter_job(
        workspace_id=own,
        job_id=t_queued.envelope.job_id,
        actor_id="operator-1",
        actor_role="researcher",
        decision="authorize_retry",
        notes="SECRET_QUEUE_NOTES never reached running state",
        idempotency_key="rec-queued-1",
    )
    assert queued_rec.reconciliation_status == "retry_authorized"
    assert queued_rec.reserved_ms == 15_000
    assert queued_rec.reservation_retained is True
    assert queued_rec.replayed is False

    queued_replay = await q.reconcile_dead_letter_job(
        workspace_id=own,
        job_id=t_queued.envelope.job_id,
        actor_id="operator-1",
        actor_role="researcher",
        decision="authorize_retry",
        notes="SECRET_QUEUE_NOTES never reached running state",
        idempotency_key="rec-queued-1",
    )
    assert queued_replay.replayed is True
    assert queued_replay.authorized_retry_key == queued_rec.authorized_retry_key

    with pytest.raises(JobRejected, match="RECONCILIATION_IDEMPOTENCY_CONFLICT"):
        await q.reconcile_dead_letter_job(
            workspace_id=own,
            job_id=t_queued.envelope.job_id,
            actor_id="operator-1",
            actor_role="researcher",
            decision="quarantine",
            notes="altered intent",
            idempotency_key="rec-queued-1",
        )

    # Foreign workspace cannot inspect or reconcile.
    foreign = "ws_" + "f" * 48
    assert await q.list_dead_letter_jobs(foreign) == []
    with pytest.raises(JobRejected, match="DEAD_LETTER_JOB_NOT_FOUND") as iso_exc:
        await q.reconcile_dead_letter_job(
            workspace_id=foreign,
            job_id=t_running.envelope.job_id,
            actor_id="attacker",
            actor_role="researcher",
            decision="quarantine",
            notes="cross workspace attempt",
            idempotency_key="rec-foreign-1",
        )
    assert iso_exc.value.status == 404

    # Reconcile running job against an already-committed CheckResult receipt.
    from app.symbol_contracts import contract_review_state_hash

    check_res = CheckResult(
        check_id="placeholder",
        workspace_id=own,
        target_uuid=target,
        outcome="supported",
        vector=VerificationVector(
            parse="supported",
            type="well_typed",
            domain="discharged",
            symbolic="supported",
        ),
        duration_ms=12,
        created_at=datetime.now(UTC),
        job_id=t_running.envelope.job_id,
        execution_image=IMAGE,
        execution_profile=t_running.envelope.execution_profile,
        contract_review_hash=contract_review_state_hash([]),
        schema_version="check-result.v3",
    )
    persisted_check = await q.store.append_check_result(
        workspace_id=own,
        target_uuid=target,
        idempotency_key="committed-before-controller-crash",
        result=check_res,
    )
    rec_receipt = await q.reconcile_dead_letter_job(
        workspace_id=own,
        job_id=t_running.envelope.job_id,
        actor_id="operator-1",
        actor_role="researcher",
        decision="reconcile_receipt",
        notes="matched committed check receipt",
        idempotency_key="rec-run-receipt-1",
    )
    assert rec_receipt.receipt_found is True
    assert rec_receipt.reconciled_receipt_id == persisted_check.result.check_id
    assert rec_receipt.reconciliation_status == "reconciled_receipt"
    assert rec_receipt.reserved_ms == 15_000
    assert rec_receipt.reservation_retained is True

    # Reservations remain consumed across all dead-letter jobs (total reserved_ms = 50_000).
    rows, _, _ = await q.store.driver.execute_query(
        "MATCH (j:ResearchJob {workspace:$workspace}) "
        "RETURN coalesce(sum(j.reserved_ms), 0) AS total_ms",
        workspace=own,
        database_=q.store.database,
    )
    assert int(rows[0]["total_ms"]) == 50_000
