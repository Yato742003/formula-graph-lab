"""H6: authenticated durable admission on the existing evidence database."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, Field

from app.episodes import workspace_group_id
from app.numerical_verification import (
    NumericalFixtureReceipt,
    NumericalFixtureReceiptResponse,
    build_numerical_suite_input,
)
from app.problem_spec import ProblemSpecSnapshot, definition_hash
from app.research_case import (
    ImplementationBindingReceipt,
    ResearchCaseReceipt,
    ResearchCaseReceiptResponse,
    make_implementation_binding,
    phase_budget_ms,
)
from app.research_compiler import CompiledCandidate, _verify_candidate_identity
from app.sandbox import IMAGE_ID
from app.security import log_audit_event
from app.worker_auth import WorkerPrincipal, configured_credentials


class JobRejected(ValueError):
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.status = status


class JobEnvelope(BaseModel):
    schema_version: Literal["research-job.v1"] = "research-job.v1"
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
    issued_at: int = Field(ge=0)
    expires_at: int = Field(ge=0)
    reserved_ms: int = Field(ge=10, le=30_000)
    model_config = {"extra": "forbid", "frozen": True, "strict": True}


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
    if envelope.operation == "numerical_fixture:run":
        validate_numerical_fixture_payload(payload)
        if (
            envelope.target_uuid != payload["candidate_id"]
            or envelope.source_hash != digest([payload["candidate_id"], payload["candidate_hash"]])
        ):
            raise JobRejected("JOB_SOURCE_MISMATCH", 422)
    elif envelope.operation == "research_case:run":
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

    async def admit(self, actor: WorkerPrincipal, *, workspace_id: str, target_uuid: str,
                    idempotency_key: str, request_hash: str, payload: dict[str, object],
                    image: str) -> JobTicket:
        actor.require("checks:run", workspace_id)
        if (set(payload) != {"formula_a", "formula_b", "format", "timeout_ms"}
                or payload.get("format") != "latex"
                or any(not isinstance(payload.get(k), str) or not 1 <= len(payload[k]) <= 20_000
                       for k in ("formula_a", "formula_b"))
                or type(payload.get("timeout_ms")) is not int
                or not 10 <= payload["timeout_ms"] <= 30_000):
            raise JobRejected("INVALID_JOB_INPUT", 422)
        if not IMAGE_ID.fullmatch(image):
            raise JobRejected("UNPINNED_SANDBOX_IMAGE", 503)
        source = await self.store.equation_source(
            workspace_id=workspace_id, equation_uuid=target_uuid,
        )
        if source.get("latex") != payload.get("formula_a"):
            raise JobRejected("JOB_SOURCE_MISMATCH", 422)
        now = int(time.time())
        job_id = str(uuid5(NAMESPACE_URL, json.dumps([workspace_id, target_uuid, idempotency_key])))
        envelope = JobEnvelope(
            job_id=job_id,
            workspace_id=workspace_id, target_uuid=target_uuid, actor_id=actor.identity,
            request_hash=request_hash, source_hash=digest(payload["formula_a"]),
            payload_hash=digest(payload), image_id=image, issued_at=now, expires_at=now + 120,
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
            job_id=job_id,
            workspace_id=workspace_id,
            target_uuid=candidate_id,
            actor_id=actor.identity,
            operation="numerical_fixture:run",
            request_hash=digest([payload, image, reserved_ms]),
            source_hash=digest([candidate_id, candidate.content_hash]),
            payload_hash=digest(payload),
            image_id=image,
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
            job_id=job_id,
            workspace_id=workspace_id,
            target_uuid=candidate_id,
            actor_id=actor.identity,
            operation="research_case:run",
            request_hash=digest([payload, binding.execution_image, reserved_ms]),
            source_hash=digest([candidate_id, binding.candidate_hash, binding.binding_hash]),
            payload_hash=digest(payload),
            image_id=binding.execution_image,
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
            return payload, None
        if not hmac.compare_digest(
            self._result_mac(job_id, row["result"]), row["result_mac"] or "",
        ):
            raise JobRejected("INVALID_RESULT_SIGNATURE", 403)
        saved = ResearchCaseReceiptResponse.model_validate_json(row["result"])
        if (saved.result.run_id != job_id or saved.result.actor_id != actor.identity
                or saved.result.candidate_id != candidate_id
                or saved.result.evaluation_role != role or saved.result.evolution_id != evolution_id
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
                envelope.workspace_id, payload, image
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
    ) -> ImplementationBindingReceipt:
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
        binding = make_implementation_binding(
            candidate,
            spec,
            parent_candidate=parent,
            execution_image=image,
            now=datetime.fromisoformat(str(payload["binding_created_at"]).replace("Z", "+00:00")),
        )
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

    async def finish(self, ticket: JobTicket, result: str):
        async with self.store.driver.session(database=self.store.database) as session:
            await session.execute_write(self._finish, ticket, result)

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
            or result.run_id != envelope.job_id
            or result.workspace_id != envelope.workspace_id
            or result.candidate_id != envelope.target_uuid
            or result.actor_id != envelope.actor_id
        ):
            raise JobRejected("JOB_RESULT_MISMATCH", 422)
        async with self.store.driver.session(database=self.store.database) as session:
            return await session.execute_write(
                self._finish_numerical_fixture,
                ticket,
                research_store,
                idempotency_key,
                result,
            )

    async def _finish_numerical_fixture(
        self,
        tx,
        ticket: JobTicket,
        research_store,
        idempotency_key: str,
        result: NumericalFixtureReceipt,
    ) -> NumericalFixtureReceiptResponse:
        await self._lock(tx)
        envelope = ticket.envelope
        row = await (await tx.run(
            "MATCH (j:ResearchJob {id:$id, state:'running', envelope:$envelope, mac:$mac}) "
            "WHERE j.expires_at>$now RETURN j.id AS id",
            id=envelope.job_id,
            envelope=envelope.model_dump_json(),
            mac=ticket.mac,
            now=int(time.time()),
        )).single()
        if row is None:
            raise JobRejected("JOB_RESULT_REQUIRES_RECONCILIATION")

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
            "j.result_mac=$result_mac RETURN j.id AS id",
            id=envelope.job_id,
            envelope=envelope.model_dump_json(),
            mac=ticket.mac,
            now=int(time.time()),
            result=result_json,
            result_mac=self._result_mac(envelope.job_id, result_json),
        )).single()
        if updated is None:
            raise JobRejected("JOB_RESULT_REQUIRES_RECONCILIATION")
        return appended

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
            return await session.execute_write(
                self._finish_research_case,
                ticket,
                research_store,
                idempotency_key,
                binding,
                result,
            )

    async def _finish_research_case(
        self,
        tx,
        ticket: JobTicket,
        research_store,
        idempotency_key: str,
        binding: ImplementationBindingReceipt,
        result: ResearchCaseReceipt,
    ) -> ResearchCaseReceiptResponse:
        await self._lock(tx)
        envelope = ticket.envelope
        row = await (await tx.run(
            "MATCH (j:ResearchJob {id:$id, state:'running', envelope:$envelope, mac:$mac}) "
            "WHERE j.expires_at>$now RETURN j.id AS id",
            id=envelope.job_id,
            envelope=envelope.model_dump_json(),
            mac=ticket.mac,
            now=int(time.time()),
        )).single()
        if row is None:
            raise JobRejected("JOB_RESULT_REQUIRES_RECONCILIATION")
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
            "j.result_mac=$result_mac RETURN j.id AS id",
            id=envelope.job_id,
            envelope=envelope.model_dump_json(),
            mac=ticket.mac,
            now=int(time.time()),
            result=result_json,
            result_mac=self._result_mac(envelope.job_id, result_json),
        )).single()
        if updated is None:
            raise JobRejected("JOB_RESULT_REQUIRES_RECONCILIATION")
        return appended

    async def _finish(self, tx, ticket, result):
        await self._lock(tx)
        row = await (await tx.run(
            "MATCH (j:ResearchJob {id:$id, state:'running', envelope:$envelope, mac:$mac}) "
            "WHERE j.expires_at>$now SET j.state='finished', j.result=$result, "
            "j.result_mac=$result_mac RETURN j.id AS id",
            id=ticket.envelope.job_id, envelope=ticket.envelope.model_dump_json(),
            mac=ticket.mac, now=int(time.time()), result=result,
            result_mac=self._result_mac(ticket.envelope.job_id, result),
        )).single()
        if row is None:
            raise JobRejected("JOB_RESULT_REQUIRES_RECONCILIATION")

    async def recover_stuck_jobs(self, now: int | None = None) -> dict[str, object]:
        """Recovers jobs that crashed or timed out while queued or running."""
        # ponytail: stuck-job recovery runs on-demand during ops health sweep,
        # ceiling: scans active jobs in single transaction,
        # upgrade: separate background sweeper cron in Phase 7.
        current_now = int(time.time()) if now is None else now
        if not getattr(self, "store", None) or not getattr(self.store, "driver", None):
            return {
                "recovered_count": 0,
                "recovered_job_ids": [],
                "recovered_at": datetime.now(UTC).isoformat(),
            }
        async with self.store.driver.session(database=self.store.database) as session:
            return await session.execute_write(self._recover_stuck_jobs, current_now)

    async def _recover_stuck_jobs(self, tx, now: int) -> dict[str, object]:
        await self._lock(tx)
        cursor = await tx.run(
            "MATCH (j:ResearchJob) "
            "WHERE j.state IN ['queued', 'running'] AND j.expires_at <= $now "
            "SET j.state = 'failed', j.failure_reason = 'JOB_EXPIRED_OR_CRASHED', "
            "j.recovered_at = $now "
            "RETURN j.id AS id, j.workspace AS workspace, j.state AS state",
            now=now,
        )
        records = [record async for record in cursor]
        recovered_ids = [r["id"] for r in records]
        for r in records:
            log_audit_event(
                "job_recovered",
                workspace_id=r.get("workspace"),
                details={"job_id": r.get("id"), "reason": "JOB_EXPIRED_OR_CRASHED"},
            )
        return {
            "recovered_count": len(recovered_ids),
            "recovered_job_ids": recovered_ids,
            "recovered_at": datetime.now(UTC).isoformat(),
        }

    async def job_health_metrics(self, now: int | None = None) -> dict[str, object]:
        """Provides job metrics for operations and health monitoring."""
        current_now = int(time.time()) if now is None else now
        if not getattr(self, "store", None) or not getattr(self.store, "driver", None):
            return {
                "total": 0,
                "active_queued": 0,
                "active_running": 0,
                "finished": 0,
                "failed": 0,
                "stuck": 0,
            }
        async with self.store.driver.session(database=self.store.database) as session:
            cursor = await session.run(
                "MATCH (j:ResearchJob) "
                "RETURN count(j) AS total, "
                "sum(CASE WHEN j.state = 'queued' AND j.expires_at > $now "
                "THEN 1 ELSE 0 END) AS active_queued, "
                "sum(CASE WHEN j.state = 'running' AND j.expires_at > $now "
                "THEN 1 ELSE 0 END) AS active_running, "
                "sum(CASE WHEN j.state = 'finished' THEN 1 ELSE 0 END) AS finished, "
                "sum(CASE WHEN j.state = 'failed' THEN 1 ELSE 0 END) AS failed, "
                "sum(CASE WHEN j.state IN ['queued', 'running'] AND j.expires_at <= $now "
                "THEN 1 ELSE 0 END) AS stuck",
                now=current_now,
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
                }
            return {
                "total": int(record["total"] or 0),
                "active_queued": int(record["active_queued"] or 0),
                "active_running": int(record["active_running"] or 0),
                "finished": int(record["finished"] or 0),
                "failed": int(record["failed"] or 0),
                "stuck": int(record["stuck"] or 0),
            }
