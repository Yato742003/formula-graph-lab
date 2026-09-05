# Delivery status

Last updated: 2026-09-05

| Task | Status | Evidence |
| --- | --- | --- |
| FGL-001 | Complete | Independent scaffold; previous route and production build verified |
| FGL-002 | Implemented; validation incomplete | Shell exists; frontend tests cover URL policy, not component interaction/responsiveness |
| FGL-003 | Complete | README, sprint plan, environment and handoff documentation |
| FGL-101 | Complete for modern IDs | Python/TypeScript URL tests; V1 does not support legacy IDs |
| FGL-102 | Complete | Total timeout, redirect cap, strict MIME, streamed size cap and error tests |
| FGL-103 | Complete for supported MathML | Twelve frozen excerpts and twelve live whole-page checks; aligned-cell assembly and source warnings |
| FGL-104 | Complete with missing metadata explicit | Revision/date, pinned URLs, authors fallback, ordered sections and stable hashes |
| FGL-201 | Complete at schema/contract layer | Planned entities, relation endpoint/condition constraints, installed SDK validation |
| FGL-202 | In progress | Ordered episodes and atomic writer implemented; database verification and semantic retry receipts pending |
| FGL-203+ | Not started | Revision semantics precede retrieval and hypotheses |

## Latest validation

- Backend offline: 71 passed. Twelve live and three integration tests are opt-in.
- Live arXiv corpus: 12 passed; see ingestion-validation.md.
- Frontend: nine URL-policy tests passed; product lint passed.
- Last production build/private deployment: previous baseline. Backend changes
  are not deployed or connected to the web import flow.
- Docker start attempts did not establish an engine. The direct-launch process
  exited and no Docker Desktop/backend process remained. No successful database
  integration result is recorded.

## Findings and limits

- Attention now yields seven display blocks; the prior nine counted MathML fragments.
- Mixed text/MathML blocks carry review warnings. Extraction confidence does not
  measure mathematical validity; precision/recall awaits the reviewed beta dataset.
- DOM authors are best effort; watermark dates describe revisions, not first submission.
- Generated anchors explicitly carry anchor_is_source=false.
- Group IDs and UUID preparation now match Graphiti 0.30.1.
- Exact graph imports have atomic receipts; semantic retry receipts and
  cross-paper invalidation guards remain incomplete.
- The viewport still uses the curated demo. Hosted Sites needs a separately
  reachable HTTPS Graph API service.
- WebMCP execution remains unverified. The last production dependency audit was
  clean; four known moderate dev-only Drizzle advisories remain.

## Next actions

1. Run test-integration.ps1 with a working Docker engine; fix database/SDK issues.
2. Finish semantic retry receipts and revision/invalidation tests using two
   revisions and an independent conflicting paper.
3. Close component-test gaps and connect authenticated import, persistence,
   retrieval and the real graph viewport.
4. Continue through formula typing, hypotheses, verification, security and beta.
   The project is not yet a finished monetizable V1.
