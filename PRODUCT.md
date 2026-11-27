# Product

<!-- impeccable:product-schema 1 -->

## Platform

Web Application & Sandboxed Symbolic/Numerical Verification Harness

## Stack

Next.js 15 (Vinext / React 19 RSC) + TypeScript 5.7 + Tailwind CSS v4 + Rust/WASM Web Worker Layout Engine (`crates/formula-layout-wasm`) + ReactFlow (`@xyflow/react`) + Three.js WebGL (`InstancedMesh`) + Cloudflare D1 (Drizzle ORM) + Python 3.12 FastAPI + Neo4j 5.26 + Ephemeral Docker cgroup Sandbox

## Users

Primary users: Theoretical physicists, ML systems researchers, and formal verification engineers who need to trace equations back to primary arXiv HTML or local `.tex` / LaTeX sources, inspect and batch-review symbol/domain contracts, run fast ad-hoc symbolic equivalence checks or bounded evolutionary campaigns, and verify or refute hypotheses without silent hallucination.

## Product Purpose

Convert scientific research papers into an immutable, temporal, source-bound mathematical knowledge graph and verification harness—strictly separating ground-truth extracted evidence (`exact_evidence`) from AI-generated hypotheses (`proposals`) and ad-hoc checks (`ad_hoc_check_not_frozen_campaign`), while verifying transformations inside isolated, zero-network symbolic and numerical sandboxes.

## Positioning

Unlike general-purpose LLM chat interfaces or unverified CAS notebooks that fabricate equations, conflate conjectures with theorems, and leak holdout evaluation criteria, FormulaGraph Lab enforces:
1. **Source-Bound Provenance**: Canonical HTTPS `arxiv.org` HTML/MathML/LaTeX ingestion (`S*.E*` anchors) and direct single-file `.tex` / LaTeX ingestion (`tex_<12hex>`, `urn:fgl:local-tex:<sha256>`, immutable `L<start>-L<end>` line anchors) with SHA-256 content hashes, section hierarchies, and revision timestamps.
2. **Gated 5-Stage Research Pipeline + Fast-Path Inspector Verification**: Sequential progression across `01 Graph → 02 Spec → 03 Compatibility → 04 Proposals → 05 Reports`, paired with inline Inspector **Batch Contract Review** (draft presets + append-only human review records) and **Quick Symbolic Verify** (`ad-hoc check · not frozen campaign`), plus `/ops` workspace health telemetry and `/help` onboarding.
3. **Hermetic Proposer/Verifier Separation & Holdout Lock**: Untrusted AI proposals require explicit human review (`accept_for_compilation` / `accept_protocol_scope`) before DSL compilation; execution runs in non-root, `--network=none` Docker containers pinned by `sha256:` image digest and `ExecutionProfile` (`sandbox-policy.v2`); holdout splits remain locked from search until finalist freeze and can be replayed offline without network or LLM calls.

## Operating Context

- **Primary Desktop Research Workspace**: Three-column workbench (`1280×720`+ and wide monitors) featuring left multi-paper source rail, center 2D Section Swimlanes (`@xyflow/react`) / 3D Sacred Timeline (`Three.js` WebGL) viewport, and right formula/provenance inspector, with keyboard navigation (`Ctrl+K` / `/` search, arrow keys for graph node traversal).
- **Responsive Narrow & Tablet Viewports**: Collapsible rails and wrapping workflow status bar tested down to `390px` width without horizontal formula overflow.
- **Verification & Operations Context**: Automated CLI & CI gates (`Makefile`, `scripts/verify-research.ps1`, `scripts/test-integration.ps1`, `scripts/test-sandbox.ps1`, `scripts/test-phase5.ps1`, `scripts/replay-bundle.ps1`) plus semantic desktop inspection via `desktop-cli`.

## Capabilities and Constraints

