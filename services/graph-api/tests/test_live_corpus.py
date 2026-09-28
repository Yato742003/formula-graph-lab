from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest
from lxml import html

from app.extractor import extract_paper
from app.fetcher import fetch_paper_html

CORPUS_PATH = Path(__file__).with_name("live_corpus.json")
ARXIV_ID = re.compile(r"^\d{4}\.\d{4,5}$")


def _corpus() -> list[dict[str, object]]:
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


def test_live_corpus_manifest_is_representative_and_deterministic() -> None:
    corpus = _corpus()
    ids = [str(case["paper_id"]) for case in corpus]

    assert len(corpus) >= 10
    assert len(ids) == len(set(ids))
    assert all(ARXIV_ID.fullmatch(paper_id) for paper_id in ids)
    assert all(str(case["title_contains"]).strip() for case in corpus)
    assert sum(int(case["min_display_equations"]) > 0 for case in corpus) >= 10


@pytest.mark.live
@pytest.mark.asyncio
@pytest.mark.parametrize("case", _corpus(), ids=lambda case: str(case["paper_id"]))
async def test_public_arxiv_html_corpus(case: dict[str, object]) -> None:
    await asyncio.sleep(3)  # Serial tests respect arXiv's request cadence.
    paper_id = str(case["paper_id"])
    html_text, final_url = await fetch_paper_html(f"https://arxiv.org/html/{paper_id}")
    first = extract_paper(html_text, final_url)
    second = extract_paper(html_text, final_url)

    assert str(case["title_contains"]).casefold() in first.title.casefold()
    assert len(first.equations) >= int(case["min_display_equations"])
    if "max_display_equations" in case:
        assert len(first.equations) <= int(case["max_display_equations"])
    assert first.version is not None
    assert first.version_published_at is not None
    assert first.authors
    assert first.sections
    assert [equation.equation_id for equation in first.equations] == [
        equation.equation_id for equation in second.equations
    ]
    assert first.source_sha256 == second.source_sha256
    assert first.model_dump() == second.model_dump()
    document = html.document_fromstring(html_text)
    source_ids = set(document.xpath("//*[@id]/@id"))
    assert len({e.equation_id for e in first.equations}) == len(first.equations)
    assert all(e.anchor in source_ids for e in first.equations if e.anchor_is_source)
    assert all(s.anchor in source_ids for s in first.sections if s.anchor_is_source)
    assert {e.equation_id for e in first.equations if e.section_id} == {
        eid for section in first.sections for eid in section.equation_ids
    }
    print(json.dumps({
        "paper_id": paper_id, "version": first.version, "title": first.title,
        "version_date": str(first.version_published_at), "authors": len(first.authors),
        "sections": len(first.sections), "equations": len(first.equations),
        "source_sha256": first.source_sha256,
    }))
