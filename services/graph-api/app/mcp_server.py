"""FormulaGraph Lab MCP Server (``fgl-mcp``).

Exposes FormulaGraph Lab's deterministic scientific verification engines over
Model Context Protocol (MCP) using ``FastMCP`` over ``stdio`` (default) or
``streamable-http``.

Epistemic & security invariants enforced across every tool:
- ``exact_evidence`` bytes and SHA-256 provenance are immutable.
- Symbolic verification runs fail-closed: Docker air-gapped sandbox
  (``FGL_SANDBOX_IMAGE=sha256:...``) by default; otherwise stops at AST,
  contract, and SymPy domain-obligation preflight (``preflight_only``) unless
  the caller explicitly opts into ``--allow-local-subprocess`` or
  ``FGL_MCP_ALLOW_LOCAL_SUBPROCESS=true``.
- Caller-supplied symbol contracts are clearly labeled
  ``mcp_caller_supplied_assumptions`` and never impersonate persisted human
  Neo4j reviews.
- Lean 4 exports always emit ``PROOF_STATUS: FROZEN_UNPROVED`` until checked
  by a real Lean 4 kernel with zero ``sorry`` and standard axioms only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from functools import partial
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.evidence_store import Neo4jEvidenceStore
from app.formula_ast import FormulaFormat, FormulaParseError, ast_node_count, parse_formula
from app.lean_formalizer import audit_lean4_declaration, generate_lean4_export
from app.models import EvidenceSearchRequest
from app.sandbox import configured_image, run_sandbox
from app.search import EvidenceSearchService, SearchCursorCodec
from app.symbol_contracts import (
    ContractReview,
    ReviewedContractValue,
    SymbolCategory,
    SymbolDomain,
    apply_contract_reviews,
    assess_domain_obligations,
    build_symbol_assumptions,
    check_shape_compatibility,
    contract_review_state_hash,
    discharge_domain_obligations_from_reviews,
    format_reviewed_assumptions,
    infer_contracts,
    infer_domain_obligations,
    infer_expression_shape,
    summarize_domain_obligations,
)
from app.tex_extractor import extract_tex
from app.verification import MAX_CHECK_AST_NODES, _run_worker_payload, run_symbolic_check

SERVER_NAME = "formula_graph_mcp"
SERVER_INSTRUCTIONS = (
    "FormulaGraph Lab deterministic scientific verification server. "
    "Use these tools to extract LaTeX equations with SHA-256 provenance, "
    "parse no-eval formula ASTs, verify shape/domain compatibility, check "
    "symbolic rewrites and multi-step derivation chains under explicit SymPy "
    "assumptions, generate/audit Lean 4 DRAFT_SORRY theorem statements, and "
    "query read-only Neo4j exact-evidence workspaces."
)

ResponseFormat = Literal["json", "markdown"]
MAX_CHAIN_STEPS = 8
_DETERMINISTIC_REVIEW_TIME = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
_READ_ONLY_LOCAL = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_READ_ONLY_DB = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)


class SymbolContractInput(BaseModel):
    """Caller-supplied symbol type, shape, domain, and constraints."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    category: SymbolCategory = "scalar"
    shape: list[int | str] | None = Field(default=None, max_length=16)
    domain: SymbolDomain = "real"
    constraints: list[str] = Field(default_factory=list, max_length=16)


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _build_caller_reviews(
    contracts: list[dict[str, object]] | None,
    *,
    scope: str,
) -> list[ContractReview]:
    """Validate caller contract specs and lift them into deterministic ContractReview objects."""
    if not contracts:
        return []
    reviews: list[ContractReview] = []
    seen: set[str] = set()
    for raw in contracts:
        spec = SymbolContractInput.model_validate(raw)
        if spec.name in seen:
            raise ValueError(f"Duplicate symbol contract provided for '{spec.name}'.")
        seen.add(spec.name)
        shape_tuple = tuple(spec.shape) if spec.shape is not None else None
        if shape_tuple is None and spec.category in {"scalar", "index"}:
            shape_tuple = ()
        value = ReviewedContractValue(
            name=spec.name,
            category=spec.category,
            shape=shape_tuple,
            domain=spec.domain,
            constraints=list(spec.constraints),
            scope=scope,
        )
        canonical = json.dumps(
            value.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(f"{scope}:{canonical}".encode()).hexdigest()
        reviews.append(
            ContractReview(
                review_id=f"mcp_rev_{digest[:16]}",
                symbol_name=spec.name,
                reviewer_id="mcp-caller",
                reviewer_role="researcher",
                decision="accepted",
                scope=scope,
                reviewed_contract=value,
                evidence=["mcp:caller_supplied_assumption"],
                reviewed_at=_DETERMINISTIC_REVIEW_TIME,
            )
        )
    return reviews


def _apply_reviews_matching(
    inferred_contracts: list,
    reviews: list[ContractReview],
    *,
    scope: str,
) -> list:
    names = {c.name for c in inferred_contracts}
    applicable = [r for r in reviews if r.symbol_name in names]
    if not applicable:
        return inferred_contracts
    return apply_contract_reviews(inferred_contracts, applicable, scope=scope)


def _combined_domain_status(statuses: list[str]) -> str:
    priority = ["contradictory", "unsupported", "unresolved", "conditional", "discharged"]
    if not statuses:
        return "discharged"
    return min(statuses, key=lambda item: priority.index(item) if item in priority else 1)


def _format_output(payload: dict[str, object], response_format: ResponseFormat) -> str:
    if response_format == "json":
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)
    lines: list[str] = []
    title = str(payload.get("tool") or "FormulaGraph Lab Result")
    lines.append(f"## {title}")
    for key, value in payload.items():
        if key == "tool":
            continue
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=False, indent=2)
            lines.append(f"\n### {key}\n```json\n{rendered}\n```")
        else:
            lines.append(f"- **{key}**: `{value}`")
    return "\n".join(lines)


