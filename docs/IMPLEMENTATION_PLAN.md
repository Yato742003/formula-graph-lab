# FormulaGraph Lab — implementation plan

## Product decision

FormulaGraph Lab is a problem-driven research workspace that turns HTML papers
into a temporal, source-bound formula graph. It helps a researcher trace the
lineage of formulas, identify compatible branches, create typed research moves,
and evaluate candidates against a frozen research objective.

The product does not promise that AI invents correct mathematics. AI proposes;
the Transformation DSL constrains and replays the change; independent checkers
produce scoped evidence; an evolution controller selects only from eligible
results under a fixed budget.

Primary user: an ML researcher, research engineer, or advanced student comparing
the mathematical lineage of several papers.

First research case: causal attention for long sequences. The initial study asks
whether a typed kernel/feature-map transformation can reduce latency or peak
memory while staying inside an approved quality-loss bound on a pinned model,
dataset, implementation, hardware profile, and compute budget. This case is a
demonstrator, not a claim that the candidate will outperform its baselines.

Primary loop:

1. Import an allowlisted HTML paper URL.
2. Review extracted equations, symbols, assumptions, and source anchors.
3. Define and freeze a versioned `ProblemSpec`, evaluator, baselines, and budget.
4. Explore citation, mathematical-lineage, compatibility, and implementation
   relationships without conflating them.
5. Select formulas and ask AI for a structured proposal.
6. Compile the proposal through an allowlisted Transformation DSL into an
   immutable candidate and explicit proof obligations.
7. Run independent static, symbolic, numerical, and empirical checks as required.
8. Save every result, counterexample, parent, version, and environment reference.
9. Admit eligible candidates to a budgeted evolution loop or export a
   reproducible research bundle.

## Non-negotiable invariants

- Source evidence, AI proposals, compiled candidates, claims, and check results
  are different immutable entity types.
- Every evidence node resolves to a paper version and exact HTML anchor.
- A claim in one paper cannot invalidate a claim in another paper.
- Only revisions in the same paper lineage may supersede earlier facts.
- Citation, mathematical derivation, approximation, common objective,
  compatibility, implementation optimization, and evolutionary parentage are
  distinct relation types.
- The earliest paper in the current corpus is not presented as the historical
  origin unless independent evidence supports that assertion.
- Extraction confidence, type inference confidence, human review, and checker
  evidence are independent fields. None automatically promotes another.
- Unknown, unsupported, timeout, and error never silently become success or
  mathematical refutation.
- The LLM never executes arbitrary code, changes a frozen evaluator, writes a
  check result, assigns fitness, or approves its own assumption.
- A numerical test supports only the tested domain, implementation, seed, dtype,
  tolerance, and suite. It is not a proof over all inputs.
- A symbolic identity does not imply empirical ML improvement; an empirical win
  does not imply symbolic equivalence.
- The ingestion service accepts only explicitly supported hosts and URL shapes.
- Raw HTML is sanitized and is never rendered or executed in the application.
- External HTML and model output are untrusted data, never instructions.
- Tenant/workspace identity is derived from authenticated server context and
  checked on every read, write, queue job, artifact, and graph traversal.
- Raw evidence is never overwritten by a later analysis or migration.

## System boundary

```text
Vinext web app
  ├─ ChatGPT sign-in and workspace metadata (Sites + D1)
  ├─ paper import, graph explorer, inspector, hypothesis workbench
  └─ HTTPS client to Graph API

Python Graph API
  ├─ URL gate and HTML fetcher
  ├─ MathML/LaTeX extraction
  ├─ source snapshots and versioned formula analysis
  ├─ Graphiti episode/ontology adapter
  ├─ typed Formula IR and Transformation DSL compiler
  ├─ proposal adapter with proposal-only permissions
  ├─ verification policy and immutable result store
  └─ isolated numerical/experiment runner interface

Neo4j
  └─ temporal evidence, typed lineage assertions, candidates, and result refs

Isolated workers
  ├─ no network or secrets; read-only pinned inputs plus bounded scratch space
  ├─ CPU/RAM/process/output/wall-clock limits and process-tree termination
  └─ worker-only identity for appending check and experiment results
```

