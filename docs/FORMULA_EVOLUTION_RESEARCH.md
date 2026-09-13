# FormulaGraph Lab: thiết kế hệ thống nghiên cứu và tiến hóa công thức có bằng chứng

## 1. Kết luận và mục đích sản phẩm

FormulaGraph Lab nên là **workspace nghiên cứu theo bài toán**, không phải công cụ ghép LaTeX rồi để AI tự đánh giá. Người dùng xác định mục tiêu, lần theo các nhánh phương pháp trong paper HTML, chọn các thành phần có khả năng kết hợp, tạo ứng viên có cấu trúc và kiểm tra chúng bằng evaluator độc lập. Sản phẩm trả về cả kết quả thất bại, điều kiện áp dụng và đường dẫn tái lập.

Ba ranh giới bắt buộc:

- **AI proposal:** đề xuất ứng viên và lý do lựa chọn; không có quyền xác nhận sự đúng đắn, chấm điểm hoặc sửa benchmark.
- **Transformation DSL:** mô tả biến đổi hữu hạn, định kiểu và tái lập được; compiler kiểm tra quyền truy cập, ngữ nghĩa và sinh nghĩa vụ chứng minh.
- **Verification pipeline:** kiểm tra các nghĩa vụ bằng công cụ độc lập, ghi bằng chứng và phạm vi kết luận; không nhận lời tự xác nhận của AI làm kết quả.

Đồ thị giúp tìm và giải thích ý tưởng. Nó không chứng minh công thức đúng. Thực nghiệm có thể hỗ trợ một tuyên bố về hiệu năng trong điều kiện cụ thể, nhưng không thay thế chứng minh một đẳng thức trên toàn miền. Ngược lại, chứng minh đẳng thức không chứng minh mô hình học tốt hơn.

Khuyến nghị triển khai: giữ hạ tầng HTML ingestion và evidence hiện tại, làm cứng semantics của Sprint 4, rồi tách Sprint 5 thành các cổng nhỏ. Chưa có cơ sở để làm lại toàn bộ app. Giá trị sản phẩm có thể nằm ở thời gian tái lập và kiểm tra được tiết kiệm cho nhóm nghiên cứu; đây là giả thuyết sản phẩm, chưa phải kết luận đã xác thực về nhu cầu trả tiền.

Tài liệu này là thiết kế đề xuất dựa trên nguồn sơ cấp và kiểm tra code tại commit `fcf3aa4`, ngày 09-09-2026. Nó không xác nhận Sprint 5 đã được triển khai hoặc một công thức mới đã có hiệu quả thực nghiệm.

## 2. Bài toán đầu tiên phải đủ cụ thể

Chọn ca nghiên cứu đầu tiên: **attention nhân quả cho chuỗi dài, tối ưu bộ nhớ và thời gian trong giới hạn suy giảm chất lượng cho phép**. Đây là phạm vi đề xuất, cần người dùng xác nhận trước khi chi tiền compute.

Không đặt mục tiêu “tìm công thức AI tốt hơn”. Một `ProblemSpec` phải chỉ rõ:

| Thành phần | Nội dung phải đóng băng trước vòng tìm kiếm |
|---|---|
| Nhiệm vụ | Ví dụ mô hình hóa chuỗi tự hồi quy; cấu hình mô hình và tokenizer cụ thể |
| Họ phương pháp | Causal attention có kernel hữu hạn chiều; các phép thay thế được phép |
| Chất lượng | Validation loss hoặc metric tác vụ; chiều tốt hơn, dung sai suy giảm được duyệt |
| Chi phí | Peak memory, latency hoặc throughput; đơn vị, batch, chiều dài chuỗi, dtype |
| Điều kiện | Dataset/split/version/hash, phần cứng, backend, ngân sách huấn luyện |
| Baseline | Attention chuẩn bằng implementation tối ưu và từng phương pháp cha |
| Ràng buộc | Không nhìn tương lai; đầu ra đúng shape; hữu hạn; có quy trình tái lập |
| Ngân sách search | Số ứng viên, GPU-hour, chi phí model, timeout, chính sách dừng |

Giá trị nghiên cứu có thể là tìm ra một miền mà ứng viên có trade-off tốt hơn, hoặc chứng minh một hướng không hiệu quả. Không buộc hệ thống phải sinh “phát minh”. Không có evaluator khả thi thì chỉ mở chế độ khám phá giả thuyết, không mở chế độ evolution tự động.

