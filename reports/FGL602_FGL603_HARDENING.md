# FGL-602/603 hardening — checkpoint 2026-10-01

## Release status

Not release-ready. Production identity testing was explicitly deferred by the
owner on 2026-10-01. Do not enable trusted identity on a public standalone origin.
No deployment, real-data migration, real backup export, secret rotation or paid
provider call was performed in this work.

## Implemented and locally verified

| Area | Delivered | Boundary |
| --- | --- | --- |
| Distributed request quota | Neo4j unique workspace node, write lock before reading history, DB clock, 120 admissions/60 seconds across replicas, bounded expiry cleanup | No RAM fallback. Store outage returns 503. Replay/foreign-workspace rejection precedes quota; quota rejection never executes the endpoint. Compute and spend reservations are separate and retained. |
| Body/provider exhaustion | 4 KiB import JSON / 2 MiB general body, 10-second total body deadline, byte buffer rather than per-chunk allocation; provider response 256 KiB and total configured deadline | Both byte-drip and oversized response have runnable tests. API container additionally uses native Uvicorn concurrency 32. Ingress/global network DoS controls still require hosting configuration. |
| Browser headers | Per-response random script nonce, same policy forwarded to SSR, caller nonce/policy removed; nosniff/no-referrer/no-store/HSTS on HTTPS, script attributes denied, frame restriction | Production HTTP/SSR smoke is not authenticated browser acceptance. Standalone framing denied; explicitly trusted Sites allows only `https://chatgpt.com`, not arbitrary origins. The identity flag is not a gateway authenticator. |
| Narrow CSS exception | Script policy has neither unsafe-inline nor unsafe-eval; style elements use nonce | `style-src-attr 'unsafe-inline'` remains for KaTeX/React Flow geometry. This is an explicit, reviewable CSS exception, **not** completion of a zero-inline-style policy. Do not relax script CSP to resolve renderer problems. |
| Hidden paid path | Setting the proposal API key no longer automatically activates Graphiti LLM/embedding work | Semantic enrichment requires separate opt-in in development and remains disabled in production until durable cost reservation/metering/reconciliation exists. Exact evidence ingestion/search remains available. |
| Correlation | Server-owned HTTP request ID, isolated request context, signed service nonce and authenticated tenant; JSON telemetry for extraction/yield, graph receipt, semantic status, candidate/parent/version and checker receipt | Logs use route templates, never raw query/body/headers. Receipt events occur after managed transactions commit. IDs in logs do not replace persisted provenance. BFF-to-worker/campaign correlation is not yet complete. |
| Provider metering | Duration/status, reserved cost bound, validated provider input/output token counts and estimated cost using configured unit prices | Missing usage stays unknown, not zero. Estimated cost is not invoice/billing truth; cached/reasoning pricing and reconciliation need operator validation. No prompt/model output/API key logged. |
| Supply chain | Full npm audit cleaned by scoped esbuild override, migration-loader regression; digest-pinned base/tool images; Graph API installs frozen uv lock without dev extras; non-root, read-only Compose API and loopback ports | Pipeline is configured with SHA-pinned actions and failing scans, not a certification. Runtime images change IDs after rebuild: no environment image pin was silently replaced. |

