# Phase 5 completion handoff

Date: 2026-10-01

The four requested follow-up items are implemented for the approved CPU-only
synthetic pilot: receipt-to-admission, executable bounded evolution, locked
holdout confirmation, end-to-end acceptance/replay, and an offline retrieval A/B.
Follow-up implementation commits: `2ad1cfd` (engine), `0a79d05` (acceptance/A-B),
and `49b018c` (UI). Nothing was pushed. Existing workspace
UI edits were preserved in their own UI delivery commit; unused local assets
were not included.

## Follow-up delivered

- **Receipt -> admission:** independently reconstruct the current candidate,
  implementation binding, checker version, frozen spec, parents, search result,
  mapping freshness and explicit human protocol-scope review. Passed measurements
  alone never grant eligibility. Reviews are append-only, workspace-scoped,
  idempotent and revocable. The authorization covers this frozen synthetic
  feature-map family, not an arbitrary transformation or a mathematical proof.
  The original conditional domain obligation remains conditional.
- **Evolution:** one durable generation per request, with server-selected Pareto
  parents, seeded bounded weight crossover/mutation, duplicate avoidance, typed
  DSL compilation, independent checking and the existing signed worker queue.
  The browser cannot submit fitness, evaluator changes or split assignments.
  Search uses two search seeds plus one validation seed. Invalid, timeout and
  infrastructure outcomes retain evidence and cannot become parents/winners.
  Campaign budgets include confirmation and refuse a phase that cannot fit the
  remaining allowance. Ledger events and their records share one timestamp.
- **Holdout:** two confirmation seeds are not executed during search. Only
  explicitly frozen finalist IDs may run them; search cannot resume after freeze.
  Finalist confirmation has a four-candidate synchronous ceiling. Holdout receipts
  cannot enter the search parent pool or determine new mutations.
- **Acceptance:** source HTML -> mathematical lineage -> reviewed function
  contracts -> reviewed mapping -> frozen spec -> mixture -> preserving lowering
  -> conditional checker -> search receipt -> human review -> admission -> two
  generations -> finalist freeze -> holdout -> export -> independent replay.
  Review revocation, premature holdout, global-proof denial, retry identity and
  winner-tampering rejection are checked. The existing R1 suite also exercises
  rejected/accepted mock AI proposals and unknown/conditional outcomes.
- **UI:** explicit search and human-scope-review controls, generation/finalist/
  confirmation controls in Reports, and campaign export. Function contracts now
  keep real input width/domain distinct from reviewed positive output domain;
  form, gateway and backend agree. Legacy full-protocol receipts remain readable
  and replayable but cannot seed evolution; users can run a new scoped search.
  Reports distinguish search-only results from holdout measurements.
- **Replay:** campaign export includes evaluated candidate bundles, sources,
  parents, bindings, checker/admission inputs, human review records, experiments,
  accounting and ordered events. Replay re-executes scientific results and native
  state transitions; no model, live database or network is needed for replay.

## Current verification

- Backend Ruff and offline Graph API gate: 620 passed, 48 deselected; includes the
  checked-in pilot manifest registration/hash/split check.
- `npm test`: 20 files, 152 tests passed; lint and non-incremental TypeScript pass.
- `npm run build`: pass; existing bundle-size and Node deprecation warnings remain.
- `pwsh -NoProfile -File test-phase5.ps1`: 29 integration tests passed using an
  isolated Neo4j database and the real pinned Docker worker. This includes both
  local CLI and clean-container, network-disabled campaign replay.
- `pwsh -NoProfile -File test-sandbox.ps1`: 7 sandbox tests and 1 independent
  compiler replay passed. Test-owned database containers and image tags removed.
- Chrome smoke: Reports loads its honest empty state in the existing local
  workspace. No live research records were created there. The populated complete
  flow was verified automatically in the isolated database, not manually in Chrome.
- `git diff --check`: pass.

## Retrieval A/B result

Recorded raw run: [`../reports/phase5-retrieval-ab.json`](../reports/phase5-retrieval-ab.json).
Four fixed anchor-finding queries, three repeats each, paired alternating order,
same workspace, paper/revision filters and k=5. Four stored, partial official HTML
fixtures are imported into isolated Neo4j. Gold anchors are evaluated outside the
controller; the controller never receives the gold labels.

| Metric | Existing retrieval | Experimental controller |
| --- | ---: | ---: |
| Correct requested source anchor | 12/12 | 12/12 |
| Recall@5 | 1.0 | 1.0 |
| Precision@5 (one gold anchor/query) | 0.20 | 0.20 |
| p50 latency, ms | 8.261 | 18.539 |
| p95 latency, ms | 145.609 | 190.859 |
| Mean estimated context tokens | 819 | 877.5 |
| Provider input/output tokens | 0/0 | 0/0 |
| Model API cost, USD | 0 | 0 |

**Decision: keep existing retrieval as default.** Controller version
`bounded-source-neighbors.v1` adds at most two existing searches and reranks
source-backed neighboring evidence; it is an offline experimental module only.
It does not implement Jev-Mem, call an LLM, or change production routing.
This small, explicit-anchor corpus is a ceiling/smoke benchmark, not evidence of
general research-answer quality or a production retrieval improvement. Latency
includes cold requests and varies with local load. Tokens are `ceil(UTF-8 bytes/4)`
context estimates, not tokenizer/provider billing. Zero API cost does not mean
zero electricity or hosting cost. Semantic-provider/model comparisons and a
larger independent, no-anchor gold corpus remain unmeasured; enable them only with
an explicitly configured provider, model and spending cap.

