"""Server-owned service capabilities and workspace grants for research workers."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Header, HTTPException
from pydantic import BaseModel, Field, SecretStr, TypeAdapter, ValidationError

WorkerRole = Literal["proposer", "compiler", "verifier", "experiment", "evolution"]
ROLE_SCOPES: dict[str, frozenset[str]] = {
    "proposer": frozenset({"proposals:create"}),
    "compiler": frozenset({"candidates:compile"}),
    "verifier": frozenset({"checks:run"}),
    "experiment": frozenset({"experiments:run"}),
    "evolution": frozenset({"evolution:select"}),
}


class WorkerCredential(BaseModel):
    identity: str = Field(min_length=1, max_length=200, pattern=r"^[a-zA-Z0-9_-]+$")
    token: SecretStr = Field(min_length=32, max_length=256)
    role: WorkerRole
    workspaces: tuple[str, ...] = Field(min_length=1, max_length=100)
    model_config = {"extra": "forbid", "frozen": True}


@dataclass(frozen=True)
class WorkerPrincipal:
    identity: str
    role: WorkerRole
    workspaces: frozenset[str]

    def require(self, scope: str, workspace_id: str) -> None:
        if scope not in ROLE_SCOPES[self.role] or workspace_id not in self.workspaces:
            raise HTTPException(status_code=403, detail="Worker scope or workspace denied.")


def configured_credentials() -> list[WorkerCredential]:
    raw = os.getenv("FGL_WORKER_IDENTITIES", "")
    try:
        if not raw or len(raw) > 65_536:
            raise ValueError("missing or oversized configuration")
        entries = TypeAdapter(list[WorkerCredential]).validate_json(raw)
        if not 1 <= len(entries) <= 50:
            raise ValueError("invalid identity count")
        names = set()
        tokens = set()
        gateway = os.getenv("SERVICE_TOKEN", "")
        for entry in entries:
            token = entry.token.get_secret_value()
            if (entry.identity in names or token in tokens
                    or token == gateway or not token.isascii()):
                raise ValueError("identity/token collision")
            if any(not w.strip() or len(w) > 200 or w == "*" for w in entry.workspaces):
                raise ValueError("invalid workspace grant")
            if len(set(entry.workspaces)) != len(entry.workspaces):
                raise ValueError("duplicate workspace grant")
            names.add(entry.identity)
            tokens.add(token)
        return entries
    except (ValidationError, ValueError) as exc:
        raise HTTPException(
            status_code=503, detail="Worker authentication is not configured.",
        ) from exc


async def require_worker(
    authorization: Annotated[str | None, Header()] = None,
) -> WorkerPrincipal:
    entries = configured_credentials()
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.isascii() or not 32 <= len(token) <= 256:
        raise HTTPException(status_code=401, detail="Invalid worker credentials.")
    matched = None
    for entry in entries:
        if secrets.compare_digest(token, entry.token.get_secret_value()):
            matched = entry
    if matched is None:
        raise HTTPException(status_code=401, detail="Invalid worker credentials.")
    return WorkerPrincipal(matched.identity, matched.role, frozenset(matched.workspaces))


async def require_research_checks_enabled() -> None:
    if os.getenv("FGL_ENABLE_RESEARCH_CHECKS", "false") != "true":
        raise HTTPException(status_code=503, detail="Research check execution is disabled.")
