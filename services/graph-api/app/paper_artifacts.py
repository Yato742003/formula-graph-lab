"""Byte-pinned source artifacts approved for isolated paper-code checks."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass

import httpx

from app.paper_artifact_manifest import (
    PERFORMER_COMMIT,
    PERFORMER_LICENSE_SHA256,
    PERFORMER_LICENSE_SIZE,
    PERFORMER_LICENSE_URL,
    PERFORMER_SOURCE_SHA256,
    PERFORMER_SOURCE_SIZE,
    PERFORMER_SOURCE_URL,
)

MAX_ARTIFACT_BYTES = 32 * 1024
MAX_LICENSE_BYTES = 16 * 1024
RESOLVE_TIMEOUT_SECONDS = 10


class ArtifactResolutionError(RuntimeError):
    """A pinned source or its license could not be verified."""


@dataclass(frozen=True)
class ResolvedPerformerArtifact:
    paper_id: str
    repository: str
    commit: str
    path: str
    source_sha256: str
    source: bytes
    license_id: str
    license_sha256: str
    license_text: bytes


async def _download_verified(
    client: httpx.AsyncClient,
    *,
    url: str,
    expected_size: int,
    expected_sha256: str,
    max_bytes: int,
) -> bytes:
    try:
        async with asyncio.timeout(RESOLVE_TIMEOUT_SECONDS):
            async with client.stream(
                "GET", url, headers={"Accept-Encoding": "identity"},
            ) as response:
                if response.is_redirect or response.history or str(response.url) != url:
                    raise ArtifactResolutionError("Pinned artifact redirected.")
                if response.status_code != 200:
                    raise ArtifactResolutionError("Pinned artifact is unavailable.")
                if response.headers.get("content-encoding", "identity").lower() not in {
                    "", "identity",
                }:
                    raise ArtifactResolutionError("Pinned artifact must be uncompressed.")
                declared = response.headers.get("content-length")
                if declared is not None:
                    if not declared.isascii() or not declared.isdigit():
                        raise ArtifactResolutionError("Pinned artifact size is invalid.")
                    if int(declared) > max_bytes:
                        raise ArtifactResolutionError("Pinned artifact exceeds its size limit.")

                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise ArtifactResolutionError("Pinned artifact exceeds its size limit.")
                    chunks.append(chunk)
    except ArtifactResolutionError:
        raise
    except (httpx.HTTPError, TimeoutError) as exc:
        raise ArtifactResolutionError("Pinned artifact could not be resolved.") from exc

    content = b"".join(chunks)
    if size != expected_size or hashlib.sha256(content).hexdigest() != expected_sha256:
        raise ArtifactResolutionError("Pinned artifact bytes failed verification.")
    return content


async def resolve_performer_artifact(
    *, client: httpx.AsyncClient | None = None,
) -> ResolvedPerformerArtifact:
    """Fetch only the author-linked Performer implementation and pinned license."""
    owns_client = client is None
    active_client = client or httpx.AsyncClient(
        timeout=httpx.Timeout(RESOLVE_TIMEOUT_SECONDS),
        follow_redirects=False,
        headers={"Accept": "application/octet-stream"},
    )
    try:
        source = await _download_verified(
            active_client,
            url=PERFORMER_SOURCE_URL,
            expected_size=PERFORMER_SOURCE_SIZE,
            expected_sha256=PERFORMER_SOURCE_SHA256,
            max_bytes=MAX_ARTIFACT_BYTES,
        )
        license_text = await _download_verified(
            active_client,
            url=PERFORMER_LICENSE_URL,
            expected_size=PERFORMER_LICENSE_SIZE,
            expected_sha256=PERFORMER_LICENSE_SHA256,
            max_bytes=MAX_LICENSE_BYTES,
        )
    finally:
        if owns_client:
            await active_client.aclose()
    return ResolvedPerformerArtifact(
        paper_id="arXiv:2009.14794v4",
        repository="google-research/google-research",
        commit=PERFORMER_COMMIT,
        path="performer/fast_self_attention/fast_self_attention.py",
        source_sha256=PERFORMER_SOURCE_SHA256,
        source=source,
        license_id="Apache-2.0",
        license_sha256=PERFORMER_LICENSE_SHA256,
        license_text=license_text,
    )
