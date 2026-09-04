from __future__ import annotations

from urllib.parse import urljoin

import httpx

from app.security import normalize_arxiv_html_url


class PaperFetchError(RuntimeError):
    """Raised when a validated paper cannot be fetched safely."""


MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 2


async def fetch_paper_html(
    paper_url: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> tuple[str, str]:
    canonical_url = normalize_arxiv_html_url(paper_url)
    owns_client = client is None
    active_client = client or httpx.AsyncClient(
        timeout=httpx.Timeout(10),
        follow_redirects=False,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "FormulaGraphLab/0.1 (+https://formula-graph-lab.right-betta-4544.chatgpt.site)",
        },
    )

    try:
        current_url = canonical_url
        for redirect_count in range(MAX_REDIRECTS + 1):
            try:
                async with active_client.stream("GET", current_url) as response:
                    if response.is_redirect:
                        if redirect_count == MAX_REDIRECTS:
                            raise PaperFetchError("Paper source redirected too many times.")
                        location = response.headers.get("location")
                        if not location:
                            raise PaperFetchError("Paper source returned an empty redirect.")
                        current_url = normalize_arxiv_html_url(urljoin(current_url, location))
                        continue

                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        raise PaperFetchError(
                            f"Paper source returned HTTP {response.status_code}."
                        ) from exc

                    content_type = response.headers.get("content-type", "").lower()
                    if not (
                        content_type.startswith("text/html")
                        or content_type.startswith("application/xhtml+xml")
                    ):
                        raise PaperFetchError("Paper source did not return HTML.")

                    declared_size = response.headers.get("content-length")
                    if declared_size and int(declared_size) > MAX_HTML_BYTES:
                        raise PaperFetchError("Paper HTML exceeds the 5 MiB limit.")

                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_HTML_BYTES:
                            raise PaperFetchError("Paper HTML exceeds the 5 MiB limit.")
                        chunks.append(chunk)

                    encoding = response.encoding or "utf-8"
                    return b"".join(chunks).decode(encoding, errors="replace"), current_url
            except httpx.TimeoutException as exc:
                raise PaperFetchError("Paper source timed out.") from exc
            except httpx.RequestError as exc:
                raise PaperFetchError("Paper source could not be reached.") from exc
    finally:
        if owns_client:
            await active_client.aclose()

    raise PaperFetchError("Paper source could not be fetched.")
