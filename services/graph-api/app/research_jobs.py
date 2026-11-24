"""H6: authenticated durable admission on the existing evidence database."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, Field, model_serializer, model_validator

from app.auth import HUMAN_ROLES
from app.episodes import workspace_group_id
from app.execution_profile import (
    ExecutionProfile,
    WorkerKind,
    capture_execution_profile,
    validate_live_profile,
)
from app.numerical_verification import (
    NumericalFixtureReceipt,
    NumericalFixtureReceiptResponse,
    build_numerical_suite_input,
)
from app.observability import emit_event
from app.problem_spec import ProblemSpecSnapshot, definition_hash
from app.research_case import (
    PER_TRIAL_TIMEOUT_MS,
    ImplementationBindingReceipt,
    ResearchCaseReceipt,
    ResearchCaseReceiptResponse,
    make_implementation_binding,
    phase_budget_ms,
    validate_protocol_profile,
)
from app.research_compiler import CompiledCandidate, _verify_candidate_identity
from app.sandbox import IMAGE_ID
from app.security import log_audit_event
from app.verification import CheckResult
from app.worker_auth import WorkerPrincipal, configured_credentials


class JobRejected(ValueError):
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.status = status


DeadLetterDecision = Literal["quarantine", "authorize_retry", "reconcile_receipt"]
DeadLetterPriorState = Literal["queued", "running", "unknown"]
DeadLetterReconcileStatus = Literal[
    "pending", "quarantined", "retry_authorized", "reconciled_receipt"
]


class DeadLetterReconcileRequest(BaseModel):
    decision: DeadLetterDecision
    notes: str = Field(min_length=3, max_length=1000)
    model_config = {"extra": "forbid", "frozen": True}


class DeadLetterReconcileResponse(BaseModel):
    reconciliation_id: str
    job_id: str
    workspace_id: str
    operation: str
    target_uuid: str
    prior_state: DeadLetterPriorState
    decision: DeadLetterDecision
    reconciliation_status: Literal["quarantined", "retry_authorized", "reconciled_receipt"]
    reserved_ms: int = Field(ge=0)
    reservation_retained: Literal[True] = True
    receipt_found: bool = False
    reconciled_receipt_id: str | None = None
    authorized_retry_key: str | None = None
    reconciled_by: str
    reconciled_at: str
    replayed: bool = False
    model_config = {"extra": "forbid", "frozen": True}


def _envelope_meta(raw_envelope: object) -> tuple[str, str]:
    if isinstance(raw_envelope, str) and raw_envelope:
        try:
            parsed = json.loads(raw_envelope)
            if isinstance(parsed, dict):
                op = parsed.get("operation")
                target = parsed.get("target_uuid")
                return (
                    op if isinstance(op, str) and op else "unknown",
                    target if isinstance(target, str) and target else "unknown",
                )
        except (ValueError, TypeError):
            pass
    return ("unknown", "unknown")


OPERATION_WORKER_KIND: dict[str, WorkerKind] = {
    "checks:run": "symbolic",
    "numerical_fixture:run": "numerical",
    "research_case:run": "research_case",
}


class JobEnvelope(BaseModel):
    schema_version: Literal["research-job.v1", "research-job.v2"] = "research-job.v1"
    job_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    workspace_id: str = Field(min_length=1, max_length=200)
    target_uuid: str = Field(min_length=1, max_length=200)
    actor_id: str = Field(min_length=1, max_length=200)
    operation: Literal[
        "checks:run", "numerical_fixture:run", "research_case:run"
    ] = "checks:run"
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    image_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    execution_profile: ExecutionProfile | None = None
    issued_at: int = Field(ge=0)
    expires_at: int = Field(ge=0)
    reserved_ms: int = Field(ge=10, le=30_000)
    model_config = {"extra": "forbid", "frozen": True, "strict": True}

    @model_validator(mode="after")
    def validate_execution_profile(self) -> JobEnvelope:
        if (self.schema_version == "research-job.v2") != (self.execution_profile is not None):
            raise ValueError("Version 2 job envelopes require a captured execution profile.")
        if self.execution_profile is not None and (
            self.execution_profile.image_id != self.image_id
            or self.execution_profile.worker_kind != OPERATION_WORKER_KIND[self.operation]
        ):
            raise ValueError("Job execution profile does not match its operation or image.")
        return self

    @model_serializer(mode="wrap")
    def serialize_envelope(self, handler):
        payload = handler(self)
        if self.schema_version == "research-job.v1":
            payload.pop("execution_profile", None)
        return payload


def digest(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def configured_queue_key() -> bytes:
    key = os.getenv("FGL_RESEARCH_QUEUE_SECRET", "")
    if not key.isascii() or not 32 <= len(key) <= 256 or key == os.getenv("SERVICE_TOKEN"):
        raise ValueError("Independent research queue secret required.")
    if any(hmac.compare_digest(key, c.token.get_secret_value()) for c in configured_credentials()):
        raise ValueError("Queue and worker credentials must differ.")
    return key.encode()


def signature(envelope: JobEnvelope, key: bytes) -> str:
    return hmac.new(key, envelope.model_dump_json().encode(), hashlib.sha256).hexdigest()


def validate_symbolic_check_payload(payload: dict[str, object]) -> None:
    base_keys = {"formula_a", "formula_b", "format", "timeout_ms"}
    review_keys = {"contract_review_ids", "contract_review_hash"}
    if review_keys & payload.keys():
        review_ids = payload.get("contract_review_ids")
        review_hash = payload.get("contract_review_hash")
        if (
            not review_keys <= payload.keys()
            or not isinstance(review_ids, (list, tuple))
            or len(review_ids) > 500
            or any(not isinstance(item, str) or not 1 <= len(item) <= 200 for item in review_ids)
            or not isinstance(review_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", review_hash)
        ):
            raise JobRejected("INVALID_JOB_INPUT", 422)
    if (
        set(payload) - review_keys != base_keys
        or payload.get("format") != "latex"
        or any(
            not isinstance(payload.get(k), str) or not 1 <= len(payload[k]) <= 20_000
            for k in ("formula_a", "formula_b")
        )
        or type(payload.get("timeout_ms")) is not int
        or not 10 <= payload["timeout_ms"] <= 30_000
    ):
        raise JobRejected("INVALID_JOB_INPUT", 422)


def validate_numerical_fixture_payload(payload: dict[str, object]) -> None:
    if (
        set(payload) != {
            "candidate_id", "candidate_hash", "suite_version", "seed", "dtype",
            "lambda", "left_rank", "right_rank",
        }
        or not isinstance(payload.get("candidate_id"), str)
        or len(payload["candidate_id"]) != 37
        or not payload["candidate_id"].startswith("cand_")
        or any(ch not in "0123456789abcdef" for ch in payload["candidate_id"][5:])
        or not isinstance(payload.get("candidate_hash"), str)
        or len(payload["candidate_hash"]) != 64
        or any(ch not in "0123456789abcdef" for ch in payload["candidate_hash"])
        or payload.get("suite_version") != "feature-kernel-fixture.v1"
        or type(payload.get("seed")) is not int
        or not 0 <= payload["seed"] <= 2**31 - 1
        or not isinstance(payload.get("dtype"), str)
        or payload["dtype"] not in {"float32", "float64", "bfloat16"}
        or type(payload.get("lambda")) not in (int, float)
        or not math.isfinite(payload["lambda"])
        or not 0 <= payload["lambda"] <= 1
        or type(payload.get("left_rank")) is not int
        or type(payload.get("right_rank")) is not int
        or not 1 <= payload["left_rank"] <= 256
        or not 1 <= payload["right_rank"] <= 256
    ):
        raise JobRejected("INVALID_NUMERICAL_FIXTURE_INPUT", 422)


def validate_research_case_payload(payload: dict[str, object]) -> None:
    scope_keys = {"evaluation_role", "evolution_id"}
    if scope_keys & payload.keys():
        role, campaign = payload.get("evaluation_role"), payload.get("evolution_id")
        if (not scope_keys <= payload.keys() or role not in {"search", "holdout"}
                or (campaign is not None and (
                    not isinstance(campaign, str) or not re.fullmatch(r"evo_[0-9a-f]{32}", campaign)
                )) or (role == "holdout" and campaign is None)):
            raise JobRejected("INVALID_RESEARCH_CASE_SCOPE", 422)
    if (
        set(payload) - scope_keys != {
            "candidate_id", "candidate_hash", "binding_id", "binding_hash", "protocol_hash",
            "binding_created_at",
        }
        or not isinstance(payload.get("candidate_id"), str)
        or not re.fullmatch(r"cand_[0-9a-f]{32}", payload["candidate_id"])
        or not isinstance(payload.get("candidate_hash"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", payload["candidate_hash"])
        or not isinstance(payload.get("binding_id"), str)
        or not re.fullmatch(r"bind_[0-9a-f]{32}", payload["binding_id"])
        or not isinstance(payload.get("binding_hash"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", payload["binding_hash"])
        or not isinstance(payload.get("protocol_hash"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", payload["protocol_hash"])
        or not isinstance(payload.get("binding_created_at"), str)
        or len(payload["binding_created_at"]) > 100
    ):
        raise JobRejected("INVALID_RESEARCH_CASE_INPUT", 422)
    try:
        created_at = datetime.fromisoformat(payload["binding_created_at"].replace("Z", "+00:00"))
        if created_at.utcoffset() is None:
            raise ValueError("Timezone is required.")
    except ValueError as exc:
        raise JobRejected("INVALID_RESEARCH_CASE_INPUT", 422) from exc


def research_case_job_payload(
    binding: ImplementationBindingReceipt, evaluation_role: str = "legacy_full",
    evolution_id: str | None = None,
) -> dict[str, object]:
    payload = {
        "candidate_id": binding.candidate_id,
        "candidate_hash": binding.candidate_hash,
        "binding_id": binding.binding_id,
        "binding_hash": binding.binding_hash,
        "protocol_hash": binding.protocol_hash,
        "binding_created_at": binding.created_at.isoformat().replace("+00:00", "Z"),
    }
    if evaluation_role != "legacy_full":
        payload.update(evaluation_role=evaluation_role, evolution_id=evolution_id)
    return payload


def verify_envelope(
    raw: str, mac: str, key: bytes, actor: WorkerPrincipal,
    payload: dict[str, object], image: str, *, now: int | None = None,
) -> JobEnvelope:
    if len(raw.encode()) > 8192 or not isinstance(mac, str) or not mac.isascii():
        raise JobRejected("INVALID_JOB_ENVELOPE", 422)
    try:
        envelope = JobEnvelope.model_validate_json(raw)
    except ValueError as exc:
        raise JobRejected("INVALID_JOB_ENVELOPE", 422) from exc
    if not hmac.compare_digest(signature(envelope, key), mac):
        raise JobRejected("INVALID_JOB_SIGNATURE", 403)
    if envelope.schema_version != "research-job.v2" or envelope.execution_profile is None:
        raise JobRejected("LEGACY_JOB_PROFILE_REQUIRED", 422)
    permission = (
        "checks:run"
        if envelope.operation == "numerical_fixture:run"
        else "experiments:run"
        if envelope.operation == "research_case:run"
        else envelope.operation
    )
    actor.require(permission, envelope.workspace_id)
    if envelope.actor_id != actor.identity:
        raise JobRejected("JOB_ACTOR_MISMATCH", 403)
    now = int(time.time()) if now is None else now
    if not envelope.issued_at <= now < envelope.expires_at <= envelope.issued_at + 120:
        raise JobRejected("JOB_EXPIRED")
    reservation_matches = (
        envelope.reserved_ms == payload.get("timeout_ms")
        if envelope.operation == "checks:run"
        else 10 <= envelope.reserved_ms <= 30_000
    )
    if (envelope.payload_hash != digest(payload) or envelope.image_id != image
            or not reservation_matches):
        raise JobRejected("JOB_INPUT_MISMATCH", 422)
    try:
        validate_live_profile(
            envelope.execution_profile, image, OPERATION_WORKER_KIND[envelope.operation],
        )
    except ValueError as exc:
        if str(exc) == "EXECUTION_PROFILE_DRIFT":
            raise JobRejected("EXECUTION_PROFILE_DRIFT", 409) from exc
        raise JobRejected("INVALID_JOB_ENVELOPE", 422) from exc
    expected_invocation_ms = (
        PER_TRIAL_TIMEOUT_MS
        if envelope.operation == "research_case:run"
        else min(envelope.reserved_ms, envelope.execution_profile.timeout_cap_ms)
    )
    if envelope.execution_profile.invocation_limit_ms != expected_invocation_ms:
        raise JobRejected("JOB_INPUT_MISMATCH", 422)
    if envelope.operation == "checks:run":
        validate_symbolic_check_payload(payload)
    elif envelope.operation == "numerical_fixture:run":
        validate_numerical_fixture_payload(payload)
        if (
            envelope.target_uuid != payload["candidate_id"]
            or envelope.source_hash != digest([payload["candidate_id"], payload["candidate_hash"]])
        ):
            raise JobRejected("JOB_SOURCE_MISMATCH", 422)
    elif envelope.operation == "research_case:run":
        try:
            validate_protocol_profile(envelope.execution_profile, image)
        except ValueError as exc:
            raise JobRejected("JOB_INPUT_MISMATCH", 422) from exc
        validate_research_case_payload(payload)
        if (
            envelope.target_uuid != payload["candidate_id"]
            or envelope.source_hash
            != digest([
                payload["candidate_id"], payload["candidate_hash"], payload["binding_hash"]
            ])
        ):
            raise JobRejected("JOB_SOURCE_MISMATCH", 422)
    return envelope


@dataclass(frozen=True)
class JobTicket:
    envelope: JobEnvelope
    mac: str
    result: str | None = None


class ResearchQueue:
    # ponytail: one Neo4j admission lock, ceiling: 4 simultaneous jobs,
    # upgrade: partitioned broker when measured admission throughput requires it.
    MAX_WORKSPACE_RUNNING = 2
    MAX_GLOBAL_RUNNING = 4
    MAX_HOURLY_RESERVED_MS = 120_000

    def __init__(self, store, key: bytes):
        self.store = store
        self.key = key

    def _result_mac(self, job_id: str, result: str) -> str:
        return hmac.new(self.key, (job_id + "\n" + result).encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def _emit_job_transition(
        envelope: JobEnvelope,
        *,
        to_state: str,
        replayed: bool,
        from_state: str | None = None,
    ) -> None:
        emit_event(
            "research_job_transition",
            job_id=envelope.job_id,
            workspace_id=envelope.workspace_id,
            operation=envelope.operation,
            target_uuid=envelope.target_uuid,
            from_state=from_state,
            to_state=to_state,
            reserved_ms=envelope.reserved_ms,
            replayed=replayed,
        )

    @staticmethod
    def _emit_dead_letter_alert(
        *,
        code: str,
        workspace_id: str,
        job_id: str,
        operation: str,
        target_uuid: str,
        prior_state: str,
        reconciliation_status: str,
    ) -> None:
        emit_event(
            "dead_letter_alert",
            level=logging.WARNING,
            code=code,
            workspace_id=workspace_id,
            job_id=job_id,
            operation=operation,
            target_uuid=target_uuid,
            prior_state=prior_state,
            reconciliation_status=reconciliation_status,
            reservation_retained=True,
        )

    async def admit(self, actor: WorkerPrincipal, *, workspace_id: str, target_uuid: str,
                    idempotency_key: str, request_hash: str, payload: dict[str, object],
                    image: str, execution_profile: ExecutionProfile | None = None) -> JobTicket:
        actor.require("checks:run", workspace_id)
        validate_symbolic_check_payload(payload)
        if not IMAGE_ID.fullmatch(image):
            raise JobRejected("UNPINNED_SANDBOX_IMAGE", 503)
        try:
            profile = execution_profile or capture_execution_profile(
                image, "symbolic", payload["timeout_ms"],
            )
            validate_live_profile(profile, image, "symbolic")
        except ValueError as exc:
            if str(exc) == "EXECUTION_PROFILE_DRIFT":
                raise JobRejected("EXECUTION_PROFILE_DRIFT", 409) from exc
            raise JobRejected("INVALID_JOB_INPUT", 422) from exc
        if profile.invocation_limit_ms != min(payload["timeout_ms"], profile.timeout_cap_ms):
            raise JobRejected("INVALID_JOB_INPUT", 422)
        source = await self.store.equation_source(
            workspace_id=workspace_id, equation_uuid=target_uuid,
        )
        if source.get("latex") != payload.get("formula_a"):
            raise JobRejected("JOB_SOURCE_MISMATCH", 422)
        now = int(time.time())
        job_id = str(uuid5(NAMESPACE_URL, json.dumps([workspace_id, target_uuid, idempotency_key])))
        envelope = JobEnvelope(
            schema_version="research-job.v2",
            job_id=job_id,
            workspace_id=workspace_id, target_uuid=target_uuid, actor_id=actor.identity,
            request_hash=request_hash, source_hash=digest(payload["formula_a"]),
            payload_hash=digest(payload), image_id=image, execution_profile=profile,
            issued_at=now, expires_at=now + 120,
            reserved_ms=payload["timeout_ms"],
        )
        async with self.store.driver.session(database=self.store.database) as session:
            raw, mac, result, result_mac = await session.execute_write(self._admit, envelope, now)
        ticket = JobTicket(JobEnvelope.model_validate_json(raw), mac, result)
        if not hmac.compare_digest(signature(ticket.envelope, self.key), mac):
            raise JobRejected("INVALID_JOB_SIGNATURE", 403)
        if result is not None and not hmac.compare_digest(
            self._result_mac(envelope.job_id, result), result_mac or "",
        ):
            raise JobRejected("INVALID_RESULT_SIGNATURE", 403)
        self._emit_job_transition(
            ticket.envelope,
            to_state="finished" if result is not None else "queued",
            replayed=result is not None,
        )
        return ticket

    async def admit_numerical_fixture(
        self,
        actor: WorkerPrincipal,
        *,
        workspace_id: str,
        candidate_id: str,
        idempotency_key: str,
        payload: dict[str, object],
        image: str,
        reserved_ms: int,
        execution_profile: ExecutionProfile | None = None,
    ) -> JobTicket:
        """Reserve bounded quota for the fixed synthetic worker, never candidate code."""
        actor.require("checks:run", workspace_id)
        validate_numerical_fixture_payload(payload)
        if (
            payload["candidate_id"] != candidate_id
            or type(reserved_ms) is not int
            or not 10 <= reserved_ms <= 30_000
            or not IMAGE_ID.fullmatch(image)
        ):
            raise JobRejected("INVALID_NUMERICAL_FIXTURE_INPUT", 422)
        if not idempotency_key or len(idempotency_key) > 200 or not idempotency_key.isascii():
            raise JobRejected("INVALID_IDEMPOTENCY_KEY", 422)
        try:
            profile = execution_profile or capture_execution_profile(
                image, "numerical", reserved_ms,
            )
            validate_live_profile(profile, image, "numerical")
        except ValueError as exc:
            if str(exc) == "EXECUTION_PROFILE_DRIFT":
                raise JobRejected("EXECUTION_PROFILE_DRIFT", 409) from exc
            raise JobRejected("INVALID_NUMERICAL_FIXTURE_INPUT", 422) from exc
        if profile.invocation_limit_ms != min(reserved_ms, profile.timeout_cap_ms):
            raise JobRejected("INVALID_NUMERICAL_FIXTURE_INPUT", 422)

        group_id = workspace_group_id(workspace_id)
        rows, _, _ = await self.store.driver.execute_query(
            "MATCH (c:ResearchCandidate {candidate_id:$candidate_id, group_id:$group}) "
            "RETURN c.payload AS payload LIMIT 1",
            candidate_id=candidate_id,
            group=group_id,
            database_=self.store.database,
        )
        if not rows:
            raise JobRejected("JOB_SOURCE_NOT_FOUND", 404)
        candidate = CompiledCandidate.model_validate_json(rows[0]["payload"])
        _verify_candidate_identity(candidate)
        if (
            candidate.workspace_id != workspace_id
            or candidate.content_hash != payload["candidate_hash"]
        ):
            raise JobRejected("JOB_SOURCE_MISMATCH", 422)
        await self._validate_numerical_fixture_spec(
            group_id=group_id,
            candidate=candidate,
            payload=payload,
            reserved_ms=reserved_ms,
        )

        now = int(time.time())
        job_id = str(uuid5(NAMESPACE_URL, json.dumps(
            [workspace_id, "numerical_fixture:run", candidate_id, idempotency_key],
        )))
        envelope = JobEnvelope(
            schema_version="research-job.v2",
            job_id=job_id,
            workspace_id=workspace_id,
            target_uuid=candidate_id,
            actor_id=actor.identity,
            operation="numerical_fixture:run",
            request_hash=digest([payload, image, reserved_ms, profile.model_dump(mode="json")]),
            source_hash=digest([candidate_id, candidate.content_hash]),
            payload_hash=digest(payload),
            image_id=image,
            execution_profile=profile,
            issued_at=now,
            expires_at=now + 120,
            reserved_ms=reserved_ms,
        )
        async with self.store.driver.session(database=self.store.database) as session:
            raw, mac, result, result_mac = await session.execute_write(
                self._admit, envelope, now
            )
        ticket = JobTicket(JobEnvelope.model_validate_json(raw), mac, result)
        if not hmac.compare_digest(signature(ticket.envelope, self.key), mac):
            raise JobRejected("INVALID_JOB_SIGNATURE", 403)
        if result is not None and not hmac.compare_digest(
            self._result_mac(envelope.job_id, result), result_mac or "",
        ):
            raise JobRejected("INVALID_RESULT_SIGNATURE", 403)
        self._emit_job_transition(
            ticket.envelope,
            to_state="finished" if result is not None else "queued",
            replayed=result is not None,
        )
        return ticket

    async def admit_research_case(
        self,
        actor: WorkerPrincipal,
        *,
        workspace_id: str,
        candidate_id: str,
        idempotency_key: str,
        binding: ImplementationBindingReceipt,
        reserved_ms: int,
        evaluation_role: str = "legacy_full",
        evolution_id: str | None = None,
    ) -> JobTicket:
        actor.require("experiments:run", workspace_id)
        payload = research_case_job_payload(binding, evaluation_role, evolution_id)
        validate_research_case_payload(payload)
        if (
            candidate_id != binding.candidate_id
            or binding.workspace_id != workspace_id
            or type(reserved_ms) is not int
            or not 10 <= reserved_ms <= 30_000
            or not IMAGE_ID.fullmatch(binding.execution_image)
            or not idempotency_key
            or len(idempotency_key) > 200
            or not idempotency_key.isascii()
        ):
            raise JobRejected("INVALID_RESEARCH_CASE_INPUT", 422)
        if (
            binding.schema_version != "implementation-binding.v2"
            or binding.execution_profile is None
        ):
            raise JobRejected("LEGACY_BINDING_PROFILE_REQUIRED", 422)
        try:
            validate_live_profile(
                binding.execution_profile, binding.execution_image, "research_case",
            )
        except ValueError as exc:
            if str(exc) == "EXECUTION_PROFILE_DRIFT":
                raise JobRejected("EXECUTION_PROFILE_DRIFT", 409) from exc
            raise JobRejected("INVALID_RESEARCH_CASE_INPUT", 422) from exc
        await self._validate_research_case_source(
            workspace_id=workspace_id,
            candidate_id=candidate_id,
            binding=binding,
            reserved_ms=reserved_ms,
            evaluation_role=evaluation_role, evolution_id=evolution_id,
        )
        now = int(time.time())
        job_id = str(uuid5(NAMESPACE_URL, json.dumps(
            [workspace_id, "research_case:run", candidate_id, idempotency_key]
        )))
        envelope = JobEnvelope(
            schema_version="research-job.v2",
            job_id=job_id,
            workspace_id=workspace_id,
            target_uuid=candidate_id,
            actor_id=actor.identity,
            operation="research_case:run",
            request_hash=digest([
                payload,
                binding.execution_image,
                reserved_ms,
                binding.execution_profile.model_dump(mode="json"),
            ]),
            source_hash=digest([candidate_id, binding.candidate_hash, binding.binding_hash]),
            payload_hash=digest(payload),
            image_id=binding.execution_image,
            execution_profile=binding.execution_profile,
            issued_at=now,
            expires_at=now + 120,
            reserved_ms=reserved_ms,
        )
        async with self.store.driver.session(database=self.store.database) as session:
            raw, mac, result, result_mac = await session.execute_write(
                self._admit, envelope, now, payload
            )
        ticket = JobTicket(JobEnvelope.model_validate_json(raw), mac, result)
        if not hmac.compare_digest(signature(ticket.envelope, self.key), mac):
            raise JobRejected("INVALID_JOB_SIGNATURE", 403)
        if result is not None and not hmac.compare_digest(
            self._result_mac(envelope.job_id, result), result_mac or ""
        ):
            raise JobRejected("INVALID_RESULT_SIGNATURE", 403)
        self._emit_job_transition(
            ticket.envelope,
            to_state="finished" if result is not None else "queued",
            replayed=result is not None,
        )
        return ticket

    @staticmethod
    async def _lock(tx):
        await (await tx.run("MERGE (q:ResearchQueueLock {id:'admission.v1'}) "
                           "SET q.serial=coalesce(q.serial,0)+1")).consume()

    async def research_case_retry(
        self, actor, workspace_id, candidate_id, key, role, evolution_id,
    ):
        """Recover the original binding timestamp; retries never manufacture a new intent."""
        actor.require("experiments:run", workspace_id)
        job_id = str(uuid5(NAMESPACE_URL, json.dumps(
            [workspace_id, "research_case:run", candidate_id, key]
        )))
        rows, _, _ = await self.store.driver.execute_query(
            "MATCH (j:ResearchJob {id:$id,workspace:$workspace}) "
            "RETURN j.envelope AS envelope,j.mac AS mac,j.input_payload AS input,"
            "j.result AS result,j.result_mac AS result_mac",
            id=job_id, workspace=workspace_id, database_=self.store.database,
        )
        if not rows:
            return None, None
        row = rows[0]
        envelope = JobEnvelope.model_validate_json(row["envelope"])
        if (envelope.actor_id != actor.identity or envelope.target_uuid != candidate_id
                or envelope.workspace_id != workspace_id
                or envelope.operation != "research_case:run"
                or not hmac.compare_digest(signature(envelope, self.key), row["mac"] or "")):
            raise JobRejected("INVALID_JOB_SIGNATURE", 403)
        if not row["input"]:
            raise JobRejected("LEGACY_JOB_REQUIRES_RECONCILIATION")
        payload = json.loads(row["input"])
        validate_research_case_payload(payload)
        if (digest(payload) != envelope.payload_hash
                or payload.get("evaluation_role") != role
                or payload.get("evolution_id") != evolution_id):
            raise JobRejected("JOB_IDEMPOTENCY_CONFLICT")
        if row["result"] is None:
            if envelope.schema_version != "research-job.v2" or envelope.execution_profile is None:
                raise JobRejected("LEGACY_JOB_REQUIRES_RECONCILIATION")
            try:
                validate_live_profile(
                    envelope.execution_profile, envelope.image_id, "research_case",
                )
            except ValueError as exc:
                if str(exc) == "EXECUTION_PROFILE_DRIFT":
                    raise JobRejected("EXECUTION_PROFILE_DRIFT", 409) from exc
                raise JobRejected("INVALID_JOB_ENVELOPE", 422) from exc
            return payload, None
        if not hmac.compare_digest(
            self._result_mac(job_id, row["result"]), row["result_mac"] or "",
        ):
            raise JobRejected("INVALID_RESULT_SIGNATURE", 403)
        saved = ResearchCaseReceiptResponse.model_validate_json(row["result"])
        if (saved.result.run_id != job_id or saved.result.actor_id != actor.identity
                or saved.result.candidate_id != candidate_id
                or saved.result.evaluation_role != role or saved.result.evolution_id != evolution_id
                or saved.binding.execution_profile != envelope.execution_profile
                or saved.result.execution_profile != envelope.execution_profile
                or research_case_job_payload(saved.binding, role, evolution_id) != payload):
            raise JobRejected("JOB_RESULT_MISMATCH", 422)
        return payload, saved.model_copy(update={"replayed": True})

    async def _admit(self, tx, envelope, now, input_payload=None):
        await self._lock(tx)
        old = await (await tx.run(
            "MATCH (j:ResearchJob {id:$id}) RETURN j.envelope AS envelope, "
            "j.mac AS mac, j.result AS result, j.result_mac AS result_mac", id=envelope.job_id,
        )).single()
        if old:
            prior = JobEnvelope.model_validate_json(old["envelope"])
            if prior.model_dump(exclude={"issued_at", "expires_at"}) != envelope.model_dump(
                exclude={"issued_at", "expires_at"},
            ):
                raise JobRejected("JOB_IDEMPOTENCY_CONFLICT")
            return old["envelope"], old["mac"], old["result"], old["result_mac"]
        counts = await (await tx.run(
            "MATCH (j:ResearchJob) WHERE j.expires_at>$now AND j.state IN ['queued','running'] "
            "RETURN count(j) AS total, "
            "sum(CASE WHEN j.workspace=$workspace THEN 1 ELSE 0 END) AS own",
            now=now, workspace=envelope.workspace_id,
        )).single()
        budget = await (await tx.run(
            "MATCH (j:ResearchJob {workspace:$workspace}) WHERE j.issued_at>$since "
            "RETURN coalesce(sum(j.reserved_ms),0) AS reserved",
            workspace=envelope.workspace_id, since=now - 3600,
        )).single()
        if (counts["total"] >= self.MAX_GLOBAL_RUNNING
                or counts["own"] >= self.MAX_WORKSPACE_RUNNING
                or budget["reserved"] + envelope.reserved_ms > self.MAX_HOURLY_RESERVED_MS):
            raise JobRejected("RESEARCH_QUOTA_EXCEEDED", 429)
        raw, mac = envelope.model_dump_json(), signature(envelope, self.key)
        await (await tx.run(
            "CREATE (j:ResearchJob {id:$id, workspace:$workspace, envelope:$envelope, "
            "mac:$mac, state:'queued', issued_at:$issued, expires_at:$expiry, reserved_ms:$ms,"
            "input_payload:$input})",
            id=envelope.job_id, workspace=envelope.workspace_id, envelope=raw, mac=mac,
            issued=envelope.issued_at, expiry=envelope.expires_at, ms=envelope.reserved_ms,
            input=json.dumps(input_payload, sort_keys=True) if input_payload is not None else None,
        )).consume()
        return raw, mac, None, None

    async def claim(self, ticket: JobTicket, actor: WorkerPrincipal, payload, image):
        envelope = verify_envelope(ticket.envelope.model_dump_json(), ticket.mac, self.key,
                                   actor, payload, image)
        if envelope.operation == "checks:run":
            source = await self.store.equation_source(
                workspace_id=envelope.workspace_id, equation_uuid=envelope.target_uuid,
            )
            if digest(source.get("latex")) != envelope.source_hash:
                raise JobRejected("JOB_SOURCE_MISMATCH", 422)
        elif envelope.operation == "numerical_fixture:run":
            await self._resolve_numerical_fixture_source(envelope, payload)
        else:
            binding = await self._research_case_binding(
                envelope.workspace_id, payload, image, envelope.execution_profile,
            )
            await self._validate_research_case_source(
                workspace_id=envelope.workspace_id,
                candidate_id=envelope.target_uuid,
                binding=binding,
                reserved_ms=envelope.reserved_ms,
                evaluation_role=str(payload.get("evaluation_role", "legacy_full")),
                evolution_id=payload.get("evolution_id"),
            )
        async with self.store.driver.session(database=self.store.database) as session:
            await session.execute_write(self._claim, ticket)
        self._emit_job_transition(
            envelope,
            from_state="queued",
            to_state="running",
            replayed=False,
        )

    async def _resolve_numerical_fixture_source(
        self,
        envelope: JobEnvelope,
        payload: dict[str, object],
    ) -> None:
        rows, _, _ = await self.store.driver.execute_query(
            "MATCH (c:ResearchCandidate {candidate_id:$candidate_id, group_id:$group}) "
            "RETURN c.payload AS payload LIMIT 1",
            candidate_id=envelope.target_uuid,
            group=workspace_group_id(envelope.workspace_id),
            database_=self.store.database,
        )
        if not rows:
            raise JobRejected("JOB_SOURCE_NOT_FOUND", 404)
        candidate = CompiledCandidate.model_validate_json(rows[0]["payload"])
        _verify_candidate_identity(candidate)
        if (
            candidate.workspace_id != envelope.workspace_id
            or candidate.candidate_id != envelope.target_uuid
            or digest([candidate.candidate_id, candidate.content_hash]) != envelope.source_hash
        ):
            raise JobRejected("JOB_SOURCE_MISMATCH", 422)
        await self._validate_numerical_fixture_spec(
            group_id=workspace_group_id(envelope.workspace_id),
            candidate=candidate,
            payload=payload,
            reserved_ms=envelope.reserved_ms,
        )

    async def _validate_numerical_fixture_spec(
        self,
        *,
        group_id: str,
        candidate: CompiledCandidate,
        payload: dict[str, object],
        reserved_ms: int,
    ) -> None:
        result, _, _ = await self.store.driver.execute_query(
            "MATCH (s:ProblemSpec {spec_id:$spec_id, group_id:$group}) "
            "RETURN s.payload AS payload LIMIT 1",
            spec_id=candidate.problem_spec_id,
            group=group_id,
            database_=self.store.database,
        )
        if not result:
            raise JobRejected("JOB_SPEC_NOT_FOUND", 404)
        spec = ProblemSpecSnapshot.model_validate_json(result[0]["payload"])
        if (
            definition_hash(spec.definition) != spec.content_hash
            or spec.workspace_id != candidate.workspace_id
            or spec.content_hash != candidate.problem_spec_hash
            or payload["dtype"] != spec.definition.dtype
            or payload["seed"] not in spec.definition.seeds
        ):
            raise JobRejected("JOB_SPEC_MISMATCH", 422)

        parent_candidate = None
        if candidate.operator == "lower_mixture_to_concatenation":
            if len(candidate.parents) != 1:
                raise JobRejected("JOB_SOURCE_MISMATCH", 422)
            parent_result, _, _ = await self.store.driver.execute_query(
                "MATCH (p:ResearchCandidate {candidate_id:$candidate_id, group_id:$group}) "
                "RETURN p.payload AS payload LIMIT 1",
                candidate_id=candidate.parents[0].entity_id,
                group=group_id,
                database_=self.store.database,
            )
            if not parent_result:
                raise JobRejected("JOB_SOURCE_NOT_FOUND", 404)
            parent_candidate = CompiledCandidate.model_validate_json(parent_result[0]["payload"])
            _verify_candidate_identity(parent_candidate)
        try:
            expected_payload = build_numerical_suite_input(
                candidate,
                spec,
                seed=payload["seed"],
                parent_candidate=parent_candidate,
            ) | {"candidate_id": candidate.candidate_id}
        except (TypeError, ValueError) as exc:
            raise JobRejected("JOB_SPEC_MISMATCH", 422) from exc
        expected_reservation = min(
            30_000, max(10, spec.definition.budget.wall_time_ms)
        )
        if payload != expected_payload or reserved_ms != expected_reservation:
            raise JobRejected("JOB_INPUT_MISMATCH", 422)

    async def _research_case_binding(
        self,
        workspace_id: str,
        payload: dict[str, object],
        image: str,
        execution_profile: ExecutionProfile | None,
    ) -> ImplementationBindingReceipt:
        if execution_profile is None:
            raise JobRejected("LEGACY_JOB_PROFILE_REQUIRED", 422)
        rows, _, _ = await self.store.driver.execute_query(
            "MATCH (c:ResearchCandidate {candidate_id:$candidate_id, group_id:$group}) "
            "RETURN c.payload AS payload LIMIT 1",
            candidate_id=payload["candidate_id"],
            group=workspace_group_id(workspace_id),
            database_=self.store.database,
        )
        if not rows:
            raise JobRejected("JOB_SOURCE_NOT_FOUND", 404)
        candidate = CompiledCandidate.model_validate_json(rows[0]["payload"])
        candidate, spec, parent = await self.store.prepare_registered_research_case(
            workspace_id=workspace_id, candidate_id=candidate.candidate_id
        )
        try:
            binding = make_implementation_binding(
                candidate,
                spec,
                parent_candidate=parent,
                execution_image=image,
                now=datetime.fromisoformat(
                    str(payload["binding_created_at"]).replace("Z", "+00:00")
                ),
                execution_profile=execution_profile,
            )
        except ValueError as exc:
            raise JobRejected("JOB_SOURCE_MISMATCH", 422) from exc
        if (
            binding.binding_id != payload["binding_id"]
            or binding.binding_hash != payload["binding_hash"]
            or binding.protocol_hash != payload["protocol_hash"]
        ):
            raise JobRejected("JOB_SOURCE_MISMATCH", 422)
        return binding

    async def _validate_research_case_source(
        self,
        *,
        workspace_id: str,
        candidate_id: str,
        binding: ImplementationBindingReceipt,
        reserved_ms: int,
        evaluation_role: str = "legacy_full",
        evolution_id: str | None = None,
    ) -> None:
        if evaluation_role == "legacy_full":
            raise JobRejected("LEGACY_FULL_PROTOCOL_IS_REPLAY_ONLY", 422)
        await self.store.authorize_protocol_phase(
            workspace_id=workspace_id, candidate_id=candidate_id,
            evaluation_role=evaluation_role, evolution_id=evolution_id,
        )
        if evolution_id is not None:
            await self.store.require_protocol_scope_review(binding)
        candidate, spec, parent = await self.store.prepare_registered_research_case(
            workspace_id=workspace_id, candidate_id=candidate_id
        )
        expected = make_implementation_binding(
            candidate,
            spec,
            parent_candidate=parent,
            execution_image=binding.execution_image,
            now=binding.created_at,
            execution_profile=binding.execution_profile,
        )
        if expected != binding or reserved_ms != phase_budget_ms(evaluation_role):
            raise JobRejected("JOB_INPUT_MISMATCH", 422)

    async def _claim(self, tx, ticket):
        await self._lock(tx)
        row = await (await tx.run(
            "MATCH (j:ResearchJob {id:$id, state:'queued', envelope:$envelope, mac:$mac}) "
            "WHERE j.expires_at>$now SET j.state='running' RETURN j.id AS id",
            id=ticket.envelope.job_id, envelope=ticket.envelope.model_dump_json(),
            mac=ticket.mac, now=int(time.time()),
        )).single()
        if row is None:
            raise JobRejected("JOB_NOT_CLAIMABLE")

    async def _inspect_finish_row(self, tx, envelope: JobEnvelope, mac: str):
        return await (await tx.run(
            "MATCH (j:ResearchJob {id:$id, envelope:$envelope, mac:$mac}) "
            "RETURN j.state AS state, j.expires_at AS expires_at, "
            "j.result AS result, j.result_mac AS result_mac, "
            "j.prior_state AS prior_state",
            id=envelope.job_id,
            envelope=envelope.model_dump_json(),
            mac=mac,
        )).single()

    @staticmethod
    async def _quarantine_late_job(
        tx, envelope: JobEnvelope, mac: str, now: int,
    ) -> DeadLetterPriorState:
        q_row = await (await tx.run(
            "MATCH (j:ResearchJob {id:$id, envelope:$envelope, mac:$mac}) "
            "WHERE j.state <> 'finished' "
            "SET j.prior_state = CASE WHEN j.state IN ['queued', 'running'] "
            "THEN j.state ELSE coalesce(j.prior_state, 'running') END, "
            "j.state = 'failed', "
            "j.dead_letter = true, "
            "j.failure_reason = coalesce(j.failure_reason, 'LATE_OR_EXPIRED_RESULT_DELIVERY'), "
            "j.reconciliation_status = CASE "
            "WHEN j.reconciliation_status IN ['reconciled_receipt', 'retry_authorized'] "
            "THEN j.reconciliation_status ELSE 'quarantined' END, "
            "j.quarantined_at = coalesce(j.quarantined_at, $now) "
            "RETURN j.prior_state AS prior_state",
            id=envelope.job_id,
            envelope=envelope.model_dump_json(),
            mac=mac,
            now=now,
        )).single()
        prior = q_row["prior_state"] if q_row else "unknown"
        return prior if prior in ("queued", "running") else "unknown"

    def _verify_finished_result_mac(self, job_id: str, row) -> str:
        stored_raw = row["result"]
        if stored_raw is None or not hmac.compare_digest(
            self._result_mac(job_id, str(stored_raw)), str(row["result_mac"] or ""),
        ):
            raise JobRejected("INVALID_RESULT_SIGNATURE", 403)
        return str(stored_raw)

    async def finish(self, ticket: JobTicket, result: str):
        if not hmac.compare_digest(signature(ticket.envelope, self.key), ticket.mac):
            raise JobRejected("INVALID_JOB_SIGNATURE", 403)
        async with self.store.driver.session(database=self.store.database) as session:
            status_tag, prior_state = await session.execute_write(self._finish, ticket, result)
        if status_tag in ("finished", "replayed"):
            self._emit_job_transition(
                ticket.envelope,
                from_state="running",
                to_state="finished",
                replayed=(status_tag == "replayed"),
            )
            return
        if status_tag == "quarantined":
            self._emit_dead_letter_alert(
                code="JOB_RESULT_REQUIRES_RECONCILIATION",
                workspace_id=ticket.envelope.workspace_id,
                job_id=ticket.envelope.job_id,
                operation=ticket.envelope.operation,
                target_uuid=ticket.envelope.target_uuid,
                prior_state=prior_state,
                reconciliation_status="quarantined",
            )
        raise JobRejected("JOB_RESULT_REQUIRES_RECONCILIATION")

    async def finish_numerical_fixture(
        self,
        ticket: JobTicket,
        *,
        research_store,
        idempotency_key: str,
        result: NumericalFixtureReceipt,
    ) -> NumericalFixtureReceiptResponse:
        envelope = ticket.envelope
        result = NumericalFixtureReceipt.model_validate(result.model_dump(mode="python"))
        if (
            envelope.operation != "numerical_fixture:run"
            or envelope.schema_version != "research-job.v2"
            or envelope.execution_profile is None
            or result.schema_version != "numerical-fixture-result.v2"
            or result.execution_image != envelope.image_id
            or result.execution_profile != envelope.execution_profile
            or result.run_id != envelope.job_id
            or result.workspace_id != envelope.workspace_id
            or result.candidate_id != envelope.target_uuid
            or result.actor_id != envelope.actor_id
            or not hmac.compare_digest(signature(envelope, self.key), ticket.mac)
        ):
            raise JobRejected("JOB_RESULT_MISMATCH", 422)
        async with self.store.driver.session(database=self.store.database) as session:
            status_tag, persisted, prior_state = await session.execute_write(
                self._finish_numerical_fixture,
                ticket,
                research_store,
                idempotency_key,
                result,
            )
        if status_tag in ("finished", "replayed") and persisted is not None:
            self._emit_job_transition(
                envelope,
                from_state="running",
                to_state="finished",
                replayed=persisted.replayed,
            )
            emit_event(
                "numerical_fixture_receipt",
                workspace_id=envelope.workspace_id,
                candidate_id=envelope.target_uuid,
                spec_id=persisted.result.problem_spec_id,
                result_id=persisted.result.result_id,
                run_id=persisted.result.run_id,
                seed=persisted.result.seed,
                outcome=persisted.result.outcome,
                replayed=persisted.replayed,
            )
            return persisted
        if status_tag == "quarantined":
            self._emit_dead_letter_alert(
                code="JOB_RESULT_REQUIRES_RECONCILIATION",
                workspace_id=envelope.workspace_id,
                job_id=envelope.job_id,
                operation=envelope.operation,
                target_uuid=envelope.target_uuid,
                prior_state=prior_state,
                reconciliation_status="quarantined",
            )
        raise JobRejected("JOB_RESULT_REQUIRES_RECONCILIATION")

    async def _finish_numerical_fixture(
        self,
        tx,
        ticket: JobTicket,
        research_store,
        idempotency_key: str,
        result: NumericalFixtureReceipt,
    ) -> tuple[str, NumericalFixtureReceiptResponse | None, DeadLetterPriorState]:
        await self._lock(tx)
        envelope = ticket.envelope
        now = int(time.time())
        row = await self._inspect_finish_row(tx, envelope, ticket.mac)
        if row is None:
            return ("missing", None, "unknown")
        if row["state"] == "finished":
            stored = NumericalFixtureReceipt.model_validate_json(
                self._verify_finished_result_mac(envelope.job_id, row)
            )
            if stored != result:
                raise JobRejected("JOB_RESULT_CONFLICT", 409)
            return (
                "replayed",
                NumericalFixtureReceiptResponse(result=stored, replayed=True),
                "running",
            )
        if row["state"] != "running" or int(row["expires_at"] or 0) <= now:
            prior = await self._quarantine_late_job(tx, envelope, ticket.mac, now)
            return ("quarantined", None, prior)

        group_id, receipt_key, intent_hash = research_store.numerical_fixture_receipt_identity(
            workspace_id=envelope.workspace_id,
            candidate_id=envelope.target_uuid,
            actor_id=envelope.actor_id,
            idempotency_key=idempotency_key,
            result=result,
        )
        appended = await research_store._tx_append_candidate_numerical_fixture(
            tx,
            group_id,
            envelope.workspace_id,
            envelope.target_uuid,
            envelope.actor_id,
            receipt_key,
            intent_hash,
            result,
        )
        result_json = result.model_dump_json()
        updated = await (await tx.run(
            "MATCH (j:ResearchJob {id:$id, state:'running', envelope:$envelope, mac:$mac}) "
            "WHERE j.expires_at>$now SET j.state='finished', j.result=$result, "
            "j.result_mac=$result_mac, j.finished_at=$now RETURN j.id AS id",
            id=envelope.job_id,
            envelope=envelope.model_dump_json(),
            mac=ticket.mac,
            now=now,
            result=result_json,
            result_mac=self._result_mac(envelope.job_id, result_json),
        )).single()
        if updated is None:
            prior = await self._quarantine_late_job(tx, envelope, ticket.mac, now)
            return ("quarantined", None, prior)
        return ("finished", appended, "running")

    async def finish_research_case(
        self,
        ticket: JobTicket,
        *,
        research_store,
        idempotency_key: str,
        binding: ImplementationBindingReceipt,
        result: ResearchCaseReceipt,
    ) -> ResearchCaseReceiptResponse:
        envelope = ticket.envelope
        binding = ImplementationBindingReceipt.model_validate(binding.model_dump(mode="python"))
        result = ResearchCaseReceipt.model_validate(result.model_dump(mode="python"))
        payload = research_case_job_payload(binding, result.evaluation_role, result.evolution_id)
        if (
            envelope.operation != "research_case:run"
            or envelope.schema_version != "research-job.v2"
            or envelope.execution_profile is None
            or binding.schema_version != "implementation-binding.v2"
            or binding.execution_profile != envelope.execution_profile
            or result.schema_version != "research-case-result.v3"
            or result.execution_profile != envelope.execution_profile
            or result.run_id != envelope.job_id
            or result.workspace_id != envelope.workspace_id
            or result.candidate_id != envelope.target_uuid
            or result.actor_id != envelope.actor_id
            or result.binding_id != binding.binding_id
            or result.binding_hash != binding.binding_hash
            or result.candidate_hash != binding.candidate_hash
            or result.protocol_hash != binding.protocol_hash
            or envelope.image_id != binding.execution_image
            or envelope.payload_hash != digest(payload)
            or envelope.source_hash != digest([
                binding.candidate_id, binding.candidate_hash, binding.binding_hash
            ])
            or not hmac.compare_digest(signature(envelope, self.key), ticket.mac)
        ):
            raise JobRejected("JOB_RESULT_MISMATCH", 422)
        async with self.store.driver.session(database=self.store.database) as session:
            status_tag, persisted, prior_state = await session.execute_write(
                self._finish_research_case,
                ticket,
                research_store,
                idempotency_key,
                binding,
                result,
            )
        if status_tag in ("finished", "replayed") and persisted is not None:
            self._emit_job_transition(
                envelope,
                from_state="running",
                to_state="finished",
                replayed=persisted.replayed,
            )
            emit_event(
                "research_case_receipt",
                workspace_id=envelope.workspace_id,
                candidate_id=envelope.target_uuid,
                spec_id=persisted.result.problem_spec_id,
                binding_id=persisted.binding.binding_id,
                result_id=persisted.result.result_id,
                run_id=persisted.result.run_id,
                evaluation_role=persisted.result.evaluation_role,
                evolution_id=persisted.result.evolution_id,
                outcome=persisted.result.outcome,
                replayed=persisted.replayed,
            )
            return persisted
        if status_tag == "quarantined":
            self._emit_dead_letter_alert(
                code="JOB_RESULT_REQUIRES_RECONCILIATION",
                workspace_id=envelope.workspace_id,
                job_id=envelope.job_id,
                operation=envelope.operation,
                target_uuid=envelope.target_uuid,
                prior_state=prior_state,
                reconciliation_status="quarantined",
            )
        raise JobRejected("JOB_RESULT_REQUIRES_RECONCILIATION")

    async def _finish_research_case(
        self,
        tx,
        ticket: JobTicket,
        research_store,
        idempotency_key: str,
        binding: ImplementationBindingReceipt,
        result: ResearchCaseReceipt,
    ) -> tuple[str, ResearchCaseReceiptResponse | None, DeadLetterPriorState]:
        await self._lock(tx)
        envelope = ticket.envelope
        now = int(time.time())
        row = await self._inspect_finish_row(tx, envelope, ticket.mac)
        if row is None:
            return ("missing", None, "unknown")
        if row["state"] == "finished":
            stored = ResearchCaseReceiptResponse.model_validate_json(
                self._verify_finished_result_mac(envelope.job_id, row)
            )
            incoming = ResearchCaseReceiptResponse(binding=binding, result=result, replayed=False)
            if stored.model_copy(update={"replayed": False}) != incoming:
                raise JobRejected("JOB_RESULT_CONFLICT", 409)
            return ("replayed", stored.model_copy(update={"replayed": True}), "running")
        if row["state"] != "running" or int(row["expires_at"] or 0) <= now:
            prior = await self._quarantine_late_job(tx, envelope, ticket.mac, now)
            return ("quarantined", None, prior)

        group_id, receipt_key, intent_hash = research_store.research_case_receipt_identity(
            workspace_id=envelope.workspace_id,
            candidate_id=envelope.target_uuid,
            actor_id=envelope.actor_id,
            idempotency_key=idempotency_key,
            binding=binding,
            result=result,
        )
        appended = await research_store._tx_append_registered_research_case(
            tx,
            group_id,
            envelope.workspace_id,
            envelope.target_uuid,
            envelope.actor_id,
            receipt_key,
            intent_hash,
            binding,
            result,
        )
        result_json = ResearchCaseReceiptResponse(
            binding=binding, result=result, replayed=False
        ).model_dump_json()
        updated = await (await tx.run(
            "MATCH (j:ResearchJob {id:$id, state:'running', envelope:$envelope, mac:$mac}) "
            "WHERE j.expires_at>$now SET j.state='finished', j.result=$result, "
            "j.result_mac=$result_mac, j.finished_at=$now RETURN j.id AS id",
            id=envelope.job_id,
            envelope=envelope.model_dump_json(),
            mac=ticket.mac,
            now=now,
            result=result_json,
            result_mac=self._result_mac(envelope.job_id, result_json),
        )).single()
        if updated is None:
            prior = await self._quarantine_late_job(tx, envelope, ticket.mac, now)
            return ("quarantined", None, prior)
        return ("finished", appended, "running")

    async def _finish(self, tx, ticket: JobTicket, result: str) -> tuple[str, DeadLetterPriorState]:
        await self._lock(tx)
        now = int(time.time())
        row = await self._inspect_finish_row(tx, ticket.envelope, ticket.mac)
        if row is None:
            return ("missing", "unknown")
        if row["state"] == "finished":
            stored_raw = self._verify_finished_result_mac(ticket.envelope.job_id, row)
            try:
                equal = json.loads(stored_raw) == json.loads(result)
            except (ValueError, TypeError):
                equal = stored_raw == result
            if not equal:
                raise JobRejected("JOB_RESULT_CONFLICT", 409)
            return ("replayed", "running")
        if row["state"] != "running" or int(row["expires_at"] or 0) <= now:
            prior = await self._quarantine_late_job(tx, ticket.envelope, ticket.mac, now)
            return ("quarantined", prior)
        updated = await (await tx.run(
            "MATCH (j:ResearchJob {id:$id, state:'running', envelope:$envelope, mac:$mac}) "
            "WHERE j.expires_at>$now SET j.state='finished', j.result=$result, "
            "j.result_mac=$result_mac, j.finished_at=$now RETURN j.id AS id",
            id=ticket.envelope.job_id, envelope=ticket.envelope.model_dump_json(),
            mac=ticket.mac, now=now, result=result,
            result_mac=self._result_mac(ticket.envelope.job_id, result),
        )).single()
        if updated is None:
            prior = await self._quarantine_late_job(tx, ticket.envelope, ticket.mac, now)
            return ("quarantined", prior)
        return ("finished", "running")

    async def recover_stuck_jobs(
        self, workspace_id: str, now: int | None = None,
    ) -> dict[str, object]:
        """Recovers jobs that crashed or timed out while queued or running."""
        # ponytail: stuck-job recovery runs on-demand during ops health sweep,
        # ceiling: scans active jobs in single transaction,
        # upgrade: separate background sweeper cron in Phase 7.
        current_now = int(time.time()) if now is None else now
        if not workspace_id:
            raise JobRejected("WORKSPACE_REQUIRED", 403)
        if not getattr(self, "store", None) or not getattr(self.store, "driver", None):
            raise JobRejected("RESEARCH_QUEUE_UNAVAILABLE", 503)
        async with self.store.driver.session(database=self.store.database) as session:
            report, alerts = await session.execute_write(
                self._recover_stuck_jobs_with_details, workspace_id, current_now,
            )
        # Managed transactions can retry: emit only after the write has committed.
        for item in alerts:
            log_audit_event(
                "job_recovered",
                workspace_id=workspace_id,
                details={
                    "job_id": item["job_id"],
                    "reason": "JOB_EXPIRED_OR_CRASHED",
                    "prior_state": item["prior_state"],
                    "reservation_retained": True,
                },
            )
            self._emit_dead_letter_alert(
                code="JOB_EXPIRED_OR_CRASHED",
                workspace_id=workspace_id,
                job_id=str(item["job_id"]),
                operation=str(item["operation"]),
                target_uuid=str(item["target_uuid"]),
                prior_state=str(item["prior_state"]),
                reconciliation_status="pending",
            )
        return report

    async def _recover_stuck_jobs(self, tx, workspace_id: str, now: int) -> dict[str, object]:
        report, _alerts = await self._recover_stuck_jobs_with_details(tx, workspace_id, now)
        return report

    async def _recover_stuck_jobs_with_details(
        self, tx, workspace_id: str, now: int,
    ) -> tuple[dict[str, object], list[dict[str, object]]]:
        await self._lock(tx)
        cursor = await tx.run(
            "MATCH (j:ResearchJob {workspace:$workspace}) "
            "WHERE j.state IN ['queued', 'running'] AND j.expires_at <= $now "
            "WITH j, j.state AS prior_state "
            "SET j.prior_state = coalesce(j.prior_state, prior_state), "
            "j.state = 'failed', j.dead_letter = true, "
            "j.failure_reason = 'JOB_EXPIRED_OR_CRASHED', "
            "j.reconciliation_status = coalesce(j.reconciliation_status, 'pending'), "
            "j.recovered_at = $now "
            "RETURN j.id AS id, j.workspace AS workspace, j.state AS state, "
            "j.prior_state AS prior_state, j.envelope AS envelope, j.reserved_ms AS reserved_ms",
            now=now, workspace=workspace_id,
        )
        records = [record async for record in cursor]
        recovered_ids = [r["id"] for r in records]
        alerts: list[dict[str, object]] = []
        for record in records:
            operation, target_uuid = _envelope_meta(record.get("envelope"))
            prior = record.get("prior_state") or record.get("state") or "unknown"
            alerts.append({
                "job_id": record["id"],
                "prior_state": prior if prior in ("queued", "running") else "unknown",
                "operation": operation,
                "target_uuid": target_uuid,
            })
        return {
            "recovered_count": len(recovered_ids),
            "recovered_job_ids": recovered_ids,
            "recovered_at": datetime.now(UTC).isoformat(),
        }, alerts

    async def job_health_metrics(
        self, workspace_id: str, now: int | None = None,
    ) -> dict[str, object]:
        """Provides job metrics for operations and health monitoring."""
        current_now = int(time.time()) if now is None else now
        if not workspace_id:
            raise JobRejected("WORKSPACE_REQUIRED", 403)
        if not getattr(self, "store", None) or not getattr(self.store, "driver", None):
            raise JobRejected("RESEARCH_QUEUE_UNAVAILABLE", 503)
        async with self.store.driver.session(database=self.store.database) as session:
            cursor = await session.run(
                "MATCH (j:ResearchJob {workspace:$workspace}) "
                "RETURN count(j) AS total, "
                "sum(CASE WHEN j.state = 'queued' AND j.expires_at > $now "
                "THEN 1 ELSE 0 END) AS active_queued, "
                "sum(CASE WHEN j.state = 'running' AND j.expires_at > $now "
                "THEN 1 ELSE 0 END) AS active_running, "
                "sum(CASE WHEN j.state = 'finished' THEN 1 ELSE 0 END) AS finished, "
                "sum(CASE WHEN j.state = 'failed' THEN 1 ELSE 0 END) AS failed, "
                "sum(CASE WHEN j.state IN ['queued', 'running'] AND j.expires_at <= $now "
                "THEN 1 ELSE 0 END) AS stuck, "
                "sum(CASE WHEN j.state = 'failed' AND coalesce(j.dead_letter, false) = true "
                "AND coalesce(j.reconciliation_status, 'pending') = 'pending' "
                "THEN 1 ELSE 0 END) AS dead_letter_pending, "
                "sum(CASE WHEN j.state = 'failed' AND coalesce(j.dead_letter, false) = true "
                "AND j.reconciliation_status = 'quarantined' "
                "THEN 1 ELSE 0 END) AS dead_letter_quarantined",
                now=current_now, workspace=workspace_id,
            )
            record = await cursor.single()
            if not record:
                return {
                    "total": 0,
                    "active_queued": 0,
                    "active_running": 0,
                    "finished": 0,
                    "failed": 0,
                    "stuck": 0,
                    "dead_letter_pending": 0,
                    "dead_letter_quarantined": 0,
                }
            return {
                "total": int(record["total"] or 0),
                "active_queued": int(record["active_queued"] or 0),
                "active_running": int(record["active_running"] or 0),
                "finished": int(record["finished"] or 0),
                "failed": int(record["failed"] or 0),
                "stuck": int(record["stuck"] or 0),
                "dead_letter_pending": int(record["dead_letter_pending"] or 0),
                "dead_letter_quarantined": int(record["dead_letter_quarantined"] or 0),
            }

    async def list_dead_letter_jobs(
        self, workspace_id: str, limit: int = 25,
    ) -> list[dict[str, object]]:
        """Lists bounded dead-letter jobs for the authenticated workspace without secrets/notes."""
        if not workspace_id:
            raise JobRejected("WORKSPACE_REQUIRED", 403)
        if not getattr(self, "store", None) or not getattr(self.store, "driver", None):
            raise JobRejected("RESEARCH_QUEUE_UNAVAILABLE", 503)
        bounded_limit = max(1, min(limit, 50))
        async with self.store.driver.session(database=self.store.database) as session:
            cursor = await session.run(
                "MATCH (j:ResearchJob {workspace:$workspace}) "
                "WHERE j.state = 'failed' AND (coalesce(j.dead_letter, false) = true "
                "OR j.failure_reason IS NOT NULL) "
                "RETURN j.id AS id, j.envelope AS envelope, j.prior_state AS prior_state, "
                "j.failure_reason AS failure_reason, "
                "coalesce(j.reconciliation_status, 'pending') AS reconciliation_status, "
                "coalesce(j.reserved_ms, 0) AS reserved_ms, "
                "j.authorized_retry_key AS authorized_retry_key, "
                "j.reconciled_receipt_id AS reconciled_receipt_id, "
                "coalesce(j.recovered_at, j.quarantined_at) AS recovered_at, "
                "j.reconciled_at AS reconciled_at, j.reconciled_by AS reconciled_by "
                "ORDER BY coalesce(j.recovered_at, j.quarantined_at, j.expires_at, 0) DESC "
                "LIMIT $limit",
                workspace=workspace_id,
                limit=bounded_limit,
            )
            rows = [row async for row in cursor]
        items: list[dict[str, object]] = []
        for row in rows:
            operation, target_uuid = _envelope_meta(row.get("envelope"))
            raw_prior = row.get("prior_state")
            prior_state = raw_prior if raw_prior in ("queued", "running") else "unknown"
            status = row.get("reconciliation_status")
            if status not in ("pending", "quarantined", "retry_authorized", "reconciled_receipt"):
                status = "pending"
            items.append({
                "job_id": str(row["id"]),
                "operation": operation,
                "target_uuid": target_uuid,
                "prior_state": prior_state,
                "failure_reason": str(row["failure_reason"]) if row.get("failure_reason") else None,
                "reconciliation_status": status,
                "reserved_ms": max(0, int(row.get("reserved_ms") or 0)),
                "reservation_retained": True,
                "retry_eligible": prior_state == "queued" and status in ("pending", "quarantined"),
                "authorized_retry_key": row.get("authorized_retry_key"),
                "reconciled_receipt_id": row.get("reconciled_receipt_id"),
                "recovered_at": (
                    int(row["recovered_at"]) if row.get("recovered_at") is not None else None
                ),
                "reconciled_at": row.get("reconciled_at"),
                "reconciled_by": row.get("reconciled_by"),
            })
        return items

    async def reconcile_dead_letter_job(
        self,
        *,
        workspace_id: str,
        job_id: str,
        actor_id: str,
        actor_role: str,
        decision: DeadLetterDecision,
        notes: str,
        idempotency_key: str,
    ) -> DeadLetterReconcileResponse:
        """Human-authorized dead-letter recovery; never reruns running jobs or refunds."""
        if not workspace_id:
            raise JobRejected("WORKSPACE_REQUIRED", 403)
        if actor_role not in HUMAN_ROLES:
            raise JobRejected("RECONCILIATION_FORBIDDEN", 403)
        cleaned_notes = notes.strip()
        if not 3 <= len(cleaned_notes) <= 1000:
            raise JobRejected("INVALID_RECONCILIATION_NOTES", 422)
        cleaned_key = idempotency_key.strip()
        if not cleaned_key or len(cleaned_key) > 200 or not cleaned_key.isascii():
            raise JobRejected("IDEMPOTENCY_KEY_REQUIRED", 400)
        if not getattr(self, "store", None) or not getattr(self.store, "driver", None):
            raise JobRejected("RESEARCH_QUEUE_UNAVAILABLE", 503)

        async with self.store.driver.session(database=self.store.database) as session:
            response = await session.execute_write(
                self._tx_reconcile_dead_letter_job,
                workspace_id=workspace_id,
                job_id=job_id,
                actor_id=actor_id,
                actor_role=actor_role,
                decision=decision,
                cleaned_notes=cleaned_notes,
                idempotency_key=cleaned_key,
            )

        log_audit_event(
            "dead_letter_reconciled",
            actor_id=actor_id,
            workspace_id=workspace_id,
            details={
                "reconciliation_id": response.reconciliation_id,
                "job_id": response.job_id,
                "decision": response.decision,
                "prior_state": response.prior_state,
                "reconciliation_status": response.reconciliation_status,
                "receipt_found": response.receipt_found,
                "reservation_retained": True,
                "replayed": response.replayed,
            },
        )
        emit_event(
            "dead_letter_reconciled",
            workspace_id=workspace_id,
            job_id=response.job_id,
            operation=response.operation,
            target_uuid=response.target_uuid,
            prior_state=response.prior_state,
            decision=response.decision,
            reconciliation_status=response.reconciliation_status,
            reconciliation_id=response.reconciliation_id,
            receipt_found=response.receipt_found,
            reconciled_receipt_id=response.reconciled_receipt_id,
            reservation_retained=True,
            replayed=response.replayed,
        )
        return response

    async def _tx_reconcile_dead_letter_job(
        self,
        tx,
        *,
        workspace_id: str,
        job_id: str,
        actor_id: str,
        actor_role: str,
        decision: DeadLetterDecision,
        cleaned_notes: str,
        idempotency_key: str,
    ) -> DeadLetterReconcileResponse:
        await self._lock(tx)
        row = await (await tx.run(
            "MATCH (j:ResearchJob {id:$job_id, workspace:$workspace}) "
            "RETURN j.state AS state, j.envelope AS envelope, j.prior_state AS prior_state, "
            "j.reserved_ms AS reserved_ms, j.reconciliation_status AS reconciliation_status, "
            "j.reconciliation_key AS reconciliation_key, "
            "j.reconciliation_intent_hash AS reconciliation_intent_hash, "
            "j.reconciliation_payload AS reconciliation_payload",
            job_id=job_id,
            workspace=workspace_id,
        )).single()
        if row is None:
            raise JobRejected("DEAD_LETTER_JOB_NOT_FOUND", 404)
        if row["state"] != "failed":
            raise JobRejected("JOB_NOT_IN_DEAD_LETTER", 409)

        intent_hash = digest([workspace_id, job_id, actor_id, actor_role, decision, cleaned_notes])
        if row.get("reconciliation_key") == idempotency_key:
            if row.get("reconciliation_intent_hash") != intent_hash:
                raise JobRejected("RECONCILIATION_IDEMPOTENCY_CONFLICT", 409)
            saved = DeadLetterReconcileResponse.model_validate_json(
                str(row["reconciliation_payload"])
            )
            return saved.model_copy(update={"replayed": True})
        if row.get("reconciliation_status") in ("retry_authorized", "reconciled_receipt"):
            raise JobRejected("JOB_ALREADY_RECONCILED", 409)

        operation, target_uuid = _envelope_meta(row.get("envelope"))
        raw_prior = row.get("prior_state")
        prior_state: DeadLetterPriorState = (
            raw_prior if raw_prior in ("queued", "running") else "unknown"
        )
        reserved_ms = max(0, int(row.get("reserved_ms") or 0))
        authorized_retry_key: str | None = None
        reconciled_receipt_id: str | None = None
        receipt_found = False

        if decision == "authorize_retry":
            if prior_state != "queued":
                raise JobRejected("UNCERTAIN_JOB_CANNOT_AUTO_RETRY", 409)
            new_status: Literal["quarantined", "retry_authorized", "reconciled_receipt"] = (
                "retry_authorized"
            )
            authorized_retry_key = f"dlq-retry:{job_id}:{digest([idempotency_key, job_id])[:16]}"
        elif decision == "reconcile_receipt":
            reconciled_receipt_id = await self._find_existing_receipt_id(
                tx, workspace_id=workspace_id, job_id=job_id,
                operation=operation, target_uuid=target_uuid,
            )
            receipt_found = reconciled_receipt_id is not None
            new_status = "reconciled_receipt" if receipt_found else "quarantined"
        else:
            new_status = "quarantined"

        reconciled_at = datetime.now(UTC).isoformat()
        response = DeadLetterReconcileResponse(
            reconciliation_id="dlq_" + digest([workspace_id, job_id, idempotency_key])[:32],
            job_id=job_id,
            workspace_id=workspace_id,
            operation=operation,
            target_uuid=target_uuid,
            prior_state=prior_state,
            decision=decision,
            reconciliation_status=new_status,
            reserved_ms=reserved_ms,
            reservation_retained=True,
            receipt_found=receipt_found,
            reconciled_receipt_id=reconciled_receipt_id,
            authorized_retry_key=authorized_retry_key,
            reconciled_by=actor_id,
            reconciled_at=reconciled_at,
            replayed=False,
        )
        await (await tx.run(
            "MATCH (j:ResearchJob {id:$job_id, workspace:$workspace}) "
            "SET j.dead_letter = true, "
            "j.reconciliation_status = $status, "
            "j.reconciliation_id = $reconciliation_id, "
            "j.reconciliation_key = $reconciliation_key, "
            "j.reconciliation_intent_hash = $intent_hash, "
            "j.reconciliation_notes_sha256 = $notes_sha256, "
            "j.reconciled_by = $reconciled_by, "
            "j.reconciled_at = $reconciled_at, "
            "j.authorized_retry_key = $authorized_retry_key, "
            "j.reconciled_receipt_id = $reconciled_receipt_id, "
            "j.reconciliation_payload = $payload "
            "RETURN j.id AS id",
            job_id=job_id,
            workspace=workspace_id,
            status=new_status,
            reconciliation_id=response.reconciliation_id,
            reconciliation_key=idempotency_key,
            intent_hash=intent_hash,
            notes_sha256=digest(cleaned_notes),
            reconciled_by=actor_id,
            reconciled_at=reconciled_at,
            authorized_retry_key=authorized_retry_key,
            reconciled_receipt_id=reconciled_receipt_id,
            payload=response.model_dump_json(),
        )).single(strict=True)
        return response

    @staticmethod
    async def _find_existing_receipt_id(
        tx, *, workspace_id: str, job_id: str, operation: str, target_uuid: str,
    ) -> str | None:
        group_id = workspace_group_id(workspace_id)
        specs = (
            (
                "checks:run",
                "MATCH (c:CheckResult {group_id:$group}) WHERE c.payload CONTAINS $job_id "
                "RETURN c.uuid AS receipt_id, c.payload AS payload",
                CheckResult,
                "job_id",
                "target_uuid",
            ),
            (
                "numerical_fixture:run",
                "MATCH (r:ResearchNumericalFixtureResult {group_id:$group, "
                "workspace_id:$workspace}) WHERE r.payload CONTAINS $job_id "
                "RETURN r.result_id AS receipt_id, r.payload AS payload",
                NumericalFixtureReceipt,
                "run_id",
                "candidate_id",
            ),
            (
                "research_case:run",
                "MATCH (r:ResearchExperimentResult {group_id:$group, "
                "workspace_id:$workspace}) WHERE r.payload CONTAINS $job_id "
                "RETURN r.result_id AS receipt_id, r.payload AS payload",
                ResearchCaseReceipt,
                "run_id",
                "candidate_id",
            ),
        )
        for op_name, query, model_cls, run_attr, target_attr in specs:
            if operation not in (op_name, "unknown"):
                continue
            cursor = await tx.run(query, group=group_id, workspace=workspace_id, job_id=job_id)
            async for rec in cursor:
                try:
                    parsed = model_cls.model_validate_json(str(rec["payload"]))
                    if getattr(parsed, run_attr) == job_id and (
                        target_uuid == "unknown" or getattr(parsed, target_attr) == target_uuid
                    ):
                        return str(rec["receipt_id"])
                except ValueError:
                    continue
        return None
