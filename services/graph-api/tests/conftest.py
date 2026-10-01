import os
import secrets
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request

from app.auth import require_service_claims
from app.main import app


@pytest.fixture
def legacy_phase5_service_auth(monkeypatch):
    """Explicitly preserve old semantic/API fixtures, not the signed-auth gate.

    New security tests do not use this fixture. There is NO legacy auth mode in
    application code. Only the old API-semantic tests replace the auth dependency.
    """
    monkeypatch.setenv("APP_ENV", "development")

    async def trusted_fixture_context(request: Request):
        expected = os.getenv("SERVICE_TOKEN", "")
        if not expected and (
            os.getenv("APP_ENV") == "production" or request.url.path.startswith("/v1/research/")
        ):
            raise HTTPException(503, "Graph API service authentication is not configured.")
        scheme, _, provided = request.headers.get("authorization", "").partition(" ")
        if expected and (
            scheme.lower() != "bearer"
            or not provided.isascii()
            or not secrets.compare_digest(provided, expected)
        ):
            raise HTTPException(401, "Invalid service credentials.")
        return SimpleNamespace(
            actor_id=request.headers.get("x-fgl-actor-id", "").strip(),
            actor_role=request.headers.get("x-fgl-actor-role", "").strip(),
            workspace_id=request.headers.get("x-fgl-workspace-id", "").strip(),
        )

    app.dependency_overrides[require_service_claims] = trusted_fixture_context
    yield
    app.dependency_overrides.pop(require_service_claims, None)
