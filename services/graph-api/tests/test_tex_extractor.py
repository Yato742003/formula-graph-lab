import hashlib

import pytest

from app.evidence import build_evidence_graph
from app.formula_ast import parse_formula
from app.tex_extractor import (
    MAX_EXPANDED_EQUATION_CHARS,
    extract_tex,
    extract_tex_macros,
)


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
    assert (
        graph.import_uuid
        == build_evidence_graph(extract_tex(source), workspace_id="test").import_uuid
    )


@pytest.mark.parametrize(
    "source",
    ["", "% $$none$$", "é" * (256 * 1024 + 1), "$$unclosed"],
    ids=["empty", "comment", "oversize", "unclosed"],
)
def test_rejected_source(source):
    with pytest.raises(ValueError):
        extract_tex(source)


def test_preamble_macros_and_declare_math_operator_expand_into_parseable_ast():
    source = r"""\title{Macro Paper}
\newcommand{\R}{\mathbb{R}}
\newcommand\E{\mathbb{E}}
\newcommand{\norm}[1]{\left\|#1\right\|}
\newcommand{\ip}[2]{\left\langle #1, #2 \right\rangle}
\newcommand{\vecx}[2][x]{#1_{#2}}
\providecommand{\R}{\mathbb{C}}
\renewcommand{\E}{\mathcal{E}}
\DeclareMathOperator{\softmax}{softmax}
\DeclareMathOperator*{\argmin}{arg\,min}
\section{Model}
\begin{equation}
\vecx{i} = \softmax(\vecx[z]{i}) + \norm{\norm{v}} + \E(v)
\end{equation}
\[
\ip{u}{w} \in \R
\]"""
    paper = extract_tex(source)
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    # Raw provenance bytes and source_fragments remain immutable!
    assert paper.source_sha256 == digest
    assert r"\vecx{i}" in paper.equations[0].source_fragments[0]
    assert paper.metadata_warnings == []

    eq0 = paper.equations[0].latex
    expected_eq0 = (
        r"x_{i} = \operatorname{softmax}(z_{i}) + "
        r"\left\|\left\|v\right\|\right\| + \mathcal{E}(v)"
    )
    assert eq0 == expected_eq0
    # Expanded equation must parse cleanly through formula_ast with softmax recognized as function.
    parsed = parse_formula(eq0)
    fn_names = {sym.name for sym in parsed.symbols if sym.category == "function"}
    assert "softmax" in fn_names

    eq1 = paper.equations[1].latex
    assert eq1 == r"\left\langle u, w \right\rangle \in \mathbb{R}"


def test_macro_command_boundary_and_protected_commands():
    source = r"""\newcommand{\E}{X}
\newcommand{\frac}[2]{HIJACK}
\newcommand{\input}[1]{HIJACK}
\newcommand{\begin}[1]{HIJACK}
\begin{equation}
\E + \Expect + \frac{1}{2}
\end{equation}"""
    macros, warnings = extract_tex_macros(source)
    assert set(macros) == {"E"}
    assert warnings == []

    paper = extract_tex(source)
    assert paper.equations[0].latex == r"X + \Expect + \frac{1}{2}"


def test_macro_direct_and_mutual_cycles_terminate_safely():
    source = r"""\newcommand{\self}{\self + 1}
\newcommand{\ping}{\pong}
\newcommand{\pong}{\ping}
\begin{equation}
y = \self + \ping
\end{equation}"""
    paper = extract_tex(source)
    assert "macro_cycle_detected" in paper.metadata_warnings
    assert len(paper.equations[0].latex) <= MAX_EXPANDED_EQUATION_CHARS
    assert r"\self" in paper.equations[0].latex
    assert r"\ping" in paper.equations[0].latex


def test_macro_billion_laughs_bomb_is_strictly_bounded():
    source = r"""\newcommand{\a}{xxxxxxxxxx}
\newcommand{\b}{\a\a\a\a\a\a\a\a\a\a}
\newcommand{\c}{\b\b\b\b\b\b\b\b\b\b}
\newcommand{\d}{\c\c\c\c\c\c\c\c\c\c}
\newcommand{\e}{\d\d\d\d\d\d\d\d\d\d}
\begin{equation}
y = \e
\end{equation}"""
    paper = extract_tex(source)
    assert "macro_expansion_limit_reached" in paper.metadata_warnings
    assert len(paper.equations[0].latex) <= MAX_EXPANDED_EQUATION_CHARS
    assert paper.equations[0].source_fragments == ["\ny = \\e\n"]


def test_macro_command_separator_prevents_token_merging():
    source = r"""\newcommand{\al}{\alpha}
\begin{equation}
\al x = \al + 1
\end{equation}"""
    paper = extract_tex(source)
    assert paper.equations[0].latex == r"\alpha x = \alpha + 1"
    parsed = parse_formula(paper.equations[0].latex)
    assert "alpha" in parsed.free_variables
    assert "x" in parsed.free_variables
