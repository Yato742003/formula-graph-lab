"""H6: authenticated durable admission on the existing evidence database."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, Field

from app.sandbox import IMAGE_ID
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
    operation: Literal["checks:run"] = "checks:run"
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
    actor.require(envelope.operation, envelope.workspace_id)
    if envelope.actor_id != actor.identity:
        raise JobRejected("JOB_ACTOR_MISMATCH", 403)
    now = int(time.time()) if now is None else now
    if not envelope.issued_at <= now < envelope.expires_at <= envelope.issued_at + 120:
        raise JobRejected("JOB_EXPIRED")
    if (envelope.payload_hash != digest(payload) or envelope.image_id != image
            or envelope.reserved_ms != payload.get("timeout_ms")):
        raise JobRejected("JOB_INPUT_MISMATCH", 422)
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
        if not IMAGE_ID.fullmatch(image):
            raise JobRejected("UNPINNED_SANDBOX_IMAGE", 503)
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

    @staticmethod
    async def _lock(tx):
        await (await tx.run("MERGE (q:ResearchQueueLock {id:'admission.v1'}) "
                           "SET q.serial=coalesce(q.serial,0)+1")).consume()

    async def _admit(self, tx, envelope, now):
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
            "mac:$mac, state:'queued', issued_at:$issued, expires_at:$expiry, reserved_ms:$ms})",
            id=envelope.job_id, workspace=envelope.workspace_id, envelope=raw, mac=mac,
            issued=envelope.issued_at, expiry=envelope.expires_at, ms=envelope.reserved_ms,
        )).consume()
        return raw, mac, None, None

    async def claim(self, ticket: JobTicket, actor: WorkerPrincipal, payload, image):
        envelope = verify_envelope(ticket.envelope.model_dump_json(), ticket.mac, self.key,
                                   actor, payload, image)
        source = await self.store.equation_source(
            workspace_id=envelope.workspace_id, equation_uuid=envelope.target_uuid,
        )
        if digest(source.get("latex")) != envelope.source_hash:
            raise JobRejected("JOB_SOURCE_MISMATCH", 422)
        async with self.store.driver.session(database=self.store.database) as session:
            await session.execute_write(self._claim, ticket)

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
