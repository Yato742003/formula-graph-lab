from __future__ import annotations

import os
import secrets
from typing import Annotated

from fastapi import Header, HTTPException


async def require_service_token(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
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