The Sites runtime does not open a raw Neo4j TCP connection. Only the Python
Graph API talks to Neo4j; the web app communicates with it over authenticated
HTTPS. The proposal service cannot call result-writing endpoints. The verifier
cannot mutate the `ProblemSpec`, evaluator, parents, or source evidence.

## Scientific trust model

FormulaGraph Lab stores a verification vector instead of a single trust score:

```text
provenance: resolved | missing | conflicting
typing: proved_under_contracts | rejected | unknown | unsupported
domain: discharged | conditional | unresolved | contradictory
symbolic: supported | refuted | unknown | unsupported | timeout | error
numerical: passed_suite | counterexample | not_run | timeout | error
empirical: supported_on_protocol | failed_on_protocol | inconclusive | not_run
human_review: pending | accepted_scope | rejected_scope
```

Each `CheckResult` names the claim, assumptions, checker/version, input hash,
seed, tolerance, resource use, witness/counterexample, and artifact references.
Gate policy consumes this vector to decide what may run, enter the parent pool,
or be published. The UI never collapses the vector into a percentage.

## Task specification contract

Every task below must be expanded in its implementation PR or handoff with:

- dependencies and migrations;
- input/output schema and API or queue contract;
- exact production files and fixtures in scope;
- positive, negative, authorization, idempotency, and resource-limit tests as
  applicable;
- commands run and exact evidence of acceptance;
- remaining unsupported cases and rollback notes.

The test bullets in this plan are mandatory scenarios, not complete test code.

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

The watermark date is the revision date, stored without inventing a timestamp
or first-submission date. Missing metadata produces warnings. Ingestion then
requires an explicit timezone-aware source reference time. Generated identifiers
must not be presented as existing source anchors.

Definition of done:

- A fixture corpus of at least 10 representative arXiv HTML pages passes.
- Re-importing identical HTML produces identical IDs and no duplicate equations.

## Sprint 2 — Graphiti temporal evidence graph

Goal: incrementally ingest structured paper episodes into Neo4j via Graphiti.

### FGL-201 Prescribed ontology

Source entities: `Paper`, `PaperVersion`, `Section`, `SourceSpan`, and
`EquationOccurrence`. Mathematical entities: `FormulaIR`, `ScopedSymbol`,
`SymbolContract`, `Assumption`, and `Claim`. Research entities:
`ResearchProblem`, `Method`, `ImplementationVersion`, `Candidate`,
`TransformationActivity`, `ExperimentSpec`, `Run`, `Result`, and
`Counterexample`.

Relation families are explicit:

- bibliography: `HAS_VERSION`, `CITES`, `REVISION_OF`, `SUPERSEDES`;
- source: `CONTAINS`, `OCCURS_IN`, `EXTRACTED_FROM`, `MAKES_CLAIM`;
- mathematics: `DERIVED_FROM`, `EQUIVALENT_UNDER`, `APPROXIMATES_UNDER`,
  `GENERALIZES_UNDER`, `ASSUMES`;
- research objective: `ADDRESSES`, `SPECIALIZES`, `SUPPORTS_REQUIREMENT`;
- implementation: `IMPLEMENTS`, `OPTIMIZES_IMPLEMENTATION_OF`;
- evolution: `USES_PARENT`, `GENERATES`, `EVALUATES`, `SUPPORTS_CLAIM`,
  `REFUTES_CLAIM`.

- Every inferred assertion stores `asserted_by`, evidence span IDs, assessment
  (`author_reported`, `machine_inferred`, `human_reviewed`, or
  `checker_supported`), assumptions, reviewer record, and schema version.
- Tests: schema serialization, allowed source/target pairs, rejected unknown
  entity/relation types, citation never becoming derivation, inferred edge
  never becoming reviewed, and multiple-parent transformation provenance.

### FGL-202 Episode builder

- One paper version is a saga.
- Metadata and each ordered section are separate episodes.
- `group_id` is a research workspace so cross-paper retrieval remains possible.
- Exact equations and deterministic edges use structured JSON/direct triplets;
  prose uses LLM-assisted extraction.
