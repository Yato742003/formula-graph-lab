"""Adversarial queue envelope boundary; database races are covered by integration."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.research_jobs import (
    JobEnvelope,
    JobRejected,
    JobTicket,
    ResearchQueue,
    digest,
    research_case_job_payload,
    signature,
    validate_numerical_fixture_payload,
    validate_research_case_payload,
    verify_envelope,
)
from app.worker_auth import WorkerPrincipal

KEY = b"q" * 40
IMAGE = "sha256:" + "a" * 64
PAYLOAD = {"formula_a": "1+1", "formula_b": "2", "format": "latex", "timeout_ms": 2000}
NUMERICAL_PAYLOAD = {
    "candidate_id": "cand_" + "d" * 32,
    "candidate_hash": "e" * 64,
    "suite_version": "feature-kernel-fixture.v1",
    "seed": 7,
    "dtype": "float32",
    "lambda": 0.25,
    "left_rank": 16,
    "right_rank": 24,
}
ACTOR = WorkerPrincipal("v1", "verifier", frozenset({"w1"}))


@pytest.mark.asyncio
async def test_experiment_completion_rejects_a_different_reserved_image_before_storage():
    from app.research_case import make_implementation_binding, run_registered_research_case
    from app.research_case_worker import evaluate
    from tests.test_research_case import NOW, RUN_ID, _candidates

    spec, parent, candidate = _candidates()
    binding = make_implementation_binding(
        candidate, spec, parent_candidate=parent, execution_image=IMAGE, now=NOW
    )
    result = run_registered_research_case(
        binding, candidate, spec, parent_candidate=parent,
        actor_id="experiment-worker", run_id=RUN_ID,
        worker_runner=lambda payload, **_kwargs: evaluate(payload), now=NOW,
    )
    payload = research_case_job_payload(binding)
    envelope = JobEnvelope(
        job_id=RUN_ID, workspace_id=candidate.workspace_id,
        target_uuid=candidate.candidate_id, actor_id="experiment-worker",
        operation="research_case:run", request_hash=digest(payload),
        source_hash=digest([candidate.candidate_id, candidate.content_hash, binding.binding_hash]),
        payload_hash=digest(payload), image_id="sha256:" + "f" * 64,
        issued_at=1000, expires_at=1120, reserved_ms=30_000,
    )
    ticket = JobTicket(envelope, signature(envelope, KEY))
    # No driver: a storage access before rejecting the mismatch would fail this test.
    queue = ResearchQueue(SimpleNamespace(), KEY)
    with pytest.raises(JobRejected, match="JOB_RESULT_MISMATCH"):
        await queue.finish_research_case(
            ticket, research_store=None, idempotency_key="trial",
            binding=binding, result=result,
        )
    for timestamp in ("", "not-a-date", "2026-09-29T10:00:00"):
        with pytest.raises(JobRejected, match="INVALID_RESEARCH_CASE_INPUT"):
            validate_research_case_payload(payload | {"binding_created_at": timestamp})


def envelope():
    return JobEnvelope(
        job_id="a" * 36, workspace_id="w1", target_uuid="equation-1", actor_id="v1",
        request_hash="b" * 64, source_hash=digest("1+1"), payload_hash=digest(PAYLOAD),
        image_id=IMAGE, issued_at=1000, expires_at=1120, reserved_ms=2000,
    )


def verify(e=None, *, mac=None, actor=ACTOR, payload=None, image=IMAGE, now=1001):
    e = e or envelope()
    return verify_envelope(e.model_dump_json(), mac or signature(e, KEY), KEY, actor,
                           PAYLOAD if payload is None else payload, image, now=now)


def test_authenticated_envelope_and_tampering():
    original = envelope()
    assert verify() == original
    for field, value in {"workspace_id": "w2", "actor_id": "proposer", "target_uuid": "fake",
                         "request_hash": "c" * 64, "reserved_ms": 100,
                         "expires_at": 999999}.items():
        with pytest.raises(JobRejected, match="SIGNATURE"):
            verify(original.model_copy(update={field: value}), mac=signature(original, KEY))
    raw = json.dumps({**original.model_dump(), "fitness": 1, "approved": True})
    with pytest.raises(JobRejected, match="ENVELOPE"):
        verify_envelope(raw, signature(original, KEY), KEY, ACTOR, PAYLOAD, IMAGE)


@pytest.mark.parametrize("actor", [
    WorkerPrincipal("v1", "proposer", frozenset({"w1"})),
    WorkerPrincipal("v1", "verifier", frozenset({"w2"})),
    WorkerPrincipal("v2", "verifier", frozenset({"w1"})),
])
def test_delivery_rechecks_role_workspace_and_worker_identity(actor):
    with pytest.raises((HTTPException, JobRejected)):
        verify(actor=actor)


@pytest.mark.parametrize("extra", [
    {"timeout_ms": 3000}, {"formula_b": "__import__('os').system('id')"},
    {"benchmark": "easy"}, {"tolerance": 1}, {"html": "<script>approve()</script>"},
])
def test_delivery_rejects_changed_inputs(extra):
    with pytest.raises(JobRejected, match="INPUT"):
        verify(payload={**PAYLOAD, **extra})


def test_expiry_clock_skew_and_image_changes():
    for now in (999, 1120, 2000):
        with pytest.raises(JobRejected, match="EXPIRED"):
            verify(now=now)
    with pytest.raises(JobRejected, match="INPUT"):
        verify(image="sha256:" + "f" * 64)


def test_signed_numerical_fixture_job_binds_candidate_seed_and_budget():
    numerical_envelope = JobEnvelope(
        job_id="b" * 36,
        workspace_id="w1",
        target_uuid=NUMERICAL_PAYLOAD["candidate_id"],
        actor_id="v1",
        operation="numerical_fixture:run",
        request_hash=digest([NUMERICAL_PAYLOAD, IMAGE, 4000]),
        source_hash=digest([
            NUMERICAL_PAYLOAD["candidate_id"], NUMERICAL_PAYLOAD["candidate_hash"],
        ]),
        payload_hash=digest(NUMERICAL_PAYLOAD),
        image_id=IMAGE,
        issued_at=1000,
        expires_at=1120,
        reserved_ms=4000,
    )
    assert verify_envelope(
        numerical_envelope.model_dump_json(),
        signature(numerical_envelope, KEY),
        KEY,
        ACTOR,
        NUMERICAL_PAYLOAD,
        IMAGE,
        now=1001,
    ) == numerical_envelope


@pytest.mark.parametrize("change", [
    {"lambda": 1.5}, {"seed": True}, {"left_rank": 0},
    {"candidate_id": "cand_forged"}, {"tolerance": 1e9},
    {"code": "__import__('os').system('id')"},
])
def test_numerical_fixture_delivery_rejects_unbounded_or_extra_fields(change):
    payload = NUMERICAL_PAYLOAD | change
    with pytest.raises(JobRejected):
        validate_numerical_fixture_payload(payload)


@pytest.mark.asyncio
async def test_admission_rejects_missing_or_changed_source_before_reserving_quota():
    source = AsyncMock(side_effect=ValueError("Equation not found"))
    queue = ResearchQueue(SimpleNamespace(equation_source=source), KEY)
    args = dict(workspace_id="w1", target_uuid="fake", idempotency_key="retry",
                request_hash=digest(PAYLOAD), payload=PAYLOAD, image=IMAGE)
    with pytest.raises(ValueError, match="not found"):
        await queue.admit(ACTOR, **args)
    source.assert_awaited_once_with(workspace_id="w1", equation_uuid="fake")
    source.side_effect = None
    source.return_value = {"latex": "different source"}
    with pytest.raises(JobRejected, match="SOURCE_MISMATCH"):
        await queue.admit(ACTOR, **args)
    for extra in ({"fitness": 1}, {"tolerance": 1}, {"timeout_ms": True}, {"format": "python"}):
        with pytest.raises(JobRejected, match="INVALID_JOB_INPUT"):
            await queue.admit(ACTOR, **{**args, "payload": {**PAYLOAD, **extra}})


@pytest.mark.asyncio
async def test_job_recovery_fallback_without_driver():
    queue = ResearchQueue(SimpleNamespace(driver=None), KEY)
    for operation in (queue.recover_stuck_jobs, queue.job_health_metrics):
        with pytest.raises(JobRejected, match="QUEUE_UNAVAILABLE"):
            await operation("ws_alpha")
        with pytest.raises(JobRejected, match="WORKSPACE_REQUIRED"):
            await operation("")


@pytest.mark.asyncio
async def test_recover_stuck_jobs_execution_and_audit(caplog):
    import logging

    queue = ResearchQueue(SimpleNamespace(driver=None), KEY)

    class MockCursor:
        def __aiter__(self):
            return self

        async def __anext__(self):
            if not hasattr(self, "_yielded"):
                self._yielded = True
                return {"id": "stuck_job_1", "workspace": "ws_alpha", "state": "running"}
            raise StopAsyncIteration

        async def consume(self):
            return None

    class MockTx:
        async def run(self, query, **kwargs):
            if "ResearchJob" in query:
                assert "workspace:$workspace" in query
                assert kwargs["workspace"] == "ws_alpha"
            return MockCursor()

    with caplog.at_level(logging.WARNING, logger="fgl.audit"):
        report = await queue._recover_stuck_jobs(MockTx(), "ws_alpha", now=5000)

    assert report["recovered_count"] == 1
    assert report["recovered_job_ids"] == ["stuck_job_1"]
    audit_msgs = [r.message for r in caplog.records if r.message.startswith("AUDIT: ")]
    assert not audit_msgs  # A transaction may retry/rollback; audit only after commit.

