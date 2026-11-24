# Product

<!-- impeccable:product-schema 1 -->

## Platform

Web Application & Sandboxed Symbolic/Numerical Verification Harness

## Stack

Next.js 15 (Vinext / React 19 RSC) + TypeScript 5.7 + Tailwind CSS v4 + ReactFlow (`@xyflow/react`) + Three.js WebGL + Cloudflare D1 (Drizzle ORM) + Python 3.12 FastAPI + Neo4j 5.26 + Ephemeral Docker cgroup Sandbox

## Users

Primary users: Theoretical physicists, ML systems researchers, and formal verification engineers who need to trace equations back to primary arXiv HTML sources, inspect symbol/domain contracts, formulate bounded mathematical transformations, and verify or refute hypotheses without silent hallucination.

## Product Purpose

Convert scientific research papers into an immutable, temporal, source-bound mathematical knowledge graph and verification harness—strictly separating ground-truth extracted evidence (`exact_evidence`) from AI-generated hypotheses (`proposals`) and verifying transformations inside isolated, zero-network symbolic and numerical sandboxes.

## Positioning

Unlike general-purpose LLM chat interfaces or unverified CAS notebooks that fabricate equations, conflate conjectures with theorems, and leak holdout evaluation criteria, FormulaGraph Lab enforces:
1. **Source-Bound Provenance**: Canonical HTTPS `arxiv.org` HTML/MathML/LaTeX ingestion with SHA-256 content hashes, section hierarchies, revision timestamps, and immutable equation anchors (`S*.E*`).
2. **Gated 5-Stage Research Pipeline**: Sequential progression across `01 Graph → 02 Spec → 03 Compatibility → 04 Proposals → 05 Reports`, paired with `/ops` workspace health telemetry and `/help` onboarding.
3. **Hermetic Proposer/Verifier Separation & Holdout Lock**: Untrusted AI proposals require explicit human review (`accept_for_compilation` / `accept_protocol_scope`) before DSL compilation; execution runs in non-root, `--network=none` Docker containers pinned by `sha256:` image digest and `ExecutionProfile` (`sandbox-policy.v2`); holdout splits remain locked from search until finalist freeze and can be replayed offline without network or LLM calls.

## Operating Context

- **Primary Desktop Research Workspace**: Three-column workbench (`1280×720`+ and wide monitors) featuring left multi-paper source rail, center 2D Section Swimlanes (`@xyflow/react`) / 3D Sacred Timeline (`Three.js` WebGL) viewport, and right formula/provenance inspector, with keyboard navigation (`Ctrl+K` / `/` search, arrow keys for graph node traversal).
- **Responsive Narrow & Tablet Viewports**: Collapsible rails and wrapping workflow status bar tested down to `390px` width without horizontal formula overflow.
- **Verification & Operations Context**: Automated CLI & CI gates (`Makefile`, `scripts/verify-research.ps1`, `scripts/test-integration.ps1`, `scripts/test-sandbox.ps1`, `scripts/test-phase5.ps1`, `scripts/replay-bundle.ps1`) plus semantic desktop inspection via `desktop-cli`.

## Capabilities and Constraints

- **Bounded arXiv Ingestion**: Accepts only canonical `https://arxiv.org/(html|abs)/<id>` URLs (`\d{4}\.\d{4,5}(v\d+)?`), enforces a 5 MiB HTML fetch cap and 4 KiB import JSON body cap, and blocks redirects, credentials, and custom ports.
- **Multi-Hop Hybrid Search**: Combines exact BM25 lexical search with optional Graphiti semantic retrieval and 2-hop graph neighborhood ranking over signed HMAC keyset cursors.
- **Symbolic, Contract & Dimensional Verification**: Structural AST parsing (`formula_ast.py`), physical/shape/domain contract reviews (`symbol_contracts.py`), SymPy symbolic equivalence checks, and bounded CPU numerical/research-case workers (`numerical_worker.py`, `research_case_worker.py`).
- **Epistemic & Scope Constraint**: Current registered research-case benchmarks operate under `synthetic_operator_only_no_product_claim` (CPU-only synthetic feature-map family). They never claim author-code reproduction, external dataset SOTA, or global mathematical proof when domain obligations remain conditional.

## Brand Commitments

- **Visual World**: **"Scientific Precision & Epistemic Honesty"** — high-clarity academic surface hierarchy inspired by mathematical typesetting (KaTeX) and structural provenance graphs, paired with strict non-overclaiming verification labels.
- **Absolute Bans on AI Slop**: Zero decorative hero marketing copy, zero fake `"Verified"` / `"100% Proven"` badges on unreviewed or conditional results, zero hidden model-generated fitness scores, and zero unstyled raw LaTeX overflow breaking card containers.
- **Palette & Contrast**: Tinted cool academic neutrals (`--background: oklch(0.975 0.012 255)`), authoritative cobalt primary (`--primary: oklch(0.46 0.18 256)`), and strictly separated semantic tokens for Evidence, Citation, Derivation, and Review (`--color-evidence`, `--color-citation`, `--color-derivation`, `--color-review`).

## Evidence on Hand

- `docs/PHASE5_COMPLETION.md`: End-to-end Phase 5 acceptance, bounded Pareto evolution, locked holdout confirmation, clean-container replay, and retrieval A/B benchmark (`reports/phase5-retrieval-ab.json`).
- `docs/PHASE5_FLOW_AUDIT.json` & `docs/UI_UPGRADE_AUDIT.md`: Preflight/postflight UX flow audit and ranked workspace hierarchy audit.
- `reports/FGL601_AUTH_HANDOFF.md`, `reports/FGL602_FGL603_HARDENING.md`, and `reports/SPRINT6_REVIEW_2026-10-01.md`: Security hardening evidence, signed `fgl1` protocol specification, Neo4j distributed quota/nonce ledger, CSP nonce policy, and open production release gates.
- Automated verification suites across `services/graph-api/tests/` and `tests/`.

## Product Principles

1. **Epistemic Separation First**: Extracted ground-truth formulas (`exact_evidence`) are immutable and cryptographically hashed; AI proposals and candidate derivations remain explicitly provisional unless independently checked and human-reviewed.
2. **Fail-Closed By Default**: Research execution flags (`FGL_ENABLE_*`), trusted Sites ingress (`FGL_TRUST_SITES_IDENTITY`), and Graphiti LLM enrichment remain disabled until explicitly configured and authorized; unknown/timeout/infrastructure failures never count as scientific support.
3. **Deterministic Reproducibility**: Every candidate, binding, check, and campaign event records its exact spec hash, parent references, image digest, and `ExecutionProfile` so bundles replay identically offline.
4. **Honest Empty & Degraded States**: Unavailable databases, unverified drafts, and missing telemetry render explicit degraded/unavailable states rather than synthetic zeros or misleading green indicators.

## Accessibility & Inclusion

Full WCAG 2.1 AA contrast compliance verified via `tests/contrast-tokens.test.ts` (`>= 4.5:1` for muted labels on card, shell, and badge surfaces), `44px` minimum interactive touch targets on onboarding toggles and stage links, semantic `aria-live` status announcements for async verification actions, arrow-key node traversal in the 2D graph viewport, and `prefers-reduced-motion` respect across transitions.
