from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit


class UnsafePaperUrl(ValueError):
    """Raised when a user-provided paper URL is outside the ingestion policy."""


_ALLOWED_HOSTS = {"arxiv.org", "export.arxiv.org"}
_MODERN_ARXIV_ID = re.compile(r"^(?P<id>\d{4}\.\d{4,5})(?P<version>v\d+)?$")


def normalize_arxiv_html_url(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        raise UnsafePaperUrl("Paper URL is required.")
    if any(character in candidate for character in ("\\", "\r", "\n", "\t", "%")):
        raise UnsafePaperUrl("Encoded or ambiguous URLs are not accepted.")

    try:
        parsed = urlsplit(candidate)
    except ValueError as exc:
        raise UnsafePaperUrl("Paper URL is malformed.") from exc

    if parsed.scheme != "https":
        raise UnsafePaperUrl("Only HTTPS paper URLs are accepted.")
    if parsed.hostname not in _ALLOWED_HOSTS:
        raise UnsafePaperUrl("Only arxiv.org HTML papers are supported in V1.")
    if parsed.username or parsed.password:
        raise UnsafePaperUrl("Credentials are not allowed in paper URLs.")
    try:
        if parsed.port is not None:
            raise UnsafePaperUrl("Custom ports are not allowed in paper URLs.")
    except ValueError as exc:
        raise UnsafePaperUrl("Paper URL contains an invalid port.") from exc

    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) != 2 or path_parts[0] not in {"abs", "html"}:
        raise UnsafePaperUrl("Use an arXiv /html/<paper-id> or /abs/<paper-id> URL.")

    match = _MODERN_ARXIV_ID.fullmatch(path_parts[1])
    if match is None:
        raise UnsafePaperUrl("Only modern numeric arXiv identifiers are supported in V1.")

    canonical_id = match.group("id") + (match.group("version") or "")
    return urlunsplit(("https", "arxiv.org", f"/html/{canonical_id}", "", ""))


def arxiv_identity(canonical_url: str) -> tuple[str, int | None]:
    paper_token = urlsplit(canonical_url).path.removeprefix("/html/")
    version_match = re.search(r"v(?P<version>\d+)$", paper_token)
    if version_match is None:
        return paper_token, None
    return paper_token[: version_match.start()], int(version_match.group("version"))
