"""Test-owned issuer: independent of the Graph API verifier implementation."""

import base64
import hashlib
import hmac
import json
import time
from uuid import uuid4

TEST_KEY = "service-test-secret-with-at-least-32-chars"
TEST_ACTOR = "user-1"
TEST_WORKSPACE = "ws_" + hashlib.sha256(TEST_ACTOR.encode()).hexdigest()[:48]


def sign_request(
    method,
    target,
    body=b"",
    *,
    actor=TEST_ACTOR,
    role=None,
    key=TEST_KEY,
    idempotency_key="",
    overrides=None,
):
    now = int(time.time())
    claims = {
        "v": 1,
        "iss": "fgl-web",
        "aud": "fgl-graph",
        "actor_id": actor,
        "actor_role": "researcher",
        "workspace_id": "ws_" + hashlib.sha256(actor.encode()).hexdigest()[:48],
        "service_role": role or ("research_read" if method == "GET" else "research_write"),
        "method": method,
        "target": target,
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "idempotency_key": idempotency_key,
        "iat": now,
        "exp": now + 60,
        "jti": str(uuid4()),
        **(overrides or {}),
    }
    payload = (
        base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )
    signature = (
        base64.urlsafe_b64encode(
            hmac.digest(key.encode(), b"fgl-service.v1\0" + payload.encode(), "sha256")
        )
        .decode()
        .rstrip("=")
    )
    headers = {
        "authorization": "Bearer fgl1." + payload + "." + signature,
        "content-type": "application/json",
    }
    if idempotency_key:
        headers["x-idempotency-key"] = idempotency_key
    return headers
