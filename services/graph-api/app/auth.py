from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from app.security import (
    MAX_PAPER_IMPORT_BYTES,
    MAX_REQUEST_BODY_BYTES,
    PayloadTooLargeError,
    check_payload_size,
    log_audit_event,
    workspace_rate_limiter,
)

HUMAN_ROLES = {"researcher", "reviewer", "admin"}
READ_PATHS = {
    "/v1/search",
    "/v1/graphs/snapshot",
    "/v1/extractions/preview",
    "/v1/formulas/parse",
    "/v1/formulas/compare",
}


@dataclass(frozen=True)
class ServiceActor:
    actor_id: str
    role: str


class ServiceClaims(BaseModel):
    """Fixed request-attestation protocol, not a browser/session JWT."""

    v: int = Field(ge=1, le=1)
    iss: Literal["fgl-web"]
    aud: Literal["fgl-graph"]
    actor_id: str = Field(pattern=r"^[\x21-\x7e]{1,200}$")
    actor_role: str = Field(pattern=r"^[a-z_]{1,32}$")
    workspace_id: str = Field(pattern=r"^ws_[a-f0-9]{48}$")
    service_role: Literal["graph_read", "graph_write", "research_read", "research_write"]
    method: Literal["GET", "POST"]
    target: str = Field(min_length=1, max_length=4096)
    body_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(max_length=200)
    iat: int
    exp: int
    jti: str = Field(
        pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$"
    )
    model_config = {"extra": "forbid", "strict": True, "frozen": True}


def personal_workspace_id(actor_id: str) -> str:
    # ponytail: personal workspaces only, ceiling: one owner, upgrade: add
    # server-owned membership grants when the Team workspace feature ships.
    return "ws_" + hashlib.sha256(actor_id.encode("utf-8")).hexdigest()[:48]


def _decode(value: str) -> bytes:
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", value):
        raise ValueError("Invalid encoding")
    result = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if base64.urlsafe_b64encode(result).decode().rstrip("=") != value:
        raise ValueError("Noncanonical encoding")
    return result


def _unique_object(pairs):
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("Duplicate claims")
    return value


async def consume_service_nonce(request: Request, nonce: str, expires_at: int) -> None:
    store = getattr(request.app.state, "evidence_store", None)
    try:
        if store is None:
            raise RuntimeError("missing store")
        accepted = await store.consume_service_nonce(nonce, expires_at)
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="Service replay protection unavailable."
        ) from exc
    if not accepted:
        raise HTTPException(status_code=401, detail="Service request already used.")