- Tests: ordering, stable episode UUID, provenance mapping, idempotent replay.

Exact nodes/structural edges use atomic Evidence/EVIDENCE_RELATION writes.
Episodes/sagas use Graphiti's Episodic/Saga schema. Optional prose enrichment
uses the separate Entity/RELATES_TO layer, with retry receipts and revision
guards required before release. Graphiti 0.30.1 loads an existing episode when
passed a UUID, so persist it first. Workspace group IDs follow its ASCII policy.

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

Goal: represent supported mathematics as typed structure while preserving all
uncertainty needed by later verification. Sprint 4 output is analysis, not a
scientific verdict.

### FGL-401 Formula AST

- Parse the supported LaTeX/MathML subset into an operator tree.
- Track free/bound variables, indices, scalar/tensor categories, functions,
  reductions, contractions, elementwise operations, masks, and distributions.
- Give every symbol a scope-aware identity; the printed name is not identity.
- Tests: golden AST corpus, malformed input, binder scope, indexed notation,
  nested binders, shadowing, and capture-avoiding substitution fixtures.

### FGL-402 Canonical identity

- Keep a versioned syntax hash for parsed structure and a versioned semantic
  hash for Formula IR plus contracts, assumptions, and operator versions.
- Apply alpha-renaming and commutative normalization only after the required
  algebraic properties and operand types are established.
- Tests: alpha-equivalent formulas match; matrices are not reordered without a
  commutativity proof; valid scalar products deduplicate; domain or contract
  changes alter semantic identity; hashes remain stable across processes.

### FGL-403 Symbol contracts

- Infer shape/domain/category with explicit provenance and confidence.
- Store human review as a separate immutable record. Extraction confidence can
  request review but can never create confirmation.
- Keep unsolved dimensions and predicates as obligations rather than treating
  them as compatible wildcards.
- Tests: tensor multiplication, broadcasting rejection, denominator predicate,
  symbol shadowing, unknown dimensions, and independent review state.

Definition of done:

- The AST and hashes expose their schema/canonicalizer version.
- Unsupported or unknown semantics remain visible and cannot be promoted by an
  empty error list.
- Sprint 4.5 hardening is still required before proposals may reach real data.

## Sprint 4.5 — mathematical trust and execution hardening

Goal: close the contract, domain, status, migration, and privilege gaps found in
the Sprint 4 review before enabling AI proposals or evolution. See
`FORMULA_EVOLUTION_RESEARCH.md`, especially sections 5, 7, and 10.

### FGL-H1 Contract trust

Dependencies: FGL-401 and FGL-403.

- Replace the current coupled confidence/confirmation model with separate
  `ExtractionAssessment`, `InferredContract`, and `ContractReview` records.
- A review records reviewer identity, role, scope, decision, evidence, schema
  version, and timestamp; no client/model field can impersonate it.
- Preserve the original source occurrence and extraction values.
- Tests: extraction confidence 1.0 does not create human confirmation; low
  confidence does not prove invalidity; model/client `approved` fields are
  rejected; review is workspace-scoped and append-only.
- Acceptance: no production path derives `confirmed` or `approved` from a
  numeric extraction/type confidence.

### FGL-H2 Domain obligations

Dependencies: FGL-H1.

- Represent predicates over expression ASTs, not only symbol strings. Track
  origin as source-reported, inferred, user-accepted, or AI-proposed.
- Use `discharged`, `conditional`, `unresolved`, `contradictory`, and
  `unsupported` outcomes. Record which evidence or checker discharges each
  obligation.
- Detect inconsistent assumption sets and reject vacuous success over an empty
  domain.
- Tests: denominator `(a+b) != 0` with `a=1,b=-1`; `x/x`; nested fractions;
  `log` and `sqrt` over declared real/complex domains; contradictory predicates;
  an AI-proposed assumption cannot discharge itself.
- Acceptance: the existing `1/(a+b)` false pass is reproduced by a regression
  test and then prevented without changing raw evidence.

### FGL-H3 Typed canonicalization

Dependencies: FGL-H1 and FGL-H2.