def _error_payload(
    tool_name: str,
    code: str,
    message: str,
    *,
    hint: str | None = None,
    response_format: ResponseFormat = "json",
) -> str:
    payload: dict[str, object] = {
        "tool": tool_name,
        "status": "error",
        "error_code": code,
        "message": message,
    }
    if hint:
        payload["hint"] = hint
    return _format_output(payload, response_format)


def _resolve_symbolic_executor(
    *,
    allow_local_subprocess: bool,
) -> tuple[Literal["docker_sandbox", "local_bounded_subprocess", "preflight_only"], str | None]:
    """Select the symbolic execution backend fail-closed."""
    try:
        image = configured_image()
        return "docker_sandbox", image
    except ValueError:
        pass
    if allow_local_subprocess or _truthy_env("FGL_MCP_ALLOW_LOCAL_SUBPROCESS"):
        return "local_bounded_subprocess", None
    return "preflight_only", None


def _epistemic_boundaries_policy() -> dict[str, object]:
    return {
        "policy_version": "fgl-mcp-epistemic.v1",
        "invariants": {
            "exact_evidence_immutability": (
                "Raw uploaded .tex bytes and source_sha256 anchors are immutable. "
                "Macro expansion only transforms display math copies and never overwrites "
                "source_fragments or source_sha256."
            ),
            "ad_hoc_scope_boundary": (
                "All fgl_verify_derivation results carry scope='ad_hoc_check_not_frozen_campaign' "
                "and never represent a frozen holdout benchmark or promotion claim."
            ),
            "fail_closed_sandbox_execution": (
                "Without FGL_SANDBOX_IMAGE=sha256:..., symbolic verification runs AST, shape, "
                "and SymPy domain-obligation preflight only ('preflight_only') unless the caller "
                "opts in via --allow-local-subprocess or FGL_MCP_ALLOW_LOCAL_SUBPROCESS=true."
            ),
            "caller_supplied_contracts": (
                "Contracts passed to MCP tools are tagged 'mcp_caller_supplied_assumptions' "
                "and are distinct from persisted human Neo4j ContractReview records."
            ),
            "lean4_proof_status": (
                "Generated Lean 4 skeletons contain 'sorry' and always report "
                "proof_status='FROZEN_UNPROVED' until checked by a real Lean 4 kernel "
                "with sorry_count == 0 and standard axioms only."
            ),
        },
        "five_bin_audit_bins": {
            "SOURCE": "Hypothesis explicitly implied by accepted source ContractReview premises.",
            "STANDING": "Explicit standing mathematical assumptions registered by the caller.",
            "TYPING": "Carrier type binder matching the reviewed symbol domain (R, Z, C).",
            "RULED": "Explicit author ruling registered by the caller.",
            "EXCESS": "Unjustified extra binder/premise or carrier mismatch — ALWAYS FAILS AUDIT.",
        },
    }


def _contract_presets_catalog() -> dict[str, object]:
    return {
        "schema_version": "fgl-mcp-contracts.v1",
        "categories": [
            "scalar",
            "vector",
            "matrix",
            "tensor",
            "function",
            "distribution",
            "index",
        ],
        "domains": ["real", "positive", "non_negative", "complex", "integer"],
        "recognized_constraint_patterns": [
            "x > 0 (positive)",
            "x >= 0 (non_negative)",
            "x != 0 (nonzero)",
            "x != 1 (not_one)",
            "x < 0 (negative)",
        ],
        "presets": [
            {
                "label": "Positive real scalar (e.g. variance, dimension, denominator)",
                "contract": {
                    "name": "x",
                    "category": "scalar",
                    "shape": [],
                    "domain": "positive",
                    "constraints": ["x > 0"],
                },
            },
            {
                "label": "Non-negative real scalar (e.g. sqrt argument)",
                "contract": {
                    "name": "y",
                    "category": "scalar",
                    "shape": [],
                    "domain": "non_negative",
                    "constraints": ["y >= 0"],
                },
            },
            {
                "label": "Non-zero real scalar (e.g. denominator)",
                "contract": {
                    "name": "d",
                    "category": "scalar",
                    "shape": [],
                    "domain": "real",
                    "constraints": ["d != 0"],
                },
            },
            {
                "label": "Non-commutative matrix (shape [N, d])",
                "contract": {
                    "name": "A",
                    "category": "matrix",
                    "shape": ["N", "d"],
                    "domain": "real",
                    "constraints": [],
                },
            },
        ],
    }


