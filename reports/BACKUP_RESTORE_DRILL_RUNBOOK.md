# FormulaGraph Lab — Retention Policy, Key Ownership & Backup/Restore Drill Runbook

- **Policy Version:** `retention-policy.v1` ([`services/graph-api/app/retention.py`](../services/graph-api/app/retention.py))
- **Automated Drill Harness:** [`scripts/drill-backup-restore.ps1`](../scripts/drill-backup-restore.ps1) + [`scripts/drill_neo4j_verify.py`](../scripts/drill_neo4j_verify.py) (`make drill-backup-restore`)
- **Latest Verified Receipt:** [`reports/backup-restore-drill-receipt.json`](backup-restore-drill-receipt.json) (`2026-11-27T06:27:02Z`)
- **Scope Boundary:** Verified end-to-end on isolated local **Cloudflare D1 (`wrangler 4.166.0`)** and isolated Docker **`neo4j:5.26-community`**. Remote production Cloudflare account (`--remote`) and production Neo4j host cutover drills remain required before switching `FGL_TRUST_SITES_IDENTITY=true` (`remote_production_drill_completed: false`).

---

## 1. Retention Policy & Epistemic Immutability Contract

FormulaGraph Lab strictly partitions persisted state into **immutable scientific/governance records** (never deleted by retention jobs) and **transient infrastructure state** (bounded expiry pruning):

| Tier | Store | Labels / Tables | Retention Rule | Automated Pruning Allowed? |
| :--- | :--- | :--- | :--- | :---: |
| **Immutable Scientific Evidence** | Neo4j 5.26 | `Evidence`, `EvidenceImport`, `Episodic`, `Saga`, `FormulaAnalysisVersion`, `AnalysisMigration` | Indefinite append-only (`source_sha256` + `S*`/`L*` anchors) | **NEVER** (fail-closed) |
| **Immutable Human Reviews & Receipts** | Neo4j 5.26 | `ContractReview`, `CheckResult`, `ProblemSpec`, `ResearchProposal`, `ProposalReview`, `CompiledCandidate`, `TransformationActivity`, `AdmissionDecision`, `NumericalFixtureResult`, `ResearchCaseResult`, `ResearchProtocolReview`, `ResearchJob`, `EvolutionCampaign` | Indefinite append-only (supports offline replay bundles & audit verification) | **NEVER** (fail-closed) |
| **Tenant Ownership & Metadata** | Cloudflare D1 | `workspaces`, `papers`, `import_jobs`, `hypotheses`, `d1_migrations` | Retain for lifecycle of workspace; point-in-time + scheduled SQL snapshots | **NEVER** (only explicit user/tenant deletion) |
| **Transient Auth & Quota State** | Neo4j 5.26 | `ServiceRequestNonce`, `WorkspaceRate` | `ServiceRequestNonce`: prune `expires_at < now - 300s` (5-minute clock-skew grace); `WorkspaceRate`: prune `expires_at < now_ms` | **YES** (strictly via allowlisted `prune_expired_transient_records`, max `5,000`/batch, no `DETACH DELETE`) |
| **In-Process Telemetry Window** | FastAPI RAM | `WorkspaceTelemetryTracker` (`observability.py`) | 15-minute sliding window, max `2,000` samples/workspace, max `256` workspaces LRU | Automatic ring-buffer eviction (zero DB writes) |

All protected label guards are enforced in code via `assert_prunable_label()` and verified by [`services/graph-api/tests/test_retention_and_backup.py`](../services/graph-api/tests/test_retention_and_backup.py).

---

## 2. Recovery Targets (RPO / RTO) & Key Ownership Matrix

| Objective | Target | Mechanism |
| :--- | :--- | :--- |
| **RPO (Recovery Point Objective)** | **≤ 15 minutes** | Cloudflare D1 Time Travel (continuous 1-minute granularity, 30-day history on paid tier) + scheduled 15-minute encrypted Neo4j logical dumps / transactional backup snapshots |
| **RTO (Recovery Time Objective)** | **≤ 30 minutes** | Two-phase D1 schema+data restore (`wrangler d1 execute`) + `neo4j-admin database load --overwrite-destination=true` + post-restore SHA-256/parity verification script |
| **Backup Integrity Verification** | **100% SHA-256** | Every exported artifact (`d1-schema.sql`, `d1-data.sql`, `neo4j.dump`) is SHA-256 hashed at creation time and verified before restore |

### Key & Secret Ownership Matrix

| Secret / Key | Purpose | Owner & Storage | Rotation / Restore Dependency |
| :--- | :--- | :--- | :--- |
| `SERVICE_TOKEN` | `fgl1` HMAC-SHA256 BFF ↔ FastAPI request signing | Platform Ops (Cloudflare Worker Secret + FastAPI Secret Manager) | Rotate dual-deploy; not stored in D1/Neo4j dumps |
| `FGL_RESEARCH_QUEUE_SECRET` | Independent HMAC key signing `JobEnvelope` | Platform Ops (FastAPI + Worker Secret Manager; MUST differ from `SERVICE_TOKEN`) | Required to verify queued jobs after Neo4j restore |
| `FGL_WORKER_IDENTITIES` | Scoped worker tokens & workspace grants | Platform Ops (Secret Manager) | Must differ from both `SERVICE_TOKEN` and queue secret |
| `NEO4J_PASSWORD` | Neo4j Bolt authentication | Database Ops (Secret Manager) | Re-provisioned on restored Neo4j container/cluster |
| `SEARCH_CURSOR_SECRET` | HMAC key for keyset pagination cursors | Platform Ops (Secret Manager) | Safe to rotate on restore (clients restart cursor) |
| **Backup Encryption Key (KMS / Age / GPG)** | Client-side encryption at rest for off-host `d1-*.sql` and `neo4j.dump` archives | Security / SRE (Cloud KMS / Hardware Vault, **strictly separated from backup object bucket IAM**) | Mandatory before storing production dumps in R2/S3/GCS |

