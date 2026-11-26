# Playbook 3 — Adversarial Security, Sharp-Edges & Supply-Chain Risk Audit

**Audited Commit Baseline:** `ee0c06546fa3` (`main`, post-Playbook 1 `1a8d11c` & Playbook 2 `ee0c065`)  
**Audit Date:** `2026-11-26`  
**Methodologies Applied:** `sharp-edges` + `differential-review` + `supply-chain-risk-auditor` (`.agents/skills/`)

---

## 1. Executive Summary

- **Application & Lockfile Advisories:** `0` known advisories across `39` direct + `695` transitive npm packages (`package-lock.json`), `0` known advisories across `12` direct + `41` transitive Python packages (`services/graph-api/uv.lock`), and `0` runtime crate dependencies in `crates/formula-layout-wasm` (`64,555` bytes WASM binary).
- **Container CVE Gate (`aquasec/trivy`):** Run `45866396138` on `ee0c065` identified **`1` fixable HIGH CVE** (`CVE-2026-124851` on `libzstd1` `1.5.7+dfsg-1` → `1.5.7+dfsg-1+deb13u1`) and **`13` unique upstream unfixed Debian Trixie HIGH CVEs** (`0` CRITICAL, `0` Python package CVEs). Playbook 3 patches `libzstd1=1.5.7+dfsg-1+deb13u1` and purges `/var/lib/apt/lists/*` across all three Dockerfiles (`Dockerfile`, `Dockerfile.sandbox`, `Dockerfile.performer.sandbox`), reducing fixable HIGH/CRITICAL container CVEs to **`0`**.
- **Differential & Sharp-Edges Hardening:** Audited recent commits (`df2c12e..ee0c065`) plus all L2/L3/L4 trust boundaries (`proxy.ts`, `app/chatgpt-auth.ts`, `lib/server/*`, `auth.py`, `worker_auth.py`, `security.py`, `sandbox.py`, `execution_profile.py`, `research_jobs.py`, `search.py`, `proposals.py`, `tex_extractor.py`, `paper_artifacts.py`). Resolved **7 concrete sharp-edge findings** (zero wire-breaking changes) and added dedicated **Threat Model P1** adversarial tests covering prompt injection, model output tampering, artifact poisoning, and evaluator/sandbox tampering.

---

## 2. Differential Security Review (`df2c12e..ee0c065` & Trust Boundaries)

### Blast Radius & Scope Triage
- **Codebase Tier:** MEDIUM (`~95` TS/TSX files + `~60` Python modules + `1` Rust WASM crate).
- **HIGH-RISK Modified Surfaces Reviewed:**
  1. `services/graph-api/app/tex_extractor.py` (`ee0c065` macro expansion) — callers: `POST /v1/evidence/import-tex` (`main.py`), `test_tex_extractor.py`, `test_pbt_invariants.py`. Verified non-Turing-complete ceilings (`MAX_MACRO_DEFS=64`, `MAX_MACRO_ARITY=4`, `MAX_MACRO_EXPANSION_DEPTH=4`, `MAX_MACRO_EXPANSIONS_PER_EQUATION=128`, `MAX_EXPANDED_EQUATION_CHARS=16384`), protected command denylist, linear-time regex matching, and raw `source_sha256`/`source_fragments` immutability. Added fail-closed NUL-byte rejection (`\x00`).
  2. `services/graph-api/app/symbolic_worker.py`, `verification.py`, `symbol_contracts.py`, `lean_formalizer.py` (`1a8d11c`) — callers: `POST /v1/checks/symbolic/quick`, `POST /v1/research/candidates/{id}/verify`, `sandbox.py`. Verified SymPy assumptions are populated strictly from accepted human `ContractReview` records, non-scalar matrices stay `commutative=False`, `force=True` is absent, and Lean 4 drafts stay `FROZEN_UNPROVED` until kernel-verified.
  3. `lib/server/inspector-request.ts`, `app/api/checks/symbolic/route.ts`, `app/api/contract-reviews/batch/route.ts` (`bba7432`, `df2c12e`, `1a8d11c`) — shared BFF proxy for Inspector write actions. Hardened missing `X-Content-Type-Options: nosniff`, upstream request-ID correlation (`x-upstream-request-id`), and non-object JSON root guard.