- Introduce scoped symbol IDs and contract-aware semantic hashing.
- Do not reorder `A*B` and `B*A` unless the applicable operator and types prove
  commutativity. Equality under one contract is not reused under another.
- Make alpha-renaming capture-avoiding across nested bounds and sibling scopes.
- Tests: unknown/matrix products are not collapsed; proven scalar products
  deduplicate; alpha-renaming avoids capture; nested bounds preserve scope;
  hash version and contract changes invalidate cached analysis.
- Acceptance: each hash is reproducible with its canonicalizer version and no
  supported noncommutative example aliases a reordered expression.

### FGL-H4 Checker result model and timeout

Dependencies: FGL-H2 and FGL-H3.

- Replace the single status ladder with the verification vector defined in this
  plan. Persist immutable `CheckResult` records.
- A CAS result distinguishes supported, refuted, unknown, unsupported, timeout,
  and error. Failure to simplify to zero is not automatically refutation.
- Enforce a real wall-clock timeout and terminate the process tree; AST-size
  limits remain a separate preflight control.
- Tests: unknown shape cannot pass; CAS-inconclusive remains unknown; a known
  identity and known counterexample have different witnesses; hung and
  memory-heavy jobs are terminated and reported separately.
- Acceptance: no API/UI path maps unknown, unsupported, timeout, or error to a
  successful or refuted scientific claim.

### FGL-H5 Versioned analysis migration

Dependencies: FGL-H1 through FGL-H4.

- Store `FormulaAnalysisVersion` separately from immutable
  `EquationOccurrence` source evidence.
- Backfill historical records with explicit analyzer/canonicalizer versions and
  migration receipts. Re-analysis appends a version; it never overwrites raw
  extraction or old results.
- Tests: identical re-import leaves raw evidence unchanged; backfill is
  idempotent; old records remain readable; partial failure resumes safely;
  rollback removes only newly derived indexes/versions.
- Acceptance: inventory counts and source hashes match before and after the
  migration, and old/new analysis can be queried independently.

### FGL-H6 Proposal and runner security boundary

Dependencies: Sprint 6 authentication primitives already present in the
current codebase; complete missing worker/queue enforcement here rather than
waiting for the rest of Sprint 6.

- Use separate service identities and authorization scopes for proposer,
  compiler, verifier, experiment worker, and evolution controller.
- Proposer cannot write check results, fitness, reviews, budgets, tolerance, or
  evaluator changes. Result endpoints accept worker identity only.
- Run generated evaluations with no network or secrets, read-only pinned inputs,
  bounded scratch space, and CPU/RAM/process/output/wall-clock limits.
- Treat HTML and retrieved prose as untrusted content. Security does not depend
  on the model following an instruction to ignore prompt injection.
- Tests: direct/indirect injection payloads produce no privileged action;
  forged/fabricated IDs and cross-workspace jobs are rejected at API, queue, and
  worker; benchmark/tolerance mutation fails; child processes die on timeout;
  result logs contain no source HTML, prompts, tokens, or credentials.
- Acceptance: a compromised or adversarial proposer can create at most a schema-
  valid untrusted proposal within its workspace and quota.

Sprint 4.5 release gate:

- H1-H6 tests pass in unit and integration suites.
- Versioned migration has a dry-run report and rollback exercise.
- Threat model covers model output, external HTML, queue messages, workers,
  artifact storage, and tenant boundaries.
- Evolution and real-model proposal endpoints remain disabled by a server-side
  feature flag.

## Sprint 5A — frozen research problem and lineage graph

Goal: define what the search is solving and construct source-backed branches
without confusing citation history with mathematical compatibility.

### FGL-G1 Versioned ProblemSpec

Dependencies: Sprint 4.5 release gate.

- Freeze task, method family, metrics/directions, quality constraints, baselines,
  dataset/splits/hashes, model/tokenizer, hardware/backend, dtype, evaluator,
  seeds, compute/search budget, allowed transforms, and stop conditions.
- Editing creates a new version and a new experiment campaign. Existing results
  never move silently to a new spec.
