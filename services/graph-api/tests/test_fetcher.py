import asyncio

import httpx
import pytest

from app.fetcher import MAX_HTML_BYTES, PaperFetchError, fetch_paper_html
from app.security import UnsafePaperUrl


@pytest.mark.asyncio
async def test_fetches_bounded_html() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://arxiv.org/html/1706.03762"
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=b"<html><body>paper</body></html>",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        body, final_url = await fetch_paper_html(
            "https://arxiv.org/abs/1706.03762",
            client=client,
        )

    assert "paper" in body
    assert final_url == "https://arxiv.org/html/1706.03762"


@pytest.mark.asyncio
async def test_rejects_cross_host_redirect() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.example/paper"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(UnsafePaperUrl):
            await fetch_paper_html(
                "https://arxiv.org/html/1706.03762",
                client=client,
            )


@pytest.mark.asyncio
async def test_rejects_non_html_and_large_sources() -> None:
    responses = iter(
        [
            httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"x"),
            httpx.Response(
                200,
                headers={
                    "content-type": "text/html",
                    "content-length": str(MAX_HTML_BYTES + 1),
                },
                content=b"x",
            ),
        ]
    )

    async def handler(_: httpx.Request) -> httpx.Response:
        return next(responses)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PaperFetchError, match="did not return HTML"):
            await fetch_paper_html("https://arxiv.org/html/1706.03762", client=client)
        with pytest.raises(PaperFetchError, match="5 MiB"):
            await fetch_paper_html("https://arxiv.org/html/1706.03762", client=client)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,headers,message", [
    (404, {}, "HTTP 404"),
    (200, {"content-type": "text/html-unsafe"}, "did not return HTML"),
    (200, {"content-type": "text/html", "content-length": "-1"}, "content length"),
    (200, {"content-type": "text/html", "content-length": "unknown"}, "content length"),
    (302, {}, "empty redirect"),
])
async def test_rejects_invalid_upstream_responses(status, headers, message) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(status, headers=headers))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(PaperFetchError, match=message):
            await fetch_paper_html("https://arxiv.org/html/1706.03762", client=client)


@pytest.mark.asyncio
async def test_follows_bounded_safe_redirects_and_rejects_loop() -> None:
    calls = []

    def redirect(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "/html/1706.03762v7"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(redirect)) as client:
        with pytest.raises(PaperFetchError, match="too many"):
            await fetch_paper_html("https://arxiv.org/html/1706.03762", client=client)
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_streaming_size_cap_without_content_length() -> None:
    class OversizedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"x" * (MAX_HTML_BYTES // 2)
            yield b"x" * (MAX_HTML_BYTES // 2 + 1)

    def response(_):
        return httpx.Response(200, headers={"content-type": "text/html"},
                              stream=OversizedStream())

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        with pytest.raises(PaperFetchError, match="5 MiB"):
            await fetch_paper_html("https://arxiv.org/html/1706.03762", client=client)


@pytest.mark.asyncio
async def test_total_deadline_covers_slow_body(monkeypatch) -> None:
    monkeypatch.setattr("app.fetcher.FETCH_TIMEOUT_SECONDS", 0.02)

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"<html>"
            await asyncio.sleep(0.2)
            yield b"</html>"

    def response(_):
        return httpx.Response(200, headers={"content-type": "text/html"}, stream=SlowStream())

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        with pytest.raises(PaperFetchError, match="timed out"):
            await fetch_paper_html("https://arxiv.org/html/1706.03762", client=client)


@pytest.mark.asyncio
async def test_http_client_timeout_is_a_typed_error() -> None:
    def response(request):
        raise httpx.ReadTimeout("timeout", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        with pytest.raises(PaperFetchError, match="timed out"):
            await fetch_paper_html("https://arxiv.org/html/1706.03762", client=client)