- **Git Blame Check:** Zero security validations, CSP rules, fail-closed flags, or auth checks were weakened or removed in `df2c12e..ee0c065`.

---

## 3. Sharp-Edges Findings & Remediations

| ID | Severity | Location | Sharp Edge / Footgun | Remediation Applied |
| :--- | :---: | :--- | :--- | :--- |
| **SE-01** | **Medium** | [`worker_auth.py`](../services/graph-api/app/worker_auth.py) | `require_worker` did not reject duplicate `Authorization` headers (unlike `auth.py:110`), and `configured_credentials()` compared worker tokens against `SERVICE_TOKEN` with `==` while not checking `FGL_RESEARCH_QUEUE_SECRET`. | Added `request: Request` duplicate-header check (`401`), switched to `secrets.compare_digest`, and rejected collisions with `FGL_RESEARCH_QUEUE_SECRET` (`503`). |
| **SE-02** | **Medium** | [`lib/server/inspector-request.ts`](../lib/server/inspector-request.ts) | Shared Inspector proxy omitted `X-Content-Type-Options: nosniff`, omitted `x-upstream-request-id` header/telemetry correlation, and forwarded raw `JSON.parse` output (`null`/`[]`) directly into caller parsers. | Added `x-content-type-options: nosniff`, propagated `x-upstream-request-id` + `upstream_request_id` telemetry, and guarded plain JSON object before `parse(data)`. |
| **SE-03** | **Medium** | [`security.py`](../services/graph-api/app/security.py) & [`bff-telemetry.ts`](../lib/server/bff-telemetry.ts) | Log sanitizers did not redact bare `fgl1.<b64>.<sig>` tokens without a `Bearer ` prefix, nor newer untrusted text keys (`tex_source`, `latex`, `source_fragments`, `research_question`, `formula_b`, `steps`). | Added `_FGL1_PATTERN` / `FGL1_PATTERN` scrubbing and expanded sensitive key lists across both L2 and L3 telemetry/audit filters. |
| **SE-04** | **Medium** | [`security.py`](../services/graph-api/app/security.py) | `RequestBodyLimitMiddleware.__init__` accepted unvalidated `body_timeout_seconds` (`0`, `-1`, `NaN`, `inf`, `True`). | Enforced finite numeric range `[0.001, 60.0]` in constructor, failing closed with `ValueError`. |
| **SE-05** | **Medium** | [`research_jobs.py`](../services/graph-api/app/research_jobs.py) | `configured_queue_key()` accepted whitespace/control chars in `FGL_RESEARCH_QUEUE_SECRET` and compared against `SERVICE_TOKEN` via `==`. | Enforced printable ASCII `^[\x21-\x7e]{32,256}$` and constant-time `hmac.compare_digest`. |
| **SE-06** | **Low** | [`search.py`](../services/graph-api/app/search.py) | `SearchCursorCodec.decode()` used `isinstance(value["s"], int)` and `value["v"] != 1`, which accept Python `bool` (`True`/`False`) as `int`. | Replaced with strict `type(...) is int` checks. |
| **SE-07** | **Low** | [`tex_extractor.py`](../services/graph-api/app/tex_extractor.py) & [`proposals.py`](../services/graph-api/app/proposals.py) | `extract_tex` did not reject NUL bytes (`\x00`), and `ProposalGenerateRequest.research_question` did not enforce plain-text HTML/control-char filtering matching `ProposalDraft`. | Added NUL-byte rejection in `extract_tex` and HTML/C0-control-char rejection in `validate_research_question`. |