- Tests: missing metric, direction, split, baseline, evaluator, or budget blocks
  evolution; unauthorized edits fail; new versions preserve lineage; result
  comparison across incompatible specs is rejected.
- Acceptance: `ProblemSpec` and evaluator snapshots are content-addressed and
  sufficient to identify all inputs needed by a worker.

### FGL-G2 Multi-paper lineage

Dependencies: FGL-201 through FGL-203 and FGL-G1.

- Build typed citation, revision, mathematical-derivation, approximation,
  shared-objective, and implementation edges with explicit evidence/assessment.
- Model many ancestors and many parents. Present “earliest ancestor in the
  indexed corpus”, not “original paper”, when historical coverage is incomplete.
- Keep papers without usable HTML as metadata-only nodes; never fill missing
  formulas from model memory or silently fall back to PDF.
- Store first-publication, paper-version, venue, and ingestion times separately.
- Tests: citation never becomes derivation; multiple ancestors survive; revision
  date does not become origin date; missing-HTML gaps remain visible; inferred
  edges are not displayed as reviewed.
- Acceptance: every non-bibliographic lineage edge opens its source spans or
  transformation/check activity and reports corpus coverage.

### FGL-G3 Compatibility graph

Dependencies: FGL-G1, FGL-G2, and typed contracts.

- Describe method/formula `requirements`, `capabilities`, typed ports, domain,
  shape, normalization, mask, and resource properties.
- Retrieval/embedding may nominate pairs; a versioned mapping plus checker or
  reviewer evidence determines compatible, incompatible, or unknown.
- Contract/version changes mark dependent compatibility mappings stale.
- Tests: similar embeddings with incompatible domains do not pass; unknown
  dimensions remain unknown; masks/normalization mismatch is visible;
  cross-workspace and stale mappings are rejected.
- Acceptance: a mashup candidate cannot compile from semantic similarity alone.

## Sprint 5B — Transformation DSL and verification policy

Goal: turn untrusted structured proposals into deterministic candidate IR and
make eligibility decisions without a misleading global status.

### FGL-D1 Versioned DSL registry

Dependencies: FGL-H2, FGL-H3, and FGL-G3.

- Each operator manifest defines name/version, semantics class
  (`preserving`, `approximation`, or `hypothesis_changing`), matched IR type,
  parameters, typed ports, preconditions, generated obligations,
  postconditions, lowering template, and resource estimate.
- Initial allowlist may include substitute operator, compose objectives, add
  regularizer, interpolate losses, add constraints, replace update rules, and
  mix compatible feature maps. Unimplemented manifests remain disabled.
- Schema uses strict fields and bounded tree/string/list sizes. It contains no
  arbitrary code, imports, shell fragments, or dynamic module names.
- Tests: unknown operator/version, extra fields, oversized/deep structures,
  wrong parameter type, and code strings are rejected; every enabled manifest
  passes golden lowering and obligation tests.
- Acceptance: the compiler has no fallback that asks the model to execute or
  judge an unsupported transformation.

### FGL-D2 Deterministic compiler

Dependencies: FGL-D1.

- Resolve server-owned workspace, snapshots, IDs, and versions; replay a bounded
  typed rewrite on an immutable parent; generate Candidate IR and obligations.
- Separate the hypothesis-changing activity from any later representation-
  preserving activity. Preserve all parents through `TransformationActivity`.
- Validate scopes before mutation and use a single controlled rewriter. Enforce
  step and output-size limits.
- Tests: same input/versions produce the same IR/hash/obligations; wrong node,
  binding, scope, parent, workspace, or stale version is rejected; parent bytes
  are unchanged; approximation/hypothesis never receives equivalence metadata.
- Acceptance: a candidate can be reconstructed byte-for-byte from parents,
  manifest versions, parameters, compiler version, and approved assumptions.

### FGL-V0 Verification and admission policy

Dependencies: FGL-H4 and FGL-G1.

- Version policy rules for `can_run_numerical`, `can_run_experiment`,
  `can_enter_parent_pool`, and `can_publish_claim` using the verification vector,
  unresolved obligations, risk class, quota, and ProblemSpec.
