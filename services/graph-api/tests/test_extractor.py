from datetime import date
from pathlib import Path

import pytest

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
    assert paper.version_published_at == date(2023, 8, 2)
    assert len(paper.sections) == 1
    assert paper.sections[0].anchor == "S3"
    assert paper.sections[0].order == 1
    assert len(paper.equations) == 2

    first = paper.equations[0]
    assert first.anchor == "S3.E1"
    assert first.equation_number == "(1)"
    assert first.section == "3 Attention"
    assert first.section_id == paper.sections[0].section_id
    assert first.extraction_method == "tex_annotation"
    assert first.confidence == 0.99
    assert "scaled dot-product attention" in (first.preceding_text or "")
    assert "magnitude of the logits" in (first.following_text or "")

    second = paper.equations[1]
    assert second.extraction_method == "alttext"
    assert second.confidence == 0.92
    assert paper.sections[0].equation_ids == [first.equation_id, second.equation_id]


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
    assert paper.equations[0].anchor_is_source is False
    assert paper.equations[0].extraction_method == "mathml_text"
    assert paper.version_published_at is None


def test_resolves_unversioned_url_and_dom_authors_from_watermark() -> None:
    paper = extract_paper(
        "<html><head><title>DOM metadata</title></head><body>"
        "<div id='watermark-tr'>arXiv:2402.08954v3 [cs.DL] 19 Mar 2024</div>"
        "<span class='ltx_creator ltx_role_author'><span class='ltx_personname'>"
        "Ada Lovelace<span class='ltx_note'>1 footnote</span></span></span>"
        "</body></html>",
        "https://arxiv.org/html/2402.08954",
    )

    assert paper.version == 3
    assert paper.version_published_at == date(2024, 3, 19)
    assert paper.authors == ["Ada Lovelace"]


def test_rejects_watermark_from_another_paper_or_version() -> None:
    with pytest.raises(ValueError, match="watermark"):
        extract_paper(
            "<html><body><div id='watermark-tr'>"
            "arXiv:2402.00001v1 [cs.AI] 01 Feb 2024"
            "</div></body></html>",
            "https://arxiv.org/html/2402.08954v1",
        )

    with pytest.raises(ValueError, match="version"):
        extract_paper(
            "<html><body><div id='watermark-tr'>"
            "arXiv:2402.08954v2 [cs.AI] 02 Feb 2024"
            "</div></body></html>",
            "https://arxiv.org/html/2402.08954v1",
        )


def test_ignores_inline_math_fragments() -> None:
    paper = extract_paper(
        "<html><body><p>Let <math id='inline-x'><mi>x</mi></math> be a scalar.</p>"
        "<div class='ltx_equation' id='E1'><math><mi>x</mi><mo>=</mo>"
        "<mn>1</mn></math></div></body></html>",
        "https://arxiv.org/html/2402.08954",
    )

    assert len(paper.equations) == 1
    assert paper.equations[0].anchor == "E1"


def test_joins_alignment_cells_and_keeps_each_numbered_equation_separate() -> None:
    paper = extract_paper(
        "<html><body><section id='S2'><h2>2 Method</h2>"
        "<table id='S2.EG1' class='ltx_equationgroup'>"
        "<tbody id='S2.E1'><tr class='ltx_equation'>"
        "<td><math id='S2.E1.m1' alttext='f(x)' display='inline'></math></td>"
        "<td><math id='S2.E1.m2' alttext='= x' display='inline'></math></td>"
        "<td><span class='ltx_tag_equation'>(1)</span></td></tr></tbody>"
        "<tbody id='S2.E2'><tr class='ltx_equation'>"
        "<td><math alttext='g(x)' display='inline'></math></td>"
        "<td><math alttext='= 1' display='inline'></math></td>"
        "<td><span class='ltx_tag_equation'>(2)</span></td></tr></tbody>"
        "</table></section></body></html>",
        "https://arxiv.org/html/2402.08954v1",
    )

    assert [(e.anchor, e.equation_number, e.latex) for e in paper.equations] == [
        ("S2.E1", "(1)", "f(x) = x"), ("S2.E2", "(2)", "g(x) = 1"),
    ]
    assert paper.equations[0].source_fragments == ["f(x)", "= x"]
    assert paper.equations[0].extraction_method == "assembled_tex"
    assert paper.sections[0].anchor == "S2"


def test_nested_sections_return_to_parent_and_keep_sections_without_equations() -> None:
    paper = extract_paper(
        "<article class='ltx_document'><h1 class='ltx_title_document'>Paper</h1>"
        "<section id='S1'><h2>1 Introduction</h2><p>Motivation.</p></section>"
        "<section id='S2'><h2>2 Method</h2><p>Parent context.</p>"
        "<section id='S2.1'><h3>2.1 Child</h3><p>Child context.</p>"
        "<math id='child' display='block' alttext='x=1'></math></section>"
        "<p>Parent conclusion.</p><math id='parent' display='block' alttext='y=2'></math>"
        "</section></article>",
        "https://arxiv.org/html/2402.08954v1",
    )

    assert [s.anchor for s in paper.sections] == ["S1", "S2", "S2.1"]
    assert [s.order for s in paper.sections] == [1, 2, 3]
    assert paper.sections[0].equation_ids == []
    assert paper.sections[2].parent_section_id == paper.sections[1].section_id
    assert paper.sections[1].text == "Parent context.\n\nParent conclusion."
    assert [e.section for e in paper.equations] == ["2.1 Child", "2 Method"]


def test_preserves_long_latex_and_comment_newlines() -> None:
    latex = "x % comment\n= " + "a+" * 2500 + "b"
    paper = extract_paper(
        "<html><body><math display='block'><semantics><mi>x</mi>"
        "<annotation encoding='application/x-tex'>" + latex +
        "</annotation></semantics></math></body></html>",
        "https://arxiv.org/html/2402.08954v1",
    )
    assert paper.equations[0].latex == latex
    assert paper.equations[0].source_fragments == [latex]


def test_metadata_does_not_invent_dates_and_pins_resolved_version() -> None:
    paper = extract_paper(
        "<html><div id='watermark-tr'>arXiv:2402.08954v2 [cs.AI] 31 Feb 2024</div>"
        "<span class='ltx_personname'>Ada Lovelace   Emmy Noether<sup>1</sup></span></html>",
        "https://arxiv.org/html/2402.08954",
    )
    assert paper.version == 2
    assert paper.version_published_at is None
    assert "version_date_unavailable" in paper.metadata_warnings
    assert str(paper.source_url) == "https://arxiv.org/html/2402.08954v2"
    assert paper.authors == ["Ada Lovelace", "Emmy Noether"]


def test_empty_html_is_a_typed_extraction_error() -> None:
    from app.extractor import PaperExtractionError

    with pytest.raises(PaperExtractionError, match="parseable"):
        extract_paper("", "https://arxiv.org/html/2402.08954v1")


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


def test_deduplicates_repeated_rendering_of_the_same_display_equation() -> None:
    paper = extract_paper(
        "<html><body><h2 id='S1'>1 Method</h2>"
        "<div id='S1.E1' class='ltx_equation'>"
        "<math alttext='x=1'></math><math alttext='x=1'></math>"
        "</div></body></html>",
        "https://arxiv.org/html/2402.08954",
    )

    assert len(paper.equations) == 1
