from pathlib import Path

from app.episodes import build_paper_episode, paper_episode_uuid
from app.extractor import extract_paper


FIXTURE = Path(__file__).parent / "fixtures" / "arxiv_sample.html"


def test_extracts_source_bound_equations() -> None:
    paper = extract_paper(
        FIXTURE.read_text(encoding="utf-8"),
        "https://arxiv.org/html/1706.03762v7",
    )

    assert paper.paper_id == "1706.03762"
    assert paper.version == 7
    assert paper.title == "A Small Attention Paper"
    assert paper.authors == ["Ada Researcher", "Emmy Scientist"]
    assert len(paper.equations) == 2

    first = paper.equations[0]
    assert first.anchor == "S3.E1"
    assert first.equation_number == "(1)"
    assert first.section == "3 Attention"
    assert first.extraction_method == "tex_annotation"
    assert first.confidence == 0.99
    assert "scaled dot-product attention" in (first.preceding_text or "")
    assert "magnitude of the logits" in (first.following_text or "")

    second = paper.equations[1]
    assert second.extraction_method == "alttext"
    assert second.confidence == 0.92


def test_equation_and_episode_ids_are_deterministic() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    first = extract_paper(html, "https://arxiv.org/html/1706.03762v7")
    second = extract_paper(html, "https://arxiv.org/abs/1706.03762v7")

    assert [item.equation_id for item in first.equations] == [
        item.equation_id for item in second.equations
    ]
    assert paper_episode_uuid(first) == paper_episode_uuid(second)
    assert build_paper_episode(first) == build_paper_episode(second)


def test_falls_back_when_metadata_and_equation_ids_are_missing() -> None:
    paper = extract_paper(
        "<html><head><title>Minimal</title></head><body>"
        "<div class='ltx_equation'><math><mi>x</mi><mo>=</mo>"
        "<mn>1</mn></math></div></body></html>",
        "https://arxiv.org/html/2402.08954",
    )

    assert paper.title == "Minimal"
    assert paper.equations[0].anchor == "generated-equation-1"
    assert paper.equations[0].extraction_method == "mathml_text"


def test_ignores_inline_math_fragments() -> None:
    paper = extract_paper(
        "<html><body><p>Let <math id='inline-x'><mi>x</mi></math> be a scalar.</p>"
        "<div class='ltx_equation' id='E1'><math><mi>x</mi><mo>=</mo>"
        "<mn>1</mn></math></div></body></html>",
        "https://arxiv.org/html/2402.08954",
    )

    assert len(paper.equations) == 1
    assert paper.equations[0].anchor == "E1"


def test_prefers_numbered_equation_block_anchor_over_math_id() -> None:
    paper = extract_paper(
        "<html><body><table id='S3.E1' class='ltx_equation'>"
        "<tr class='ltx_equation'><td><math id='S3.E1.m1' display='block'>"
        "<mi>x</mi></math></td><td><span class='ltx_tag_equation'>(1)</span>"
        "</td></tr></table></body></html>",
        "https://arxiv.org/html/2402.08954",
    )

    assert paper.equations[0].anchor == "S3.E1"
    assert paper.equations[0].equation_number == "(1)"