---

## 3. Critical Operational Finding Discovered During Live Drill

> [!WARNING]
> **Wrangler 4 Single-File `d1 export` Foreign-Key Ordering Trap**
>
> During execution of the real drill on `wrangler 4.166.0`, running a single-file `wrangler d1 export site-creator-d1 --output backup.sql` followed by `wrangler d1 execute site-creator-d1 --file backup.sql` **failed** with:
> `X [ERROR] no such table: main.workspaces: SQLITE_ERROR`
>
> **Root Cause:** `wrangler d1 export` emits tables in alphabetical order (`hypotheses` → `import_jobs` → `papers` → `workspaces`) and immediately interleaves `INSERT INTO hypotheses` before `CREATE TABLE workspaces` has executed, triggering SQLite foreign-key validation error even with `PRAGMA defer_foreign_keys=TRUE;`.
>
> **Verified Production-Safe Fix (Two-Phase Export & Restore):**
> Always export schema and data separately (`--no-data` and `--no-schema`) and restore schema first, then data:
> 1. `wrangler d1 export site-creator-d1 [--local|--remote] --no-data --output d1-schema.sql`
> 2. `wrangler d1 export site-creator-d1 [--local|--remote] --no-schema --output d1-data.sql`
> 3. Restore onto brand-new D1 target:
>    - `wrangler d1 execute site-creator-d1 [--local|--remote] --file d1-schema.sql`
>    - `wrangler d1 execute site-creator-d1 [--local|--remote] --file d1-data.sql`

---

## 4. Reproducible Step-by-Step Procedures

### A. Automated Local/Isolated Drill (Zero Side Effects)

```powershell
# Full isolated drill: Neo4j 5.26 Community (Docker volumes) + Cloudflare D1 (temp cwd)
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\drill-backup-restore.ps1

# Cloudflare D1-only drill (when Docker daemon is unavailable)
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\drill-backup-restore.ps1 -D1Only
```

### B. Production Cloudflare D1 Backup & Restore

```bash
# 1. Export immutable schema + data snapshots (hash both immediately)
wrangler d1 export site-creator-d1 --remote --no-data --output d1-schema-$(date -u +%Y%m%dT%H%M%SZ).sql
wrangler d1 export site-creator-d1 --remote --no-schema --output d1-data-$(date -u +%Y%m%dT%H%M%SZ).sql
sha256sum d1-schema-*.sql d1-data-*.sql > d1-checksums.sha256

# 2. Point-in-time recovery alternative (Cloudflare Time Travel)
wrangler d1 time-travel info site-creator-d1
wrangler d1 time-travel restore site-creator-d1 --timestamp="<ISO-8601-UTC>"

# 3. Clean-target restore from verified SQL backups
sha256sum -c d1-checksums.sha256
wrangler d1 execute site-creator-d1 --remote --file d1-schema-<ts>.sql
wrangler d1 execute site-creator-d1 --remote --file d1-data-<ts>.sql
```

### C. Production Neo4j 5.26 Community Backup & Restore

```bash
# 1. Quiesce FastAPI writes, stop Neo4j cleanly so store files checkpoint
docker stop -t 30 <neo4j-container>

# 2. Create offline logical dump and compute SHA-256 checksum
docker run --rm \
  -v <neo4j-data-vol>:/data \
  -v /secure/backups:/backups \
  neo4j:5.26-community \
  neo4j-admin database dump neo4j --to-path=/backups
sha256sum /secure/backups/neo4j.dump > /secure/backups/neo4j.dump.sha256

# 3. Restore onto brand-new empty volume
sha256sum -c /secure/backups/neo4j.dump.sha256
docker run --rm \
  -v <new-neo4j-data-vol>:/data \
  -v /secure/backups:/backups \
  neo4j:5.26-community \
  neo4j-admin database load neo4j --from-path=/backups --overwrite-destination=true
```

---

## 5. Latest Measured Drill Evidence (`2026-11-27T06:27:02Z`)

Recorded verbatim in [`reports/backup-restore-drill-receipt.json`](backup-restore-drill-receipt.json):

| Store | Backup Method | Backup Size | Backup SHA-256 (prefix) | Measured Backup Time | Measured Restore RTO | Parity & Safety Checks |
| :--- | :--- | :---: | :--- | :---: | :---: | :--- |
| **Cloudflare D1 (`site-creator-d1`)** | `wrangler d1 export --local` (`--no-data` + `--no-schema`) | `3,762 B` | schema `626fde7c…` / data `d957693f…` | `2,900 ms` | **`2,983 ms`** | `4/4` tables (`workspaces`, `papers`, `import_jobs`, `hypotheses`) restored `1=1`; cross-store `source_sha256` exact match (`927dac1fad8f…`) |
| **Neo4j 5.26 (`neo4j:5.26-community`)** | `neo4j-admin database dump` → destroy volume → `database load` | `42,127 B` | `53556e32…` | `4,401 ms` | **`14,676 ms`** | `1` expired `ServiceRequestNonce` + `1` expired `WorkspaceRate` pruned; `1` active nonce & `100%` protected labels intact (`Evidence: 9`, `ContractReview: 1`, `CheckResult: 1`, `FormulaAnalysisVersion: 3`); restored `review_hash` (`ac706ba2…`), `CheckResult` (`supported`), and live `EvidenceSearchService` (`3` hits) verified |

**Total End-to-End Drill Wall Time:** `66,600 ms` (well inside the 30-minute RTO target for both stores).
