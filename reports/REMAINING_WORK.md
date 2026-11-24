# FormulaGraph Lab — Remaining Work & Release Checklist

Cập nhật: `2026-11-24` (sau đợt đại tu giao diện **Linear × Cursor 3-Zone Multi-Tenant SaaS Workbench** tích hợp `.open-design.json` và Visual Manifest Builder cho Bước 2).  
Tham chiếu chi tiết: [`FGL602_FGL603_HARDENING.md`](FGL602_FGL603_HARDENING.md), [`SPRINT6_REVIEW_2026-10-01.md`](SPRINT6_REVIEW_2026-10-01.md), [`../DESIGN.md`](../DESIGN.md), [`../docs/PHASE5_COMPLETION.md`](../docs/PHASE5_COMPLETION.md).

> **Trạng thái phát hành hiện tại:** **Chưa release-ready (Production gates vẫn mở).**  
> Không bật `FGL_TRUST_SITES_IDENTITY=true` trên origin công khai và không mở các cờ gọi AI/model khi chưa chốt đủ các cổng dưới đây.

---

## 1. Đã hoàn thành gần đây (chốt trong đợt đại tu SaaS Workbench này)

- **Gắn kết Open Design (`linear-app` + `dashboard`) & Hợp nhất khung SaaS 3 vùng (`.wb-shell`):**
  - Hợp nhất toàn bộ 5 bước (`01 Graph` → `02 Spec` → `03 Compatibility` → `04 Proposals` → `05 Reports`) cùng `/ops` và `/help` vào một bàn làm việc desktop đồng nhất: **Cột trái `252px`** (tenant workspace, ngữ cảnh `Paper`/`Spec` được ghim, chấm trạng thái nhận thức `6px`, phím tắt `Alt+1..5`) | **Thanh điều hướng sticky `42px`** + vùng chính full-height trên `/graph` | **Inspector phải `320px`** đóng/mở bằng `[` / `]`.
  - Bổ sung thanh tiến trình import 4 bước xác định (`.wb-import-stepper`) và chế độ xem thử **`Sample walkthrough`** chỉ đọc (`Read-only preview · not stored evidence`) kèm biểu đồ SVG nội tuyến trên `/reports` nhằm trị cold-start mà không giả mạo trạng thái `"Verified"`.
- **Visual Manifest Builder đồng bộ 2 chiều với JSON trong `/spec`:**
  - Người dùng có thể chỉnh trực quan `Primary metric`, `Primary guardrail`, `Pinned runtime`, `Search budget & seeds`, và các phép biến đổi DSL được phép mà vẫn đồng bộ tức thời hai chiều với 13 ô JSON bên dưới; lỗi cú pháp JSON được cảnh báo an toàn không sập UI.
- **Bảo mật & Chuỗi cung ứng (đã đẩy `origin/main` ở `92cdb00`):** Vá sạch toàn bộ `npm audit` (0 advisory), `pip-audit` (`certifi 2026.11.20`), các gói Debian đã có bản vá trên 3 Dockerfile, `FGL-603 ExecutionProfile` (`sandbox-policy.v2`), và bỏ qua `@openai/sites-vite-plugin` khi thiếu `.openai/hosting.json` trên CI.

---

## 2. Việc cần làm ngay khi quay lại chạy local

1. **Giải phóng xung đột cổng `8000` (local):**
   - Container `ketoan-backend-backend-1` (dự án khác) đang chiếm `0.0.0.0:8000->8000/tcp`, khiến Next.js BFF nhận `404`/`502` thay vì nói chuyện với FastAPI Graph API của FormulaGraph Lab.
   - Tắt hoặc đổi cổng container đó rồi chạy `.\run.ps1 -NoNgrok` để dùng dữ liệu Neo4j thật.
2. **Chạy lại kiểm thử tích hợp & sandbox thật sau đợt sửa này:**
   - `.\test-integration.ps1` (Neo4j thật cô lập + tmpfs).
   - `.\test-sandbox.ps1` và `.\test-phase5.ps1` (Docker sandbox thật + clean-container replay cho `ExecutionProfile`).
3. **Bật AI proposal drafting (chỉ khi cần test AI):**
   - Các cờ `FGL_ENABLE_PROPOSALS` và `FGL_ENABLE_PROPOSAL_GENERATION` hiện giữ `false` (fail-closed). Chỉ bật khi đã cấu hình provider/model và trần chi phí (`cost cap` / `daily cap`).

---

## 3. Hạng mục kỹ thuật & Security Release Gates chưa làm xong (theo thứ tự ưu tiên)

### P0 — Bắt buộc trước khi đưa lên Staging / Production