- **Bounded arXiv & Direct `.tex` Ingestion**: Accepts canonical `https://arxiv.org/(html|abs)/<id>` URLs (`\d{4}\.\d{4,5}(v\d+)?`, 5 MiB HTML fetch cap, 4 KiB import JSON body cap, zero redirects/credentials/custom ports) and direct single-file `.tex` / LaTeX source uploads (`POST /api/imports/tex`, 512 KiB cap, bounded non-Turing-complete `\newcommand`/`\renewcommand`/`\providecommand`/`\DeclareMathOperator` expansion with cycle/depth/size ceilings and immutable raw `source_sha256`/`source_fragments`; external `\input{}`/`\include{}` file reads remain ignored).
- **Multi-Hop Hybrid Search**: Combines exact BM25 lexical search with optional Graphiti semantic retrieval and 2-hop graph neighborhood ranking over signed HMAC keyset cursors.
- **Symbolic, Contract & Dimensional Verification**: Structural AST parsing (`formula_ast.py`), single and batch physical/shape/domain contract reviews (`symbol_contracts.py`, `BatchContractReviewPanel`), SymPy assumption-driven domain obligation discharge (`discharged` / `unresolved` / `contradictory`, keeping matrices non-commutative and branch cuts sound without `force=True`), single-step & multi-step derivation chain checks (`QuickVerifyPanel`, `POST /v1/checks/symbolic/quick`), deterministic Lean 4 / Mathlib `DRAFT_SORRY` export with fail-closed five-bin statement auditing (`lean_formalizer.py`, `PROOF_STATUS: FROZEN_UNPROVED`), and bounded CPU numerical/research-case workers (`numerical_worker.py`, `research_case_worker.py`).
- **Agent-Native MCP Server (`fgl-mcp`) & Live Operations Observability (`/ops`)**: Optional `FastMCP` server (`services/graph-api/app/mcp_server.py`, `stdio` + `streamable-http`) exposing 7 read-only tools (`fgl_extract_tex`, `fgl_analyze_formula`, `fgl_check_compatibility`, `fgl_verify_derivation`, `fgl_export_lean4_draft`, `fgl_audit_lean4_statement`, `fgl_search_evidence`) and 2 policy/schema resources with fail-closed sandbox execution; `/ops` surfaces tenant-partitioned 15-minute `P50 / P95 / P99` route latency, `4xx / 5xx` error rates, and live BFF round-trip latency (`x-bff-duration-ms`) alongside dead-letter reconciliation and automated D1/Neo4j disaster-recovery drills.
- **Epistemic & Scope Constraint**: Quick symbolic checks remain explicitly scoped to `ad_hoc_check_not_frozen_campaign`, and registered research-case benchmarks operate under `synthetic_operator_only_no_product_claim` (CPU-only synthetic feature-map family). They never claim author-code reproduction, external dataset SOTA, or global mathematical proof when domain obligations remain conditional.

## Brand Commitments

- **Visual World**: **"Scientific Precision & Epistemic Honesty"** — high-clarity academic surface hierarchy inspired by mathematical typesetting (KaTeX) and structural provenance graphs, paired with strict non-overclaiming verification labels.
- **Absolute Bans on AI Slop**: Zero decorative hero marketing copy, zero fake `"Verified"` / `"100% Proven"` badges on unreviewed or conditional results, zero hidden model-generated fitness scores, and zero unstyled raw LaTeX overflow breaking card containers.
- **Palette & Contrast**: Tinted cool academic neutrals (`--background: oklch(0.975 0.012 255)`), authoritative cobalt primary (`--primary: oklch(0.46 0.18 256)`), and strictly separated semantic tokens for Evidence, Citation, Derivation, and Review (`--color-evidence`, `--color-citation`, `--color-derivation`, `--color-review`).

## Evidence on Hand