async def require_service_claims(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> ServiceClaims:
    expected = os.getenv("SERVICE_TOKEN", "")
    scheme, _, provided = (authorization or "").partition(" ")
    if not re.fullmatch(r"[\x21-\x7e]{32,256}", expected):
        raise HTTPException(
            status_code=503, detail="Graph API service authentication is not configured."
        )
    if scheme.lower() != "bearer" or not 1 <= len(provided) <= 8192:
        raise HTTPException(status_code=401, detail="Invalid service credentials.")
    if request.headers.getlist("authorization") != [authorization]:
        raise HTTPException(status_code=401, detail="Invalid service credentials.")
    max_body_bytes = (
        MAX_PAPER_IMPORT_BYTES
        if request.url.path == "/v1/imports"
        else MAX_REQUEST_BODY_BYTES
    )
    content_length_header = request.headers.get("content-length")
    if content_length_header:
        try:
            cl = int(content_length_header)
            check_payload_size(cl, max_bytes=max_body_bytes)
        except PayloadTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except ValueError:
            pass
    try:
        version, payload, signature = provided.split(".")
        if version != "fgl1":
            raise ValueError("Unknown protocol")
        wanted = hmac.digest(
            expected.encode("ascii"), b"fgl-service.v1\0" + payload.encode("ascii"), "sha256"
        )
        if not hmac.compare_digest(_decode(signature), wanted):
            raise ValueError("Invalid signature")
        claims = ServiceClaims.model_validate(
            json.loads(_decode(payload), object_pairs_hook=_unique_object)
        )
        now = int(time.time())
        if (
            not now < claims.exp
            or not claims.iat < claims.exp <= claims.iat + 60
            or claims.iat > now + 5
        ):
            raise ValueError("Expired or future request")
        target = request.scope["raw_path"].decode("ascii")
        query = request.scope["query_string"].decode("ascii")
        if query:
            target += "?" + query
        if claims.method != request.method or claims.target != target:
            raise ValueError("Request target changed")
        raw_body = await request.body()
        check_payload_size(len(raw_body), max_bytes=max_body_bytes)
        if claims.body_sha256 != hashlib.sha256(raw_body).hexdigest():
            raise ValueError("Request body changed")
        for header, value in (
            ("x-fgl-actor-id", claims.actor_id),
            ("x-fgl-actor-role", claims.actor_role),
            ("x-fgl-workspace-id", claims.workspace_id),
        ):
            supplied = request.headers.getlist(header)
            if supplied and supplied != [value]:
                raise ValueError("Context header changed")
        keys = request.headers.getlist("idempotency-key") + request.headers.getlist(
            "x-idempotency-key"
        )
        if len(keys) > 1 or (keys[0] if keys else "") != claims.idempotency_key:
            raise ValueError("Idempotency key changed")
    except PayloadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except (ValueError, TypeError, UnicodeError, ValidationError) as exc:
        log_audit_event(
            "tamper_detected"
            if any(k in str(exc).lower() for k in ("signature", "changed", "tampered"))
            else "auth_failed",
            actor_id=request.headers.get("x-fgl-actor-id"),
            workspace_id=request.headers.get("x-fgl-workspace-id"),
            details={"error": str(exc), "path": request.url.path},
        )
        raise HTTPException(status_code=401, detail="Invalid service credentials.") from exc
    if claims.actor_role not in HUMAN_ROLES:
        raise HTTPException(status_code=403, detail="A human service role is required.")
    if claims.workspace_id != personal_workspace_id(claims.actor_id):
        raise HTTPException(status_code=403, detail="Service workspace denied.")
    if request.method == "POST" and request.url.path in READ_PATHS:
        required_role = "graph_read"
    elif request.method == "POST" and request.url.path in {"/v1/imports", "/v1/contract-reviews"}:
        required_role = "graph_write"
    elif request.url.path.startswith("/v1/research/") and request.method in {"GET", "POST"}:
        required_role = "research_read" if request.method == "GET" else "research_write"
    else:
        raise HTTPException(status_code=403, detail="Service operation denied.")
    if claims.service_role != required_role:
        raise HTTPException(status_code=403, detail="Service scope denied.")
    allowed, retry_after = workspace_rate_limiter.check(claims.workspace_id)
    if not allowed:
        log_audit_event(
            "quota_exceeded",
            actor_id=claims.actor_id,
            workspace_id=claims.workspace_id,
            details={"retry_after": retry_after, "path": request.url.path},
        )
        raise HTTPException(
            status_code=429,
            detail="Workspace quota exceeded. Please slow down.",
            headers={"Retry-After": str(int(retry_after))},
        )
    # A signed request cannot select another workspace through its payload/query.
    try:
        body = json.loads(raw_body) if raw_body else {}
    except (ValueError, UnicodeError):
        body = {}  # The endpoint's strict request schema rejects malformed JSON.
    if (
        isinstance(body, dict)
        and "workspace_id" in body
        and body["workspace_id"] != claims.workspace_id
    ) or "workspace_id" in request.query_params:
        log_audit_event(
            "cross_workspace_denied",
            actor_id=claims.actor_id,
            workspace_id=claims.workspace_id,
            details={"path": request.url.path},
        )
        raise HTTPException(status_code=403, detail="Service workspace denied.")
    await consume_service_nonce(request, claims.jti, claims.exp)
    return claims


async def require_service_token(
    _claims: Annotated[ServiceClaims, Depends(require_service_claims)],
) -> None:
    pass


async def require_human_service_actor(
    claims: Annotated[ServiceClaims, Depends(require_service_claims)],
) -> ServiceActor:
    actor_id = claims.actor_id
    role = claims.actor_role
    if not actor_id or len(actor_id) > 200 or not actor_id.isascii():
        raise HTTPException(status_code=401, detail="A bounded service actor is required.")
    if role not in HUMAN_ROLES:
        raise HTTPException(status_code=403, detail="A human reviewer role is required.")
    return ServiceActor(actor_id=actor_id, role=role)


async def require_research_service_actor(
    claims: Annotated[ServiceClaims, Depends(require_service_claims)],
) -> tuple[ServiceActor, str]:
    actor = await require_human_service_actor(claims)
    workspace_id = claims.workspace_id
    if not workspace_id or len(workspace_id) > 200:
        raise HTTPException(status_code=400, detail="A bounded workspace ID is required.")
    return actor, workspace_id
