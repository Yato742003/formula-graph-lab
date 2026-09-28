"""Adversarial queue envelope boundary; database races are covered by integration."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.research_jobs import (
    JobEnvelope,
    JobRejected,
    ResearchQueue,
    digest,
    signature,
    validate_numerical_fixture_payload,
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