### Residual Accepted Design Notes (Non-Breaking)
- **`SearchCursorCodec` Key Fallback (`main.py:230`):** When `SEARCH_CURSOR_SECRET` is unset, `main.py` falls back to `SERVICE_TOKEN`. Operators deploying to staging/production should configure a dedicated 32+ char `SEARCH_CURSOR_SECRET` distinct from `SERVICE_TOKEN`.
- **CSS Inline Style Exception (`proxy.ts:22`):** `style-src-attr 'unsafe-inline'` remains intentionally scoped to CSS attributes for KaTeX/ReactFlow geometry while `script-src` remains strictly nonce + `'strict-dynamic'` + `'wasm-unsafe-eval'`.

---

## 4. Supply-Chain Risk Audit (Measured Verbatim by `supply-chain-risk-auditor`)

### A. Frontend / BFF (`package.json`, `package-lock.json` @ `ee0c06546fa3`)
- **Direct dependencies:** `39` (`15` production runtime, `24` dev/build-time).
- **Direct advisories:** **`0 / 39`** assessed-clean.
- **Transitive advisories:** **`0 / 695`** registry-verified locked packages assessed-clean.
- **Deprecated/yanked:** `0 / 39`; **Repository archived:** `0 / 39`; **Install-time script execution (direct):** `0 / 39`.
- **Direct findings reaching production (`2` maintenance/publisher notes, `2` upstream Scorecard flags):**
  - `clsx@2.1.1` (173,568,112/wk): no push in 2 years (last `2024-06-10`); single human publisher (`lukeed`) of 1 listed.
  - `class-variance-authority@0.7.1` (84,993,563/wk): single human publisher (`joebell93`) of 1 listed.
  - `katex@0.18.7` (46,667,054/wk): upstream OpenSSF Scorecard `Dangerous-Workflow` 0/10.
  - `three@0.186.0` (38,494,970/wk): upstream OpenSSF Scorecard `Binary-Artifacts` 7/10.
- **Build-time only findings:** Single publisher (`types`, DefinitelyTyped automation) on `@types/katex`, `@types/node`, `@types/react`, `@types/react-dom`, `@types/three`; `vite@8.3.8` OpenSSF Scorecard `Binary-Artifacts` 1/10.
- **Coverage Table (npm):**

| Criterion | Tier | Assessed | Flagged | Not assessable |
| :--- | :--- | :---: | :---: | :---: |
| Known advisories | A | 39/39 | 0 | 0 |
| Deprecated or yanked | A | 39/39 | 0 | 0 |
| Repository archived | A | 39/39 | 0 | 0 |
| Maintenance activity | A | 39/39 | 1 | 0 |
| Publisher concentration | B | 10/39 | 7 | 29 *(CI provenance)* |
| Install-time script execution | B | 39/39 | 0 | 0 |
| Dangerous CI workflow | scorecard | 21/39 | 1 | 18 *(no Scorecard)* |
| Checked-in binaries | scorecard | 21/39 | 2 | 18 *(no Scorecard)* |

### B. Backend API (`services/graph-api/pyproject.toml`, `uv.lock` @ `ee0c06546fa3`)
- **Direct dependencies:** `12` (`8` production runtime, `4` dev).
- **Direct advisories:** **`0 / 12`** assessed-clean.
- **Transitive advisories:** **`0 / 41`** registry-verified locked packages assessed-clean (`pip-audit --require-hashes` green in CI).
- **Flagged criteria:** `0 / 12`.
- **Coverage Table (PyPI):**

| Criterion | Tier | Assessed | Flagged | Not assessable |
| :--- | :--- | :---: | :---: | :---: |
| Known advisories | A | 12/12 | 0 | 0 |
| Deprecated or yanked | A | 12/12 | 0 | 0 |
| Repository archived | A | 12/12 | 0 | 0 |
| Maintenance activity | A | 12/12 | 0 | 0 |
| Publisher concentration | B | 0/12 | 0 | 12 *(PyPI has no ACL)* |
| Install-time script execution | B | 0/12 | 0 | 12 *(wheel vs sdist)* |
| Dangerous CI workflow | scorecard | 7/12 | 0 | 5 *(no Scorecard)* |
| Checked-in binaries | scorecard | 7/12 | 0 | 5 *(no Scorecard)* |

