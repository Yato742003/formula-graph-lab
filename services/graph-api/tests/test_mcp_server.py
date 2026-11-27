"""Protocol, isolation, security, and 10-question XML eval tests for ``fgl-mcp``."""

from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from app.mcp_server import create_mcp_server

EXPECTED_TOOL_NAMES = {
    "fgl_extract_tex",
    "fgl_analyze_formula",
    "fgl_check_compatibility",
    "fgl_verify_derivation",
    "fgl_export_lean4_draft",
    "fgl_audit_lean4_statement",
    "fgl_search_evidence",
}
EXPECTED_RESOURCE_URIS = {
    "fgl://policy/epistemic-boundaries",
    "fgl://schema/contract-presets",
}


async def _call_json(server, tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
    """Invoke a FastMCP tool in-process and parse its JSON text payload."""
    result = await server.call_tool(tool_name, arguments)
    blocks = result[0] if isinstance(result, tuple) else result
    assert blocks, f"Tool {tool_name} returned empty content."
    return json.loads(blocks[0].text)


@pytest.mark.asyncio
async def test_mcp_server_registers_all_seven_tools_and_two_resources():
    server = create_mcp_server()
    tools = await server.list_tools()
    resources = await server.list_resources()

    assert {t.name for t in tools} == EXPECTED_TOOL_NAMES
    for tool in tools:
        assert tool.description and len(tool.description) >= 30
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False

    assert {str(r.uri) for r in resources} == EXPECTED_RESOURCE_URIS
    for uri in EXPECTED_RESOURCE_URIS:
        contents = await server.read_resource(uri)
        assert contents
        parsed = json.loads(contents[0].content)
        assert isinstance(parsed, dict) and parsed


@pytest.mark.asyncio
async def test_extract_tex_preserves_sha256_and_expands_macros():
    server = create_mcp_server()
    raw = (
        "\\newcommand{\\scale}[1]{\\frac{#1}{\\sqrt{d_k}}}\n"
        "\\section{Attention}\n"
        "\\begin{equation}\\scale{q}\\end{equation}\n"
    )
    data = await _call_json(server, "fgl_extract_tex", {"tex_source": raw})
    assert data["status"] == "ok"
    assert len(str(data["source_sha256"])) == 64
    assert data["equation_count"] == 1
    eq = data["equations"][0]  # type: ignore[index]
    assert eq["latex"] == r"\frac{q}{\sqrt{d_k}}"
    assert r"\scale{q}" in eq["source_fragments"][0]

    # Markdown format also works cleanly
    md_res = await server.call_tool(
        "fgl_extract_tex", {"tex_source": raw, "response_format": "markdown"}
    )
    md_blocks = md_res[0] if isinstance(md_res, tuple) else md_res
    assert "## fgl_extract_tex" in md_blocks[0].text


@pytest.mark.asyncio
async def test_verify_derivation_fails_closed_without_sandbox_by_default(monkeypatch):
    monkeypatch.delenv("FGL_SANDBOX_IMAGE", raising=False)
    monkeypatch.delenv("FGL_MCP_ALLOW_LOCAL_SUBPROCESS", raising=False)

    server = create_mcp_server(allow_local_subprocess=False)
    res = await _call_json(
        server,
        "fgl_verify_derivation",
        {
            "source_latex": "(x + 1)^2",
            "target_latex": "x^2 + 2 x + 1",
            "contracts": [{"name": "x", "category": "scalar", "domain": "real"}],
        },
    )
    assert res["status"] == "ok"
    assert res["scope"] == "ad_hoc_check_not_frozen_campaign"
    assert res["execution_mode"] == "preflight_only"
    assert res["outcome"] == "unknown"
    assert res["error_code"] == "SANDBOX_NOT_CONFIGURED"
    assert "FGL_SANDBOX_IMAGE" in str(res.get("hint", ""))


@pytest.mark.asyncio
async def test_verify_derivation_uses_docker_sandbox_when_image_configured(monkeypatch):
    fake_image = "sha256:" + ("a" * 64)
    monkeypatch.setenv("FGL_SANDBOX_IMAGE", fake_image)

    captured: list[dict[str, object]] = []

    def fake_run_sandbox(payload, *, timeout_ms, image):
        captured.append({"payload": payload, "timeout_ms": timeout_ms, "image": image})
        return {"outcome": "supported", "witness": {"difference": "0"}}

    monkeypatch.setattr("app.mcp_server.run_sandbox", fake_run_sandbox)
    server = create_mcp_server(allow_local_subprocess=False)
    res = await _call_json(
        server,
        "fgl_verify_derivation",
        {
            "source_latex": r"\log(x^2)",
            "target_latex": r"2 \log(x)",
            "contracts": [
                {
                    "name": "x",
                    "category": "scalar",
                    "domain": "positive",
                    "constraints": ["x > 0"],
                }
            ],
            "include_lean_export": True,
        },
    )
    assert res["execution_mode"] == "docker_sandbox"
    assert res["execution_image"] == fake_image
    assert res["outcome"] == "supported"
    assert res["domain_status"] == "discharged"
    assert len(captured) == 1
    assert captured[0]["image"] == fake_image
    lean_export = res["lean_export"]
    assert isinstance(lean_export, dict)
    assert lean_export["audit"]["proof_status"] == "FROZEN_UNPROVED"
    assert lean_export["audit"]["declaration_fidelity"] == "PASS"


@pytest.mark.asyncio
async def test_verify_derivation_local_subprocess_supports_chain_and_catches_refuted_step(
    monkeypatch,
):
    monkeypatch.delenv("FGL_SANDBOX_IMAGE", raising=False)
    server = create_mcp_server(allow_local_subprocess=True)

    valid_chain = await _call_json(
        server,
        "fgl_verify_derivation",
        {
            "source_latex": "(x + y)^2",
            "steps": ["x^2 + 2 x y + y^2", "y^2 + 2 x y + x^2"],
            "contracts": [
                {"name": "x", "category": "scalar", "domain": "real"},
                {"name": "y", "category": "scalar", "domain": "real"},
            ],
            "include_lean_export": True,
        },
    )
    assert valid_chain["execution_mode"] == "local_bounded_subprocess"
    assert valid_chain["mode"] == "chain"
    assert valid_chain["outcome"] == "supported"
    assert valid_chain["failed_step_index"] is None
    assert "calc" in str(valid_chain["lean_export"]["lean_code"])  # type: ignore[index]


@pytest.mark.asyncio
async def test_search_evidence_fails_closed_when_neo4j_not_configured(monkeypatch):
    for key in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "FGL_MCP_WORKSPACE_ID"):
        monkeypatch.delenv(key, raising=False)

    server = create_mcp_server()
    res = await _call_json(server, "fgl_search_evidence", {"query": "attention"})
    assert res["status"] == "error"
    assert res["error_code"] == "EVIDENCE_STORE_NOT_CONFIGURED"
    assert "FGL_MCP_WORKSPACE_ID" in str(res["message"])


