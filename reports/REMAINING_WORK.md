# FormulaGraph Lab — Remaining Work & Release Checklist

Cập nhật: `2026-11-26` (sau khi hoàn tất & kiểm chứng toàn bộ 3 chặng **Researcher Daily-Driver v1.1.0**: Direct `.tex` Import, Batch Contract Review, Quick Symbolic Verify + 1-command sandbox setup).  
Tham chiếu chi tiết: [`FGL602_FGL603_HARDENING.md`](FGL602_FGL603_HARDENING.md), [`SPRINT6_REVIEW_2026-10-01.md`](SPRINT6_REVIEW_2026-10-01.md), [`../DESIGN.md`](../DESIGN.md), [`../docs/PHASE5_COMPLETION.md`](../docs/PHASE5_COMPLETION.md).

> **Trạng thái phát hành hiện tại:** **Chưa release-ready (Production gates vẫn mở).**  
> Không bật `FGL_TRUST_SITES_IDENTITY=true` trên origin công khai và không mở các cờ gọi AI/model khi chưa chốt đủ các cổng dưới đây.

---

## Researcher Daily-Driver v1.1.0

- Stage 1 (`6b0d79d`) bổ sung nhập tệp hoặc dán LaTeX với SHA-256 và anchor theo dòng.
- Stage 2 (`bba7432`) đưa bản nháp contract và preset vào Inspector. Mỗi quyết định của người duyệt có một bản ghi riêng.
- Stage 3 hoàn tất kiểm chứng (`verify-research.ps1`, `35` Neo4j integration, `7` sandbox + `1` clean replay, `35` Phase 5, production HTTP/CSP smoke `4173`, và QA trình duyệt thật `1440×900` trọn vòng `.tex` → Batch Review → Quick Verify `supported`/`refuted` với `0` lỗi console/HTTP). Kết quả Quick Verify luôn giữ nguyên phạm vi `ad-hoc check · not frozen campaign`.
- **Playbook 1 (SymPy Assumptions, Domain Obligation Discharge, Multi-Step Derivation Chains & Lean 4 Five-Bin Statement Audit):** Ánh xạ `ContractReview` của người dùng sang SymPy assumptions (giữ ma trận không giao hoán, không dùng `force=True`), tự động xả nghĩa vụ miền (`discharged` kèm chứng nhận truy vết `review_id` / `CONTRADICTORY_DOMAIN`), hỗ trợ chuỗi suy diễn nhiều bước $E_0 \Rightarrow E_1 \Rightarrow \dots \Rightarrow E_k$ chỉ đích danh bước gãy (`failed_step_index`), và xuất bản nháp **Lean 4 / Mathlib (`DRAFT_SORRY`)** kèm kiểm toán 5-ngăn độc lập (`DECLARATION_FIDELITY`, `DRAFT_ALLOWLIST`, `PROOF_STATUS: FROZEN_UNPROVED` — không giả mạo `PROVED` khi chưa qua kernel Lean). Kiểm chứng đầy đủ: `728` Pytest offline + `220` Vitest (`34` files) + `8` Docker sandbox + `1` clean replay + `35` Neo4j integration + `35` Phase 5 + HTTP/CSP smoke `4173` + E2E trình duyệt `1440×900` (chuỗi 2 bước `supported` + `discharged` + Lean 4 `calc` & chuỗi sai `refuted` tại bước 2, `0` console/HTTP/overlap errors).

## 1. Đã hoàn thành gần đây

