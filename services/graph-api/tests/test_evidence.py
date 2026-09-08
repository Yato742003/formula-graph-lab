import json
from pathlib import Path

import pytest

from app.evidence import analyze_equation, build_evidence_graph, build_reported_claim
from app.extractor import extract_paper
from app.models import ExtractedEquation

FIXTURE = Path(__file__).parent / "fixtures" / "arxiv_sample.html"


def test_exact_graph_preserves_every_formula_and_source_episode():
    paper = extract_paper(FIXTURE.read_text(encoding="utf-8"), "https://arxiv.org/html/1706.03762v7")
    graph = build_evidence_graph(paper, workspace_id="a")
    equations = [n for n in graph.nodes if n["kind"] == "Equation"]
    payloads = [json.loads(n["payload"]) for n in equations]
    for payload, equation in zip(payloads, paper.equations, strict=True):
        analysis = payload.pop("formula_analysis")
        assert payload == equation.model_dump(mode="json")
        assert analysis["status"] == "well_typed"
        assert len(analysis["canonical_hash"]) == 64
        assert analysis["contracts"]
    symbols = [node for node in graph.nodes if node["kind"] == "Symbol"]
    assert symbols
    symbol_ids = {node["uuid"] for node in symbols}
    symbol_relations = [edge for edge in graph.edges if edge["relation"] in {"defines", "uses"}]
    assert symbol_relations
    assert all(edge["target_uuid"] in symbol_ids for edge in symbol_relations)
    episode_ids = {e.uuid for e in graph.episodes}
    assert all(set(e["episode_uuids"]) <= episode_ids for e in graph.edges)
    assert graph == build_evidence_graph(paper, workspace_id="a")
    other = build_evidence_graph(paper, workspace_id="b")
    assert {n["uuid"] for n in graph.nodes}.isdisjoint({n["uuid"] for n in other.nodes})


def _paper(version: int, paper_id: str = "2402.08954"):
    return extract_paper(
        f"<html><div id='watermark-tr'>arXiv:{paper_id}v{version} [cs.AI] "
        f"0{version} Feb 2024</div><section id='S1'><h2>1 Result</h2>"
        f"<math id='S1.E1' display='block' alttext='x={version}'></math></section></html>",
        f"https://arxiv.org/html/{paper_id}v{version}",
    )


def test_revisions_share_paper_but_not_snapshot_identity():
    first = build_evidence_graph(_paper(1), workspace_id="lab")
    second = build_evidence_graph(_paper(2), workspace_id="lab")
    first_paper = next(n for n in first.nodes if n["kind"] == "Paper")
    second_paper = next(n for n in second.nodes if n["kind"] == "Paper")
    assert first_paper["uuid"] == second_paper["uuid"]
    assert first.paper_version_uuid != second.paper_version_uuid
    assert {e.uuid for e in first.episodes}.isdisjoint({e.uuid for e in second.episodes})


def test_cross_paper_claims_remain_distinct_and_source_bound():
    left = build_evidence_graph(_paper(1, "2402.08954"), workspace_id="lab")
    right = build_evidence_graph(_paper(1, "2402.08955"), workspace_id="lab")
    left_claim = build_reported_claim(
        left, logical_id="result-1", statement="The objective converges.",
        source_anchor="S1.E1",
    )
    right_claim = build_reported_claim(
        right, logical_id="result-1", statement="The objective diverges.",
        source_anchor="S1.E1",
    )
    assert left_claim.uuid != right_claim.uuid
    assert left_claim.paper_id != right_claim.paper_id
    assert json.loads(left_claim.payload)["status"] == "reported"
    assert json.loads(right_claim.payload)["status"] == "reported"


def test_claim_rejects_fabricated_anchor():
    graph = build_evidence_graph(_paper(1), workspace_id="lab")
    with pytest.raises(ValueError, match="source"):
        build_reported_claim(
            graph, logical_id="fake", statement="Fabricated.", source_anchor="missing",
        )


def test_unsupported_formula_is_explicit_without_failing_import():
    equation = ExtractedEquation(
        equation_id="eq-unsupported",
        anchor="S1.E1",
        latex=r"\begin{matrix}x\end{matrix}",
        extraction_method="alttext",
        confidence=0.9,
    )
    analysis, parsed, contracts = analyze_equation(equation)
    assert parsed is None
    assert contracts == []
    assert analysis["status"] == "unsupported"
    assert analysis["error"]["code"] == "UNSUPPORTED_ENVIRONMENT"