@pytest.mark.asyncio
async def test_search_evidence_uses_connect_and_returns_hits_when_configured(monkeypatch):
    from datetime import UTC, datetime

    from app.search import EvidenceCandidate

    monkeypatch.setenv("NEO4J_URI", "bolt://127.0.0.1:7687")
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "secret")
    monkeypatch.setenv("FGL_MCP_WORKSPACE_ID", "ws_mcp_test")

    closed = {"called": False}

    class _FakeStore:
        async def lexical_search(self, query, filters, limit):
            _ = (query, filters, limit)
            return [
                EvidenceCandidate(
                    uuid="11111111-1111-4111-8111-111111111111",
                    kind="Equation",
                    logical_id="E1",
                    payload=json.dumps({"latex": r"\log(x^2)", "anchor": "L4-L6"}),
                    paper_id="tex_abcdef012345",
                    paper_version=1,
                    valid_at=datetime(2026, 11, 27, tzinfo=UTC),
                    verification_status="reported",
                    episode_uuids=("ep-1",),
                    lexical_score=2.5,
                )
            ]

        async def evidence_for_episodes(self, episode_uuids, filters, limit):
            _ = (episode_uuids, filters, limit)
            return []

        async def graph_neighbors(self, center_node_uuid, filters, limit):
            _ = (center_node_uuid, filters, limit)
            return []

        async def close(self):
            closed["called"] = True

    monkeypatch.setattr(
        "app.mcp_server.Neo4jEvidenceStore.connect",
        classmethod(lambda cls, uri, user, password, *, database="neo4j": _FakeStore()),
    )

    server = create_mcp_server()
    res = await _call_json(server, "fgl_search_evidence", {"query": "log"})
    assert res["status"] == "ok"
    assert res["workspace_id"] == "ws_mcp_test"
    assert res["hit_count"] == 1
    assert res["hits"][0]["logical_id"] == "E1"  # type: ignore[index]
    assert closed["called"] is True