- **FGL-603 — End-to-End Telemetry Correlation & Controlled Dead-Letter Recovery (mã nguồn + kiểm chứng offline hoàn tất `2026-11-25`):**
  - **Tương quan L1–L4 sau commit:** Tạo [`lib/server/bff-telemetry.ts`](../lib/server/bff-telemetry.ts) phát `x-request-id` (BFF) + `x-upstream-request-id` (từ FastAPI) cùng log JSON đã redact trên [`/api/imports`](../app/api/imports/route.ts), [`lib/server/paper-import.ts`](../lib/server/paper-import.ts) và [`/api/research/[...slug]`](../app/api/research/[...slug]/route.ts). Bổ sung hậu kiểm sau commit cho `problem_spec_frozen`, `proposal_spend_transition`, `proposal_recorded`, `admission_policy_decision`, `evolution_campaign_transition`, `research_job_transition`, `numerical_fixture_receipt`, `research_case_receipt`, và cảnh báo mức `warning`/`error` (`dead_letter_alert`, `proposal_provider_alert`, `db_unavailable_alert`) đã redact `"notes"` / `"rationale"` tại [`security.py`](../services/graph-api/app/security.py) & [`observability.py`](../services/graph-api/app/observability.py).
  - **Dead-letter recovery có kiểm soát ([`research_jobs.py`](../services/graph-api/app/research_jobs.py) & [`main.py`](../services/graph-api/app/main.py)):** Xử lý double delivery idempotent trên `finish*` (`JOB_RESULT_CONFLICT` `409` khi lệch payload), tự động quarantine khi giao trễ/hết hạn (`JOB_RESULT_REQUIRES_RECONCILIATION` `409`), lưu `prior_state` (`queued` vs `running`) trong `_recover_stuck_jobs`, bổ sung `GET /v1/research/ops/dashboard` (`dead_letters`) và `POST /v1/research/ops/jobs/{job_id}/reconcile` (`quarantine`, `authorize_retry`, `reconcile_receipt` tra cứu `CheckResult` / `ResearchNumericalFixtureResult` / `ResearchExperimentResult`). Giữ bất biến an toàn: **không** chạy lại job bất định (`prior_state != 'queued'` → `409 UNCERTAIN_JOB_CANNOT_AUTO_RETRY`), **không** hoàn `reserved_ms`/`reserve_usd`, **không** promote winner.
  - **Giao diện vận hành `/ops` ([`components/ops-dashboard.tsx`](../components/ops-dashboard.tsx)):** Bổ sung bảng **Dead-Letter & Reconciliation Ledger** (`tabular-nums`, `6px` semantic dot, rationale bắt buộc, khóa nút `Authorize Retry` với job `running`). Kiểm chứng toàn diện xanh 100%: `verify-research.ps1` (`700` Pytest offline + `208` Vitest trên `28` tệp + Ruff/Oxlint/tsc `0` lỗi), `test-integration.ps1` (`35/35` test Neo4j thật cô lập), và `npm run build` pass.
- **Gắn kết Open Design (`linear-app` + `dashboard`) & Hợp nhất khung SaaS 3 vùng (`.wb-shell`):**
  - Hợp nhất toàn bộ 5 bước (`01 Graph` → `02 Spec` → `03 Compatibility` → `04 Proposals` → `05 Reports`) cùng `/ops` và `/help` vào một bàn làm việc desktop đồng nhất: **Cột trái `252px`** (tenant workspace, ngữ cảnh `Paper`/`Spec` được ghim, chấm trạng thái nhận thức `6px`, phím tắt `Alt+1..5`) | **Thanh điều hướng sticky `42px`** + vùng chính full-height trên `/graph` | **Inspector phải `320px`** đóng/mở bằng `[` / `]`.
  - Bổ sung thanh tiến trình import 4 bước xác định (`.wb-import-stepper`) và chế độ xem thử **`Sample walkthrough`** chỉ đọc (`Read-only preview · not stored evidence`) kèm biểu đồ SVG nội tuyến trên `/reports` nhằm trị cold-start mà không giả mạo trạng thái `"Verified"`.
- **Visual Manifest Builder đồng bộ 2 chiều với JSON trong `/spec`:**
  - Người dùng có thể chỉnh trực quan `Primary metric`, `Primary guardrail`, `Pinned runtime`, `Search budget & seeds`, và các phép biến đổi DSL được phép mà vẫn đồng bộ tức thời hai chiều với 13 ô JSON bên dưới; lỗi cú pháp JSON được cảnh báo an toàn không sập UI.
- **Bảo mật & Chuỗi cung ứng (đã đẩy `origin/main` ở `92cdb00`):** Vá sạch toàn bộ `npm audit` (0 advisory), `pip-audit` (`certifi 2026.11.20`), các gói Debian đã có bản vá trên 3 Dockerfile, `FGL-603 ExecutionProfile` (`sandbox-policy.v2`), và bỏ qua `@openai/sites-vite-plugin` khi thiếu `.openai/hosting.json` trên CI.

---

## 2. Việc cần làm ngay khi quay lại chạy local

1. **Giải phóng xung đột cổng `8000` (nếu bật dev live):**
   - Kiểm tra xem có container dự án khác (ví dụ `ketoan-backend-backend-1`) chiếm `0.0.0.0:8000->8000/tcp` không trước khi chạy `.\scripts\run.ps1 -NoNgrok` để nối Neo4j thật.
2. **Bật AI proposal drafting (chỉ khi cần test AI):**
   - Các cờ `FGL_ENABLE_PROPOSALS` và `FGL_ENABLE_PROPOSAL_GENERATION` hiện giữ `false` (fail-closed). Chỉ bật khi đã cấu hình provider/model và trần chi phí (`cost cap` / `daily cap`).

