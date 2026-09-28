import json
from pathlib import Path

import pytest

from app.extractor import extract_paper

CORPUS = Path(__file__).parent / "fixtures" / "corpus"
CASES = json.loads((CORPUS / "expected.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["paper_id"])
def test_frozen_source_blocks_keep_fragments_numbers_and_anchors(case) -> None:
    source = (CORPUS / (case["paper_id"] + ".html")).read_text(encoding="utf-8")
    paper = extract_paper(source, case["source_url"])
    assert len(paper.equations) == len(case["equations"])
    for equation, expected in zip(paper.equations, case["equations"], strict=True):
        assert equation.anchor == expected["anchor"]
        assert equation.equation_number == expected["equation_number"]
        assert equation.source_fragments == expected["source_fragments"]
        assert equation.anchor_is_source
    assert paper.version is not None
    assert paper.version_published_at is not None
    assert paper.model_dump() == extract_paper(source, str(paper.source_url)).model_dump()


def test_text_in_math_layout_is_flagged_for_review() -> None:
    source = (CORPUS / "1810.04805.html").read_text(encoding="utf-8")
    paper = extract_paper(source, "https://arxiv.org/html/1810.04805v2")
    assert all("non_mathml_content_requires_review" in eq.warnings for eq in paper.equations)