- Conditional candidates may run only in an explicitly restricted domain. They
  cannot be presented as globally valid.
- Infrastructure failure, checker timeout, and scientific refutation have
  separate retry/admission behavior.
- Tests: policy matrix for pass/reject/unknown/conditional; self-added assumption
  cannot open a gate; invalid candidate never enters parent pool; policy update
  creates a new version and does not rewrite old decisions.
- Acceptance: every transition has a persisted policy decision with rule ID,
  input result IDs, actor, and timestamp.

## Sprint 5C — independent verification and experiment harness

Goal: produce scoped, reproducible evidence before a candidate can influence
evolution or a user-facing claim.

### FGL-V1 Static and symbolic checkers

Dependencies: FGL-D2 and FGL-V0.

- Check scope, type, shape, operators, masks, domain predicates, and consistency
  of assumptions before CAS work.
- Run supported symbolic rules/CAS under explicit assumptions and budgets.
  Preserve proof steps/certificates where available and counterexamples where
  found; do not label ordinary CAS output as formal proof.
- Tests: known identities and non-identities, domain holes such as `x/x`, matrix
  noncommutativity, contradictory assumptions, unsupported constructs, and real
  timeout/error paths.
- Acceptance: each result states the exact claim and domain it supports/refutes,
  with checker/version and reproducible input hash.

### FGL-V2 Isolated numerical runner

Dependencies: FGL-H6, FGL-V1, and FGL-V0.

- Run seeded reference, property, limiting-case, gradient, and NaN/Inf checks in
  an isolated process. Persist exact counterexample inputs.
- Pin dtype-specific tolerance, input generators, seed, implementation version,
  environment, and resource limits. The proposer cannot change them.
- For causal attention, perturb future tokens and confirm earlier outputs do not
  change within the pinned tolerance; test near-zero denominators and extreme
  norms/dtypes.
- Tests: deterministic replay, causal future perturbation, singularities,
  overflow/underflow, finite gradients, tolerance enforcement, quota, timeout,
  process cleanup, and artifact isolation.
- Acceptance: passing means only `passed_suite` for that suite; no code path
  promotes it to symbolic equivalence.

### FGL-V3 Frozen experiment harness

Dependencies: FGL-G1, FGL-H6, FGL-V0, and FGL-V2.

- Execute a staged protocol: A reference correctness; B hardware microbenchmark;
  C pilot training; D confirmatory multi-seed runs on a frozen protocol; E
  external validation when a broader claim is requested.
- Compare each candidate with its parents and an optimized baseline under the
  same task-relevant controls. Attention requires matched rank/capacity and
  matched compute budget; other method families define their own controls in
  `ProblemSpec`.
- Record all attempted candidates, search cost, raw results, effect size,
  uncertainty, warmup/synchronization method, hardware/software, nondeterminism,
  and failures. Token-level observations are not independent runs.
- Separate search/tuning data from locked confirmation data. If a holdout result
  influences a new proposal or selection, it becomes search data and cannot
  confirm the final claim.
- Tests: same shapes/dtype/budget; matched controls; seed/environment manifest;
  evaluator hash mismatch; model and controller cannot access holdout; failed
  run is not a score of zero or a winner; replay bundle resolves every artifact.
- Acceptance: an empirical claim names its complete protocol and uncertainty.
  `not_run` remains visible when compute is unavailable.

## Sprint 5D — AI proposals and controlled evolution

Goal: add model creativity only after deterministic boundaries and evaluators
are enforced.

### FGL-P1 AI proposal adapter

Dependencies: FGL-G1 through FGL-G3, FGL-D1, and FGL-H6. It can be developed
against mocks earlier but remains feature-flagged until Sprint 5C passes.

- Retrieve an approved graph snapshot and failures within a bounded context.
- Return strict structured output containing ProblemSpec reference, parents,
  transform/operator version, target/bindings, evidence span IDs, proposed
  assumptions, and expected effect.
- Server creates IDs, workspace, semantics class, obligations, evaluator refs,
  review state, and fitness. Retry malformed output only within a fixed cap.
