# FormulaGraph Lab — implementation plan

## Product decision

FormulaGraph Lab is a research workspace that turns HTML papers into a temporal,
source-bound formula graph. AI may propose relationships and transformations, but
only deterministic parsers and symbolic/numerical checks can promote a proposal
to a verified state.

Primary user: an ML researcher, research engineer, or advanced student comparing
the mathematical lineage of several papers.

Primary loop:

1. Import an allowlisted HTML paper URL.
2. Review extracted equations, symbols, assumptions, and source anchors.
3. Explore relationships across paper versions and other papers.
4. Select formulas and ask AI for typed transformations.
5. Run shape, domain, symbolic, and numerical checks.
6. Save the result as a versioned hypothesis with complete provenance.

## Non-negotiable invariants

- Evidence and AI hypotheses are different entity types and never share a
  verification status.
- Every evidence node resolves to a paper version and exact HTML anchor.
- A claim in one paper cannot invalidate a claim in another paper.
- Only revisions in the same paper lineage may supersede earlier facts.
- The LLM never executes arbitrary Python and never writes a verified result.
- The ingestion service accepts only explicitly supported hosts and URL shapes.
- Raw HTML is sanitized and is never rendered or executed in the application.
- Tenant/workspace identity is checked server-side on every write.

## System boundary

```text
Vinext web app
  ├─ ChatGPT sign-in and workspace metadata (Sites + D1)
  ├─ paper import, graph explorer, inspector, hypothesis workbench
  └─ HTTPS client to Graph API

Python Graph API
  ├─ URL gate and HTML fetcher
  ├─ MathML/LaTeX extraction
  ├─ formula normalization and validation
  ├─ Graphiti episode/ontology adapter
  └─ experiment runner interface

Neo4j
  └─ temporal evidence, claims, formula relationships, hypotheses
```

The Sites runtime does not open a raw Neo4j TCP connection. Only the Python
Graph API talks to Neo4j; the web app communicates with it over authenticated
HTTPS.

## Sprint 0 — repository and contracts

Goal: a new project independent from `quantum-platform-v2`, with a recognizable
working surface and repeatable checks.

### FGL-001 Project scaffold

- Flow: create Vinext site with Shadcn, D1, and ChatGPT auth capabilities.
- Output: project metadata, lockfile, local development command.
- Tests: production build; route returns HTTP 200.

### FGL-002 Research workspace shell

- Flow: URL field → paper evidence list → graph → formula inspector.
- States: demo, invalid URL, accepted URL, selected formula.
- Tests: component interaction test for validation and node selection.

### FGL-003 Architecture and handoff contract

- Output: this plan, README, environment example, service boundary.
- Tests: documentation commands are exercised on a clean checkout.

Definition of done:

- The project runs without reading or changing the old quantum repository.
- The first viewport exposes the research workflow, not a marketing page.
- Build, frontend tests, and backend unit tests have named commands.

## Sprint 1 — deterministic HTML/MathML ingestion

Goal: transform an arXiv HTML page into stable structured extraction output.

### FGL-101 Strict URL policy

- Input: user-provided string.
- Flow: parse → require HTTPS → reject credentials and custom ports → match exact
  allowlisted host → normalize `/abs/<id>` to `/html/<id>` → reject other paths.
- Output: canonical URL or typed validation error.
- Tests: valid modern/legacy arXiv IDs, query stripping, HTTP rejection,
  subdomain confusion, user-info attack, encoded path attack, localhost/private
  address, unexpected redirect.

### FGL-102 Safe fetcher

- Flow: canonical URL → bounded HTTP request → verify final URL and content type
  → enforce byte limit → return UTF-8 HTML.
- Limits: 10 seconds, 5 MiB, no automatic cross-host redirects.
- Tests: timeout, oversized body, wrong MIME, redirect, non-2xx response.

### FGL-103 Equation extractor

- Flow: parse DOM → locate MathML → prefer TeX annotation → fall back to
  `alttext`/MathML text → find equation number, section, surrounding paragraph,
  and anchor → deduplicate.
- Output: `ExtractedEquation[]`.
- Tests: annotated MathML, presentation-only MathML, nested equation, duplicate
  rendering, missing ID, malformed HTML.

### FGL-104 Paper version metadata

- Flow: extract title, authors, arXiv ID/version, published date, section order.
- Tests: versioned and unversioned URL, missing optional metadata, deterministic
  hash for repeated imports.

Definition of done:

- A fixture corpus of at least 10 representative arXiv HTML pages passes.
- Re-importing identical HTML produces identical IDs and no duplicate equations.

## Sprint 2 — Graphiti temporal evidence graph

Goal: incrementally ingest structured paper episodes into Neo4j via Graphiti.

### FGL-201 Prescribed ontology

Entities: `Paper`, `PaperVersion`, `Section`, `Equation`, `Symbol`,
`Assumption`, `Claim`, `Concept`, `Method`, and `Experiment`.

Edges: `HAS_VERSION`, `CONTAINS`, `DEFINES`, `USES`, `ASSUMES`,
`CITES`, `MAKES_CLAIM`, `ABOUT`, `DERIVED_FROM`, `APPROXIMATES`,
`GENERALIZES`, `EQUIVALENT_UNDER`, and `SUPERSEDES`.

- Tests: schema serialization, allowed source/target pairs, rejected unknown
  entity and relation types.

### FGL-202 Episode builder