FunSearch cho thấy mô hình đề xuất có thể được đặt trong vòng tìm kiếm với chương trình khởi đầu và bộ đánh giá do người dùng định nghĩa. AlphaEvolve cũng kết hợp sinh chương trình, đánh giá tự động và lựa chọn tiến hóa. Bài học áp dụng ở đây là **định nghĩa bài toán và evaluator trước khi tăng khả năng sinh**, không phải giả định thành công của hai hệ thống sẽ tự chuyển sang mọi công thức trong paper. [FunSearch — Fawzi & Romera Paredes, 2023](https://deepmind.google/blog/funsearch-making-new-discoveries-in-mathematical-sciences-using-large-language-models/); [AlphaEvolve — Google DeepMind, 2025](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/).

## 3. Không có thực nghiệm thì tin được điều gì?

Không dùng một nhãn `verified` chung hoặc một tỷ lệ “độ tin cậy 95%”. Mỗi nhận định phải là một `Claim` có phạm vi và bằng chứng riêng.

| Loại nhận định | Bằng chứng cần có | Không được suy ra |
|---|---|---|
| Paper chứa công thức này | HTML đã pin, source span, kiểm tra trích xuất | Công thức đúng hoặc paper đáng tin tuyệt đối |
| Công thức có nghĩa | Kiểu, shape, miền, phạm vi biến và nghĩa toán tử đầy đủ | Tương đương công thức gốc |
| Hai biểu thức tương đương | Biến đổi hợp lệ dưới giả thiết đã nêu, hoặc proof certificate được kiểm tra | Chất lượng ML tốt hơn |
| Công thức xấp xỉ một công thức khác | Sai số, miền, phân phối, giới hạn hoặc xác suất được định nghĩa | Đẳng thức chính xác |
| Chạy ổn trên bộ test | Test inputs, seeds, dtype, tolerance, counterexample search | Không bao giờ lỗi ở đầu vào khác |
| Tốt hơn baseline | Thực nghiệm đối chứng tái lập, độ bất định, cùng điều kiện | Tốt hơn mọi bài toán, phần cứng và ngân sách |
| Có tính mới | Tìm kiếm prior art có phạm vi và chuyên gia đánh giá | Graph chưa có node giống nên chắc chắn mới |

Ví dụ: `x/x = 1` đúng khi `x != 0`, không phải hai hàm có cùng miền xác định nếu biểu thức đầu cho phép xét tại 0. `sqrt(x^2) = x` cần điều kiện phù hợp; trên số thực tổng quát vế trái là `abs(x)`. SymPy cũng làm rõ ảnh hưởng của assumptions và trạng thái chưa biết trong suy luận; compiler không được chuyển “chưa biết” thành đúng. [SymPy — Assumptions](https://docs.sympy.org/latest/guides/assumptions.html).

Hai tuyến nghiên cứu hợp lệ:

1. **Tuyến toán học:** nêu mệnh đề và giả thiết, tìm chứng minh hoặc phản ví dụ. Không nhất thiết cần huấn luyện model. Kết quả CAS phải mang danh tính checker và assumptions; không gọi là chứng minh hình thức nếu chưa có certificate được kernel độc lập kiểm tra.
2. **Tuyến ứng dụng AI:** ngoài tính hợp lệ toán học còn cần thực nghiệm học và đo hiệu năng. Một identity đẹp hoặc độ phức tạp tiệm cận thấp không đủ để kết luận hữu ích.

## 4. Đồ thị lịch sử: không phải một cây duy nhất

Một paper có thể kế thừa nhiều phương pháp, sử dụng một định lý cũ, thay một toán tử và tối ưu implementation. Vì vậy dữ liệu gốc phải là **đồ thị nhiều loại quan hệ**; tree chỉ là một phép chiếu để đọc một nhánh.

### 4.1 Các lớp node và cạnh

| Lớp | Node chính | Cạnh và ý nghĩa |
|---|---|---|
| Thư mục | `Paper`, `PaperVersion` | `CITES`, `REVISION_OF`; bằng chứng lịch sử xuất bản |
| Nguồn | `SourceSpan`, `EquationOccurrence` | `EXTRACTED_FROM`, `OCCURS_IN`; trỏ HTML cụ thể |
| Toán học | `FormulaIR`, `SymbolContract`, `Assumption`, `Claim` | `DERIVED_FROM`, `EQUIVALENT_UNDER`, `APPROXIMATES_UNDER` |
| Mục tiêu | `ResearchProblem`, `Method` | `ADDRESSES`, `SPECIALIZES`, `SUPPORTS_REQUIREMENT` |
| Implementation | `ImplementationVersion` | `IMPLEMENTS`, `OPTIMIZES_IMPLEMENTATION_OF` |
| Tìm kiếm | `Candidate`, `TransformationActivity` | nhiều `USES_PARENT`, một hoặc nhiều `GENERATES` |
| Thực nghiệm | `ExperimentSpec`, `Run`, `Result`, `Counterexample` | `EVALUATES`, `SUPPORTS_CLAIM`, `REFUTES_CLAIM` |

Quan hệ trích dẫn hướng từ paper mới đến tài liệu được trích. Khi UI hiển thị dòng phát triển từ cũ tới mới, phải ghi rõ đây là hướng hiển thị đảo lại. Quan hệ suy dẫn toán học chỉ được tạo khi có đoạn nguồn hoặc hoạt động biến đổi chứng minh điều đó. “Cùng chủ đề” và “cùng công thức” không chứng minh tác giả này kế thừa tác giả kia.

Mỗi assertion trên cạnh có `asserted_by`, `source_span_ids`, `assessment`, `assumptions`, `review_record_id`, `created_at` và version. Giá trị assessment gồm `author_reported`, `machine_inferred`, `human_reviewed`, `checker_supported`; tuyệt đối không gộp chúng thành một boolean. Cạnh suy diễn tự động ban đầu dùng nét đứt.

Mô hình Entity–Activity–Agent của PROV-O phù hợp để lưu ai tạo ra dữ liệu nào, từ nguồn nào, qua hoạt động nào. Quan hệ `wasDerivedFrom` của chuẩn là provenance tổng quát, không tự chứa bằng chứng toán học; cần chuyên biệt hóa bằng semantics riêng của FormulaGraph. [W3C — PROV-O](https://www.w3.org/TR/prov-o/).

### 4.2 Tìm tổ tiên và tìm nhánh bổ trợ

Luồng truy hồi đề xuất:

1. Chuẩn hóa DOI/arXiv ID, tách phiên bản và pin HTML content hash.
2. Lấy bibliography từ HTML; metadata API hỗ trợ đối chiếu identity và tìm citing papers.
3. Từ một `ResearchProblem`, lọc các paper có bằng chứng giải quyết mục tiêu tương ứng.
4. Mở rộng tổ tiên theo giới hạn độ sâu/số node/chi phí do người dùng chọn; hiển thị coverage và các khoảng trống.
5. Trích equation cùng phần định nghĩa ký hiệu, giả thiết, đoạn trước/sau và theorem context. Không chỉ trích riêng LaTeX.
6. Tạo cạnh lịch sử từ nguồn; tạo cạnh tương thích toán học qua checker. Hai loại cạnh không thay thế nhau.
7. Tìm nhánh bổ trợ qua yêu cầu/khả năng: ví dụ một phương pháp cần positive kernel, một nhánh cung cấp feature map phù hợp. Embedding chỉ tìm ứng viên, không xác nhận tương thích.

OpenAlex có thể hỗ trợ dựng citation graph, nhưng bản thân tài liệu của họ ghi nhận việc ghép citations có thể thiếu so với bibliography. Do đó không gọi node sớm nhất tìm được là “paper gốc của toàn bộ hướng nghiên cứu”. Nhãn đúng là “tổ tiên sớm nhất trong corpus hiện có”. [OpenAlex — Citations](https://help.openalex.org/data/works/citations/).

Tôn trọng HTML-only: paper không có bản HTML đọc được trở thành node metadata với `source_status=unavailable_html`. Không âm thầm chuyển PDF, không nhờ AI nhớ và điền công thức vào nguồn thiếu. Người dùng có thể cung cấp HTML hợp lệ hoặc ghi chú do họ nhập, nhưng ghi chú là nguồn riêng.

Lưu riêng ngày công bố đầu tiên, ngày phiên bản, ngày hội nghị/tạp chí và ngày hệ thống thu nhận. Không lấy ngày sửa arXiv làm ngày phát minh. Paper mới không tự làm công thức cũ hết đúng; bản sửa, rút bài, giả thiết bị phản bác và claim hết hiệu lực là các sự kiện riêng.

### 4.3 Graphiti nằm ở đâu?

Giữ Graphiti cho ngữ cảnh, provenance của quan sát và retrieval quan hệ qua thời gian. README mô tả temporal knowledge graph và hybrid retrieval; đó không phải hợp đồng của một mathematical verifier. Phần định kiểu, proof obligation và verdict phải là dữ liệu ứng dụng có version và kiểm soát ghi riêng. Kiểm tra tương thích với phiên bản dependency đang pin trước khi dùng API từ README hiện hành. [Graphiti — repository chính thức](https://github.com/getzep/graphiti).

Không cần thêm một database mới chỉ vì thiết kế này. Có thể giữ storage hiện tại và bổ sung các record ứng dụng với UUID, hash, constraints và service ownership. Source evidence bất biến; derived analysis có version; graph view có thể dựng lại từ các record đó.

## 5. Ranh giới thực thi và quyền sở hữu

```text
Paper HTML → immutable source snapshot → extraction + contract review
                                            ↓
ProblemSpec + approved graph snapshot → AI Proposal (untrusted)
                                            ↓
                                  DSL compiler / policy gate
                                            ↓
                               Candidate IR + proof obligations
                                            ↓
                          isolated verification / experiment workers
                                            ↓
                       immutable results + scoped claims + counterexamples
                                            ↓
                              evolution controller → parent pool
```

| Thành phần | Được đọc | Được ghi | Không được làm |
|---|---|---|---|
| Ingestion | URL được phép, metadata | Snapshot và extraction record | Tin chỉ dẫn trong HTML; tự xác nhận theorem |
| AI proposer | ProblemSpec, catalogue, evidence được cấp, phản hồi search | Proposal theo schema | Ghi verdict, benchmark, ownership, secret hoặc executable tùy ý |
| Compiler | Proposal, immutable snapshots, registry | Candidate IR, obligations, compile diagnostics | Hỏi AI xem phép biến đổi có đúng rồi lấy đó làm verdict |
| Verifier | Candidate và evaluator đã pin | CheckResult, witness, metrics | Thay ProblemSpec, mutate parent, làm theo lời nhắc trong paper |
| Evolution controller | Kết quả search hợp lệ và ngân sách | Lịch chọn cha, generation, stop events | Tự nới chất lượng tối thiểu hoặc cho AI xem holdout |
| Người dùng/reviewer | Nguồn, contracts, báo cáo | Review record, phê duyệt spec/version mới | Ghi đè lịch sử kết quả đã có |

Thiết kế coi model là bên gửi yêu cầu không đáng tin, dù model do chính hệ thống gọi. JSON hợp lệ mới chỉ qua cổng cú pháp. Cần kiểm tra ID thuộc workspace, snapshot tồn tại, operator được phép, ngân sách và toàn bộ kiểu/tham chiếu. Workspace lấy từ danh tính đã xác thực, không từ output model.

HTML paper có thể chứa indirect prompt injection. Tách nội dung nguồn khỏi chỉ dẫn hệ thống, giới hạn quyền và kiểm tra output bằng code là những lớp phòng thủ; prompt hoặc RAG không đủ bảo vệ ranh giới. [OWASP — LLM01:2025 Prompt Injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/).

Verifier chạy tiến trình/container cô lập: không mạng, không secret, filesystem input chỉ đọc, scratch giới hạn, giới hạn CPU/RAM/process/output/wall-clock; hủy cả process tree khi timeout. Giới hạn AST node không thay thế timeout của CAS. GPU job có quota riêng, hàng đợi và watchdog. Compiler chỉ sinh từ template/operator đã kiểm duyệt; không `eval`, `exec`, shell hay import tùy ý từ chuỗi model.

Đây là thiết kế kiểm soát an toàn, không phải tuyên bố đã đạt chứng nhận ISO 27001. Triển khai cần threat model và kiểm thử thực tế riêng.

## 6. Transformation DSL: ba semantics không được trộn

### 6.1 Phân loại biến đổi

| Nhóm | Ví dụ | Nghĩa vụ và nhãn |
|---|---|---|
| Bảo toàn nghĩa | Đổi tên biến bị ràng buộc; kết hợp lại phép nhân hợp lệ | Chứng minh equivalence dưới contracts; không bảo đảm bitwise float equality |
| Xấp xỉ | Thay softmax kernel bằng random features | Phải nêu approximation target, error metric, miền và điều kiện |
| Thay giả thuyết | Đổi kernel, thêm regularizer, trộn loss, đổi update rule | Tạo mục tiêu/phương pháp mới; cần thử nghiệm, không gắn nhãn tương đương |

FGL-501 có thể giữ toàn bộ danh mục mong muốn, nhưng triển khai từng operator phải có manifest đầy đủ: `name`, `version`, matched IR type, parameter schema, preconditions, generated obligations, postconditions, semantics class, lowering template, resource estimate và tests. Operator chưa có manifest/checker phải bị từ chối, không fallback sang code model.

Pattern rewrite nên áp dụng lên typed IR qua một rewriter duy nhất, với giới hạn số bước và kiểm tra match trước mutation. Đây là hướng tham khảo từ MLIR, không phải yêu cầu đưa toàn bộ LLVM/MLIR vào MVP Python. [MLIR — PatternRewriter](https://mlir.llvm.org/docs/PatternRewriter/).

### 6.2 Typed IR tối thiểu

IR phải biểu diễn rõ `Scalar`, `Tensor`, `Index`, `Function`, reduction, contraction, elementwise operation, mask và bound variable. Mỗi symbol có ID theo scope, domain và shape constraints; tên in ra như `x` không phải identity.

Ví dụ `Q: Tensor[batch, heads, length, d]`, `K: Tensor[batch, heads, length, d]`, `V: Tensor[batch, heads, length, dv]`. Dấu nhân không tự quyết định là matmul hay elementwise. Dimension chưa biết là biến ràng buộc cần giải, không phải wildcard để coi mọi shape đều hợp lệ.

Giữ hai loại hash: syntax hash cho cây đã parse; semantic hash cho IR cùng symbol contracts, assumptions, operator versions và canonicalizer version. Không dùng commutative sort nếu chưa chứng minh toán tử/operand cho phép. Cache verification còn phải khóa evaluator, nguồn, environment, seed và tolerance.

### 6.3 Proposal mẫu và quyền sinh dữ liệu

Đây là JSON minh họa schema, không phải request có ID thật:

```json
{
  "schema_version": "proposal.v1",
  "problem_snapshot_id": "problem-attention-v1",
  "parent_candidate_ids": ["parent-kernel-a", "parent-kernel-b"],
  "transform": {
    "operator": "mix_positive_feature_maps",
    "operator_version": "1",
    "target_node_id": "attention-kernel-port",
    "parameters": {"lambda": 0.3},
    "bindings": {"left": "feature-a", "right": "feature-b"}
  },
  "evidence_span_ids": ["span-linear-attention"],
  "proposed_assumptions": ["both feature maps are strictly positive"],
  "expected_effect": "Trade off feature families while preserving finite-rank evaluation"
}
```

Model chỉ được đề xuất assumptions; compiler phải phân loại chúng thành dữ kiện có nguồn, điều kiện người dùng chấp nhận, hoặc nghĩa vụ chưa được giải. Không được chứng minh điều kiện của chính mình bằng cách thêm nó vào danh sách đã đúng.

Schema dùng `additionalProperties=false`; reject các field như `verified`, `fitness`, `approved`, `workspace_id`, `benchmark_patch`. Server tự tạo `candidate_id`, scope, content hash, compiler version, semantics class và obligations dựa trên operator manifest, không nhận các giá trị quyết định này từ AI.

## 7. Verification pipeline và kết quả có phạm vi

### 7.1 Các cổng

1. **Source gate:** resolve mọi ref, kiểm tra quyền và hash; nguồn thiếu thì lưu lỗi hoặc nguồn chưa biết, không bịa.
2. **Compile gate:** validate schema, replay transformation trên snapshot cha; parent không đổi; sinh IR và obligations.
3. **Static gate:** scope, domain, shape, masks, loại toán tử, điều kiện chia/log/sqrt; trả về unresolved nếu chưa đủ thông tin.
4. **Symbolic gate:** áp dụng rule đã kiểm tra; CAS theo assumptions và tài nguyên giới hạn. “Không rút gọn được về 0” không phải phản chứng.
5. **Numerical gate:** test biên, singularity, randomized/property tests, limiting cases, gradients; lưu input phản ví dụ đầy đủ.
6. **Empirical gate:** chạy protocol đã đóng băng; so sánh baseline; ghi uncertainty và chi phí.
7. **Review/publication gate:** người dùng xem nguồn, khác biệt, failure và phạm vi; xuất research bundle.

Các gate có thể chạy độc lập khi phù hợp nhưng policy không được thăng hạng dựa trên kết quả thiếu. Một candidate chưa được chứng minh toàn miền có thể được chạy trong sandbox với domain hạn chế đã xác nhận; UI phải nêu điều kiện và chưa được gọi nó là an toàn tổng quát.

### 7.2 Thay status ladder bằng verification vector

```json
{
  "candidate_id": "candidate-example",
  "provenance": "resolved",
  "typing": "proved_under_contracts",
  "domain": "conditional",
  "symbolic": "unknown",
  "numerical": "passed_test_suite",
  "empirical": "not_run",
  "human_review": "pending",
  "unresolved_obligations": ["denominator-positive-on-target-domain"],
  "check_result_ids": ["check-example"]
}
```

Mỗi `CheckResult` chứa claim ID, status (`supported`, `refuted`, `unknown`, `unsupported`, `timeout`, `error`), assumptions, checker/version, input hash, seed, tolerance, resource usage và artifact references. Kết quả `supported` của numerical checker chỉ hỗ trợ claim “qua suite này”, không hỗ trợ claim “đúng với mọi input”.

Chứng minh hoặc phản ví dụ phải đi cùng miền. `unsupported` khác `invalid`. `timeout` khác `false`. Human review là metadata trực giao, không phải mức toán học cao hơn mọi checker. Không tự nâng kết quả test random thành symbolic proof.

## 8. Ca nghiên cứu attention và một mashup có thể kiểm tra

### 8.1 Các nhánh làm seed

| Nhánh | Vai trò trong corpus đầu tiên | Quan hệ cần thể hiện |
|---|---|---|
| Transformer, 2017 | Scaled dot-product attention làm điểm neo được chọn | Không gọi là nguồn gốc mọi attention |
| Linear Transformers, 2020 | Dùng feature map và tính kết hợp để đánh giá kernel attention | Thay họ kernel có thể thay toán tử; không mặc nhiên bằng softmax |
| Performer, 2020/ICLR 2021 | FAVOR+ xấp xỉ softmax attention bằng random features | Nhánh approximation với điều kiện và sai số |
| FlashAttention, 2022 | Thuật toán IO-aware cho exact attention | Nhánh implementation; không phải công thức attention mới |

Nguồn: [Attention Is All You Need — Vaswani và cộng sự](https://arxiv.org/html/1706.03762v7), [Transformers are RNNs — Katharopoulos và cộng sự](https://proceedings.mlr.press/v119/katharopoulos20a.html), [Rethinking Attention with Performers — Choromanski và cộng sự](https://arxiv.org/html/2009.14794v4), [FlashAttention — Dao và cộng sự](https://arxiv.org/html/2205.14135v2).

Bảng là phân loại phương pháp cho ca nghiên cứu, không khẳng định đã kiểm tra mọi cạnh ảnh hưởng lịch sử giữa bốn paper. Pipeline phải truy tiếp bibliography và đoạn nguồn trước khi dựng cạnh chính thức. Bản HTML Transformer được pin là revision v7, không phải năm phát minh. “Exact” của FlashAttention nói về thuật toán attention, không đòi hỏi output bitwise giống mọi thứ tự tính floating point.

### 8.2 Một phép phối hợp có nghĩa toán học

Xét kernel attention:

```text
o_i = [sum_{j <= i} k(q_i, k_j) v_j] / [sum_{j <= i} k(q_i, k_j)]
k_a(q, k) = phi_a(q)^T phi_a(k)
k_b(q, k) = phi_b(q)^T phi_b(k)
k_lambda = lambda * k_a + (1 - lambda) * k_b
phi_lambda(x) = concat(sqrt(lambda) * phi_a(x),
                       sqrt(1 - lambda) * phi_b(x))
```

Với lambda là hằng số thực trong `[0,1]`, hai feature map tương thích và đầu ra thực, tích vô hướng của `phi_lambda` đúng bằng kernel trộn. Nếu maps có từng thành phần dương nghiêm ngặt và prefix có ít nhất một token, mẫu số dương. Nếu chỉ không âm, phải xử lý khả năng mẫu bằng 0. Khi rank được giữ cố định, có thể dùng prefix sums của feature/key-value để tránh dựng ma trận attention toàn phần.

Đây là **identity minh họa suy ra trực tiếp từ phép nối vector**, không phải tuyên bố mới trong khoa học. Thay kernel của một parent bằng kernel trộn là một giả thuyết phương pháp mới; chuyển từ kernel trộn sang feature concatenation là bước biểu diễn tương đương dưới điều kiện trên. Hai hoạt động phải có record khác nhau.

Điểm dễ nhầm: trộn hai đầu ra đã normalize thường không bằng normalize một kernel trộn. Ngoài ra tổng rank tăng từ `r_a` hoặc `r_b` lên `r_a+r_b`, nên cải thiện chất lượng có thể chỉ do tăng capacity. Baseline cần matched-rank, matched-budget và các endpoint lambda=0/1. Không hứa tốc độ tăng chỉ vì công thức có dạng tuyến tính theo chiều dài chuỗi.

### 8.3 Kiểm tra nhỏ đã thực hiện trong nghiên cứu

Đã chạy kiểm tra NumPy float64, seed 42, 5 token, hai feature rank 3 và 2, lambda=0.3, feature entries dương. Kết quả:

```text
max_abs_kernel_error               2.220446049250313e-16
max_abs_output_error               1.6653345369377348e-16
normalized_output_mix_difference   0.03212066426907223
```

Hai số đầu đối chiếu kernel trộn trực tiếp với feature concatenation và cách tính kết hợp. Số cuối cho thấy việc trộn output normalize khác với kernel mixing trên bộ input đó. Đây chỉ là sanity check **không mask** của identity lõi; chưa kiểm tra toàn bộ causal implementation, chưa training, chưa GPU benchmark và không phải bằng chứng về hiệu quả mô hình.

Mã tái lập của kiểm tra đã chạy:

```python
import numpy as np
rng = np.random.default_rng(42)
a = rng.random((5, 3)) + 0.1
b = rng.random((5, 2)) + 0.1
c = rng.random((5, 3)) + 0.1
d = rng.random((5, 2)) + 0.1
v = rng.normal(size=(5, 4))
lam = 0.3
q = np.concatenate((np.sqrt(lam)*a, np.sqrt(1-lam)*b), axis=1)
k = np.concatenate((np.sqrt(lam)*c, np.sqrt(1-lam)*d), axis=1)
w = lam*(a @ c.T) + (1-lam)*(b @ d.T)
direct = (w @ v) / w.sum(axis=1, keepdims=True)
assoc = (q @ (k.T @ v)) / (q @ k.sum(axis=0))[:, None]
w1, w2 = a @ c.T, b @ d.T
mixed_outputs = (lam*(w1 @ v)/w1.sum(axis=1, keepdims=True)
                 + (1-lam)*(w2 @ v)/w2.sum(axis=1, keepdims=True))
print(np.max(np.abs(q @ k.T - w)))
print(np.max(np.abs(direct - assoc)))
print(np.max(np.abs(mixed_outputs - direct)))
```

## 9. Thực nghiệm và mashup evolution

### 9.1 Protocol từ rẻ đến đắt

| Giai đoạn | Kiểm tra | Điều kiện chuyển tiếp |
|---|---|---|
| A: reference CPU | Direct vs reassociated; causal prefix; singularities; gradients; lambda endpoints | Hết lỗi correctness đã biết; unresolved được ghi rõ và policy cho phép |
| B: microbenchmark | Warmup, synchronization, latency distribution, peak memory; cùng dtype/device/shapes | Không hồi quy vượt ngân sách; so cả naive reference và fused baseline |
| C: pilot training | Cùng dataset/model/token budget; parent ablations; matched rank | Có tín hiệu khả thi, không chỉ thắng do tăng capacity hoặc compute |
| D: confirmatory run | Nhiều seeds, cấu hình đóng băng, split chưa dùng để search | Báo cáo effect size và uncertainty; lưu toàn bộ thất bại |
| E: external validation | Dataset/chiều dài/hardware ngoài miền tối ưu ban đầu | Mới được mở rộng claim tương ứng |

Test nhân quả bắt buộc: thay mọi token tương lai và kiểm tra output prefix không đổi trong tolerance. Test số học phải gồm input cực trị, norm lớn/nhỏ, mẫu gần 0, overflow, underflow và dtype hỗ trợ. Tolerance phải gắn dtype và reference; AI không được tự nới để pass.

Đề xuất khởi đầu 3–5 seeds cho pilot là lựa chọn vận hành, không bảo đảm đủ độ mạnh thống kê. Số lần chạy xác nhận phụ thuộc variance, chi phí và mức chênh lệch cần phát hiện. Không coi token trong cùng một run là các lần lặp độc lập. Nghiên cứu của Bouthillier và cộng sự chỉ ra nhiều nguồn biến thiên trong benchmark ML, củng cố yêu cầu kiểm soát sampling, initialization và tuning. [Accounting for Variance in Machine Learning Benchmarks, MLSys 2021](https://bouthilx.github.io/publication/2021-04-07-accounting-for-variance).

Evaluator phải version hóa: dataset hashes, splits, model code, training recipe, hardware/software, measured units, metric direction, tolerance, seeds, baseline artifact IDs, raw outputs và thống kê tổng hợp. Reproducibility không đồng nghĩa luôn có bitwise determinism trên mọi GPU; ghi mức tái lập và nguồn nondeterminism.

### 9.2 Evolution có kiểm soát

Chọn cha trong tập đã đạt các gate bắt buộc, ưu tiên các điểm Pareto về chất lượng–latency–memory, đồng thời duy trì đa dạng cấu trúc. Mutation/crossover chỉ trên typed ports và registry; không nối chuỗi LaTeX. Mỗi con lưu tất cả parents và transformation activity để giải thích cả nhánh nhiều cha.

Luồng một generation:

```text
select feasible diverse parents
→ propose bounded transformations
→ compile and deduplicate under semantic contracts
→ static checks → numerical checks → budgeted experiments
→ record all outcomes
→ update Pareto archive using search metrics only
→ stop if budget exhausted / plateau / user stop
```

Không gộp timeout, hạ tầng lỗi và công thức sai vào cùng điểm 0. Định nghĩa retry budget riêng; kết quả không đầy đủ không được âm thầm trở thành winner. Candidate bị bác bỏ vẫn lưu counterexample để tránh lặp lại.

Tách dữ liệu tuning/search và holdout. Khi liên tục thử ứng viên dựa trên feedback của một tập dữ liệu, có nguy cơ overfit cả quy trình nghiên cứu. Chỉ đánh giá holdout sau khi chốt finalists; không đưa chi tiết holdout vào prompt hoặc vòng selection tiếp theo. Nếu đã dùng nó để điều chỉnh, phải coi nó là dữ liệu search và thiết lập xác nhận độc lập mới. Đây là biện pháp vận hành đề xuất, không phải triển khai bảo đảm lý thuyết reusable holdout. [Dwork và cộng sự — The reusable holdout, 2015](https://arxiv.org/abs/1506.02629).

Mọi claim cuối cùng phải nêu số ứng viên đã thử, tổng chi phí search và tiêu chí chọn winner. Không chỉ công bố run tốt nhất. Paper và graph chủ yếu định hướng không gian tìm kiếm; evaluator mới quyết định bằng chứng cho mục tiêu đã chọn.

## 10. Các chặn cần xử lý trong code hiện tại

Kiểm tra này tập trung vào ranh giới toán học của Sprint 4; không phải full audit toàn bộ repository. Không sửa product code trong lượt nghiên cứu.

| Vị trí tại HEAD đã đọc | Phát hiện | Rủi ro và yêu cầu |
|---|---|---|
| `symbol_contracts.py:134` | `confirmed` được suy ra từ confidence threshold | Extraction confidence không phải sự xác nhận domain/type; cần record xác nhận độc lập |
| Suy luận mẫu số trong `symbol_contracts.py` | Với `1/(a+b)` sinh `a != 0`, `b != 0`, rồi checker không báo lỗi | `a=1,b=-1` vẫn làm mẫu bằng 0; lưu predicate trên cả denominator AST |
| `formula_ast.py:798` | Chữ thường không styled được xem là scalar để canonicalize | Nếu type chưa biết hoặc là matrix, sort tích làm sai semantics; hash phải dùng contracts |
| `formula_ast.py:984` | `simplify(a-b)==0` trả bool; domain không được trả kèm | Không phân biệt không chứng minh được với phản chứng; không được đánh mất domain holes |
| `symbol_contracts.py:355` | Dimension `?` được coi compatible | Giữ trạng thái constraint chưa giải; chưa đủ điều kiện kết luận well-typed |
| `evidence.py:193–198` | Không có lỗi và contracts được confirmed thì gắn `well_typed` | Cần kiểm tra coverage và unresolved obligations, không chỉ danh sách lỗi rỗng |

Kết quả diagnostic read-only đã chạy bằng interpreter của dự án:

```text
input: 1/(a+b)
contracts: [('a', ['!= 0'], True), ('b', ['!= 0'], True)]
domain_errors: []
sympy_equivalent(x/x, 1): True
canonical_hash(a*b) == canonical_hash(b*a): True
```

Dòng cuối là hợp lệ cho scalar giao hoán, nhưng không đủ an toàn khi hệ thống chưa chứng minh kiểu. Dòng `x/x` chỉ minh họa thiếu điều kiện `x != 0`, không phủ nhận identity trên miền hợp lệ.

Ngoài ra cần audit capture-avoiding substitution, phạm vi alpha-renaming ở bounds và hash stability khi đổi thứ tự sibling; chưa có reproduction trong nghiên cứu này nên đây là **test gaps**, không trình bày như lỗi đã xác nhận.

`evidence_store.py` có đường trả lại import đã hoàn tất. Do đó không giả định import lại sẽ cập nhật mọi analysis cũ. Tách `FormulaAnalysisVersion` khỏi source occurrence bất biến; backfill có version, idempotent và không ghi đè lịch sử. Trước migration, inventory record thực tế và kiểm tra rollback.

## 11. Skeleton kiến trúc để bàn giao triển khai

Các đường dẫn dưới đây là **module đề xuất**, chưa được tạo:

```text
services/graph-api/app/research/
  problem_spec.py          # immutable problem/evaluator snapshots
  lineage.py               # citation vs derivation assertions
  contracts.py             # typed symbols and assumption provenance
  ir.py                    # typed nodes, scoped IDs, semantic hashing
  dsl/registry.py          # versioned operator manifests
  dsl/compiler.py          # deterministic rewrite and obligations
  proposals.py             # model adapter; proposal-only permissions
  verification/results.py # scoped verdict schema
  verification/static.py  # shapes/domain/scoping
  verification/symbolic.py
  verification/runner.py  # isolated process boundary
  experiments.py          # frozen evaluator and result manifests
  evolution.py            # budget + selection + generation lineage
```

Skeleton luồng điều phối, không phải mã chạy trực tiếp:

```python
def submit_proposal(auth, raw_proposal):
    proposal = Proposal.validate_strict(raw_proposal)
    # Workspace and policy always come from authenticated server context.
    snapshot = repository.resolve_authorized_snapshot(auth, proposal)
    compiled = compiler.compile(snapshot, proposal, operator_registry)
    candidate = repository.save_immutable_candidate(auth, compiled)
    queue.enqueue_static_checks(candidate.id)  # idempotency key required
    return candidate.id

def verify_candidate(job):
    candidate, policy = repository.load_job_inputs(job)
    result = isolated_runner.check(candidate, policy.pinned_checker)
    repository.append_result(job.idempotency_key, result)
    # Gate policy consumes typed results, never model-generated verdicts.
    next_step = gate_policy.next(candidate, result, policy)
    queue.enqueue_if_allowed(next_step)
```

API đề xuất: tạo snapshot problem; gửi proposal; đọc candidate/obligations; yêu cầu verification; tạo experiment; đọc lineage. Mọi write phải kiểm tra ownership và role ở server. Endpoint ghi kết quả chỉ worker identity được gọi; API public không cho người dùng hoặc model tự POST một `passed` result.

Queue dùng idempotency key gắn candidate hash + checker/evaluator version + run seed. Chống hai worker ghi hai kết quả cho cùng attempt; retry tạo attempt riêng, không xóa lịch sử. Sau thay evaluator phải tạo experiment campaign mới; không trộn điểm với campaign cũ.

## 12. Kế hoạch theo cổng, task và test

Các gói dưới đây thay thế cách coi FGL-501–503 là ba đầu việc nhỏ. Chưa ước lượng ngày hoàn thành vì chưa đo năng lực GPU, ngân sách và độ phủ operator. Không bắt đầu automation evolution trước khi các cổng nền tảng đạt điều kiện.

| Gói / task | Deliverable | Test bắt buộc / điều kiện nghiệm thu |
|---|---|---|
| H1 — Contract trust | Tách extraction confidence, type inference, review record | Confidence 1.0 không tạo human confirmation; AI không thể set approved |
| H2 — Domain obligations | Predicate AST và trạng thái unknown/conditional | `a+b != 0`; `a=1,b=-1`; x/x; log/sqrt; giả thiết do AI thêm không tự được discharge |
| H3 — Typed canonicalization | Scoped symbol IDs, semantic hash version | Matrix AB khác BA; scalar hợp lệ vẫn dedup; alpha-renaming tránh capture; nested bounds |
| H4 — Checker outcomes | Kết quả nhiều trạng thái, timeout thực | Unknown shape không pass; CAS inconclusive không refuted; kill hung worker; unsupported giữ nguyên |
| H5 — Analysis migration | Versioned analysis tách source | Reimport không đổi raw evidence; backfill idempotent; đọc được bản cũ; rollback không mất nguồn |
| G1 — ProblemSpec | Schema, version, baseline và budget | Thiếu metric/split/evaluator không được start evolution; edit spec tạo version mới |
| G2 — Multi-paper lineage | Source-backed typed edges và coverage | Citation không thành derivation; nhiều ancestors; paper chỉ metadata; revision date không thành origin date |
| G3 — Compatibility graph | Requirements/capabilities + reviewed mappings | Embedding giống nhưng domain khác bị đánh dấu incompatible/unknown; cross-workspace refs bị chặn |
| D1 — DSL registry | Manifest và strict schema | Unknown operator/version/extra fields/oversized tree bị reject; không thực thi code strings |
| D2 — Compiler | Replayable rewrite, obligations, parent immutability | Same input/version tạo same IR; wrong node/binding/scope bị reject; hypothesis không thành equivalence |
| V1 — Static + symbolic | Independent checkers và result records | Identity đúng, domain holes, phản ví dụ, timeout; assumptions/version hiển thị đủ |
| V2 — Numerical runner | Seeded reference/property/gradient suite | Causal future perturbation; singularities; NaN/Inf; tolerance pin; phản ví dụ tái lập |
| V3 — Experiment harness | Frozen evaluator + parent/fused baselines | Cùng shapes/dtype/budget; matched-rank; warmup/sync; seed/environment manifest; no holdout access |
| P1 — AI proposal adapter | Output có cấu trúc, nguồn và retry cap | Fabricated ID, injected HTML, tự set fitness, nới benchmark bị chặn; không có model thì mock vẫn test được |
| E1 — Evolution controller | Pareto archive, diversity, budget, generations | Budget dừng đúng; dedup không trộn contracts; invalid không làm parent; lỗi hạ tầng không thành winner |
| U1 — Research UX | Traceable branch, checks, comparison, export | Mọi edge mở được nguồn/lý do; chưa test không có badge thành công; keyboard và loading/error states |
| R1 — End-to-end demo | Paper HTML → lineage → proposal → check → report | Có cả candidate bị bác bỏ và conditional candidate; tái lập từ bundle; không cần model tự chấm |

Thứ tự: H1–H5 → G1–G3 và D1–D2 → V1–V3 → P1 → E1 → R1. U1 làm xuyên suốt sau khi schema cơ bản ổn định. Có thể phát triển model adapter bằng mocks sớm, nhưng không cho chạy tự động trên ứng viên thực trước các gate.

Definition of Done cho bản đầu có mashup: một bài toán được đóng băng; một corpus HTML có provenance; ít nhất một phép biến đổi bảo toàn nghĩa và một phép tạo giả thuyết mới; có ví dụ reject/unknown/conditional/pass; thí nghiệm hoặc trạng thái chưa chạy được trình bày trung thực; mọi kết quả truy về đúng parents, versions và evaluator. Không dùng tiêu chí “AI tạo được công thức trông hợp lý”.

## 13. UX nghiên cứu và tiêu chí giá trị

Trang đầu bắt đầu bằng “Bạn muốn cải thiện điều gì?” và một ProblemSpec mẫu, không mở ngay một graph lớn. Workspace gồm danh sách câu hỏi, graph có bộ lọc quan hệ, panel nguồn/contract và panel kiểm chứng. Có chế độ timeline để xem quá trình hình thành nhánh, không chỉ lực hút node.

Một thao tác “kết hợp hai nhánh” phải mở preview: phần nào thay đổi, giả thiết nào thêm, điều kiện nào chưa biết, chi phí kiểm tra ước lượng và tiêu chí thành công. Nút hành động là “Tạo giả thuyết” rồi “Kiểm tra”, không phải “Tạo công thức đúng”.

Graph có các lớp bật/tắt: citations, mathematical lineage, compatibility, evolution và experiment evidence. Mỗi edge trả lời được “vì sao nối?”, với đoạn nguồn hoặc transformation record. Khi thiếu tổ tiên HTML, thể hiện khoảng trống thay vì tạo cây đầy đủ giả.

Candidate card tách bốn câu hỏi: lấy từ đâu; đúng dưới điều kiện nào; đã test những gì; kết quả trên bài toán nào. Màu sắc đi kèm chữ/icon để không phụ thuộc màu. Comparison view giữ cả thất bại và trade-off, export được research bundle cho AI/người khác tiếp tục.

Để đánh giá giá trị sản phẩm, pilot với nhóm người đang tái lập hoặc cải tiến attention: đo thời gian tìm nguồn/giả thiết, số lỗi phát hiện trước training, tỷ lệ run tái lập được và thời gian chuyển từ paper sang một thí nghiệm kiểm soát. Chỉ sau pilot mới quyết định đóng gói trả phí; báo cáo này không có dữ liệu khách hàng hoặc bằng chứng doanh thu.

## 14. Giới hạn và quyết định cần chốt

Nghiên cứu hỗ trợ kiến trúc phân quyền và phương pháp kiểm chứng; chưa chứng minh FormulaGraph sẽ tạo ra thuật toán mới hoặc có thị trường trả tiền. Sanity check đã chạy không đại diện cho benchmark attention. Chưa chạy full test suite, training, GPU benchmark hay security penetration test trong lượt này.

Corpus attention trong báo cáo là seed để triển khai, chưa phải tổng quan lịch sử đầy đủ. Tính mới, ảnh hưởng lịch sử và các claim ưu việt phải được đánh giá riêng. Kiểm tra code đã tìm thấy các vấn đề cụ thể ở contract/domain/status nhưng không thay thế audit toàn bộ Sprint 0–4.

Ba quyết định trước implementation: xác nhận attention là ca đầu tiên; chốt dataset/model/hardware và ngân sách; duyệt chính sách cho candidate có nghĩa vụ chưa giải. Nếu chưa có compute, làm tới static/numerical harness và ghi `empirical=not_run`, không đưa ra tuyên bố về chất lượng mô hình.

Quyết định kiến trúc đề xuất: **AI sinh giả thuyết; DSL giới hạn và tái lập biến đổi; verifier tạo bằng chứng; đồ thị lưu quan hệ và phạm vi; evolution chỉ tối ưu mục tiêu đã đóng băng.**

## Nguồn tham khảo

Các nguồn dưới đây là paper hoặc tài liệu của tác giả/tổ chức phát triển. Trang tài liệu sống được đọc theo phiên bản hiển thị khi nghiên cứu; implementation phải pin dependency riêng. Dùng HTML và trang abstract/metadata, không xử lý PDF.

1. Getzep. *Graphiti*. Repository và README hiện hành. https://github.com/getzep/graphiti
2. W3C. *PROV-O: The PROV Ontology*. https://www.w3.org/TR/prov-o/
3. LLVM/MLIR. *PatternRewriter*. Tài liệu hiện hành. https://mlir.llvm.org/docs/PatternRewriter/
4. SymPy. *Assumptions*. Tài liệu hiển thị bản 1.14.0. https://docs.sympy.org/latest/guides/assumptions.html
5. Alhussein Fawzi, Bernardino Romera Paredes. *FunSearch: Making new discoveries in mathematical sciences using Large Language Models*. Google DeepMind, 14-12-2023. https://deepmind.google/blog/funsearch-making-new-discoveries-in-mathematical-sciences-using-large-language-models/
6. Google DeepMind, AlphaEvolve team. *AlphaEvolve: A Gemini-powered coding agent for designing advanced algorithms*. 14-05-2025. https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/
7. Ashish Vaswani và cộng sự. *Attention Is All You Need*. 2017; HTML revision v7 được tham chiếu. https://arxiv.org/html/1706.03762v7
8. Angelos Katharopoulos, Apoorv Vyas, Nikolaos Pappas, François Fleuret. *Transformers are RNNs: Fast Autoregressive Transformers with Linear Attention*. ICML 2020. https://proceedings.mlr.press/v119/katharopoulos20a.html
9. Krzysztof Choromanski và cộng sự. *Rethinking Attention with Performers*. arXiv 2020; ICLR 2021; HTML v4 được tham chiếu. https://arxiv.org/html/2009.14794v4
10. Tri Dao và cộng sự. *FlashAttention: Fast and Memory-Efficient Exact Attention with IO-Awareness*. 2022; HTML v2 được tham chiếu. https://arxiv.org/html/2205.14135v2
11. OpenAlex. *Citations — Works*. Tài liệu hiện hành. https://help.openalex.org/data/works/citations/
12. Xavier Bouthillier và cộng sự. *Accounting for Variance in Machine Learning Benchmarks*. MLSys 2021, trang của tác giả. https://bouthilx.github.io/publication/2021-04-07-accounting-for-variance
13. Cynthia Dwork và cộng sự. *The reusable holdout: Preserving validity in adaptive data analysis*. 2015. https://arxiv.org/abs/1506.02629
14. OWASP Gen AI Security Project. *LLM01:2025 Prompt Injection*. https://genai.owasp.org/llmrisk/llm01-prompt-injection/
