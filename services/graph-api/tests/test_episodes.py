import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.episodes import build_paper_episodes
from app.extractor import extract_paper

FIXTURE = Path(__file__).parent / "fixtures" / "arxiv_sample.html"


def test_ordered_episodes_preserve_metadata_equations_and_warnings():
    paper = extract_paper(FIXTURE.read_text(encoding="utf-8"), "https://arxiv.org/html/1706.03762v7")
    episodes = build_paper_episodes(paper, workspace_id="研究 / lab")
    assert episodes == build_paper_episodes(paper, workspace_id="研究 / lab")
    metadata, section = [json.loads(e.body) for e in episodes]
    assert metadata["paper"]["version_published_at"] == "2023-08-02"
    assert metadata["section_order"] == [paper.sections[0].section_id]
    assert section["equations"] == [e.model_dump(mode="json") for e in paper.equations]


def test_missing_dates_require_an_explicit_aware_reference_time():
    paper = extract_paper("<html><title>Undated</title></html>", "https://arxiv.org/html/2402.08954")
    with pytest.raises(ValueError, match="source reference time"):
        build_paper_episodes(paper, workspace_id="a")
    with pytest.raises(ValueError, match="timezone"):
        build_paper_episodes(paper, workspace_id="a", reference_time=datetime(2024, 2, 14))
    assert build_paper_episodes(
        paper, workspace_id="a", reference_time=datetime(2024, 2, 14, tzinfo=UTC),
    )


def test_unsectioned_equations_are_not_dropped():
    paper = extract_paper(
        "<html><math id='E1' display='block' alttext='x=1'></math></html>",
        "https://arxiv.org/html/2402.08954v1",
    )
    episodes = build_paper_episodes(
        paper, workspace_id="a", reference_time=datetime(2024, 2, 14, tzinfo=UTC),
    )
    assert json.loads(episodes[0].body)["unsectioned_equations"][0]["anchor"] == "E1"


def test_invalid_section_mapping_is_rejected_before_graph_write():
    paper = extract_paper(FIXTURE.read_text(encoding="utf-8"), "https://arxiv.org/html/1706.03762v7")
    paper.sections[0].equation_ids = ["fabricated"]
    with pytest.raises(ValueError, match="mapping"):
        build_paper_episodes(paper, workspace_id="a")
