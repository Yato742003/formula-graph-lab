# FGL-601 — authentication và authorization

Ngày: 2026-10-01. Trạng thái: đã triển khai và kiểm chứng local; **chưa đạt
production release gate** cho đến khi xác minh cổng danh tính Sites/origin thật.
Không push, deploy, đổi secret đang dùng hoặc gọi OpenAI API trong công việc này.

## Phase 5 đã chốt

| Commit | Nội dung |
| --- | --- |
| `2ad1cfd` | Receipt → admission, evolution, khóa holdout |
| `0a79d05` | Acceptance/replay và retrieval A/B |
| `49b018c` | UI workspace và các thao tác nghiên cứu |
| `194018d` | Completion handoff, giới hạn của synthetic pilot |

FGL-601 đã được chốt tại `88277cd`. Bản review ngày 2026-10-01 tiếp tục kiểm tra
các commit FGL-602/603/604 và các thay đổi UI local; xem
`reports/SPRINT6_REVIEW_2026-10-01.md` để biết các lỗi đã sửa và gate còn mở.
Các số test bên dưới là kết quả tại thời điểm bàn giao FGL-601, không phải số
test của working tree mới nhất.

## Những gì thay đổi

- Giữ đăng nhập/đăng xuất ChatGPT/Sites. Không thêm password database, OAuth
  server, dependency, database hoặc hệ thống session mới.
- Production build không nhận danh tính từ header nếu chưa bật
  `FGL_TRUST_SITES_IDENTITY=true`. Chỉ bật sau khi xác minh trusted Sites ingress.
  `APP_ENV=development` không thể bật local identity trong production build;
  build production cũng luôn yêu cầu Graph API dùng HTTPS.
- Danh tính được giới hạn độ dài/ký tự trước khi tạo workspace. Browser không
  chọn actor/role/workspace. D1 xác minh ownership; server web ký context đã xác
  thực. Graph API kiểm tra lại workspace cá nhân bằng cùng SHA-256 user ID.
- Import, search, snapshot, contract review và mọi research gateway request đều
  dùng token HMAC-SHA256 ngắn hạn, một lần/request. Shared secret không còn được
  gửi dưới dạng bearer token. Không có legacy authentication mode trong app.
- Token kiểm tra issuer, audience, protocol version, actor, role, workspace,
  method, raw path/query, SHA-256 của đúng body UTF-8, idempotency key, thời gian
  và UUID nonce. Thay header/nội dung/đường dẫn hoặc dùng lại token đều bị từ chối.
- Nonce được consume nguyên tử trong Neo4j, có unique constraint và expiry index.
  Nhiều API replica chia sẻ cùng ledger. DB lỗi/thiếu ledger trả 503, không bỏ
  qua xác thực. Native FastAPI dependency cache tránh consume hai lần trong
  cùng request. Request mới phải có nonce mới, dù business idempotency key giữ
  nguyên. Nonce hết hạn giữ thêm 5 phút; mỗi request dọn tối đa 100 nonce cũ.
- Các API cũ dùng fixture trusted context **chỉ trong tests/conftest.py**, có
  opt-in tường minh. Những test này kiểm tra business/trust semantics, không
  chứng minh signed authentication; bộ `test_service_auth.py` và integration
  mới không dùng adapter hay override xác thực.

## Quyền tối thiểu

| Service role | Quyền request |
| --- | --- |
| `graph_read` | POST search, snapshot, extraction preview, formula parse/compare |
| `graph_write` | POST imports, contract reviews |
| `research_read` | GET research history, graph traversal, reports/bundles trong workspace |
| `research_write` | POST research commands/review trong workspace |

Mỗi token còn bị khóa vào đúng method/target/body, không dùng lại cho một endpoint
khác. Frontend chỉ gán human role `researcher`. Header browser tự nhận `admin`,
worker/proposer, workspace khác hoặc reviewer ID không được dùng làm danh tính.

Worker credentials vẫn do server quản lý, riêng với gateway key và queue key,
có role + danh sách workspace + scope cố định. Gateway token không dùng để gọi
worker-only proposal/check endpoints; worker token không được duyệt thay người.
Queue envelope, candidate/result/artifact references và replay vẫn dùng các
kiểm tra scope/hash/provenance đã có; không mở endpoint tải artifact tự do.

Giới hạn có chủ ý: một personal workspace/người. Đây chưa phải Team membership
hay phân quyền organization/admin. Bổ sung grant/membership do server quản lý
khi triển khai Team, không lấy permission từ payload/model.

## Protocol và cấu hình

Định dạng cố định, **không phải JWT/session token**:

```text
Bearer fgl1.<base64url(JSON claims)>.<base64url(HMAC-SHA256)>
MAC input = UTF-8("fgl-service.v1\0" + encoded claims)
issuer=fgl-web, audience=fgl-graph, TTL <= 60 seconds
```

Không có tùy chọn algorithm do caller chọn. JSON duplicate/unknown claims,
encoding không canonical và kiểu dữ liệu không đúng bị reject. Sai chữ ký/time/
binding/replay trả 401; sai workspace/human role/service scope trả 403; cấu hình
hoặc replay store chưa sẵn sàng trả 503. `/health` vẫn public.