- Tests: malformed response, fabricated/stale/cross-workspace ID, missing source,
  indirect prompt injection, extra `verified`/`fitness`/`approved` fields,
  benchmark or tolerance changes, exhaustion of retry and quota; all tests run
  with a deterministic model mock.
- Acceptance: model unavailability cannot corrupt state, and the only successful
  model write is an untrusted `Proposal`.

### FGL-E1 Evolution controller

Dependencies: FGL-P1, FGL-V0 through FGL-V3.

- Select feasible, diverse parents from a Pareto archive using the frozen metric
  directions for quality, latency, and memory. Apply typed mutations/crossovers,
  not LaTeX string concatenation.
- Track generations, full parentage, selection events, retries, evaluator/policy
  versions, quota, and stop reason. Retain rejected candidates and
  counterexamples to prevent repeated dead ends.
- Never expose locked confirmation data to proposal or selection. Finalists are
  frozen before confirmatory evaluation.
- Tests: exact budget/stop behavior; deterministic selection for a seeded mock;
  diversity and dedup respect contracts; invalid/stale candidates cannot become
  parents; infrastructure errors cannot win; all parents survive round-trip;
  holdout feedback cannot trigger a new generation.
- Acceptance: every winner is reconstructible and reports total search cost and
  all compared candidates, not only the best run.

## Sprint 5E — research UX and end-to-end demonstrator

Goal: make provenance, uncertainty, trade-offs, and failures understandable to
a researcher and exportable to another human or AI.

### FGL-U1 Research UX

Dependencies: schemas from Sprint 5A through Sprint 5D; implement incrementally
after each schema stabilizes.

- Start from a research question and `ProblemSpec`, not an unfiltered graph.
- Offer separate toggles for citation, mathematical lineage, compatibility,
  implementation, evolution, and experiment evidence. Inferred/proposed edges
  are visually distinct and state why they exist.
- Candidate view answers: source/parents, conditions of validity, checks run,
  unresolved obligations, and empirical scope. No single trust percentage or
  success badge for `not_run`, unknown, unsupported, timeout, or error.
- Preview transform diff, new assumptions, estimated check cost, and gate result
  before execution. Keep failures and Pareto trade-offs visible.
- Tests: every edge opens source/reason; metadata-only gaps render safely;
  keyboard navigation, color-independent status, 200% zoom, mobile fallback,
  loading/empty/error/partial states, authorization, and bundle export.
- Acceptance: usability review finds no evidence/proposal/result ambiguity and
  a user can explain why a candidate did or did not pass each gate.

### FGL-R1 Attention end-to-end demonstrator

Dependencies: all Sprint 4.5 and Sprint 5 tasks.

- Curate a small HTML corpus containing scaled dot-product attention, linear
  attention, Performer, and FlashAttention as different mathematical,
  approximation, and implementation branches. Verify all official lineage
  assertions from source spans; do not infer missing history as fact.
- Freeze one causal long-context `ProblemSpec`; import → lineage → compatible
  ports → proposal → compile → obligations → checks → staged experiment → report.
- Include one representation-preserving transformation and one
  hypothesis-changing transformation, plus rejected, unknown, conditional, and
  passing outcomes.
- Use the positive feature-map mixture from the research report only as an
  illustrative candidate. Its small NumPy identity check is not an empirical
  performance result.
- Tests: clean-checkout bundle replay; source/version/evaluator resolution;
  worker isolation; no model self-scoring; report includes failures, search cost,
  assumptions, environment, and limitations.
- Acceptance: an independent developer can reproduce the recorded stage results
  from the exported bundle without hidden model judgment.

Sprint 5 has two separate completion gates:

- **Engine complete:** frozen ProblemSpec, provenance corpus, typed/replayable
  transformations, verification vector, policy decisions, isolated workers,
  and end-to-end outcomes for reject/unknown/conditional/pass.
- **Research case complete:** the frozen empirical protocol has actually run and
  reports candidate-versus-parent/baseline results, uncertainty, all trials, and
  search cost. A negative or inconclusive result is acceptable; absence of an
  experiment is not completion of the research case.

Neither gate uses “the AI generated a plausible-looking formula” as evidence.

