# Delivery status

Last updated: 2026-09-06

| Task | Status | Evidence |
| --- | --- | --- |
| FGL-001 | Complete | Independent scaffold; previous route and production build verified |
| FGL-002 | Complete | Component tests cover import success/failure and formula-node selection; responsive styling is present, but no browser visual QA was requested |
| FGL-003 | Complete | README, sprint plan, environment and handoff documentation |
| FGL-101 | Complete for modern IDs | Python/TypeScript URL tests; V1 does not support legacy IDs |
| FGL-102 | Complete | Total timeout, redirect cap, strict MIME, streamed size cap and error tests |
| FGL-103 | Complete for supported MathML | Twelve frozen excerpts and twelve live whole-page checks; aligned-cell assembly and source warnings |
| FGL-104 | Complete with missing metadata explicit | Revision/date, pinned URLs, authors fallback, ordered sections and stable hashes |
| FGL-201 | Complete at schema/contract layer | Planned entities, relation endpoint/condition constraints, installed SDK validation |
| FGL-202 | Complete | Ordered episodes, atomic evidence writes, episode-resolving provenance, idempotent replay and enrichment retry receipts pass Neo4j integration tests |
| FGL-203 | Complete | Out-of-order v1/v2/v3 imports, `SUPERSEDES`, historical lookup and independent cross-paper disagreement pass against Neo4j |
| FGL-301 | Implemented; live semantic-provider validation pending | Tenant-scoped BM25, Graphiti semantic adapter, two-hop graph traversal, filters, RRF ranking and signed keyset pagination pass unit and Neo4j integration tests |
| FGL-302 | Partial UI shell | Search results and an in-memory graph are visible; persisted graph reads, pan/zoom, relation filters and keyboard graph navigation remain |
| FGL-303+ | Not started | Provenance history, formula typing, hypotheses, verification and beta remain later gates |

## Latest validation

- Backend offline: 89 passed, 18 opt-in tests deselected, two upstream deprecation warnings.
- Live arXiv corpus: 12 passed; see ingestion-validation.md.
- Neo4j integration: 6 passed, 101 deselected; the isolated container and network
  were removed by the runner.
- Frontend: 6 files and 29 tests passed; product lint and production build passed.
- Local Worker end-to-end: two authenticated imports of arXiv `1706.03762v7`
  produced 7 equations, 39 nodes, 58 edges and 31 episodes. The replay returned
  `replayed=true`; D1 held 1 workspace, 1 paper, 2 successful jobs and 0 failed jobs.
- Local Worker search smoke: the persisted Attention graph returned three ranked
  hits; the first was the exact Attention equation through lexical retrieval and
  a signed next-page cursor was present. Semantic retrieval reported unavailable
  because no model API key was configured.
- Production dependency audit: 0 vulnerabilities. Four moderate advisories remain
  in dev-only Drizzle tooling and are not force-upgraded.
- Docker Desktop 4.74.0 / Engine 29.4.3 is working after a clean WSL backend
  restart. The latest isolated integration container and network were removed.
- Production build exposes `/`, `/api/imports`, and `/api/search`. The source is
  not yet connected to a publicly reachable HTTPS Graph API in the hosted environment.

## Findings and limits

- Attention now yields seven display blocks; the prior nine counted MathML fragments.
- Mixed text/MathML blocks carry review warnings. Extraction confidence does not
  measure mathematical validity; precision/recall awaits the reviewed beta dataset.
- DOM authors are best effort; watermark dates describe revisions, not first submission.
- Generated anchors explicitly carry anchor_is_source=false.
- Group IDs and UUID preparation now match Graphiti 0.30.1.
- Exact graph imports have atomic receipts; semantic enrichment has explicit
  pending/succeeded/reconciliation states and cannot ambiguously replay.
- The viewport uses the curated demo before import and real source-bound nodes
  afterward. Persisted graph retrieval on reload belongs to FGL-302.
- Search never accepts a browser-supplied workspace. The Worker derives the
  workspace from the authenticated user, verifies D1 ownership, and the Graph API
  maps semantic episode IDs back to immutable exact-evidence nodes.
- The real Graphiti semantic-provider path has a contract test but has not been
  smoke-tested with a live model API key; lexical and graph retrieval degrade cleanly.
- Cloudflare Workers does not implement `redirect: "error"`; the Graph API client
  uses `manual` and never forwards its bearer token through a redirect.
- Hosted Sites still needs a separately reachable HTTPS Graph API service plus
  runtime `GRAPH_API_URL` and `GRAPH_API_SERVICE_TOKEN` values.
- WebMCP execution remains unverified. The last production dependency audit was
  clean; four known moderate dev-only Drizzle advisories remain.

## Next actions

1. Implement FGL-302 persisted graph retrieval so imports survive page reload and
   the viewport no longer depends on an in-memory response.
2. Implement pan/zoom, relation filters and keyboard navigation on the persisted
   graph, then connect search selection to the shared inspector.
3. Deploy the Python Graph API/Neo4j boundary behind public HTTPS, configure hosted
   secrets, and rerun the authenticated import smoke test in the private site.
4. Continue through formula typing, hypotheses, verification, remaining security
   controls and the reviewed beta corpus.
   The project is not yet a finished monetizable V1.
