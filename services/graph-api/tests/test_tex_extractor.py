import hashlib

import pytest

from app.evidence import build_evidence_graph
from app.tex_extractor import extract_tex


def test_local_source_anchors_and_provenance():
    source = r"""\title{A {nested} title}
\author{Ada\and Grace, Kim}
% $$ignored$$
\input{other}
\section{Methods}
\subsection*{Derivation}
\begin{align*}
x &= 1 \\ y &= 2 \label{eq:test}
\end{align*}
\[y = 3\%\] % \[ignored\]
$$z=4$$
\begin{equation}w=5\end{equation}"""
    paper = extract_tex(source)
    digest = hashlib.sha256(source.encode()).hexdigest()
    assert paper.paper_id == "tex_" + digest[:12]
    assert paper.source_url == "urn:fgl:local-tex:" + digest
    assert paper.title == "A {nested} title"
    assert paper.authors == ["Ada", "Grace", "Kim"]
    assert paper.sections[1].parent_section_id == "S1"
    assert paper.metadata_warnings == ["external_tex_includes_ignored"]
    assert [e.anchor for e in paper.equations] == ["L7-L9", "L10-L10", "L11-L11", "L12-L12"]
    assert paper.equations[0].latex == r"x &= 1 \\ y &= 2"
    assert paper.equations[1].latex == r"y = 3\%"
    assert paper.equations[0].equation_number == "eq:test"
    graph = build_evidence_graph(paper, workspace_id="test")
    assert graph.import_uuid == build_evidence_graph(
        extract_tex(source), workspace_id="test").import_uuid


@pytest.mark.parametrize(
    "source", ["", "% $$none$$", "é" * (256 * 1024 + 1), "$$unclosed"],
    ids=["empty", "comment", "oversize", "unclosed"],
)
def test_rejected_source(source):
    with pytest.raises(ValueError):
        extract_tex(source)
