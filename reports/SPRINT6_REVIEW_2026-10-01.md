# Review bản cập nhật Sprint 6 — 2026-10-01

Checkpoint tiếp theo sau commit/push: [FGL602_FGL603_HARDENING.md](FGL602_FGL603_HARDENING.md).
Báo cáo dưới đây giữ kết quả lịch sử của lượt review ban đầu; checkpoint mới
ghi rõ phần đã harden, kết quả scan Linux còn fail và các gate chưa hoàn tất.

## Phạm vi và kết luận

Đọc commit `88277cd` (601), `4abd6da` (602), `a0ffb1b`, `5d5235c`,
`137733c` (603), `6d4b622` (604), cùng bốn file UI chưa commit của người dùng.
Giữ layout hai cột, câu hỏi mẫu và kích thước Graph mới; sửa lỗi ở các helper
chung theo Ponytail, không thêm framework/state-management/database mới.

FGL-601 đã có commit; không cần commit lại cùng nội dung. FGL-602/603 có tiến bộ
nhưng **chưa hoàn tất production gate**. Các sửa đã chia thành commit security
`568584e`, UI `7ff847b`, dependency `772f1c6`; push được thực hiện theo yêu cầu
người dùng, không đồng nghĩa deploy hay production acceptance. Không thay `.env`,
không gọi model và không thay database thật.

## Lỗi đã sửa

| Mức | Lỗi trước review | Sửa và kiểm chứng |
| --- | --- | --- |
| P1 | Ops dùng `MATCH (j:ResearchJob)` toàn cục. Người dùng A có thể xem số job và làm job hết hạn của B chuyển sang failed. | Mọi method metrics/recovery bắt buộc nhận workspace từ identity đã xác thực; query có workspace filter. HTTP auth regression và Neo4j integration hai workspace. Recovery vẫn giữ reservation, không hoàn tiền quota, không chạy lại job. |
| P1 | Body cap nằm trong auth dependency, sau khi FastAPI đã đọc/parse JSON. Worker-only route không đi qua guard này. | ASGI body guard chạy trước parser, kiểm tra declared length và từng chunk, bao phủ mọi HTTP route; import JSON giới hạn 4 KiB vì chỉ chứa URL. Test chunk vượt cap dừng đọc trước endpoint ở search/import/worker. HTML tải về vẫn dùng fetcher cap 5 MiB. |
| P1 | Audit ghi `str(ValidationError)` và header actor chưa xác minh: có thể chứa prompt/secret và gây nhầm người thực hiện. | Chỉ ghi loại lỗi, không dùng header chưa xác thực làm actor. Test sentinel trong claim/header không xuất hiện trong audit. |
| P1 | Log filter chỉ xử lý lớp ngoài; tuple chứa dict, nested extras và traceback có thể lộ nguồn/secret. Filter gắn root logger không tự xử lý handler riêng của logger con; cài nhiều lần thêm filter. | Redact đệ quy args/extras, loại traceback chứa giá trị, lọc HTML fragment và nội dung theo key nhạy cảm; gắn filter lên handler hiện có, idempotent. Test handler riêng, nested source/prompt/token và exception. Audit được sanitize trước serialize, không phụ thuộc riêng filter của sink. |
| P2 | Imports/search/contract review chỉ chặn `Sec-Fetch-Site: cross-site`; không đối chiếu Origin và sibling same-site origin. | Dùng một helper same-origin cho cả bốn BFF; kiểm tra Origin, fallback Referer, Fetch Metadata. JSON-only và không cấp cross-origin CORS vẫn giữ nguyên. Test foreign/sibling/null origin và same-origin hợp lệ. |
| P2 | Queue không có DB trả zero/“recovery thành công”; dashboard xanh khi request lỗi hoặc thiếu telemetry, hiển thị HTML cap 10 MiB không đúng fetcher. | Recovery unavailable trả 503; metrics unavailable hiển thị degraded/unknown, số liệu không bị coi là healthy. HTML cap 5 MiB đúng; cấu hình checker/import không mang nhãn đã chạy kiểm thử. Validate kiểu telemetry tránh render crash; không cache kết quả Ops. |
| P2 | Card compact bị rule Equation/Paper chi tiết override width, không khớp layout; test vẫn đòi 280px dù card mới 320px. | Detailed rule loại `.is-compact`; compact 220px, detailed 320px. Test CSS và viewport cập nhật theo kích thước mới. |
| P2 | Empty state Compatibility luôn đề xuất cặp công thức của một paper hard-code dù chưa import/chưa review. Preset chỉ điền task/family nhưng gọi “verified benchmark”; sidebar luôn ghi 13 sections configured kể cả JSON hỏng. | Bỏ cặp hard-code; giữ câu hỏi mẫu dưới nhãn draft; đếm JSON đọc được, không gọi là đã xác minh. “Spec Target/Check Port” đổi thành “Open Spec/Open Compatibility” vì chỉ mở màn hình, không bind hay kiểm chứng công thức. |
| P1/P2 | Audit dependency Node và Python phát hiện phiên bản có advisory. | Nâng bộ Cloudflare Vite/Wrangler/workers types tương thích; cập nhật transitive patch trong npm lock. Python pytest 9.0.3, urllib3 2.8.0; cập nhật uv lock và pip của môi trường local. Không dùng `audit fix --force`. |