## Sprint 6 — identity, security, and operations

Goal: complete production identity, operations, audit, and organization-wide
security controls. Security needed to isolate proposals and workers is pulled
forward into FGL-H6 and cannot wait for this sprint.

### FGL-601 Authentication and authorization

- ChatGPT/Sites identity for the hosted UI.
- Signed service token between web app and Graph API.
- Role- and workspace-aware authorization for graph, research, artifact, queue,
  worker, and result operations; service roles follow least privilege.
- Tests: anonymous denial, cross-workspace access/traversal, expired/replayed
  token, forged headers, wrong service role, and ownership derived from request
  data instead of authenticated context.

### FGL-602 OWASP controls

- SSRF allowlist, output encoding, CSP, CSRF where applicable, rate/compute
  quotas, request/body/artifact caps, secret isolation, immutable audit events,
  dependency/container scanning, retention, backup, and incident runbooks.
- Cover prompt injection, improper model-output handling, excessive agency,
  resource exhaustion, artifact poisoning, and evaluator tampering in the threat
  model. Controls enforce damage limits; they do not claim the model is immune.
- Tests: OWASP-oriented abuse cases; logs contain no source HTML, prompt content,
  tokens, or credentials; tamper and quota events are auditable.

### FGL-603 Observability

- Correlation ID from import to episode/graph write.
- Metrics: import duration, extraction yield, model cost, graph writes,
  verification outcomes, unknown/timeout/error rates, candidate cost, queue
  saturation, policy decisions, and experiment spend.
- Tests: retry behavior, partial failure recovery, idempotency, duplicate worker
  delivery, dead-letter recovery, alert redaction, and audit-chain completeness.

Release note: following these controls supports secure engineering and an ISO
27001-aligned management process, but it does not itself constitute ISO 27001
certification.

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
- At least one engine-complete research bundle is independently replayed.
- If marketing claims empirical improvement, the corresponding research case
  completion gate has passed; otherwise the product states that experiments are
  pending, negative, or inconclusive.
- Pilot metrics include time from paper to controlled experiment, contract/domain
  errors found before training, reproducible-run rate, and researcher retention.
- Paid packaging is validated with target users before treating pricing or
  revenue as established product evidence.

## Execution order and release gates

The dependency order is mandatory:

```text
Sprint 0-4 baseline
→ Sprint 4.5 H1-H6
→ Sprint 5A G1-G3
→ Sprint 5B D1-D2 and V0
→ Sprint 5C V1-V3
→ Sprint 5D P1 and E1
→ Sprint 5E U1 and R1
→ Sprint 6 production completion
→ Sprint 7 paid beta
```

Frontend work may proceed against versioned mocks, but it cannot weaken a gate.
Proposal and evolution features stay disabled in production until their direct
dependencies and authorization tests pass. Later tasks do not redefine an
earlier unknown as success to unblock themselves.

## AI handoff protocol

At the end of every implementation session, the next AI must:

1. Read this plan, `README.md`, `docs/STATUS.md`, and
   `docs/FORMULA_EVOLUTION_RESEARCH.md`.
2. Run `git status --short`; never discard unrelated user changes.
3. Run the tests for the area being changed before editing.
4. Pick the earliest unfinished task ID; do not start a later sprint to avoid a
   failing gate.
5. Expand the selected task using the Task specification contract in this plan.
   Record dependencies, schemas, files, fixtures, tests, migration, and rollback
   before implementation.
6. Preserve raw evidence and existing user changes. Never rewrite a scientific
   result, source occurrence, or old analysis to make a new test pass.
7. Update task status and authoritative evidence in `docs/STATUS.md`; distinguish
   implemented, tested, externally validated, and not run.
8. Record commands and exact failures, but never record source HTML, prompts,
   credentials, tokens, or private experiment data.
9. Finish with targeted tests, relevant integration/build checks, migration
   evidence when applicable, and a concise list of unsupported cases and risks.

No AI may mark a sprint complete from prose alone. Completion requires the
named files, tests, migrations, runtime behavior, rendered UX where applicable,
and evidence demanded by each acceptance criterion.
