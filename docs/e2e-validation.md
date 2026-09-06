# End-to-end validation

Validated on 2026-09-06 with Docker Desktop 4.74.0, Docker Engine 29.4.3,
Neo4j 5.26 Community, Wrangler 4.129.0, and the production Vinext Worker build.

## Boundary exercised

```text
authenticated Worker request
  -> server-derived workspace identity
  -> D1 import job
  -> bearer-authenticated Graph API
  -> bounded arXiv HTML extraction
  -> atomic Neo4j exact-evidence import
  -> D1 paper/job completion
```

The test used `https://arxiv.org/html/1706.03762`. The Graph API pinned the
source to revision v7 and returned seven extracted display equations.

## Recorded results

First import:

- HTTP 200
- 39 exact-evidence nodes
- 58 evidence relations
- 31 source episodes
- `replayed=false`

Second identical import:

- HTTP 200
- the same node, relation and episode counts
- `replayed=true`

D1 after both requests:

- 1 workspace
- 1 paper
- 2 successful import jobs
- 0 failed import jobs

This proves idempotency at the evidence graph while retaining a separate audit
job for each authenticated request. Integration tests additionally verify that
every `EVIDENCE_RELATION.episode_uuids` value resolves to an `Episodic` node in
the same workspace group.

## Repeatable checks

Use temporary local-only secrets; never commit them or paste them into logs.

```powershell
npm test
npm run lint
npm run build
.\.venv\Scripts\python.exe -m ruff check services\graph-api
.\.venv\Scripts\python.exe -m pytest services\graph-api
.\test-integration.ps1
npm audit --omit=dev
```

After a production build, apply the immutable local D1 migration before starting
the Worker:

```powershell
npx wrangler d1 execute site-creator-d1 --local `
  --file .\drizzle\0000_daffy_firebrand.sql `
  --config .\dist\server\wrangler.json
```

Start the Graph API with a temporary service token and a loopback Neo4j URI,
then start Wrangler with `GRAPH_API_URL`, `GRAPH_API_SERVICE_TOKEN`, and
`APP_ENV=development` bindings. Submit the import twice with a distinct
`oai-authenticated-user-id`, and query the local D1 counts. Remove the temporary
container/network and close ports 18000, 18100, and the randomized Bolt port.

## Hosted limit

This is local end-to-end evidence. The private Sites deployment cannot perform
real imports until the Python Graph API is deployed at public HTTPS and the two
Graph API runtime values are configured in the hosted Worker. The web runtime
must never connect directly to Neo4j over Bolt.