Audit log hiện là **telemetry**, không phải append-only immutable store. Việc sửa
docstring không làm hoàn tất yêu cầu immutable audit của FGL-602. Filter không
thể nhận biết mọi prompt tự do nằm trong một chuỗi bất kỳ: không log raw source,
provider payload hay model output; cần kiểm tra log sink/SDK ở deployment thật.

## Kiểm chứng

Kết quả tại working tree sau sửa (cập nhật sau lượt chạy cuối):

- Backend offline: 677 tests đạt; Ruff đạt.
- Frontend: 182 tests/25 files đạt; lint và TypeScript đạt.
- Neo4j integration: 32 tests đạt sau nâng cấp pytest, gồm regression mới
  cho Ops isolation/recovery; runner đã cleanup project test riêng.
- Production build sau dependency upgrade: đạt. Vẫn có cảnh báo chunk >500 kB
  và Node/dependency deprecation.
- `npm audit --omit=dev`: 0 advisory trong lockfile đã nâng cấp.
- `npm audit`: còn 4 moderate, thuộc nhánh `drizzle-kit` → esbuild-kit/esbuild.
  Không có high/critical trong lockfile hiện tại. Không tự downgrade drizzle-kit
  xuống 0.18.1 theo gợi ý audit vì làm thay đổi công cụ migration.
- `uv tool run pip-audit --path .venv/Lib/site-packages`: không có advisory
  được biết tại thời điểm scan sau nâng cấp. Đây là environment local, không
  thay thế scan image Linux/base OS của bản deploy.

Lệnh chạy từ repo root:

```powershell
pwsh -NoProfile -File verify-research.ps1
pwsh -NoProfile -File test-integration.ps1
npm run build
npm audit --omit=dev
npm audit
uv tool run pip-audit --path .venv/Lib/site-packages
```

Integration dùng Compose project ngẫu nhiên và tmpfs, không sửa DB thật.
Pip-audit chạy bằng tool riêng của uv, không thêm vào runtime app dependencies.
Các cảnh báo deprecation và chunk lớn không tự biến thành release success.
Không có kiểm chứng hình ảnh Chrome/tablet/mobile hay ingress production trong
review code này; không coi component tests là bằng chứng QA trình duyệt.

## Việc cần làm tiếp — theo thứ tự

### 1. Gate production identity FGL-601 (bắt buộc trước release)

Owner: operator + developer. Cần URL staging, URL origin/direct worker và hai
tài khoản. Giữ `FGL_TRUST_SITES_IDENTITY=false` đến khi đủ bằng chứng.

- Unauthenticated gửi header `oai-authenticated-user-*` giả phải bị từ chối.
- User A đã login gửi header giả của B vẫn phải là A.
- Direct origin/worker và alternate domain không bypass gateway.
- Hai tài khoản không đọc chéo source, spec, candidate, bundle/artifact.
- Kiểm tra login/logout, HTTPS, clock, secret độc lập và Neo4j constraints thật.
- Acceptance thật: Graph → Spec → Compatibility → Proposals → Reports → export.
- Chỉ bật model khi operator đã cấu hình provider/model, giá và cap/billing.

### 2. Hoàn tất FGL-602 còn thiếu

