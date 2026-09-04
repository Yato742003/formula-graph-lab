# Delivery status

Last updated: 2026-09-05

| Task | Status | Evidence |
| --- | --- | --- |
| FGL-001 | Complete | Scaffold installed; local route HTTP 200; production build passes |
| FGL-002 | Complete | Authenticated workspace, URL import, graph selection, inspector and responsive states implemented |
| FGL-003 | Complete | README, sprint plan, status file, environment and service boundary documented |
| FGL-101 | Complete | Strict TypeScript/Python URL gates; security regression tests pass |
| FGL-102 | Complete | Bounded fetcher covers redirects, MIME, status, timeout and size |
| FGL-103 | In progress | Live arXiv check on 1706.03762 returned 9 display equations; Eq. (1) resolves to anchor S3.E1; 10-paper corpus is pending |
| FGL-104 | In progress | Title/authors/version/hash implemented; publication timestamp extraction is pending |
| FGL-201 | In progress | Prescribed entity/edge ontology and validated adapter contract exist |
| FGL-202 | In progress | Stable structured paper-version episode exists; ordered section episodes are pending |
| FGL-203+ | Not started | Follow implementation plan in order |

## Current risks

- The graph viewport uses curated demonstration relationships while live
  extraction results are reported by the import flow.
- Graphiti requires a separately operated Python/Neo4j service; Sites must call
  that service over HTTPS.
- Mathematical equivalence must not be delegated to the LLM.
- WebMCP source is implemented, but no supported WebMCP execution context was
  available in this session to validate registration and execution.
- Four moderate npm advisories remain in the development-only Drizzle migration
  toolchain; production dependencies have no known npm advisory.

## Latest validation evidence

- Frontend: 9 Vitest cases passed.
- Backend: 26 pytest cases passed.
- Product lint: passed.
- Vinext production build: passed; Worker route and import API emitted.
- Production npm audit: zero known advisories.
- Docker Compose configuration and PowerShell runner parsing: passed.
- Live extraction: `Attention Is All You Need` produced 9 display equations with
  exact section, equation number, and source-block anchor.