- One paper version is a saga.
- Metadata and each ordered section are separate episodes.
- `group_id` is a research workspace so cross-paper retrieval remains possible.
- Exact equations and deterministic edges use structured JSON/direct triplets;
  prose uses LLM-assisted extraction.
- Tests: ordering, stable episode UUID, provenance mapping, idempotent replay.

### FGL-203 Revision semantics

- New version creates `SUPERSEDES` links.
- Only facts scoped to the same paper identity are invalidation candidates.
- Cross-paper disagreements become separate `Claim` nodes.
- Tests: v2 supersedes v1; unrelated paper remains valid; historical query
  returns the correct version.

Definition of done:

- Docker integration test imports two versions and one conflicting paper.
- Every graph edge can be traced back to one or more episode UUIDs.

## Sprint 3 — retrieval and graph UX

Goal: answer research questions with hybrid retrieval and inspectable evidence.

### FGL-301 Search API

- Semantic + BM25 + graph traversal.
- Filters: workspace, paper, version, entity type, verification status, time.
- Tests: exact symbol search, paraphrase search, graph-neighbor ranking, tenant
  isolation, deterministic pagination.

### FGL-302 Graph viewport

- Pan/zoom, keyboard navigation, node type legend, relation filters.
- Selecting a node updates the same inspector used by search results.
- Tests: keyboard selection, filters, small screen fallback, 200% text zoom.

### FGL-303 Provenance inspector

- Show source text, HTML anchor, episode, extraction confidence, paper version,
  and graph relation history.
- Tests: broken anchor state, superseded fact, multiple supporting episodes.

## Sprint 4 — formula canonicalization and type system

Goal: compare mathematical structure rather than raw LaTeX strings.

### FGL-401 Formula AST

- Parse supported LaTeX/MathML subset into operator tree.
- Track free/bound variables, indices, scalars, vectors, matrices, tensors,
  functions, and distributions.
- Tests: golden AST corpus, malformed input, binder scope, indexed notation.

### FGL-402 Canonical identity

- Alpha-renaming, commutative normalization where mathematically valid, stable
  canonical hash.
- Tests: renamed-equivalent formulas match; non-equivalent formulas do not;
  hash stability across processes.

### FGL-403 Symbol contracts

- Infer/confirm shape, domain, constraints, and local scope.
- Human confirmation is required below the confidence threshold.
- Tests: tensor multiplication, broadcasting rejection, denominator domain,
  symbol shadowing between sections.

## Sprint 5 — hypothesis mashup and verification

Goal: generate research moves that are explicit, typed, reproducible, and honest.

### FGL-501 Transformation DSL

Allowed operations include substitute operator, compose objectives, add
regularizer, interpolate losses, add constraints, and replace update rules.

- Tests: schema validation, unsupported operation rejection, no arbitrary code.

### FGL-502 AI proposal service

- Retrieve evidence → construct constrained prompt → return structured
  transformation → validate DSL → save as unverified `Hypothesis`.
- Tests: malformed model output, fabricated node ID, missing assumptions,
  evidence/hypothesis separation.

### FGL-503 Verification pipeline

- Shape and domain checks.
- Symbolic simplification/equivalence where supported.
- Limiting cases, randomized counterexamples, finite gradient and NaN/Inf checks.
- Statuses: `invalid`, `well_typed`, `numerically_plausible`,
  `symbolically_verified`, `human_reviewed`.
- Tests: known identities, known counterexamples, singularities, deterministic
  seeded runs, resource budget enforcement.

## Sprint 6 — identity, security, and operations

Goal: private workspaces and production-safe ingestion.

### FGL-601 Authentication and authorization

- ChatGPT/Sites identity for the hosted UI.
- Signed service token between web app and Graph API.
- Workspace ownership checks on every read/write.
- Tests: anonymous denial, cross-workspace access, expired token, forged headers.

### FGL-602 OWASP controls

- SSRF allowlist, output encoding, CSP, rate limits, request/body caps, secret
  isolation, audit events, dependency scanning.
- Tests: OWASP-oriented abuse cases; logs contain no source HTML or credentials.

### FGL-603 Observability

- Correlation ID from import to episode/graph write.
- Metrics: import duration, extraction yield, model cost, graph writes,
  verification outcomes.
- Tests: retry behavior, partial failure recovery, idempotency.

## Sprint 7 — beta and monetization

Goal: a narrow paid beta for researchers.

- Free: public graph exploration and limited imports.
- Pro: private workspaces, larger import quota, mashup runs, parameter sweeps,
  notebook/JAX/PyTorch export.
- Team: shared graph, review workflow, private ontology, audit trail.

Beta gate:

- 30 curated papers in one mathematical family.
- At least 90% equation extraction precision on the reviewed fixture set.
- Zero evidence/hypothesis presentation ambiguity in usability testing.
- At least five researchers complete import → relation → hypothesis → validation
  without developer help.

## AI handoff protocol

At the end of every implementation session, the next AI must:

1. Read this plan and `README.md`.
2. Run `git status --short`; never discard unrelated user changes.
3. Run the tests for the area being changed before editing.
4. Pick the earliest unfinished task ID; do not start a later sprint to avoid a
   failing gate.
5. Update the task status and evidence in `docs/STATUS.md`.
6. Record commands and exact failures, but never record credentials.
7. Finish with build/tests and a concise list of remaining risks.