| Task | Deliverable tối thiểu | Acceptance test |
| --- | --- | --- |
| CSP và browser security headers | Policy phù hợp SSR/React/KaTeX/2D/3D; nonce/hash cho script nếu framework hỗ trợ. Không dùng `unsafe-inline` để đánh dấu xong. | Production build trong browser không có CSP violation phá UI; inline injection bị chặn; không frame được workspace. |
| Distributed rate enforcement | Limiter hiện là in-memory, ceiling một API process. Dùng storage/gateway có sẵn để quota dùng chung và cleanup bounded. | Hai replica cùng workspace không nhân đôi quota; restart không reset compute/spend reservations; expired workspace entries không tăng RAM vô hạn. |
| Durable append-only audit | Lưu tamper/denial/quota/review/queue transitions vào sink có retention, quyền append-only và export/verification. | Mutation hoặc xóa event bị deny/detect; rollback/retry không ghi event “thành công” giả; outage fail policy rõ; kiểm tra toàn chain. |
| Supply-chain gate | CI scan npm/uv lock và image Linux/base OS; xử lý/waiver 4 moderate drizzle tooling với owner/expiry; pin image digest của worker. | Không có high/critical chưa xử lý; không expose drizzle/dev server công khai; mỗi scan có revision + thời điểm + phạm vi. |
| Retention/backup/incident | Chính sách nguồn, receipts, log/PII, nonce, job reservation; export backup mã hóa cho D1/Neo4j và restore vào môi trường mới. Runbook rotate key/revoke worker/isolate ingress/reconcile uncertain results. | Restore drill tái tạo source/hash/review và replay bundle; không tự xóa evidence khi dọn transient data; worker bị revoke không còn chạy. |
| Threat model hoàn chỉnh | Data-flow và abuse cases cho prompt injection, model output, agency, exhaustion, artifact poisoning, evaluator tampering. | Injected HTML không thành instruction/quyền; AI không tự review/score; provenance, frozen evaluator và holdout gate vẫn fail closed. |

Nguồn tham chiếu:
[OWASP CSRF](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html),
[OWASP Logging](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html),
[OWASP CSP](https://cheatsheetseries.owasp.org/cheatsheets/Content_Security_Policy_Cheat_Sheet.html).
Đây là hardening theo yêu cầu repo; không phải chứng nhận ISO 27001.

### 3. FGL-603: dashboard chưa phải observability đầy đủ

- Correlation ID xuyên suốt import → exact/semantic graph → candidate → receipt.
- Metrics thực: import duration/yield, provider cost, graph writes, outcomes
  unknown/timeout/error, queue saturation, spend và policy decisions.
- Recovery sweep hiện chỉ đánh dấu expired job failed; thêm đường reconcile/
  dead-letter có quyền, chứng cứ và retry idempotent, không tạo winner giả.
- Resource knobs mới cần ghi execution profile thực vào receipt/request identity
  để replay không nhầm config cũ/mới. Cấu hình sandbox image không chứng minh
  image đã tồn tại/đã chạy; “configured” chỉ là cấu hình.
- Acceptance: inject provider/DB/worker failure, double delivery, controller death;
  alert được redact, không sửa source/parent/evaluator/holdout.

### 4. UI acceptance và beta (sau security gates)

- QA trình duyệt desktop/tablet/390px, keyboard/focus, hash/formula overflow,
  reduced motion và compact/detailed graph; thử request failure/empty states.
- Kiểm tra tab/nav có giữ URL context; các câu hỏi mẫu không phải supported
  benchmark/evaluator. Chỉ quảng bá mathematical family có implementation thật.
- Chuẩn bị corpus 30 paper một family, kiểm định extraction precision, cho
  5 researcher chạy flow không có developer hướng dẫn. Không dùng synthetic
  CPU pilot làm tuyên bố hiệu năng sản phẩm hoặc kết quả nghiên cứu mới.

## Handoff và commit

Các thay đổi UI đã có từ người dùng vẫn được giữ; không dùng reset/checkout.
Nên chia các sửa mới thành: (1) Ops tenant boundary/recovery; (2) body/log/origin
guards; (3) UI/status regressions; (4) dependency locks và report. Người tiếp
nhận phải review diff và chạy checks trên chính revision được commit, không
dựa vào số test lịch sử. Không push/deploy chỉ vì local checks xanh.
