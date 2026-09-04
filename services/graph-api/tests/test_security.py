import pytest

from app.security import UnsafePaperUrl, arxiv_identity, normalize_arxiv_html_url


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "https://arxiv.org/html/1706.03762",
            "https://arxiv.org/html/1706.03762",
        ),
        (
            "https://arxiv.org/abs/1706.03762v7?download=1#page",
            "https://arxiv.org/html/1706.03762v7",
        ),
        (
            "https://export.arxiv.org/html/2402.08954",
            "https://arxiv.org/html/2402.08954",
        ),
    ],
)
def test_normalizes_supported_arxiv_urls(value: str, expected: str) -> None:
    assert normalize_arxiv_html_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "http://arxiv.org/html/1706.03762",
        "https://evil.example/html/1706.03762",
        "https://arxiv.org.evil.example/html/1706.03762",
        "https://user@arxiv.org/html/1706.03762",
        "https://arxiv.org:443/html/1706.03762",
        "https://arxiv.org/pdf/1706.03762",
        "https://arxiv.org/html/../admin",
        "https://arxiv.org/html/1706%2e03762",
        "https://arxiv.org/html/hep-th/9901001",
    ],
)
def test_rejects_urls_outside_v1_policy(value: str) -> None:
    with pytest.raises(UnsafePaperUrl):
        normalize_arxiv_html_url(value)


def test_splits_paper_identity_and_version() -> None:
    assert arxiv_identity("https://arxiv.org/html/1706.03762v7") == (
        "1706.03762",
        7,
    )
    assert arxiv_identity("https://arxiv.org/html/2402.08954") == (
        "2402.08954",
        None,
    )
