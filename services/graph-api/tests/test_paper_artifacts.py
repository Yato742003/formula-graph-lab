import hashlib

import httpx
import pytest

from app.paper_artifacts import (
    PERFORMER_COMMIT,
    PERFORMER_LICENSE_URL,
    PERFORMER_SOURCE_URL,
    ArtifactResolutionError,
    _download_verified,
)


@pytest.mark.asyncio
async def test_verified_download_accepts_only_exact_pinned_bytes():
    content = b"reviewed source bytes"
    requests = []

    def handler(request):
        requests.append(str(request.url))
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, content=content, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        actual = await _download_verified(
            client,
            url="https://raw.githubusercontent.com/example/repo/commit/source.py",
            expected_size=len(content),
            expected_sha256=hashlib.sha256(content).hexdigest(),
            max_bytes=64,
        )

    assert actual == content
    assert requests == ["https://raw.githubusercontent.com/example/repo/commit/source.py"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "content", "headers"),
    [
        (302, b"", {"location": "https://attacker.invalid/source.py"}),
        (200, b"modified source", {}),
        (200, b"too large", {"content-length": "9000"}),
        (200, b"compressed", {"content-encoding": "gzip"}),
    ],
)
async def test_verified_download_rejects_redirect_hash_and_size_mismatch(
    status_code, content, headers,
):
    def handler(request):
        return httpx.Response(status_code, content=content, headers=headers, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ArtifactResolutionError):
            await _download_verified(
                client,
                url="https://raw.githubusercontent.com/example/repo/commit/source.py",
                expected_size=7,
                expected_sha256="0" * 64,
                max_bytes=64,
            )


def test_performer_reference_is_fixed_to_author_repository_revision_and_paths():
    assert PERFORMER_COMMIT == "2260bcc3f9946ae8f07e39bc0ab4a98f2acfacd4"
    assert PERFORMER_SOURCE_URL.endswith(
        f"/{PERFORMER_COMMIT}/performer/fast_self_attention/fast_self_attention.py"
    )
    assert PERFORMER_LICENSE_URL.endswith(f"/{PERFORMER_COMMIT}/LICENSE")
    assert "master" not in PERFORMER_SOURCE_URL and "main" not in PERFORMER_SOURCE_URL