### C. Container Image CVE Audit (`Container CVE Gate` — `aquasec/trivy`)
- **Patched in Playbook 3:** Upgraded `libzstd1` from `1.5.7+dfsg-1` to `1.5.7+dfsg-1+deb13u1` (resolves **`CVE-2026-124851`**, HIGH) and purged `/var/lib/apt/lists/*` in `Dockerfile`, `Dockerfile.sandbox`, and `Dockerfile.performer.sandbox`.
- **Remaining Unfixed Upstream OS CVEs (`13` unique HIGH, `0` CRITICAL, `0` fixable):**
  - `CVE-2025-69720` (`ncurses` / `libncursesw6` / `libtinfo6`)
  - `CVE-2026-107494`, `CVE-2026-124709` (`openssl` / `libssl3t64`)
  - `CVE-2026-119373`, `CVE-2026-119374`, `CVE-2026-119379` (`libsqlite3-0`)
  - `CVE-2026-16742` (`libsystemd0` / `libudev1`)
  - `CVE-2026-54369` (`libacl1`)
  - `CVE-2026-76642`, `CVE-2026-78408`, `CVE-2026-78409`, `CVE-2026-78410` (`util-linux` / `mount` / `login` / `bsdutils` / `libblkid1` / `libmount1` / `libuuid1`)
  - `CVE-2026-9538` (`perl-base`)
- **Judgment & Next Action:**
  - `clsx@2.1.1` is a 239-byte feature-complete utility; leaving it locked in `package-lock.json` poses lower churn risk than replacing it, while `katex` and `three` remain pinned with integrity hashes in `package-lock.json`.
  - Direct npm packages execute `0` install scripts; however, transitive packages (`workerd` / `esbuild` / `sharp` if present) require postinstall binaries, so `npm ci --ignore-scripts` is not viable without disabling Cloudflare Workers runtime downloads.
  - Because `test_deployment_security.py` intentionally forbids `--ignore-unfixed`, `Container CVE Gate` remains fail-closed until Debian Trixie publishes fixes for the 13 upstream OS CVEs or an operator approves migrating base images (requiring `/usr/bin/timeout` and `manylinux` glibc compatibility for `jaxlib` in `Dockerfile.performer.sandbox`).

---

## 5. Threat Model P1 Adversarial Verification

All four P1 abuse classes are now covered by automated regression tests in [`services/graph-api/tests/test_threat_model_p1.py`](../services/graph-api/tests/test_threat_model_p1.py) and [`tests/quick-symbolic-route.test.ts`](../tests/quick-symbolic-route.test.ts):

1. **Prompt Injection (Paper HTML & `.tex`):** Injected override strings inside HTML prose and `.tex` comments do not alter `source_sha256` or extracted formulas; compromised model outputs attempting to inject `review_state="accepted"`, `approved=True`, `fitness`, unauthorized parent/source spans, HTML markup, or duplicate JSON keys are rejected fail-closed by `validate_model_proposal`.
2. **Artifact Poisoning:** `_download_verified` / `resolve_performer_artifact` rejects 1-byte SHA-256 tamper, HTTP `302` redirects, and non-identity `content-encoding`.
3. **Evaluator & Sandbox Tampering:** All five sandbox output validators (`validate_worker_result`, `validate_numerical_worker_result`, `validate_experiment_worker_result`, `validate_performer_worker_result`, `validate_research_case_worker_result`) reject forged `passed_suite`, `NaN`/non-rational witnesses, and extra fields; `validate_live_profile` aborts on `EXECUTION_PROFILE_DRIFT`.
4. **Sharp-Edge Boundary Abuse:** Verified duplicate `Authorization` header rejection on `require_worker`, queue-secret collision & whitespace rejection, `RequestBodyLimitMiddleware` invalid timeout rejection, `SearchCursorCodec` `bool`-as-`int` rejection, and L2/L3 `fgl1.` + TeX/formula log redaction.
