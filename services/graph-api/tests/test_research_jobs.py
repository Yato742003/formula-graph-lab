"""Adversarial queue envelope boundary; database races are covered by integration."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.execution_profile import ExecutionProfile, capture_execution_profile
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
    validate_symbolic_check_payload,
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
    other_image = "sha256:" + "f" * 64
    envelope = JobEnvelope(
        schema_version="research-job.v2",
        job_id=RUN_ID, workspace_id=candidate.workspace_id,
        target_uuid=candidate.candidate_id, actor_id="experiment-worker",
        operation="research_case:run", request_hash=digest(payload),
        source_hash=digest([candidate.candidate_id, candidate.content_hash, binding.binding_hash]),
        payload_hash=digest(payload), image_id=other_image,
        execution_profile=capture_execution_profile(other_image, "research_case", 5_000),
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
    profile = capture_execution_profile(IMAGE, "symbolic", 2000)
    return JobEnvelope(
        schema_version="research-job.v2",
        job_id="a" * 36, workspace_id="w1", target_uuid="equation-1", actor_id="v1",
        request_hash="b" * 64, source_hash=digest("1+1"), payload_hash=digest(PAYLOAD),
        image_id=IMAGE, execution_profile=profile, issued_at=1000, expires_at=1120,
        reserved_ms=2000,
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
    profile = capture_execution_profile(IMAGE, "numerical", 4000)
    numerical_envelope = JobEnvelope(
        schema_version="research-job.v2",
        job_id="b" * 36,
        workspace_id="w1",
        target_uuid=NUMERICAL_PAYLOAD["candidate_id"],
        actor_id="v1",
        operation="numerical_fixture:run",
        request_hash=digest([NUMERICAL_PAYLOAD, IMAGE, 4000, profile.model_dump(mode="json")]),
        source_hash=digest([
            NUMERICAL_PAYLOAD["candidate_id"], NUMERICAL_PAYLOAD["candidate_hash"],
        ]),
        payload_hash=digest(NUMERICAL_PAYLOAD),
        image_id=IMAGE,
        execution_profile=profile,
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


def test_execution_profile_tampering_breaks_hmac_signature():
    original = envelope()
    mac = signature(original, KEY)
    assert original.execution_profile is not None
    for field, value in {
        "ram_bytes": 512 * 1024**2,
        "cpu_cores": 2,
        "timeout_cap_ms": 20_000,
        "invocation_limit_ms": 1500,
    }.items():
        mutated_profile = original.execution_profile.model_copy(update={field: value})
        tampered = original.model_copy(update={"execution_profile": mutated_profile})
        with pytest.raises(JobRejected, match="INVALID_JOB_SIGNATURE"):
            verify(tampered, mac=mac)
    other_image = "sha256:" + "f" * 64
    tampered_image = original.model_copy(
        update={
            "image_id": other_image,
            "execution_profile": original.execution_profile.model_copy(
                update={"image_id": other_image}
            ),
        }
    )
    with pytest.raises(JobRejected, match="INVALID_JOB_SIGNATURE"):
        verify(tampered_image, mac=mac, image=other_image)


def test_verify_envelope_rejects_live_execution_profile_drift(monkeypatch):
    original = envelope()
    mac = signature(original, KEY)
    for env_key, env_val in (
        ("FGL_WORKER_RAM_LIMIT_MB", "512"),
        ("FGL_WORKER_CPU_CORES", "2"),
        ("FGL_WORKER_TIMEOUT_MS", "20000"),
    ):
        monkeypatch.setenv(env_key, env_val)
        with pytest.raises(JobRejected, match="EXECUTION_PROFILE_DRIFT") as exc_info:
            verify(original, mac=mac)
        assert exc_info.value.status == 409
        assert isinstance(exc_info.value, ValueError)
        monkeypatch.delenv(env_key, raising=False)


def test_verify_envelope_checks_invocation_limit_and_rejects_legacy_v1():
    original = envelope()
    assert original.execution_profile is not None
    bad_profile = original.execution_profile.model_copy(update={"invocation_limit_ms": 1234})
    bad_envelope = original.model_copy(update={"execution_profile": bad_profile})
    with pytest.raises(JobRejected, match="JOB_INPUT_MISMATCH"):
        verify(bad_envelope, mac=signature(bad_envelope, KEY))

    legacy_v1 = JobEnvelope(
        schema_version="research-job.v1",
        job_id="a" * 36,
        workspace_id="w1",
        target_uuid="equation-1",
        actor_id="v1",
        request_hash="b" * 64,
        source_hash=digest("1+1"),
        payload_hash=digest(PAYLOAD),
        image_id=IMAGE,
        issued_at=1000,
        expires_at=1120,
        reserved_ms=2000,
    )
    legacy_json = legacy_v1.model_dump_json()
    assert "execution_profile" not in json.loads(legacy_json)
    assert JobEnvelope.model_validate_json(legacy_json) == legacy_v1
    with pytest.raises(JobRejected, match="LEGACY_JOB_PROFILE_REQUIRED"):
        verify(legacy_v1, mac=signature(legacy_v1, KEY))


@pytest.mark.asyncio
async def test_admit_rejects_drifted_profile_and_legacy_research_case_binding(monkeypatch):
    from app.research_case import make_implementation_binding
    from tests.test_research_case import NOW, _candidates

    drifted_profile = capture_execution_profile(IMAGE, "symbolic", 2000)
    monkeypatch.setenv("FGL_WORKER_RAM_LIMIT_MB", "512")
    queue = ResearchQueue(SimpleNamespace(), KEY)
    with pytest.raises(JobRejected, match="EXECUTION_PROFILE_DRIFT") as exc_info:
        await queue.admit(
            ACTOR,
            workspace_id="w1",
            target_uuid="eq-1",
            idempotency_key="k1",
            request_hash=digest(PAYLOAD),
            payload=PAYLOAD,
            image=IMAGE,
            execution_profile=drifted_profile,
        )
    assert exc_info.value.status == 409
    monkeypatch.delenv("FGL_WORKER_RAM_LIMIT_MB", raising=False)

    spec, parent, candidate = _candidates()
    exp_actor = WorkerPrincipal(
        "experiment-worker", "experiment", frozenset({candidate.workspace_id})
    )
    legacy_binding = make_implementation_binding(
        candidate, spec, parent_candidate=parent, execution_image=IMAGE, now=NOW, legacy=True
    )
    with pytest.raises(JobRejected, match="LEGACY_BINDING_PROFILE_REQUIRED"):
        await queue.admit_research_case(
            exp_actor,
            workspace_id=candidate.workspace_id,
            candidate_id=candidate.candidate_id,
            idempotency_key="legacy-bind-1",
            binding=legacy_binding,
            reserved_ms=18_000,
            evaluation_role="search",
        )


@pytest.mark.asyncio
async def test_research_case_retry_legacy_v1_replay_vs_incomplete_rejection(monkeypatch):
    from app.research_case import (
        ResearchCaseReceiptResponse,
        make_implementation_binding,
        run_registered_research_case,
    )
    from app.research_case_worker import evaluate
    from tests.test_research_case import NOW, _candidates

    spec, parent, candidate = _candidates()
    actor = WorkerPrincipal("experiment-worker", "experiment", frozenset({candidate.workspace_id}))
    legacy_binding = make_implementation_binding(
        candidate, spec, parent_candidate=parent, execution_image=IMAGE, now=NOW, legacy=True
    )
    legacy_payload = research_case_job_payload(legacy_binding, "search", None)
    from uuid import NAMESPACE_URL, uuid5

    job_id = str(uuid5(NAMESPACE_URL, json.dumps(
        [candidate.workspace_id, "research_case:run", candidate.candidate_id, "legacy-retry-key"]
    )))
    legacy_result = run_registered_research_case(
        legacy_binding,
        candidate,
        spec,
        parent_candidate=parent,
        actor_id=actor.identity,
        run_id=job_id,
        worker_runner=lambda payload, **_kwargs: evaluate(payload),
        now=NOW,
        evaluation_role="search",
        historical_replay=True,
    )
    legacy_envelope = JobEnvelope(
        schema_version="research-job.v1",
        job_id=job_id,
        workspace_id=candidate.workspace_id,
        target_uuid=candidate.candidate_id,
        actor_id=actor.identity,
        operation="research_case:run",
        request_hash=digest([legacy_payload, IMAGE, 18_000]),
        source_hash=digest([
            candidate.candidate_id, candidate.content_hash, legacy_binding.binding_hash,
        ]),
        payload_hash=digest(legacy_payload),
        image_id=IMAGE,
        issued_at=1000,
        expires_at=1120,
        reserved_ms=18_000,
    )
    legacy_mac = signature(legacy_envelope, KEY)
    saved_json = ResearchCaseReceiptResponse(
        binding=legacy_binding, result=legacy_result, replayed=False
    ).model_dump_json()

    driver = SimpleNamespace(execute_query=AsyncMock(return_value=(
        [{
            "envelope": legacy_envelope.model_dump_json(),
            "mac": legacy_mac,
            "input": json.dumps(legacy_payload, sort_keys=True),
            "result": saved_json,
            "result_mac": ResearchQueue(SimpleNamespace(), KEY)._result_mac(job_id, saved_json),
        }],
        None,
        None,
    )))
    queue = ResearchQueue(SimpleNamespace(driver=driver, database="neo4j"), KEY)
    payload_out, replayed = await queue.research_case_retry(
        actor, candidate.workspace_id, candidate.candidate_id, "legacy-retry-key", "search", None,
    )
    assert payload_out == legacy_payload
    assert replayed is not None
    assert replayed.replayed is True
    assert replayed.result.execution_profile is None

    # Incomplete legacy v1 job must be rejected instead of manufacturing a new binding.
    driver.execute_query.return_value = (
        [{
            "envelope": legacy_envelope.model_dump_json(),
            "mac": legacy_mac,
            "input": json.dumps(legacy_payload, sort_keys=True),
            "result": None,
            "result_mac": None,
        }],
        None,
        None,
    )
    with pytest.raises(JobRejected, match="LEGACY_JOB_REQUIRES_RECONCILIATION"):
        await queue.research_case_retry(
            actor,
            candidate.workspace_id,
            candidate.candidate_id,
            "legacy-retry-key",
            "search",
            None,
        )

    # Incomplete v2 job with env drift must also fail closed with EXECUTION_PROFILE_DRIFT.
    v2_binding = make_implementation_binding(
        candidate, spec, parent_candidate=parent, execution_image=IMAGE, now=NOW
    )
    v2_payload = research_case_job_payload(v2_binding, "search", None)
    assert v2_binding.execution_profile is not None
    v2_envelope = JobEnvelope(
        schema_version="research-job.v2",
        job_id=job_id,
        workspace_id=candidate.workspace_id,
        target_uuid=candidate.candidate_id,
        actor_id=actor.identity,
        operation="research_case:run",
        request_hash=digest([
            v2_payload,
            IMAGE,
            18_000,
            v2_binding.execution_profile.model_dump(mode="json"),
        ]),
        source_hash=digest([
            candidate.candidate_id, candidate.content_hash, v2_binding.binding_hash,
        ]),
        payload_hash=digest(v2_payload),
        image_id=IMAGE,
        execution_profile=v2_binding.execution_profile,
        issued_at=1000,
        expires_at=1120,
        reserved_ms=18_000,
    )
    driver.execute_query.return_value = (
        [{
            "envelope": v2_envelope.model_dump_json(),
            "mac": signature(v2_envelope, KEY),
            "input": json.dumps(v2_payload, sort_keys=True),
            "result": None,
            "result_mac": None,
        }],
        None,
        None,
    )
    monkeypatch.setenv("FGL_WORKER_RAM_LIMIT_MB", "512")
    with pytest.raises(JobRejected, match="EXECUTION_PROFILE_DRIFT"):
        await queue.research_case_retry(
            actor,
            candidate.workspace_id,
            candidate.candidate_id,
            "legacy-retry-key",
            "search",
            None,
        )


@pytest.mark.asyncio
async def test_finish_rejects_mismatched_execution_profile_before_storage(monkeypatch):
    from app.numerical_verification import (
        build_numerical_suite_input,
        make_numerical_fixture_receipt,
        run_candidate_numerical_fixture,
    )
    from app.numerical_worker import _evaluate
    from app.research_case import make_implementation_binding, run_registered_research_case
    from app.research_case_worker import evaluate
    from tests.test_research_case import NOW, RUN_ID, _candidates

    spec, parent, candidate = _candidates()
    queue = ResearchQueue(SimpleNamespace(), KEY)

    # 1. finish_research_case mismatch
    binding_256 = make_implementation_binding(
        candidate, spec, parent_candidate=parent, execution_image=IMAGE, now=NOW
    )
    result_256 = run_registered_research_case(
        binding_256, candidate, spec, parent_candidate=parent,
        actor_id="experiment-worker", run_id=RUN_ID,
        worker_runner=lambda payload, **_kwargs: evaluate(payload), now=NOW,
    )
    assert binding_256.execution_profile is not None
    # Build a distinct valid ExecutionProfile for envelope (e.g. different cpu_cores in envelope)
    different_profile = ExecutionProfile(
        image_id=IMAGE,
        worker_kind="research_case",
        ram_bytes=256 * 1024**2,
        cpu_cores=2,
        timeout_cap_ms=10_000,
        invocation_limit_ms=5_000,
    )
    rc_payload = research_case_job_payload(binding_256)
    rc_envelope = JobEnvelope(
        schema_version="research-job.v2",
        job_id=RUN_ID,
        workspace_id=candidate.workspace_id,
        target_uuid=candidate.candidate_id,
        actor_id="experiment-worker",
        operation="research_case:run",
        request_hash=digest(rc_payload),
        source_hash=digest([
            candidate.candidate_id, candidate.content_hash, binding_256.binding_hash,
        ]),
        payload_hash=digest(rc_payload),
        image_id=IMAGE,
        execution_profile=different_profile,
        issued_at=1000,
        expires_at=1120,
        reserved_ms=30_000,
    )
    with pytest.raises(JobRejected, match="JOB_RESULT_MISMATCH"):
        await queue.finish_research_case(
            JobTicket(rc_envelope, signature(rc_envelope, KEY)),
            research_store=None,
            idempotency_key="rc-mismatch",
            binding=binding_256,
            result=result_256,
        )

    # 2. finish_numerical_fixture mismatch
    monkeypatch.setattr("app.numerical_verification.configured_image", lambda: IMAGE)
    monkeypatch.setattr(
        "app.numerical_verification.run_numerical_sandbox",
        lambda payload, **_kwargs: _evaluate(payload),
    )
    raw_num = run_candidate_numerical_fixture(
        candidate, spec, seed=spec.definition.seeds[0], parent_candidate=parent
    )
    num_receipt = make_numerical_fixture_receipt(
        raw_num, workspace_id=candidate.workspace_id, actor_id="v1", run_id=RUN_ID
    )
    num_payload = build_numerical_suite_input(
        candidate, spec, seed=spec.definition.seeds[0], parent_candidate=parent
    ) | {"candidate_id": candidate.candidate_id}
    monkeypatch.setenv("FGL_WORKER_RAM_LIMIT_MB", "512")
    num_profile_512 = capture_execution_profile(IMAGE, "numerical", 30_000)
    monkeypatch.delenv("FGL_WORKER_RAM_LIMIT_MB", raising=False)
    num_envelope = JobEnvelope(
        schema_version="research-job.v2",
        job_id=RUN_ID,
        workspace_id=candidate.workspace_id,
        target_uuid=candidate.candidate_id,
        actor_id="v1",
        operation="numerical_fixture:run",
        request_hash=digest([num_payload, IMAGE, 30_000, num_profile_512.model_dump(mode="json")]),
        source_hash=digest([candidate.candidate_id, candidate.content_hash]),
        payload_hash=digest(num_payload),
        image_id=IMAGE,
        execution_profile=num_profile_512,
        issued_at=1000,
        expires_at=1120,
        reserved_ms=30_000,
    )
    with pytest.raises(JobRejected, match="JOB_RESULT_MISMATCH"):
        await queue.finish_numerical_fixture(
            JobTicket(num_envelope, signature(num_envelope, KEY)),
            research_store=None,
            idempotency_key="num-mismatch",
            result=num_receipt,
        )


def test_validate_symbolic_check_payload_supports_4_and_6_keys():
    validate_symbolic_check_payload(PAYLOAD)
    validate_symbolic_check_payload({
        **PAYLOAD,
        "contract_review_ids": ("rev-1",),
        "contract_review_hash": "a" * 64,
    })
    for bad in (
        {**PAYLOAD, "contract_review_ids": ("rev-1",)},
        {**PAYLOAD, "contract_review_hash": "a" * 64},
        {**PAYLOAD, "contract_review_ids": (), "contract_review_hash": "not-hex"},
        {**PAYLOAD, "fitness": 1},
    ):
        with pytest.raises(JobRejected, match="INVALID_JOB_INPUT"):
            validate_symbolic_check_payload(bad)

