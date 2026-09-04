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