The nonce policy follows the [OWASP CSP guidance](https://cheatsheetseries.owasp.org/cheatsheets/Content_Security_Policy_Cheat_Sheet.html).
The CSS-attribute exception is separate from script authorization; see
[MDN style-src-attr](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy/style-src-attr).
The esbuild override addresses the maintainer's
[dev-server advisory](https://github.com/evanw/esbuild/security/advisories/GHSA-67mh-4wv8-2f99);
it does not downgrade Drizzle or change migration history.

## Check commands and evidence scope

Run from repository root:

```powershell
pwsh -NoProfile -File verify-research.ps1
pwsh -NoProfile -File test-integration.ps1
pwsh -NoProfile -File test-sandbox.ps1
npm run build
npm audit
# Start the built worker on an unused local port, then in another terminal:
npm start -- --port 4173 --ip 127.0.0.1 --log-level error
node tests/production-security-smoke.mjs http://127.0.0.1:4173
```

Latest recorded runs: 687 backend offline tests; 186 frontend tests; 33 real
Neo4j integration tests; 7 sandbox tests and one clean-container replay test.
Ruff, lint, TypeScript, production build and production HTTP smoke pass.
Frozen runtime requirements audit: 41 packages, zero known advisories.
Full and production-only npm audit: zero known advisories at scan time.

## Linux scan is a failing release gate

The first Graph API image scan found high-severity OS package findings. Exact
available Debian patches were installed for PCRE2 and OpenSSL packages, not a
blanket unpinned distro upgrade. The subsequent scan still reports **44 high
package/advisory pairs, eight distinct CVEs, zero critical**, all without a fixed
version in that scan. These are not 44 independent bugs; several CVEs affect
multiple util-linux binary packages.

Remaining CVEs: `CVE-2026-76642`, `CVE-2026-78408`, `CVE-2026-78409`,
`CVE-2026-78410`, `CVE-2026-54369`, `CVE-2025-69720`, `CVE-2026-16742`,
`CVE-2026-9538`. The report is scanner evidence, not a complete reachability
analysis or a declaration that every finding is exploitable in this app.

Reproduce using the pinned scanner in `.github/workflows/security-gates.yml`.
Local JSON evidence is under ignored `.logs/security/`; scans contain no source
HTML or provider payload. CI retains scan reports for 30 days by revision.
Final local API scan: source at `b048405` (backend changes in `e6c8ed9`),
image `sha256:b30484823ffd4d9e7d680bb00f3fc82def8b0b7863e04aaeeab13f4f8501eede`,
report `.logs/security/graph-api-final.json`. The Performer image also builds;
that alone is not its complete protocol/replay verification or an OS scan.
CI does **not** use `--ignore-unfixed`, `continue-on-error`, or an automatic
exception. An operator-approved exception, if any, must identify CVE, affected
image digest, reachability evidence, owner and expiry. None has been granted.

## Remaining implementation work, in order

1. **Execution-profile identity (603).** Capture normalized memory/CPU/effective
   timeout alongside image/module/protocol at admission. Bind the profile into
   signed job identity and versioned receipts; reject config drift before run.
   Preserve old receipt readability without treating old resources as current.
   Tests: different profiles cannot replay each other; changing environment
   between queue admission/execution fails closed; sandbox observed settings
   match the receipt. This is still open, not covered by telemetry.
2. **Complete telemetry/recovery (603).** Correlate BFF, import, candidate,
   worker job, admission and campaign using persisted IDs; record numerical/
   empirical outcomes and queue/policy/spend transitions after commit. Add
   controlled dead-letter reconciliation with separately authorized retry and
   idempotency. Never rerun uncertain jobs, refund reservations or promote a
   winner just because recovery changed a status. Test double delivery,
   controller death, DB/provider failure and redacted actionable alerts.
3. **Durable audit (602).** Select an append-only external sink with a distinct
   writer credential, encryption, retention and verification/export. Standard
   logs here remain telemetry. Design fail policy per security operation and
   test sink outage, TX retry/rollback and delete/edit denial or detection.
   Do not call a normal mutable Neo4j collection or local logfile immutable.
4. **Retention/backup/incident (602).** Approve storage/retention/RPO/RTO and
   encryption key ownership, then implement and run an isolated D1 + Neo4j
   restore drill. Restore source bytes/hashes, review records, versions, job
   reservations and replay bundles, not just graph node counts. No cleanup may
   delete immutable evidence or release an uncertain reservation. Runbook below
   is a proposed operational procedure, not a completed backup test.
5. **Supply-chain release decision (602).** Execute new CI on the pushed
   revision, scan all three actual image variants, address reachable findings
   or obtain scoped exceptions. Validate the rebuilt Performer worker before
   replacing any deployment image ID. npm/Python scan success cannot override
   a failing base-OS scan.
6. **Browser/identity acceptance (601/602).** Deferred by owner: staging and
   direct-origin/header forgery tests, two-user isolation, authenticated
   desktop/tablet/mobile CSP and source-to-export flow. Keep release gate open.

## Proposed retention and incident procedure — approval required

Evidence, frozen specs, reviews and receipts are durable research records; any
deletion/retention period requires an approved policy, not an automatic TTL.
Nonce and request-rate history are transient; their bounded cleanup must never
share labels/queries with evidence, audit records or compute/spend reservations.
Operational telemetry contains pseudonymous tenant/actor IDs and needs access
restriction and a retention policy. CI scan artifacts use a separate 30-day
TTL; that is not a legal or research-record retention requirement.

Backup drill: identify the exact staging D1 database and Neo4j volume; choose
vendor-supported consistent snapshot/export procedures; encrypt before upload
using separately controlled keys; store manifest of revision, hashes, image
IDs, schema and event checkpoint. Restore only into new isolated resources,
disable model/worker capabilities, verify two-user ownership and immutable
source bytes, then replay an exported bundle. Compare reservations and pending
jobs before reconnecting workers. Record measured RPO/RTO and restore evidence.
Do not stop or dump the owner's live database without authorization.

Incident: restrict ingress first; preserve redacted logs, IDs and receipts;
disable new paid/model/worker admission; rotate web/API signing key together
and revoke affected worker identities using separate keys. Nonce replay ledger
must stay intact. Quarantine uncertain jobs and reserve budget while an operator
reconciles receipts/results. Never overwrite a parent, evaluator or holdout to
repair an incident. Reopen ingress only after negative forgery/isolation tests
and a recorded operational decision.

Threat-model follow-up: explicitly test HTML prompt injection, fabricated source
IDs, model output attempting approve/score, artifact poisoning, evaluator/holdout
tampering, cross-tenant operations, quota exhaustion and infrastructure failure.
Existing trust tests are regression evidence, not blanket OWASP/ISO compliance.
