from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from typing import Annotated

from fastapi import Header, HTTPException


@dataclass(frozen=True)
class ServiceActor:
    actor_id: str
    role: str


def _require_valid_service_token(authorization: str | None) -> None:
    expected = os.getenv("SERVICE_TOKEN")
    is_production = os.getenv("APP_ENV", "development") == "production"
    if not expected:
        if is_production:
            raise HTTPException(
                status_code=503,
                detail="Graph API service authentication is not configured.",
            )
        return

    scheme, _, provided = (authorization or "").partition(" ")
    if (
        scheme.lower() != "bearer"
        or not provided
        or not secrets.compare_digest(provided, expected)
    ):
        raise HTTPException(status_code=401, detail="Invalid service credentials.")


async def require_service_token(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    _require_valid_service_token(authorization)


async def require_human_service_actor(
    authorization: Annotated[str | None, Header()] = None,
    x_fgl_actor_id: Annotated[str | None, Header()] = None,
    x_fgl_actor_role: Annotated[str | None, Header()] = None,
) -> ServiceActor:
    """Authenticate a service-carried human identity for review writes.

    The browser cannot choose these headers; the authenticated web route derives
    them from its platform identity. FGL-H6 will split the shared service token
    into narrower service roles before model-backed proposal endpoints open.
    """
    _require_valid_service_token(authorization)
    actor_id = (x_fgl_actor_id or "").strip()
    role = (x_fgl_actor_role or "").strip()
    if not actor_id or len(actor_id) > 200 or not actor_id.isascii():
        raise HTTPException(status_code=401, detail="A bounded service actor is required.")
    if role not in {"researcher", "reviewer", "admin"}:
        raise HTTPException(status_code=403, detail="A human reviewer role is required.")
    return ServiceActor(actor_id=actor_id, role=role)


async def require_research_service_actor(
    authorization: Annotated[str | None, Header()] = None,
    x_fgl_actor_id: Annotated[str | None, Header()] = None,
    x_fgl_actor_role: Annotated[str | None, Header()] = None,
    x_fgl_workspace_id: Annotated[str | None, Header()] = None,
) -> tuple[ServiceActor, str]:
    expected = os.getenv("SERVICE_TOKEN")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Graph API service authentication is not configured.",
        )
    scheme, _, provided = (authorization or "").partition(" ")
    if (
        scheme.lower() != "bearer"
        or not provided
        or not secrets.compare_digest(provided, expected)
    ):
        raise HTTPException(status_code=401, detail="Invalid service credentials.")
    actor_id = (x_fgl_actor_id or "").strip()
    role = (x_fgl_actor_role or "").strip()
    if not actor_id or len(actor_id) > 200 or not actor_id.isascii():
        raise HTTPException(status_code=401, detail="A bounded service actor is required.")
    if role not in {"researcher", "reviewer", "admin"}:
        raise HTTPException(status_code=403, detail="A human research role is required.")
    workspace_id = (x_fgl_workspace_id or "").strip()
    if not workspace_id or len(workspace_id) > 200:
        raise HTTPException(status_code=400, detail="A bounded workspace ID is required.")
    return ServiceActor(actor_id=actor_id, role=role), workspace_id