| # | Hạng mục | Chi tiết còn thiếu |
| :-: | :--- | :--- |
| **1** | **FGL-601/602 — Staging Identity & Multi-Tenant Acceptance** | Chủ động tạm hoãn trước đó. Cần URL staging + direct origin/worker + 2 tài khoản thật để test: giả mạo header `oai-authenticated-user-*` bị chặn, User A không mạo danh User B, direct origin không bypass gateway, cô lập tuyệt đối dữ liệu giữa 2 tài khoản, kiểm tra CSP thật trên desktop/tablet/mobile. **Giữ `FGL_TRUST_SITES_IDENTITY=false` đến khi đạt.** |
| **2** | **FGL-602 — Supply-Chain & Linux Base-OS CVE Gate** | `npm audit` (0 advisory), `pip-audit` (`certifi` → `2026.11.20`, 0 advisory), và các gói Debian đã có bản vá (`apt=3.0.4`, `libapt-pkg7.0=3.0.4`, `zlib1g=1:1.3.dfsg+really1.3.1-1+deb13u1`) đã được vá sạch (0 CRITICAL trên toàn repo). 3 image (`graph-api`, `math-worker`, `performer-worker`) hiện chỉ còn **13 CVE mức High chưa có bản vá từ Debian upstream** (`CVE-2026-76642`, `78408–78410`, `54369`, `119373/74/79`, `107494`, `124709`, `16742`, `9538`, `CVE-2025-69720`) nên Trivy CI vẫn giữ fail-closed đúng hợp đồng cho đến khi Debian tung bản vá hoặc operator phê duyệt base image mới / exception có hạn dùng. |
| **3** | **FGL-603 — End-to-End Telemetry Correlation & Dead-Letter Recovery** | Nối correlation ID xuyên suốt từ BFF → import → candidate → worker job → admission → campaign bằng ID bền vững; ghi nhận outcome số học/thực nghiệm và chuyển trạng thái queue/spend sau commit. Xây đường dead-letter reconciliation + retry idempotent có phân quyền (không chạy lại job bất định, không hoàn quota/tạo winner giả). Test double delivery, controller death, lỗi DB/provider. |
| **4** | **FGL-602 — Durable Append-Only Audit Sink** | Log hiện tại chỉ là telemetry cục bộ, chưa phải kho audit bất biến. Cần chọn external append-only sink có writer credential riêng, mã hóa, retention, export/xác minh toàn chuỗi, và test từ chối/phát hiện sửa-xóa + fail policy khi sink lỗi. |
| **5** | **FGL-602 — Retention Policy & Backup/Restore Drill (D1 + Neo4j)** | Chốt chính sách retention/RPO/RTO và quyền sở hữu khóa mã hóa; chạy diễn tập backup + restore vào môi trường trắng cho cả Cloudflare D1 và Neo4j, xác minh khôi phục đủ source bytes/hashes, review records, versions, job reservations và replay bundles (không xóa nhầm evidence khi dọn dữ liệu tạm). |

### P1 — Bảo mật & Kiểm chứng mở rộng

- **Ngoại lệ CSP CSS (`style-src-attr 'unsafe-inline'`):** Hiện giữ có chủ đích cho KaTeX và ReactFlow geometry ([`proxy.ts`](../proxy.ts)). Nếu mục tiêu là chính sách zero-inline-style hoàn toàn, cần đánh giá giải pháp thay thế mà không nới lỏng `script-src`.
- **Threat model hoàn chỉnh:** Rà soát và kiểm thử đầy đủ abuse cases cho prompt injection từ HTML bài báo, model output tampering, artifact poisoning và evaluator tampering.
- **Mở rộng phạm vi khoa học (vượt pilot CPU tổng hợp):** Hiện tại benchmark đăng ký (`attention-mashup-cpu-synthetic.v1`) chỉ mang phạm vi `synthetic_operator_only_no_product_claim` trên CPU. Các đánh giá dữ liệu thực, mô hình thực hoặc so sánh retrieval quy mô lớn (no-anchor gold corpus) chưa được thực hiện.

### P2 — Cải tiến trải nghiệm UX/UI tiếp theo (tùy chọn)

- **QA trình duyệt đầy đủ với dữ liệu Neo4j sống:** Chạy tay trọn luồng `Graph (import arXiv thật) → Spec (freeze) → Compatibility (map & review) → Proposals (compile) → Reports (compare + evolution + replay bundle)` trên desktop, tablet và mobile (`390px`) sau khi giải phóng cổng `8000`.
- **Cập nhật ảnh chụp màn hình trong `README.md`:** Hai ảnh hiện tại dưới `docs/screenshots/` là bản cũ; có thể chụp lại giao diện Linear × Cursor mới sau khi nối lại Neo4j có bài báo thật.
