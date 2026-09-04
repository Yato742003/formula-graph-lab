# FormulaGraph Lab

FormulaGraph Lab converts HTML research papers into a temporal, source-bound
formula graph. It separates extracted evidence from AI hypotheses and provides a
path toward typed formula transformations with symbolic and numerical checks.

This is a new project. It does not import from or modify
`quantum-platform-v2`.

## What works now

- Sign-in-gated research workspace built with Vinext/React.
- Strict arXiv URL normalization in both TypeScript and Python.
- Safe bounded HTML fetcher with validated redirects and MIME/size limits.
- MathML extraction that keeps display equations, TeX annotations, section
  context, source anchors, and deterministic IDs.
- Graphiti 0.30.1 ontology and structured episode adapter.
- D1 schema for workspaces, paper imports, and hypothesis metadata.
- Neo4j + Graph API Docker Compose definition.
- WebMCP `stage_paper_import` progressive-enhancement tool.
- Frontend, backend, lint, migration, and production-build checks.

The graph viewport currently uses a curated Attention graph while the extraction
response is shown in the import status. Persisting extracted results to a live
Neo4j instance is the next planned slice.

## Architecture

```text
Browser
  └─ Vinext / Sites
       ├─ ChatGPT sign-in
       ├─ D1 workspace metadata
       └─ authenticated HTTPS proxy
            └─ Python Graph API
                 ├─ URL security gate
                 ├─ HTML/MathML extractor
                 ├─ Graphiti episode adapter
                 └─ Neo4j
```

The web runtime never connects to Neo4j over a raw socket. Graph access is owned
by the Python service.

## Local development

Requirements:

- Node.js 22.13 or newer
- Python 3.12
- Docker Desktop when running Neo4j

On Windows, the local stack can be started with one command. It creates a
git-ignored `.env` using cryptographically secure random service and Neo4j
secrets, starts services in the background, and writes logs under `.logs`:

```powershell
.\run.ps1
```

Stop the managed processes and Neo4j container with:

```powershell
.\stop.ps1
```

Install the web app:

```powershell
npm install
npm run dev
```

Create the Python environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".\services\graph-api[dev]"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir services\graph-api --reload
```

Copy `.env.example` to a local environment file and replace every placeholder.
Never commit that file.

Run Neo4j and the Graph API:

```powershell
docker compose up --build
```

## Verification

```powershell
npm test
npm run lint
npm run build
.\.venv\Scripts\python.exe -m pytest services\graph-api
npm audit --omit=dev
```

Generate a D1 migration after changing `db/schema.ts`:

```powershell
npm run db:generate
```

Inspect every generated SQL file before publishing. Never rewrite an already
applied migration.

## Security boundary

- V1 accepts only HTTPS `arxiv.org`/`export.arxiv.org` URLs with modern numeric
  identifiers.
- Credentials, custom ports, encoded paths, arbitrary redirects, non-HTML
  responses, and bodies larger than 5 MiB are rejected.
- Browser writes require a signed-in user; Graph API writes use a separate
  service token.
- Raw HTML is parsed server-side and is never rendered in the browser.
- LLM output is unverified hypothesis data. It cannot promote itself to
  `symbolically_verified` or `human_reviewed`.

## Delivery plan

The sprint plan, task IDs, test expectations, and AI handoff procedure are in
[`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md). Current evidence
and remaining risks are tracked in [`docs/STATUS.md`](docs/STATUS.md).
