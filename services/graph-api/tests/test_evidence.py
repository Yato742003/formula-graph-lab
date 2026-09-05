import json
from pathlib import Path

from app.evidence import build_evidence_graph
from app.extractor import extract_paper

FIXTURE = Path(__file__).parent / "fixtures" / "arxiv_sample.html"


def test_exact_graph_preserves_every_formula_and_source_episode():
    paper = extract_paper(FIXTURE.read_text(encoding="utf-8"), "https://arxiv.org/html/1706.03762v7")
    graph = build_evidence_graph(paper, workspace_id="a")
    equations = [n for n in graph.nodes if n["kind"] == "Equation"]
    assert [json.loads(n["payload"]) for n in equations] == [
        e.model_dump(mode="json") for e in paper.equations
    ]
    episode_ids = {e.uuid for e in graph.episodes}
    assert all(set(e["episode_uuids"]) <= episode_ids for e in graph.edges)
    assert graph == build_evidence_graph(paper, workspace_id="a")
    other = build_evidence_graph(paper, workspace_id="b")
    assert {n["uuid"] for n in graph.nodes}.isdisjoint({n["uuid"] for n in other.nodes})