def create_mcp_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    allow_local_subprocess: bool = False,
) -> FastMCP:
    """Construct a configured FormulaGraph Lab ``FastMCP`` instance."""
    server = FastMCP(
        name=SERVER_NAME,
        instructions=SERVER_INSTRUCTIONS,
        host=host,
        port=port,
        stateless_http=True,
        json_response=True,
    )

    @server.resource(
        "fgl://policy/epistemic-boundaries",
        name="epistemic_boundaries",
        title="FormulaGraph Lab Epistemic Boundaries Policy",
        description=(
            "Immutable evidence rules, fail-closed execution policy, and Lean 4 five-bin "
            "audit guarantees enforced by fgl-mcp."
        ),
        mime_type="application/json",
    )
    def read_epistemic_policy() -> str:
        return json.dumps(_epistemic_boundaries_policy(), indent=2, ensure_ascii=False)

    @server.resource(
        "fgl://schema/contract-presets",
        name="contract_presets",
        title="Symbol Contract Schema & Presets",
        description=(
            "Supported categories, domains, constraint syntax, and ready-to-use contract "
            "presets for fgl_check_compatibility, fgl_verify_derivation, and Lean 4 tools."
        ),
        mime_type="application/json",
    )
    def read_contract_presets() -> str:
        return json.dumps(_contract_presets_catalog(), indent=2, ensure_ascii=False)

    @server.tool(
        name="fgl_extract_tex",
        title="Extract Equations and Provenance from LaTeX Source",
        description=(
            "Statically extract sections and display equations from a .tex document with "
            "bounded, cycle-safe \\newcommand/\\DeclareMathOperator macro expansion while "
            "preserving immutable raw source_sha256, line anchors (L*), and raw source_fragments."
        ),
        annotations=_READ_ONLY_LOCAL,
    )
    def fgl_extract_tex(
        tex_source: Annotated[
            str,
            Field(
                min_length=1,
                max_length=524_288,
                description="Full or partial .tex source containing at least one display equation.",
            ),
        ],
        title: Annotated[
            str | None,
            Field(default=None, max_length=2000, description="Optional fallback document title."),
        ] = None,
        filename: Annotated[
            str | None,
            Field(default=None, max_length=255, description="Optional source filename label."),
        ] = None,
        response_format: Annotated[
            ResponseFormat,
            Field(default="json", description="Output format: 'json' (default) or 'markdown'."),
        ] = "json",
    ) -> str:
        try:
            paper = extract_tex(tex_source, title=title, filename=filename)
        except ValueError as exc:
            return _error_payload(
                "fgl_extract_tex",
                "TEX_EXTRACTION_ERROR",
                str(exc),
                hint=(
                    "Ensure tex_source is UTF-8 without NUL bytes, <= 512 KiB, has balanced "
                    "display math delimiters (\\[...\\], $$...$$, or equation/align environments), "
                    "and contains at least one non-empty display equation."
                ),
                response_format=response_format,
            )
        payload: dict[str, object] = {
            "tool": "fgl_extract_tex",
            "status": "ok",
            "paper_id": paper.paper_id,
            "source_kind": paper.source_kind,
            "source_sha256": paper.source_sha256,
            "source_url": paper.source_url,
            "title": paper.title,
            "authors": list(paper.authors),
            "metadata_warnings": list(paper.metadata_warnings),
            "section_count": len(paper.sections),
            "equation_count": len(paper.equations),
            "sections": [sec.model_dump(mode="json") for sec in paper.sections],
            "equations": [eq.model_dump(mode="json") for eq in paper.equations],
        }
        return _format_output(payload, response_format)

    @server.tool(
        name="fgl_analyze_formula",
        title="Parse Formula AST, Syntax Hash, Inferred Contracts, and Domain Obligations",
        description=(
            "Parse a LaTeX or MathML formula without eval(), compute its alpha-canonical "
            "syntax_hash, infer draft symbol contracts (category/shape/domain), check static "
            "shape compatibility, and list all domain definedness obligations (denominators, "
            "log arguments/bases, sqrt arguments)."
        ),
        annotations=_READ_ONLY_LOCAL,
    )
    def fgl_analyze_formula(
        formula: Annotated[
            str,
            Field(min_length=1, max_length=20_000, description="LaTeX or MathML expression."),
        ],
        source_format: Annotated[
            FormulaFormat,
            Field(default="latex", description="Input syntax: 'latex' (default) or 'mathml'."),
        ] = "latex",
        include_ast: Annotated[
            bool,
            Field(default=False, description="Include full recursive AST node dictionary."),
        ] = False,
        response_format: Annotated[
            ResponseFormat,
            Field(default="json", description="Output format: 'json' or 'markdown'."),
        ] = "json",
    ) -> str:
        try:
            parsed = parse_formula(formula, source_format=source_format)
        except FormulaParseError as exc:
            return _error_payload(
                "fgl_analyze_formula",
                exc.code,
                str(exc),
                hint="Check LaTeX syntax, balanced braces, and supported mathematical operators.",
                response_format=response_format,
            )
        contracts = infer_contracts(parsed)
        result_shape, shape_errors = infer_expression_shape(contracts, parsed)
        obligations = infer_domain_obligations(contracts, parsed)
        assessment = assess_domain_obligations(obligations)
        payload: dict[str, object] = {
            "tool": "fgl_analyze_formula",
            "status": "ok",
            "source_format": parsed.source_format,
            "syntax_hash": parsed.syntax_hash,
            "syntax_hash_version": parsed.syntax_hash_version,
            "canonicalizer_version": parsed.canonicalizer_version,
            "ast_node_count": ast_node_count(parsed.root),
            "free_variables": list(parsed.free_variables),
            "bound_variables": list(parsed.bound_variables),
            "symbols": [sym.to_dict() for sym in parsed.symbols],
            "inferred_contracts": [
                {**c.model_dump(mode="json"), "status": "inferred_draft"} for c in contracts
            ],
            "result_shape": list(result_shape) if result_shape is not None else None,
            "shape_errors": [
                {
                    "message": err.message,
                    "location": err.node_path,
                    "symbols": list(err.symbols),
                }
                for err in shape_errors
            ],
            "domain_status": assessment.status,
            "domain_obligations": summarize_domain_obligations(assessment),
            "requires_review": any(c.review_required for c in contracts),
        }
        if include_ast:
            payload["ast"] = parsed.root.to_dict()
        return _format_output(payload, response_format)

    @server.tool(
        name="fgl_check_compatibility",
        title="Check Shape Compatibility and Discharge Domain Obligations",
        description=(
            "Evaluate shape/dimensional compatibility (no implicit broadcasting) and attempt "
            "exact SymPy domain-obligation discharge (denominators, log, sqrt) for a formula "
            "under caller-supplied symbol contracts. Fails closed when symbols lack contracts "
            "or domain premises are insufficient or contradictory."
        ),
        annotations=_READ_ONLY_LOCAL,
    )
    def fgl_check_compatibility(
        formula: Annotated[
            str,
            Field(min_length=1, max_length=20_000, description="LaTeX or MathML formula to check."),
        ],
        contracts: Annotated[
            list[dict[str, object]] | None,
            Field(
                default=None,
                description=(
                    "List of symbol contracts: "
                    "{name, category, shape, domain, constraints}. "
                    "See resource fgl://schema/contract-presets."
                ),
            ),
        ] = None,
        source_format: Annotated[
            FormulaFormat,
            Field(default="latex", description="Input syntax: 'latex' or 'mathml'."),
        ] = "latex",
        response_format: Annotated[
            ResponseFormat,
            Field(default="json", description="Output format: 'json' or 'markdown'."),
        ] = "json",
    ) -> str:
        scope = "mcp:compatibility"
        try:
            parsed = parse_formula(formula, source_format=source_format)
        except FormulaParseError as exc:
            return _error_payload(
                "fgl_check_compatibility",
                exc.code,
                str(exc),
                response_format=response_format,
            )
        try:
            reviews = _build_caller_reviews(contracts, scope=scope)
        except (ValidationError, ValueError) as exc:
            return _error_payload(
                "fgl_check_compatibility",
                "INVALID_CONTRACT_SPEC",
                str(exc),
                hint="Read resource fgl://schema/contract-presets for valid contract fields.",
                response_format=response_format,
            )
        inferred = infer_contracts(parsed)
        resolved = _apply_reviews_matching(inferred, reviews, scope=scope)
        result_shape, shape_errors = infer_expression_shape(resolved, parsed)
        explicit_errors = check_shape_compatibility(resolved, parsed)
        obligations = infer_domain_obligations(resolved, parsed)
        discharged = discharge_domain_obligations_from_reviews(obligations, reviews, scope=scope)
        assessment = assess_domain_obligations(obligations, discharged)
        applied_assumptions = format_reviewed_assumptions(reviews, scope=scope)
        symbol_assumptions = build_symbol_assumptions(reviews, scope=scope)

        compatible = len(explicit_errors) == 0 and assessment.status == "discharged"
        payload: dict[str, object] = {
            "tool": "fgl_check_compatibility",
            "status": "ok",
            "compatible": compatible,
            "contract_provenance": "mcp_caller_supplied_assumptions",
            "contract_review_hash": contract_review_state_hash(reviews) if reviews else None,
            "syntax_hash": parsed.syntax_hash,
            "result_shape": list(result_shape) if result_shape is not None else None,
            "shape_status": "well_typed" if not shape_errors else "ill_typed",
            "shape_errors": [
                {
                    "message": err.message,
                    "location": err.node_path,
                    "symbols": list(err.symbols),
                }
                for err in shape_errors
            ],
            "domain_status": assessment.status,
            "domain_obligations": summarize_domain_obligations(assessment),
            "applied_assumptions": applied_assumptions,
            "sympy_assumptions": symbol_assumptions,
        }
        return _format_output(payload, response_format)

    @server.tool(
        name="fgl_verify_derivation",
        title="Verify Single Symbolic Rewrite or Multi-Step Derivation Chain",
        description=(
            "Verify algebraic equivalence for a single rewrite (source_latex -> target_latex) "
            "or a multi-step derivation chain (source_latex -> steps[0] -> ... -> steps[k-1], "
            "up to 8 steps) with shape checking, SymPy domain discharge, non-commutative "
            "matrix preservation, and optional audited Lean 4 DRAFT_SORRY export. Runs fail-closed "
            "in Docker sandbox when FGL_SANDBOX_IMAGE is set, local bounded subprocess only if "
            "explicitly opted in, or preflight_only otherwise."
        ),
        annotations=_READ_ONLY_LOCAL,
    )
    def fgl_verify_derivation(
        source_latex: Annotated[
            str,
            Field(min_length=1, max_length=20_000, description="Starting formula E_0 in LaTeX."),
        ],
        target_latex: Annotated[
            str | None,
            Field(
                default=None,
                max_length=20_000,
                description="Target formula E_1 for a single-step check (omit if passing steps).",
            ),
        ] = None,
        steps: Annotated[
            list[str] | None,
            Field(
                default=None,
                max_length=MAX_CHAIN_STEPS,
                description=(
                    "Ordered derivation steps [E_1, E_2, ..., E_k] (1 to 8 steps). "
                    "Each adjacent pair (E_{i-1}, E_i) is checked independently."
                ),
            ),
        ] = None,
        contracts: Annotated[
            list[dict[str, object]] | None,
            Field(
                default=None,
                description="Caller-supplied symbol contracts with domain/shape/constraints.",
            ),
        ] = None,
        timeout_ms: Annotated[
            int,
            Field(
                default=3_000,
                ge=100,
                le=10_000,
                description="Per-step execution deadline in milliseconds (100..10000).",
            ),
        ] = 3_000,
        include_lean_export: Annotated[
            bool,
            Field(
                default=False,
                description="Also emit an audited Lean 4 DRAFT_SORRY skeleton for the derivation.",
            ),
        ] = False,
        response_format: Annotated[
            ResponseFormat,
            Field(default="json", description="Output format: 'json' or 'markdown'."),
        ] = "json",
    ) -> str:
        if steps is None and not target_latex:
            return _error_payload(
                "fgl_verify_derivation",
                "MISSING_TARGET_OR_STEPS",
                "Provide either target_latex (single step) or steps (1..8 derivation steps).",
                response_format=response_format,
            )
        if steps is not None:
            cleaned_steps = [s.strip() for s in steps if isinstance(s, str) and s.strip()]
            if not 1 <= len(cleaned_steps) <= MAX_CHAIN_STEPS:
                return _error_payload(
                    "fgl_verify_derivation",
                    "INVALID_STEPS",
                    f"steps must contain between 1 and {MAX_CHAIN_STEPS} non-empty formulas.",
                    response_format=response_format,
                )
            if target_latex and target_latex.strip() != cleaned_steps[-1]:
                return _error_payload(
                    "fgl_verify_derivation",
                    "TARGET_STEPS_MISMATCH",
                    "When both target_latex and steps are set, target_latex must equal steps[-1].",
                    response_format=response_format,
                )
            chain_targets = cleaned_steps
        else:
            assert target_latex is not None
            chain_targets = [target_latex.strip()]

        scope = "mcp:derivation"
        try:
            reviews = _build_caller_reviews(contracts, scope=scope)
        except (ValidationError, ValueError) as exc:
            return _error_payload(
                "fgl_verify_derivation",
                "INVALID_CONTRACT_SPEC",
                str(exc),
                hint="Read resource fgl://schema/contract-presets for valid contract fields.",
                response_format=response_format,
            )

        exec_mode, sandbox_image = _resolve_symbolic_executor(
            allow_local_subprocess=allow_local_subprocess,
        )
        if exec_mode == "docker_sandbox":
            assert sandbox_image is not None
            worker_runner = partial(run_sandbox, image=sandbox_image)
        elif exec_mode == "local_bounded_subprocess":
            worker_runner = _run_worker_payload
        else:
            def worker_runner(payload, *, timeout_ms):  # type: ignore[misc]
                _ = (payload, timeout_ms)
                return {
                    "outcome": "unknown",
                    "error_code": "SANDBOX_NOT_CONFIGURED",
                }

        chain_all = [source_latex.strip(), *chain_targets]
        step_results: list[dict[str, object]] = []
        failed_step_index: int | None = None
        all_domain_statuses: list[str] = []
        merged_obligations: dict[str, dict[str, object]] = {}
        collected_domain_models = []

        for idx in range(len(chain_all) - 1):
            lhs = chain_all[idx]
            rhs = chain_all[idx + 1]
            check = run_symbolic_check(
                lhs,
                rhs,
                source_format="latex",
                timeout_ms=timeout_ms,
                worker_runner=worker_runner,
                contract_reviews=reviews,
                contract_review_scope=scope if reviews else None,
            )
            step_domain_status = check.vector.domain
            all_domain_statuses.append(step_domain_status)

            step_obligations_summary: list[dict[str, object]] = []
            try:
                p_lhs = parse_formula(lhs)
                p_rhs = parse_formula(rhs)
                if (
                    ast_node_count(p_lhs.root) <= MAX_CHECK_AST_NODES
                    and ast_node_count(p_rhs.root) <= MAX_CHECK_AST_NODES
                ):
                    c_lhs = _apply_reviews_matching(infer_contracts(p_lhs), reviews, scope=scope)
                    c_rhs = _apply_reviews_matching(infer_contracts(p_rhs), reviews, scope=scope)
                    o_lhs = infer_domain_obligations(c_lhs, p_lhs)
                    o_rhs = infer_domain_obligations(c_rhs, p_rhs)
                    d_lhs = discharge_domain_obligations_from_reviews(o_lhs, reviews, scope=scope)
                    d_rhs = discharge_domain_obligations_from_reviews(o_rhs, reviews, scope=scope)
                    a_lhs = assess_domain_obligations(o_lhs, d_lhs)
                    a_rhs = assess_domain_obligations(o_rhs, d_rhs)
                    for item in (
                        *summarize_domain_obligations(a_lhs),
                        *summarize_domain_obligations(a_rhs),
                    ):
                        oid = str(item["obligation_id"])
                        if oid not in merged_obligations:
                            merged_obligations[oid] = item
                        step_obligations_summary.append(item)
                    collected_domain_models.extend(a_lhs.obligations)
                    collected_domain_models.extend(a_rhs.obligations)
            except FormulaParseError:
                pass

            if check.outcome != "supported" and failed_step_index is None:
                failed_step_index = idx

            step_results.append(
                {
                    "step_index": idx,
                    "from_latex": lhs,
                    "to_latex": rhs,
                    "outcome": check.outcome,
                    "error_code": check.error_code,
                    "vector": check.vector.model_dump(mode="json"),
                    "witness": check.witness,
                    "counterexample": check.counterexample,
                    "duration_ms": check.duration_ms,
                    "domain_obligations": step_obligations_summary,
                }
            )

        outcomes = [str(s["outcome"]) for s in step_results]
        if all(o == "supported" for o in outcomes):
            overall_outcome = "supported"
        elif "refuted" in outcomes:
            overall_outcome = "refuted"
        else:
            overall_outcome = next(o for o in outcomes if o != "supported")

        first_error = next(
            (s["error_code"] for s in step_results if s["error_code"] is not None),
            None,
        )
        applied_assumptions = format_reviewed_assumptions(reviews, scope=scope) if reviews else []
        review_hash = contract_review_state_hash(reviews) if reviews else None

        payload: dict[str, object] = {
            "tool": "fgl_verify_derivation",
            "status": "ok",
            "scope": "ad_hoc_check_not_frozen_campaign",
            "contract_provenance": "mcp_caller_supplied_assumptions",
            "contract_review_hash": review_hash,
            "execution_mode": exec_mode,
            "execution_image": sandbox_image,
            "mode": "chain" if len(chain_targets) > 1 else "single",
            "step_count": len(step_results),
            "outcome": overall_outcome,
            "failed_step_index": failed_step_index,
            "error_code": first_error,
            "domain_status": _combined_domain_status(all_domain_statuses),
            "domain_obligations": list(merged_obligations.values()),
            "applied_assumptions": applied_assumptions,
            "steps": step_results,
        }
        if exec_mode == "preflight_only" and first_error == "SANDBOX_NOT_CONFIGURED":
            payload["hint"] = (
                "Preflight (AST, shape, and SymPy domain discharge) passed, but symbolic worker "
                "execution is fail-closed because FGL_SANDBOX_IMAGE is not set. Set "
                "FGL_SANDBOX_IMAGE=sha256:... for Docker isolation or start fgl-mcp with "
                "--allow-local-subprocess (or FGL_MCP_ALLOW_LOCAL_SUBPROCESS=true) for local runs."
            )
        if include_lean_export:
            lean_exp = generate_lean4_export(
                source_formula=source_latex.strip(),
                target_formula=chain_targets[-1],
                derivation_steps=chain_targets if len(chain_targets) > 1 else None,
                contract_reviews=reviews,
                domain_obligations=collected_domain_models,
                source_equation_uuid="mcp-ad-hoc",
                contract_review_hash=review_hash,
                scope=scope if reviews else None,
            )
            payload["lean_export"] = lean_exp.model_dump(mode="json")

        return _format_output(payload, response_format)

    @server.tool(
        name="fgl_export_lean4_draft",
        title="Generate and Five-Bin Audit a Lean 4 / Mathlib DRAFT_SORRY Theorem",
        description=(
            "Translate a pinned source/target equation (or multi-step derivation chain into a "
            "Lean `calc` block) plus accepted scalar/index contracts into a Mathlib-idiomatic "
            "Lean 4 `DRAFT_SORRY` theorem, then immediately run the independent fail-closed "
            "five-bin binder audit (SOURCE/STANDING/TYPING/RULED/EXCESS). Always reports "
            "PROOF_STATUS: FROZEN_UNPROVED."
        ),
        annotations=_READ_ONLY_LOCAL,
    )
    def fgl_export_lean4_draft(
        source_latex: Annotated[
            str,
            Field(min_length=1, max_length=20_000, description="Source formula E_0 in LaTeX."),
        ],
        target_latex: Annotated[
            str,
            Field(min_length=1, max_length=20_000, description="Target formula E_k in LaTeX."),
        ],
        contracts: Annotated[
            list[dict[str, object]],
            Field(
                min_length=1,
                description="Accepted scalar/index symbol contracts for every free symbol.",
            ),
        ],
        derivation_steps: Annotated[
            list[str] | None,
            Field(
                default=None,
                max_length=MAX_CHAIN_STEPS,
                description="Optional ordered intermediate+final steps for a Lean `calc` skeleton.",
            ),
        ] = None,
        source_equation_uuid: Annotated[
            str,
            Field(default="mcp-ad-hoc", max_length=200, description="Optional provenance UUID."),
        ] = "mcp-ad-hoc",
        response_format: Annotated[
            ResponseFormat,
            Field(default="json", description="Output format: 'json' or 'markdown'."),
        ] = "json",
    ) -> str:
        scope = "mcp:lean4"
        try:
            reviews = _build_caller_reviews(contracts, scope=scope)
        except (ValidationError, ValueError) as exc:
            return _error_payload(
                "fgl_export_lean4_draft",
                "INVALID_CONTRACT_SPEC",
                str(exc),
                response_format=response_format,
            )
        review_hash = contract_review_state_hash(reviews) if reviews else None
        export = generate_lean4_export(
            source_formula=source_latex,
            target_formula=target_latex,
            derivation_steps=derivation_steps,
            contract_reviews=reviews,
            source_equation_uuid=source_equation_uuid,
            contract_review_hash=review_hash,
            scope=scope,
        )
        payload: dict[str, object] = {
            "tool": "fgl_export_lean4_draft",
            "contract_provenance": "mcp_caller_supplied_assumptions",
            "contract_review_hash": review_hash,
            **export.model_dump(mode="json"),
        }
        return _format_output(payload, response_format)

    @server.tool(
        name="fgl_audit_lean4_statement",
        title="Audit Any Lean 4 Theorem Against Pinned Source LaTeX (Five-Bin Protocol)",
        description=(
            "Independently audit a Lean 4 theorem/lemma declaration (e.g., authored by an AI "
            "or proved via lean-lsp-mcp) against its pinned source/target LaTeX and contracts. "
            "Fails closed on EXCESS binders, carrier mismatches, conclusion drift, forbidden "
            "tokens (admit/axiom/native_decide/unsafe/sorryAx/header sorry), or non-standard "
            "axioms."
        ),
        annotations=_READ_ONLY_LOCAL,
    )
    def fgl_audit_lean4_statement(
        lean_code: Annotated[
            str,
            Field(min_length=1, max_length=65_536, description="Lean 4 theorem source code."),
        ],
        source_latex: Annotated[
            str,
            Field(min_length=1, max_length=20_000, description="Pinned source LaTeX expression."),
        ],
        target_latex: Annotated[
            str,
            Field(min_length=1, max_length=20_000, description="Pinned target LaTeX expression."),
        ],
        contracts: Annotated[
            list[dict[str, object]],
            Field(
                min_length=1,
                description="Accepted scalar/index symbol contracts for every free symbol.",
            ),
        ],
        standing_assumptions: Annotated[
            dict[str, str] | None,
            Field(
                default=None,
                description="Optional mapping of standing Lean prop -> citation string.",
            ),
        ] = None,
        author_rulings: Annotated[
            dict[str, str] | None,
            Field(
                default=None,
                description="Optional mapping of author-ruled Lean prop -> citation string.",
            ),
        ] = None,
        kernel_checked: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    "Set True ONLY when an external Lean 4 kernel verified this exact code "
                    "with zero sorry."
                ),
            ),
        ] = False,
        kernel_axioms: Annotated[
            list[str] | None,
            Field(
                default=None,
                description="Axioms reported by `#print axioms` after kernel verification.",
            ),
        ] = None,
        response_format: Annotated[
            ResponseFormat,
            Field(default="json", description="Output format: 'json' or 'markdown'."),
        ] = "json",
    ) -> str:
        scope = "mcp:lean4-audit"
        try:
            reviews = _build_caller_reviews(contracts, scope=scope)
        except (ValidationError, ValueError) as exc:
            return _error_payload(
                "fgl_audit_lean4_statement",
                "INVALID_CONTRACT_SPEC",
                str(exc),
                response_format=response_format,
            )
        try:
            report = audit_lean4_declaration(
                lean_code=lean_code,
                source_formula=source_latex,
                target_formula=target_latex,
                contract_reviews=reviews,
                standing_assumptions=standing_assumptions,
                author_rulings=author_rulings,
                scope=scope,
                kernel_checked=kernel_checked,
                kernel_axioms=kernel_axioms,
            )
        except Exception as exc:
            return _error_payload(
                "fgl_audit_lean4_statement",
                "LEAN_AUDIT_ERROR",
                str(exc),
                response_format=response_format,
            )
        payload: dict[str, object] = {
            "tool": "fgl_audit_lean4_statement",
            "status": "ok",
            **report.model_dump(mode="json"),
        }
        return _format_output(payload, response_format)

    @server.tool(
        name="fgl_search_evidence",
        title="Read-Only Workspace-Scoped Neo4j Exact Evidence Search",
        description=(
            "Search persisted exact evidence (equations, sections, papers) in Neo4j scoped "
            "strictly to the configured workspace (FGL_MCP_WORKSPACE_ID). Fails closed with "
            "actionable configuration guidance when Neo4j environment variables are not set."
        ),
        annotations=_READ_ONLY_DB,
    )
    async def fgl_search_evidence(
        query: Annotated[
            str,
            Field(min_length=1, max_length=500, description="Lexical/equation search query."),
        ],
        paper_id: Annotated[
            str | None,
            Field(default=None, max_length=64, description="Optional paper_id filter."),
        ] = None,
        limit: Annotated[
            int,
            Field(default=10, ge=1, le=50, description="Maximum hits to return (1..50)."),
        ] = 10,
        response_format: Annotated[
            ResponseFormat,
            Field(default="json", description="Output format: 'json' or 'markdown'."),
        ] = "json",
    ) -> str:
        neo4j_uri = os.getenv("NEO4J_URI", "").strip()
        neo4j_user = os.getenv("NEO4J_USER", "").strip()
        neo4j_password = os.getenv("NEO4J_PASSWORD", "").strip()
        workspace_id = os.getenv("FGL_MCP_WORKSPACE_ID", "").strip()
        missing = [
            key
            for key, val in (
                ("NEO4J_URI", neo4j_uri),
                ("NEO4J_USER", neo4j_user),
                ("NEO4J_PASSWORD", neo4j_password),
                ("FGL_MCP_WORKSPACE_ID", workspace_id),
            )
            if not val
        ]
        if missing:
            missing_list = ", ".join(missing)
            return _error_payload(
                "fgl_search_evidence",
                "EVIDENCE_STORE_NOT_CONFIGURED",
                f"Read-only Neo4j search is unavailable; missing env: {missing_list}.",
                hint=(
                    "Set NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, and FGL_MCP_WORKSPACE_ID "
                    "(workspace tenant boundary) to enable read-only workspace evidence search."
                ),
                response_format=response_format,
            )

        raw_secret = os.getenv("SEARCH_CURSOR_SECRET", "").strip()
        cursor_secret = (
            raw_secret
            if len(raw_secret.encode("utf-8")) >= 32
            else hashlib.sha256(f"fgl-mcp-cursor:{workspace_id}".encode()).hexdigest()
        )
        store = Neo4jEvidenceStore.connect(
            neo4j_uri,
            neo4j_user,
            neo4j_password,
            database=os.getenv("NEO4J_DATABASE") or "neo4j",
        )
        try:
            service = EvidenceSearchService(
                repository=store,
                cursor_codec=SearchCursorCodec(cursor_secret),
            )
            req = EvidenceSearchRequest(
                workspace_id=workspace_id,
                query=query,
                paper_id=paper_id,
                limit=limit,
            )
            res = await service.search(req)
        except Exception as exc:
            return _error_payload(
                "fgl_search_evidence",
                "EVIDENCE_SEARCH_FAILED",
                f"Neo4j search failed ({type(exc).__name__}): {exc}",
                hint="Verify Neo4j is reachable and FGL_MCP_WORKSPACE_ID is valid.",
                response_format=response_format,
            )
        finally:
            await store.close()

        payload: dict[str, object] = {
            "tool": "fgl_search_evidence",
            "status": "ok",
            "workspace_id": workspace_id,
            "query": query,
            "hit_count": len(res.hits),
            "semantic_available": res.semantic_available,
            "next_cursor": res.next_cursor,
            "hits": [hit.model_dump(mode="json") for hit in res.hits],
        }
        return _format_output(payload, response_format)

    return server


mcp = create_mcp_server()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="fgl-mcp",
        description="FormulaGraph Lab Model Context Protocol (MCP) server.",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
        help="MCP transport protocol (default: stdio).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host for streamable-http transport (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Bind port for streamable-http transport (default: 8765).",
    )
    parser.add_argument(
        "--allow-local-subprocess",
        action="store_true",
        help=(
            "Explicitly allow bounded local no-eval SymPy subprocess execution when "
            "FGL_SANDBOX_IMAGE is unset (otherwise fails closed at preflight_only)."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    server = create_mcp_server(
        host=args.host,
        port=args.port,
        allow_local_subprocess=args.allow_local_subprocess,
    )
    server.run(transport=args.transport)
    return 0


if __name__ == "__main__":
    sys.exit(main())
