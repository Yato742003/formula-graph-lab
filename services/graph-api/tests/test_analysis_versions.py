import json
from datetime import UTC, datetime

from app.analysis_versions import make_analysis_version, source_hash
from app.evidence import build_evidence_graph
from app.extractor import extract_paper


def test_legacy_analysis_is_excluded_from_source_identity():
    raw = {"latex": "x/x", "confidence": 0.8, "source_fragments": ["x/x"]}
    legacy = {**raw, "formula_analysis": {"status": "well_typed"}}
    assert source_hash(json.dumps(raw)) == source_hash(json.dumps(legacy))
    assert source_hash(json.dumps(raw)) != source_hash(json.dumps({**raw, "confidence": 1}))


def test_analysis_identity_binds_source_and_analyzer_without_mutation():
    source = json.dumps({"latex": "x", "confidence": 0.8})
    analysis = {"status": "analyzed", "syntax_hash": "a" * 64}
    original = json.dumps(analysis, sort_keys=True)
    first = make_analysis_version("equation-1", source, analysis)
    assert first == make_analysis_version("equation-1", source, analysis)
    assert first["uuid"] != make_analysis_version("equation-2", source, analysis)["uuid"]
    assert first["uuid"] != make_analysis_version(
        "equation-1", source, analysis, analyzer_version="new-analyzer",
    )["uuid"]
    assert first["uuid"] != make_analysis_version(
        "equation-1", json.dumps({"latex": "y", "confidence": 0.8}), analysis,
    )["uuid"]
    assert json.dumps(analysis, sort_keys=True) == original


def test_unsupported_equation_keeps_source_and_versioned_diagnostic():
    paper = extract_paper(
        "<html><section id='S1'><h2>Result</h2>"
        "<math id='S1.E1' display='block' alttext='\\begin{matrix}x\\end{matrix}'/>"
        "</section></html>",
        "https://arxiv.org/html/2402.08954v1",
    )
    graph = build_evidence_graph(
        paper, workspace_id="unit-workspace",
        reference_time=datetime(2024, 2, 14, tzinfo=UTC),
    )
    equation = next(node for node in graph.nodes if node["kind"] == "Equation")
    assert json.loads(equation["payload"]) == paper.equations[0].model_dump(mode="json")
    version = graph.analyses[0]
    assert version["equation_uuid"] == equation["uuid"]
    assert version["source_hash"] == source_hash(equation["payload"])
    assert json.loads(version["payload"])["status"] == "unsupported"