1. Sinh secret ngẫu nhiên riêng cho gateway, ví dụ 32 random bytes dưới dạng 64
   ký tự hex. Đặt cùng giá trị cho `GRAPH_API_SERVICE_TOKEN` ở web và
   `SERVICE_TOKEN` ở Graph API. Không commit, đưa vào browser hay chia sẻ với model.
   Cả hai chấp nhận 32–256 ký tự printable ASCII không chứa khoảng trắng.
2. Production dùng `APP_ENV=production`, Graph API URL HTTPS và mạng/ingress đã
   bảo vệ. Web và API cần nâng cấp cùng đợt: client cũ gửi raw bearer bị reject.
3. Restart Graph API để lifespan tạo constraint/index mới trong Neo4j hiện có.
   Không sửa source occurrence hay các receipt đã lưu.
4. Đồng bộ clock giữa web/API/replicas. Issued-at tương lai chỉ được lệch tối đa
   5 giây; không cho phép token hết hạn tiếp tục chạy.
5. Giữ worker/queue/cursor secrets riêng. Key rotation cần phối hợp hai service;
   chưa có key ring/multi-issuer tự động vì hiện chỉ có một cặp trusted services.

HMAC nghĩa là web và Graph API cùng nắm signing key, phải cùng thuộc trusted
boundary; Graph API có thể tạo chữ ký nếu bị compromise. Chưa triển khai
asymmetric issuer vì không có yêu cầu nhiều issuer độc lập. Đặc tính shared-MAC
này được lưu ý trong [OWASP REST Security](https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html).
Workspace/role được kiểm tra trên từng operation, theo nguyên tắc deny-by-default
của [OWASP Authorization](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html).
Đây không phải tuyên bố chứng nhận ISO 27001 hoặc hoàn tất FGL-602/603.

## Kiểm chứng và cách chạy

Kết quả cuối: 657 backend offline checks và 174 frontend tests; Ruff, lint,
TypeScript và production build đạt. Neo4j integration đạt 31 tests, gồm hai test
auth mới. Build vẫn có cảnh báo chunk >500 kB/Node deprecation; dependencies
Graphiti/Starlette vẫn có deprecation warnings. Không xem warnings này là bằng
chứng production readiness.

Các checks dùng fixture HTML, synthetic CPU pilot và database test tách biệt;
không gửi nguồn/prompt sang provider, không cần API key hay phát sinh model cost.

```powershell
# Repo root: Ruff + backend offline + frontend lint/TS/tests
pwsh -NoProfile -File verify-research.ps1

# Neo4j thật, Compose project ngẫu nhiên và tmpfs; không đụng DB đang dùng
pwsh -NoProfile -File test-integration.ps1

# Chỉ chạy auth integration nếu cần vòng kiểm tra ngắn
pwsh -NoProfile -File test-integration.ps1 -TestPaths tests/test_service_auth_integration.py

npm run build
```

Bộ mới có golden vector dùng chung giữa WebCrypto và Python; anonymous denial,
missing config, expired/future token, replay, role/header/body/query tampering,
cross-workspace import/read, worker/gateway separation, request-scoped cache,
ownership BFF và local login/logout/forged header stripping. Integration xác
minh 6 lần consume đồng thời qua hai driver chỉ nhận một lần, expiry grace,
HTTP import → search → freeze Spec, từ chối đọc Spec của người khác và replay
qua driver khác.

Installed Sites dev middleware được kiểm tra bằng HTTP loopback thật: header
giả bị xóa, login local chỉ tạo `local_seedy`, cookie HttpOnly/SameSite và
signout/cross-origin guard hoạt động. Đây **không phải** bằng chứng cổng Sites
production đã làm như vậy. Cổng web 3000 không chạy ở thời điểm probe manual;
không ghi nhận browser acceptance của bản deploy.

## Production release gate còn mở

`FGL_TRUST_SITES_IDENTITY` là xác nhận cấu hình của operator, **không phải chữ
ký bảo vệ header**. Không bật trên một standalone public Wrangler worker.
Identity header chỉ có ý nghĩa khi trusted platform gateway đã xác thực người
dùng, xóa header do client gửi và chặn truy cập đi vòng vào origin.

- [ ] Xác nhận behavior của ingress Sites thật: unauthenticated request gửi
  `oai-authenticated-user-*` giả vẫn bị từ chối; authenticated request giả ID
  người khác không đổi user thực.
- [ ] Kiểm tra direct worker/origin URL và alternate domain không bypass gateway.
- [ ] Bật trusted-ingress flag sau khi hai checks trên đạt; thử login/logout
  ChatGPT trên deployment với hai tài khoản, không chỉ localhost.
- [ ] Xác nhận secret/HTTPS/clock/Neo4j constraints bằng cấu hình deploy thật;
  test isolation bằng hai tài khoản và worker sai grant, không đọc chéo artifact.
- [ ] Chạy flow Graph → Spec → Compatibility → Proposals → Reports và export
  dưới identity thật; không bật model generation nếu chưa cấu hình cap/billing.

Nếu không xác minh được trusted Sites-only ingress, giữ flag false và không
release. Cần identity attestation/OAuth hoặc reverse proxy phù hợp môi trường
deploy; không được lấy header client làm danh tính để “cho chạy được”.