---

## 3. Hạng mục kỹ thuật & Security Release Gates chưa làm xong (theo thứ tự ưu tiên)

### P0 — Bắt buộc trước khi đưa lên Staging / Production

| # | Hạng mục | Chi tiết còn thiếu |
| :-: | :--- | :--- |
| **1** | **FGL-601/602 — Staging Identity & Multi-Tenant Acceptance** | Chủ động tạm hoãn trước đó. Cần URL staging + direct origin/worker + 2 tài khoản thật để test: giả mạo header `oai-authenticated-user-*` bị chặn, User A không mạo danh User B, direct origin không bypass gateway, cô lập tuyệt đối dữ liệu giữa 2 tài khoản, kiểm tra CSP thật trên desktop/tablet/mobile. **Giữ `FGL_TRUST_SITES_IDENTITY=false` đến khi đạt.** |
| **2** | **FGL-602 — Supply-Chain & Linux Base-OS CVE Gate** | `npm audit` (0 advisory), `pip-audit` (`certifi` → `2026.11.20`, 0 advisory), và các gói Debian đã có bản vá (`apt=3.0.4`, `libapt-pkg7.0=3.0.4`, `zlib1g=1:1.3.dfsg+really1.3.1-1+deb13u1`) đã được vá sạch (0 CRITICAL trên toàn repo). 3 image (`graph-api`, `math-worker`, `performer-worker`) hiện chỉ còn **13 CVE mức High chưa có bản vá từ Debian upstream** (`CVE-2026-76642`, `78408–78410`, `54369`, `119373/74/79`, `107494`, `124709`, `16742`, `9538`, `CVE-2025-69720`) nên Trivy CI vẫn giữ fail-closed đúng hợp đồng cho đến khi Debian tung bản vá hoặc operator phê duyệt base image mới / exception có hạn dùng. |
| **3** | **FGL-602 — Durable Append-Only Audit Sink** | Log hiện tại (`fgl.telemetry` / `fgl.audit`) chỉ là telemetry cục bộ, chưa phải kho audit bất biến. Cần chọn external append-only sink có writer credential riêng, mã hóa, retention, export/xác minh toàn chuỗi, và test từ chối/phát hiện sửa-xóa + fail policy khi sink lỗi. |
| **4** | **FGL-602 — Retention Policy & Backup/Restore Drill (D1 + Neo4j)** | Chốt chính sách retention/RPO/RTO và quyền sở hữu khóa mã hóa; chạy diễn tập backup + restore vào môi trường trắng cho cả Cloudflare D1 và Neo4j, xác minh khôi phục đủ source bytes/hashes, review records, versions, job reservations và replay bundles (không xóa nhầm evidence khi dọn dữ liệu tạm). |

### P1 — Bảo mật & Kiểm chứng mở rộng

- **Ngoại lệ CSP CSS (`style-src-attr 'unsafe-inline'`):** Hiện giữ có chủ đích cho KaTeX và ReactFlow geometry ([`proxy.ts`](../proxy.ts)). Nếu mục tiêu là chính sách zero-inline-style hoàn toàn, cần đánh giá giải pháp thay thế mà không nới lỏng `script-src`.
- **Threat model hoàn chỉnh:** Rà soát và kiểm thử đầy đủ abuse cases cho prompt injection từ HTML bài báo, model output tampering, artifact poisoning và evaluator tampering.
- **Mở rộng phạm vi khoa học (vượt pilot CPU tổng hợp):** Hiện tại benchmark đăng ký (`attention-mashup-cpu-synthetic.v1`) chỉ mang phạm vi `synthetic_operator_only_no_product_claim` trên CPU. Các đánh giá dữ liệu thực, mô hình thực hoặc so sánh retrieval quy mô lớn (no-anchor gold corpus) chưa được thực hiện.

### P2 — Cải tiến trải nghiệm UX/UI tiếp theo (tùy chọn)

- **QA trình duyệt đầy đủ với dữ liệu Neo4j sống:** Chạy tay trọn luồng `Graph (import arXiv thật) → Spec (freeze) → Compatibility (map & review) → Proposals (compile) → Reports (compare + evolution + replay bundle)` trên desktop, tablet và mobile (`390px`) sau khi giải phóng cổng `8000`.
- [x] README đã cập nhật ảnh 2D, 3D, Spec, Ops và GIF sáu cảnh tại commit `4c8f278`.