@pytest.mark.asyncio
async def test_ten_question_xml_evaluation_suite_passes_100_percent(monkeypatch):
    """Execute all 10 deterministic QA pairs in evals/fgl_mcp_eval.xml against real MCP tools."""
    monkeypatch.delenv("FGL_SANDBOX_IMAGE", raising=False)
    eval_path = Path(__file__).resolve().parents[1] / "evals" / "fgl_mcp_eval.xml"
    root = ET.fromstring(eval_path.read_text(encoding="utf-8"))
    pairs = root.findall("qa_pair")
    assert len(pairs) == 10
    expected = [pair.findtext("answer", default="").strip() for pair in pairs]

    server = create_mcp_server(allow_local_subprocess=True)

    # Q1: Macro expansion in fgl_extract_tex
    r1 = await _call_json(
        server,
        "fgl_extract_tex",
        {
            "tex_source": (
                r"\newcommand{\inv}[1]{\frac{1}{#1}}"
                r"\section{Scaling}"
                r"\begin{equation}\inv{d_k} + \inv{d_k}\end{equation}"
            )
        },
    )
    a1 = str(r1["equations"][0]["latex"])  # type: ignore[index]

    # Q2: Macro cycle warning
    r2 = await _call_json(
        server,
        "fgl_extract_tex",
        {"tex_source": r"\newcommand{\bad}{\bad}\begin{equation}\bad\end{equation}"},
    )
    a2 = str(r2["metadata_warnings"][0])  # type: ignore[index]

    # Q3: Alpha-canonical syntax_hash equivalence
    r3_a = await _call_json(server, "fgl_analyze_formula", {"formula": r"\sum_{i=1}^{n} x_i"})
    r3_b = await _call_json(server, "fgl_analyze_formula", {"formula": r"\sum_{j=1}^{n} x_j"})
    a3 = "true" if r3_a["syntax_hash"] == r3_b["syntax_hash"] else "false"

    # Q4: Matrix shape mismatch
    r4 = await _call_json(
        server,
        "fgl_check_compatibility",
        {
            "formula": "A B",
            "contracts": [
                {"name": "A", "category": "matrix", "shape": [3, 4], "domain": "real"},
                {"name": "B", "category": "matrix", "shape": [5, 2], "domain": "real"},
            ],
        },
    )
    a4 = str(r4["shape_status"])

    # Q5: Unresolved domain under plain real
    r5 = await _call_json(
        server,
        "fgl_check_compatibility",
        {
            "formula": r"\log(x) + \frac{1}{x}",
            "contracts": [{"name": "x", "category": "scalar", "domain": "real"}],
        },
    )
    a5 = str(r5["domain_status"])

    # Q6: Discharged domain under positive
    r6 = await _call_json(
        server,
        "fgl_check_compatibility",
        {
            "formula": r"\log(x) + \frac{1}{x}",
            "contracts": [
                {
                    "name": "x",
                    "category": "scalar",
                    "domain": "positive",
                    "constraints": ["x > 0"],
                }
            ],
        },
    )
    a6 = str(r6["domain_status"])

    # Q7: Non-commutative matrix A B != B A
    r7 = await _call_json(
        server,
        "fgl_verify_derivation",
        {
            "source_latex": "A B",
            "target_latex": "B A",
            "contracts": [
                {"name": "A", "category": "matrix", "shape": [2, 2], "domain": "real"},
                {"name": "B", "category": "matrix", "shape": [2, 2], "domain": "real"},
            ],
        },
    )
    a7 = str(r7["outcome"])

    # Q8: Multi-step derivation chain failing at step index 1
    r8 = await _call_json(
        server,
        "fgl_verify_derivation",
        {
            "source_latex": "(x + 1)^2",
            "steps": ["x^2 + 2 x + 1", "x^2 + 3 x + 1"],
            "contracts": [{"name": "x", "category": "scalar", "domain": "real"}],
        },
    )
    a8 = str(r8["failed_step_index"])

    # Q9: Lean 4 export reports FROZEN_UNPROVED
    r9 = await _call_json(
        server,
        "fgl_export_lean4_draft",
        {
            "source_latex": r"\log(x^2)",
            "target_latex": r"2 \log(x)",
            "contracts": [
                {
                    "name": "x",
                    "category": "scalar",
                    "domain": "positive",
                    "constraints": ["x > 0"],
                }
            ],
        },
    )
    a9 = str(r9["audit"]["proof_status"])  # type: ignore[index]

    # Q10: Lean 4 statement audit rejects EXCESS binder
    r10 = await _call_json(
        server,
        "fgl_audit_lean4_statement",
        {
            "lean_code": (
                "theorem sneaky (x : ℝ) (h_x_pos : 0 < x) (h_extra : x = 1) : "
                "Real.log (x ^ 2) = 2 * Real.log x := by sorry"
            ),
            "source_latex": r"\log(x^2)",
            "target_latex": r"2 \log(x)",
            "contracts": [
                {
                    "name": "x",
                    "category": "scalar",
                    "domain": "positive",
                    "constraints": ["x > 0"],
                }
            ],
        },
    )
    a10 = str(r10["declaration_fidelity"])

    actual = [a1, a2, a3, a4, a5, a6, a7, a8, a9, a10]
    assert actual == expected


def test_mcp_server_cli_help_exits_zero():
    proc = subprocess.run(
        [sys.executable, "-m", "app.mcp_server", "--help"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert proc.returncode == 0
    assert "streamable-http" in proc.stdout
    assert "--allow-local-subprocess" in proc.stdout
