# Sprint 4.5 and Sprint 5 implementation handoff

Updated 2026-09-13. The current worktree passes the offline verification script,
production build, and isolated Neo4j integration suite. Sprint 4.5 remains partial
because the H6 container and research-queue controls are not implemented.

## Implemented in this continuation

- H5 source/analysis separation now uses content-addressed records binding the
  equation UUID, complete source hash, analysis content and analyzer versions.
- Reimport appends analysis without rewriting the stored Equation payload,
  including historical payloads that contain embedded analysis.
- Graph snapshots join active analysis at read time. Immutable Neo4j Records
  are converted to dictionaries before response enrichment.
- Analysis history is paginated and tenant-scoped; retired versions remain
  explicitly queryable.
- Migration runs in bounded transactions with a locked receipt and cursor.
  Legacy analysis is preserved as `legacy-embedded.v0` alongside new analysis.
  The receipt binds source-only and exact stored-byte hashes for every equation.
- Rollback retires only versions introduced/restored by that receipt, preserving
  pre-existing active versions and all raw evidence. A fresh migration run ID
  can restore retired versions. Source verification is a separate read-only CLI
  operation; inventory can also run before migration without schema writes.
- H6 worker credentials now have fixed role capabilities and explicit workspace
  grants. Research check execution defaults to disabled. Gateway/worker token
  reuse, duplicate credentials and wildcard grants fail closed.
- Checker requests resolve the tenant-scoped Equation before running and reject
  a left-hand formula that differs from source. Proposer credentials cannot run
  checks, even with forged identity headers.
- The legacy compare API now uses the bounded checker and includes the result
  vector, preserving unknown outcomes and domain holes.
- H1 retry reconciliation now compares intent fields only (excluding
  server-generated `reviewed_at`). Replayed receipts return the original
  stored timestamp from Neo4j, not the retry's fresh timestamp. Equality
  checks deserialize and compare via `model_dump(exclude={"reviewed_at"})`.
- H3 typed canonicalization and scope audit: binder-bound declarations
  (e.g. `\sum_i x_i`) correctly isolate the binder to local scope while bound
  expressions (e.g. `\int_a^b f(x) dx` or `\sum_{i=i}^i`) resolve bounds in outer
  scope. Bound variables alpha-rename capture-avoidingly; production
  `analyze_equation` binds semantic normalization to explicit `core.add.v1` and
  `core.multiply.v1` operator semantics; `append_contract_review` re-analyzes the
  equation and persists a new `FormulaAnalysisVersion` connecting reviewed contracts
  to production semantic IR.
- H4 / H6 bounded execution and request idempotency:
  Worker execution now enforces a 256MB RAM ceiling (via Windows Job Object
  `JOB_OBJECT_LIMIT_PROCESS_MEMORY` + `JOB_OBJECT_LIMIT_JOB_MEMORY` and POSIX
  `RLIMIT_AS`), CPU limits, wall-clock timeout with full process-tree termination
  (`taskkill /T /F` on Windows and `os.killpg` on POSIX), and output byte caps
  (`MAX_WORKER_OUTPUT_BYTES = 64KB`) after worker exit. Environments are sanitized;
  HTTP proxy null-routing is defense in depth, not raw-socket isolation. Finite
  counterexample validation distinguishes exact finite differences
  from indeterminate/non-finite forms (`nan`, `zoo`, `oo` remain unknown). Uncontracted
  symbol coverage is verified before CAS evaluation. Check result idempotency
  reconciliation ignores timer/duration variance and returns original stored receipts.
  The legacy `sympy_equivalent` boolean helper is explicitly marked non-authoritative.

## Local verification

From the repository root, with existing `.venv` and npm dependencies installed:

```powershell
pwsh -NoProfile -File .\verify-research.ps1
pwsh -NoProfile -File .\verify-research.ps1 -IncludeIntegration
```

The script fails at the first failing check. The second command also runs the
isolated Neo4j suite through `test-integration.ps1`; Docker Desktop must be ready.
Neither command has been run during this continuation.

Targeted backend tests, if isolating a failure:

```powershell
Push-Location .\services\graph-api
..\..\.venv\Scripts\python.exe -m pytest tests/test_analysis_versions.py tests/test_check_api.py tests/test_formula_api.py -q
Pop-Location
```

## Migration exercise

Configure `NEO4J_URI`, `NEO4J_USER`, and `NEO4J_PASSWORD` for the intended test
database. Replace `WORKSPACE_ID` with an existing workspace ID. Inventory is
read-only. Migrate creates analysis and receipt records in that workspace.

```powershell
Push-Location .\services\graph-api
..\..\.venv\Scripts\python.exe -m app.migrate_analysis inventory --workspace WORKSPACE_ID
..\..\.venv\Scripts\python.exe -m app.migrate_analysis migrate --workspace WORKSPACE_ID --run-id h5-exercise-001
# Use receipt_id from the preceding JSON output:
..\..\.venv\Scripts\python.exe -m app.migrate_analysis verify --workspace WORKSPACE_ID --receipt RECEIPT_UUID
..\..\.venv\Scripts\python.exe -m app.migrate_analysis rollback --workspace WORKSPACE_ID --receipt RECEIPT_UUID
..\..\.venv\Scripts\python.exe -m app.migrate_analysis inventory --workspace WORKSPACE_ID
Pop-Location
```

Run the exercise while imports are paused: inventory compares the complete set
of equations before/after, so concurrent imports are reported as inventory
changes. Receipt verification separately checks exactly the migrated sources.
Interrupted batches resume with the same run ID. A rolled-back run requires a
new run ID. No database migration has been executed by this continuation.

## Remaining required work

The Sprint 4.5 release gate and Sprint 5 are **not complete**.

1. H4 & H4/H6: [COMPLETED] Immutable CheckResult request idempotency is enforced
   with variance-tolerant reconciliation; finite counterexample validation distinguishes
   real constant differences from non-finite/indeterminate forms; uncontracted symbol
   coverage is audited; worker memory is clamped to 256MB with child process tree
   termination on timeout and output byte caps; legacy boolean CAS helper is deprecated.
2. H5 migration tooling: [COMPLETED] Dry-run, batch migration, and rollback CLI paths
   in `services/graph-api/app/migrate_analysis.py` are hardened with explicit env checks
   and unit tested in `test_migrate_analysis_cli.py`. Migration integration fixture is fixed.
   Note: live Docker Neo4j daemon run requires elevated environment.
3. H6 & Threat Model: [PARTIAL] `docs/THREAT_MODEL.md` authored covering all 6 mandatory
   vectors (model output, external HTML, queue messages, workers, artifact storage, tenant boundaries).
   Role/tenant isolation and feature gates (`PROPOSALS_ENABLED=False`, `EVOLUTION_ENABLED=False`,
   `FGL_ENABLE_RESEARCH_CHECKS=False`) are enforced in code. Container sandboxing scheduled for Sprint 5.
4. Implement Sprint 5: G1-G3, D1-D2, V0-V3, P1, E1, U1 and R1 with the
   dependencies, tests and acceptance criteria in IMPLEMENTATION_PLAN.md.
7. Complete engine and research-case acceptance, including frozen attention
   evaluator, preserving/hypothesis transforms, rejection/unknown/conditional
   examples, export replay and end-to-end UX. No broad completion claim is
   supported by the present worktree.

Preserve unrelated edits to `run.ps1` and the research document. No commit,
push, deployment, model call or destructive source migration was performed.
