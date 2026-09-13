# FormulaGraph Lab

[![Next.js](https://img.shields.io/badge/Next.js-15-black?style=flat-square&logo=next.js)](https://nextjs.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com/)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python)](https://python.org/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.7-3178C6?style=flat-square&logo=typescript)](https://typescriptlang.org/)
[![Neo4j](https://img.shields.io/badge/Neo4j-5.26-008CC1?style=flat-square&logo=neo4j)](https://neo4j.com/)
[![License](https://img.shields.io/badge/License-MIT-blue?style=flat-square)](LICENSE)

> **Temporal, source-bound mathematical knowledge graph and verification harness.**  
> FormulaGraph Lab converts scientific research papers into an immutable, provenance-tracked formula graph—strictly separating extracted ground-truth evidence from AI hypotheses, and verifying mathematical transformations through isolated symbolic sandboxes.

---

## Key Capabilities

### 🪐 Dual-Mode 2D & 3D Interactive Viewport
- **2D Evidence Flow (ReactFlow)**: High-density node-link diagram for papers, sections, equations, and symbol contracts with auto-packing grid layout and edge routing.
- **3D Sacred Timeline (Three.js / WebGL)**: Volumetric timeline visualizing derivation lineage, equipped with dynamic cylindrical camera bounding-box framing and a deterministic golden-angle spiral layout that eliminates unreadable clumping and viewport dead space.

### 📐 Source-Bound MathML & LaTeX Extraction
- Safe, bounded arXiv ingestion engine with validated redirects, MIME filtering, and size quotas.
- Parses complex MathML and LaTeX equations while maintaining exact source anchors, section hierarchies, revision timestamps, and deterministic equation IDs.

### 🔬 Symbolic & Dimensional Verification
- **Abstract Syntax Tree (AST) Parsing**: Structural mathematical representation enabling deterministic transformation rewrites.
- **Dimensional Guard**: Analytical $[M^a L^b T^c]$ physical dimension validation across equations and tensor contractions.
- **CAS Verification**: Detects higher-derivative instabilities, pole residues, and diffeomorphism invariance violations.

### 🛡️ Container Sandbox Isolation
- **Defense-in-Depth Execution**: Untrusted formulas and verification tasks execute in ephemeral, non-root sandbox containers (`Dockerfile.sandbox`).
- **Strict Resource Boundaries**: Hardware cgroups and JobObjects enforce CPU, RAM (512 MB), wall-clock timeouts (10s), and complete network egress lockdown.
- **Role Separation**: Clear boundary separating untrusted AI **Proposers** from authoritative mathematical **Verifiers**.

### 🔍 Multi-Hop Hybrid Search
- Combines exact BM25 lexical search with Graphiti-backed semantic vector retrieval and 2-hop graph neighborhood ranking.
- Supports temporal filtering, signed keyset pagination, and tenant workspace isolation.

---

## Architecture

```text
┌─────────────────────────────────────────────────────────────────────────┐
│                      Client Browser (Next.js 15)                        │
│   ┌───────────────────────────┐       ┌─────────────────────────────┐   │
│   │   2D Flow View (ReactFlow)│       │   3D Sacred Timeline (WebGL)│   │
│   └─────────────┬─────────────┘       └──────────────┬──────────────┘   │
└─────────────────┼────────────────────────────────────┼──────────────────┘
                  ▼                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│              Next.js Application Proxy & Workspace Layer                │
│   • Cloudflare D1 Workspace DB       • Auth & Rate Limiting Gate        │
│   • Keyset Search Pagination         • Signed Graph-API Boundary        │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │ (mTLS / Signed Service Token)
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                  Python Graph API Service (FastAPI)                     │
│   ┌───────────────────────────┐       ┌─────────────────────────────┐   │
│   │  ArXiv HTML/MathML Parser │       │   Hybrid BM25 + Vector Ser. │   │
│   └─────────────┬─────────────┘       └──────────────┬──────────────┘   │
│                 │                                    │                  │
│   ┌─────────────▼─────────────┐       ┌──────────────▼──────────────┐   │
│   │  AST Verification Engine  │       │  Neo4j Exact Evidence Graph │   │
│   └─────────────┬─────────────┘       └─────────────────────────────┘   │
└─────────────────┼───────────────────────────────────────────────────────┘
                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│               Isolated Execution Sandbox (Docker / cgroups)             │
│   • Zero Network Egress               • Memory Limit: 512 MB            │
│   • Read-Only Root Filesystem         • Timeout: 10s Wall-Clock         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Quickstart

### Prerequisites
- **Node.js**: `v22.13` or newer
- **Python**: `3.12` or newer
- **Docker Desktop**: Running (for Neo4j and sandbox isolation)

### 1. Launch with One Command (Windows)
The repository includes an orchestrated runner that provisions random cryptographically secure secrets, initializes the virtual environments, tracks exact 1:1 process PIDs, and launches the entire stack:

```powershell
.\run.ps1
```

- Web App: `http://localhost:3000`
- Graph API Docs: `http://localhost:8000/docs`
- Neo4j Browser: `http://localhost:7474`
- Service Logs: `.\.logs\`

### 2. Graceful Shutdown
To cleanly terminate background workers, Python servers, and Docker containers:

```powershell
.\stop.ps1
```

---

## Manual Setup

If you prefer to run services individually:

### Frontend (Next.js / TypeScript)
```bash
npm install
npm run dev
```

### Backend (FastAPI / Python 3.12)
```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e "./services/graph-api[dev]"
uvicorn app.main:app --app-dir services/graph-api --reload --port 8000
```

### Neo4j Database
```bash
docker compose up -d neo4j
```

---

## Environment Configuration

Copy `.env.example` to `.env` in the root directory:

| Variable | Description | Default |
| :--- | :--- | :--- |
| `NEO4J_URI` | Neo4j Bolt connection string | `bolt://127.0.0.1:7687` |
| `NEO4J_USER` | Neo4j username | `neo4j` |
| `NEO4J_PASSWORD` | Neo4j authentication password | *(Auto-generated by run.ps1)* |
| `GRAPH_API_URL` | Upstream Python service URL | `http://127.0.0.1:8000` |
| `SERVICE_TOKEN` | Signed shared secret between proxy and API | *(Auto-generated by run.ps1)* |
| `OPENAI_API_KEY` | *(Optional)* Enables semantic vector retrieval | `None` (Falls back to BM25) |

---

## Verification & Code Quality

Run the comprehensive test and linting suite:

```powershell
# Type checking & Frontend Lint
npm run lint
npm run build

# Python Formatting & Linting
ruff check services/graph-api
ruff format --check services/graph-api
```

---

## Security Invariants

1. **Exact Evidence Immutability**: Ground-truth formulas extracted from papers are stored with cryptographic hashes and cannot be overwritten by LLM generations.
2. **Strict Ingestion Gate**: Only accepts canonical HTTPS `arxiv.org` URLs with modern numeric IDs (`\d{4}\.\d{4,5}`). Query parameters, credentials, custom ports, and redirects are blocked.
3. **Sandbox Lockdown**: Code and formula execution occurs in non-root Docker containers with dropped capabilities (`--cap-drop=ALL`), no network access (`--network=none`), and strict resource cgroups.
4. **No Direct Database Access from Web**: Next.js proxy communicates with Neo4j solely through the authenticated, signed Python Graph API layer.

---

## License

This project is licensed under the [MIT License](LICENSE).