- `reports/benchmark-summary.json`: Reproducible empirical benchmark across 14 frozen arXiv HTML excerpts (`24/24 = 100%` source-anchor extraction at `0.60 ms` median / `1.66 ms` P95; `19/24 = 79.2%` strict fail-closed AST parse covering `100%` of real mathematical equations in the corpus at `0.28 ms` median / `0.70 ms` P95) plus ephemeral Docker sandbox lifecycle latencies.
- `docs/PHASE5_COMPLETION.md`: End-to-end Phase 5 acceptance, bounded Pareto evolution, locked holdout confirmation, clean-container replay, and retrieval A/B benchmark (`reports/phase5-retrieval-ab.json`).
- `docs/PHASE5_FLOW_AUDIT.json` & `docs/UI_UPGRADE_AUDIT.md`: Preflight/postflight UX flow audit and ranked workspace hierarchy audit.
- `reports/FGL601_AUTH_HANDOFF.md`, `reports/FGL602_FGL603_HARDENING.md`, `reports/PLAYBOOK3_SECURITY_SUPPLY_CHAIN_AUDIT.md`, `reports/BACKUP_RESTORE_DRILL_RUNBOOK.md`, `reports/backup-restore-drill-receipt.json`, `reports/SPRINT6_REVIEW_2026-10-01.md`, and `reports/REMAINING_WORK.md`: Security hardening evidence, signed `fgl1` protocol specification, Neo4j distributed quota/nonce ledger, CSP `'wasm-unsafe-eval'` + nonce policy, Threat Model P1 verification, isolated Cloudflare D1 + Neo4j 5.26 backup/restore drill receipt (`RTO 2.98s` D1 / `14.68s` Neo4j), and open production release gates.
- `.agents/skills/MANIFEST.json`, `services/graph-api/evals/fgl_mcp_eval.xml`, & Automated verification suites across `services/graph-api/tests/`, `tests/`, and `crates/formula-layout-wasm/`: Curated 24-skill agentic engineering arsenal paired with tri-language Property-Based Testing (`Hypothesis`, `fast-check`, `proptest`), 10-question `fgl-mcp` deterministic eval suite, offline regression, Neo4j integration, adversarial Docker sandbox, and HTTP/CSP smoke gates.

## Strategic Horizon (`v1.2+` Roadmap — Planned, Not Yet Claimed)

1. **Live Lean 4 Kernel Checking & Z3 Nonlinear Inequality Discharge**: Connect the audited `lean_formalizer.py` `DRAFT_SORRY` output (and `fgl_audit_lean4_statement` MCP tool) to an isolated Lean 4 / Mathlib kernel checker (promoting to `PROVED` strictly on zero-`sorry` standard-axiom compilation) and pair SymPy assumption discharge with Z3 SMT for nonlinear domain inequalities.
2. **Multi-File `.tex` Project Graphs & Differential Formal Fuzzing**: Extend single-file `.tex` ingestion to bounded multi-file archives with cross-file dependency tracking and differential SymPy vs. Lean 4 fuzzing across richer matrix/tensor and custom LaTeX environments.
3. **Standalone Unix CLI & Packaged MCP Registry Distribution**: Package `fgl-mcp` and a standalone researcher CLI for zero-config PyPI / Homebrew installation alongside live remote workspace token authentication.
4. **Production Staging Cutover & Release-Gate Closure**: Complete staging multi-tenant ingress verification (`FGL_TRUST_SITES_IDENTITY`), external durable append-only audit sink, remote production Cloudflare D1 (`--remote`) / Neo4j KMS-encrypted backup drill, and upstream Debian base-image CVE remediation for a green `Container CVE Gate`.

## Product Principles

1. **Epistemic Separation First**: Extracted ground-truth formulas (`exact_evidence`) are immutable and cryptographically hashed; AI proposals and candidate derivations remain explicitly provisional unless independently checked and human-reviewed.
2. **Fail-Closed By Default**: Research execution flags (`FGL_ENABLE_*`), trusted Sites ingress (`FGL_TRUST_SITES_IDENTITY`), and Graphiti LLM enrichment remain disabled until explicitly configured and authorized; unknown/timeout/infrastructure failures never count as scientific support.
3. **Deterministic Reproducibility**: Every candidate, binding, check, and campaign event records its exact spec hash, parent references, image digest, and `ExecutionProfile` so bundles replay identically offline.
4. **Honest Empty & Degraded States**: Unavailable databases, unverified drafts, and missing telemetry render explicit degraded/unavailable states rather than synthetic zeros or misleading green indicators.

## Accessibility & Inclusion

Full WCAG 2.1 AA contrast compliance verified via `tests/contrast-tokens.test.ts` (`>= 4.5:1` for muted labels on card, shell, and badge surfaces), `44px` minimum interactive touch targets on onboarding toggles and stage links, semantic `aria-live` status announcements for async verification actions, arrow-key node traversal in the 2D graph viewport, and `prefers-reduced-motion` respect across transitions.