## Reproduce and use the pilot

```powershell
pwsh -NoProfile -File verify-research.ps1
pwsh -NoProfile -File test-phase5.ps1
pwsh -NoProfile -File test-sandbox.ps1
pwsh -NoProfile -File replay-bundle.ps1 -Bundle campaign.json -Evolution
```

The test runner creates its own disposable Neo4j project and pinned worker image.
Ordinary integration runs may use the identical reference evaluator in-process;
`test-phase5.ps1` specifically exercises real Docker execution and clean replay.

For an interactive pilot, configure the existing compiler/research-case feature
flags, sandbox image, queue key and authorized experiment worker using
`.env.example`; no model credentials are necessary. The sample frozen definition
is [`../reports/phase5-pilot-spec.json`](../reports/phase5-pilot-spec.json): paste its
task/family/dtype into Spec step 1 and the corresponding JSON sections into
Advanced manifest in step 2, then review/freeze. Its ten-minute wall allowance
includes user pauses after campaign start; it is not a long-running background job.

1. Import Linear Attention HTML; select the stored `phi` occurrences around
   `S3.E4` and `S3.E5`. Review `phi` as a function with real input `[64]`, output
   rank `16`, strictly-positive output, no normalization/mask, non-causal function
   contract, and resource `not_applicable`. This instantiates generic paper notation
   for our reference worker; it is not an author-code reproduction claim.
2. Create/review their compatibility mapping. Compile the mixture (acceptance seed
   weight `0.75`) and explicitly lower it to concatenation. Inspect the conditional
   checker result, run frozen CPU search, then write notes and authorize the
   frozen-family protocol scope. A negative/inconclusive result is not a parent.
3. In Reports select this Spec, start a campaign, paste the lowered candidate ID
   as seed and run a generation. Further generations use server-selected parents.
   Inspect receipts, select Pareto finalists, explicitly freeze, then confirm.
4. Export campaign bundle and replay it with the command above. A confirmation
   failure can correctly leave no winner; do not loosen the frozen threshold.

## Remaining scope limitations

The operational holdout boundary is enforced, but synthetic seeds/generator are
public: this is not a secret, external ML holdout. Our reference family has one
registered quality metric. Generic Pareto engine tests cover quality/latency/
memory, but real model multi-objective evaluators are not implemented by this
pilot. No paper author code, real dataset/model quality, revenue or product speed
claim is established. API receipt hashes are content digests, not signatures.
Scientific replay permits absolute float64 differences up to `1e-12` for
cross-platform libm/Python patch variation; statuses, quality gates, identities,
scopes and transitions must still match exactly. Original receipts stay immutable.
Compute accounting is a bounded single-CPU wall-time charging proxy, not hardware
CPU profiling. Paid proposal generation remains explicitly configured/feature-flagged.

## Historical baseline before this follow-up (2026-09-29)

### Delivered then

- Candidate-bound implementation binding and registered protocol descriptor.
- Persisted, workspace-scoped research-case queue execution with feature flag,
  worker authorization, idempotency and atomic result append.
- Five frozen seeds with search/validation/holdout assignment, matched parent
  feature budget, causal perturbation, finite-output checks and holdout CI.
- Distinct supported/failed/inconclusive outcomes; malformed, timeout and scope
  failures cannot become scientific support.
- Replay bundle export and report evidence for bindings and research cases.
  Completed protocol results rerun from the frozen bundle; operational wall time
  is excluded from scientific replay comparison.
- One UI action for the registered protocol. The old low-level numerical fixture
  remains visible only as historical diagnostic evidence and is no longer an
  additional user workflow.
- Flow audit: `docs/PHASE5_FLOW_AUDIT.json`.

### Verification then

- `ruff check app tests`: pass.
- Offline Graph API pytest gate: pass.
- Frontend Vitest: 17 files, 135 tests passed.
- TypeScript no-emit and frontend lint: pass.
- Production build: pass; existing chunk-size and Node deprecation warnings remain.
- Neo4j integration: 27 passed, 629 deselected.
- Docker sandbox gate: 7 passed in the worker suite and 1 isolation probe passed.
- `git diff --check`: pass.

### Scope and exclusions then

The result scope is `synthetic_operator_only_no_product_claim`. It uses the
server reference implementation and does not claim to execute an author's paper
code, reproduce external model quality, or establish revenue/performance. A
future exact-paper V2 binding still needs a server-resolved, byte-verified
artifact and its own protocol receipt. Paid/OpenAI proposal generation remains
feature-flagged and requires explicit model/price configuration.

### Historical commits (not commits from this follow-up)

- `e9836b8` persist gated CPU research cases
- `3feba9e` replay registered research evidence
- `923e7e7` streamline research protocol UX
- `d00e8b4` record UX flow audit
- `d7f54af` retain research-case receipt ordering
- `46bd5ee` bind research replay parents
- `4a25869` cover research-case report evidence
