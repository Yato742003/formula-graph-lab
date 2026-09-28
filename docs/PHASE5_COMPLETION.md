# Phase 5 completion handoff

Date: 2026-09-29

Phase 5 is complete for the approved zero-cost CPU synthetic research case. The
implementation is split into reviewable commits; nothing was pushed.

## Delivered

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

## Verification

- `ruff check app tests`: pass.
- Offline Graph API pytest gate: pass.
- Frontend Vitest: 17 files, 135 tests passed.
- TypeScript no-emit and frontend lint: pass.
- Production build: pass; existing chunk-size and Node deprecation warnings remain.
- Neo4j integration: 27 passed, 629 deselected.
- Docker sandbox gate: 8 passed in the main suite and 1 isolation probe passed.
- `git diff --check`: pass.

## Scope and exclusions

The result scope is `synthetic_operator_only_no_product_claim`. It uses the
server reference implementation and does not claim to execute an author's paper
code, reproduce external model quality, or establish revenue/performance. A
future exact-paper V2 binding still needs a server-resolved, byte-verified
artifact and its own protocol receipt. Paid/OpenAI proposal generation remains
feature-flagged and requires explicit model/price configuration.

## Commits

- `e9836b8` persist gated CPU research cases
- `3feba9e` replay registered research evidence
- `923e7e7` streamline research protocol UX
- `d00e8b4` record UX flow audit
- `d7f54af` retain research-case receipt ordering
- `46bd5ee` bind research replay parents
