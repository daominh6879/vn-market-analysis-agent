# EXPLAIN — Khái niệm & Kiến trúc

Giải thích "tại sao", kiến trúc hệ thống, bài học rút ra. Kết quả thực nghiệm → xem `NOTES.md`.

---

## RAGAS là gì?

RAGAS = Retrieval Augmented Generation Assessment. Framework đánh giá chất lượng hệ thống RAG tự động — không cần human annotator.

**Vấn đề RAGAS giải quyết:**

RAG pipeline có 2 bước có thể fail độc lập:
1. Retrieval — tìm documents liên quan
2. Generation — dùng documents đó trả lời

```
Retrieval tốt + model bịa thêm  → vô dụng
Model tốt + retrieval sai docs  → vô dụng
```

Unit test thông thường không đo được từng bước. RAGAS dùng LLM làm judge để đo từng chiều riêng.

### 4 metrics

| Metric | Đo cái gì | Câu hỏi judge |
|---|---|---|
| faithfulness | Model có bịa không? | Answer có dựa hoàn toàn vào context không? |
| answer_relevancy | Answer có đúng câu hỏi không? | Model có trả lời đúng trọng tâm không? |
| context_precision | Retrieval có xếp hạng tốt không? | Docs liên quan có đứng đầu không? |
| context_recall | Retrieval có đủ không? | Ground truth có cover trong docs tìm được không? |

**Cách faithfulness hoạt động:**
1. Tách answer thành các "claims"
2. Hỏi LLM: claim này có trong context không?
3. `faithfulness = số claims được support / tổng claims`

Vì dùng LLM để judge → không cần human annotator → tự động hóa được trong CI.

### Tại sao cần RAGAS trong khóa học này

Bài 4 thiết lập "unit test cho AI behavior":
- Baseline = "model trần" (không context) → điểm thấp → đúng
- Sau khi thêm RAG thật → điểm phải tăng
- Regression test tự động verify cải thiện, phát hiện khi chunking/embedding mới làm hỏng thứ đang chạy

---

## Kiến trúc Eval pipeline

### Hai LLM, hai vai trò

```
Câu hỏi tài chính
        │
        ▼
┌─────────────────────┐
│  LLM chính          │  Theo LLM_PROVIDER env (DeepSeek/Anthropic/OpenAI)
│  ask_baseline()     │  Trả lời câu hỏi HPG
│  ask_with_rag()     │  Trả lời với RAG context
└─────────────────────┘
        │ answer
        ▼
┌─────────────────────┐
│  RAGAS judge        │  Local Ollama hoặc cloud API
│  compute_ragas()    │  Chấm điểm answer — không trả lời câu hỏi tài chính
└─────────────────────┘
```

### Cấu trúc `golden_hpg.yaml`

25 câu hỏi về BCTC Hòa Phát (HPG), manually curated.

| Group | Câu | Cách chấm |
|---|---|---|
| `table_lookup` | 8 | RAGAS |
| `text_interpretation` | 5 | RAGAS |
| `multi_period` | 4 | RAGAS |
| `multi_source` | 3 | RAGAS |
| `no_answer` | 3 | pass/fail (model phải từ chối) |
| `out_of_scope` | 2 | pass/fail (model phải từ chối) |

### Flow tổng thể

```
golden_hpg.yaml
     │
     ├─ eval questions  ──► ask_with_rag()  ──► LLM answer + contexts
     │                                                  │
     │                                          compute_ragas()
     │                                                  │
     ├─ refusal questions ──► ask_with_rag() ──► is_refusal() check
     │
     └─ regression_check vs baseline.json (exit 1 nếu drop > ngưỡng)
```

### Ragas import fix

`ragas 0.4.x` hard-import `ChatVertexAI` từ `langchain_community` đã bị xóa. Fix: inject module thật từ `langchain-google-vertexai` vào `sys.modules` trước khi ragas load. Project không dùng VertexAI — fix chỉ để ragas import thành công.

---

## Chunking — Tại sao các chiến lược khác nhau

### fixed_512

Cắt đều 512 chars, overlap 64. Đơn giản, predictable. Phù hợp khi nội dung đồng nhất (văn xuôi thuyết minh). Vấn đề: cắt ngang giữa bảng tài chính → chunk mất ngữ cảnh header.

### structural

Cắt tại ranh giới markdown (##, ---). Chunk size ≤800 chars. Phù hợp khi document có cấu trúc rõ ràng. Với HPG PDF scan: OCR đôi khi không giữ được header → structural boundaries kém chính xác hơn mong đợi.

### hierarchical — tại sao không phải "thoả hiệp"

```
Parent (1200 chars) ──► trả về cho LLM (ngữ cảnh đủ)
    └── Child (400 chars) ──► dùng để embed + tìm kiếm (precision cao)
```

Hai mục tiêu (retrieval precision vs generation quality) được tách ra thay vì đánh đổi.
- fixed_512 embed chunk lớn → tìm kiếm kém chính xác hơn
- structural embed chunk lớn → tương tự
- hierarchical embed child nhỏ → tìm đúng → trả parent lớn → model có đủ context

Điểm yếu: index chậm nhất (388 child chunks, 871s), refusal_pass_rate 0.800 (thấp hơn fixed).

---

## Metadata prepend — khi nào có lợi

**Nguyên tắc:** Metadata chỉ có giá trị khi nó mang thông tin **phân biệt** giữa các chunk.

```
1 file  → [HPG|2025] gắn vào TẤT CẢ chunk
        = noise giống hệt nhau
        → không phân biệt được
        → chỉ làm lệch vector space
        → KHÔNG dùng

Nhiều file → chunk "Doanh thu thuần đạt 165.000 tỷ" từ HPG và VNM trông giống nhau
           → metadata [HPG|2025] giúp retrieval pull đúng nguồn
           → NÊN dùng
```

**Khi nào bật lại:** Sau Bài 12 khi index có nhiều ticker (HPG, VNM, FPT) và nhiều năm.

---

## Embedding model — tầng quyết định của RAG

### Tại sao embedding quan trọng hơn LLM chính

```
Câu hỏi → [embed] → vector → cosine search → top-k chunks → LLM → answer
                     ↑
               Nếu sai tại đây
               → kéo sai chunks
               → faithfulness cao vẫn vô nghĩa
               → LLM không bịa nhưng trả lời từ sai nội dung
```

LLM giỏi không cứu được retrieval sai. Embedding model quyết định vector space — cosine similarity giữa câu hỏi và chunk phụ thuộc hoàn toàn vào chất lượng embed.

### Tại sao bge-m3 tốt cho tiếng Việt

**nomic-embed-text:** Train chủ yếu trên tiếng Anh. Khi gặp "Phải thu ngắn hạn" vs "Phải trả ngắn hạn", vector space không phân biệt tốt → top-k kéo sai chunk.

**bge-m3 (BAAI BGE-M3):**
- Train trên 100+ ngôn ngữ, tiếng Việt được represent nhiều hơn
- Multi-granularity: đồng thời tạo dense vector + sparse vector (BM25-like) + ColBERT multi-vector
- Ollama hiện chỉ dùng dense vector — sparse + hybrid sẽ khai thác sau khi có nhiều tài liệu

**mxbai-embed-large:** Multilingual nhưng tập trung tiếng Anh + châu Âu. Tiếng Việt ít hơn bge-m3 → context_precision 0.025 (tệ hơn nomic 4 lần).

### Bài học: luôn đo, đừng assume

"Multilingual model" không đồng nghĩa tốt cho tiếng Việt tài chính. Phải benchmark thực tế trên domain cụ thể.

### context_recall là metric quan trọng nhất ở giai đoạn retrieval

Faithfulness cao chỉ có nghĩa "model không bịa từ context đang có". Nếu context_recall thấp, model trả lời đúng chỉ khi tình cờ kéo được chunk đúng. Tăng context_recall = tăng nền tảng cho mọi cải thiện tiếp theo.

### Tại sao score tuyệt đối vẫn thấp dù đã dùng bge-m3

context_recall 0.200 = chỉ 20% ground truth được cover. Lý do: phần lớn câu hỏi golden (q01–q07, q14–q17) lấy từ file XLS hợp nhất — không có trong PDF đã index. Đây là vấn đề **dữ liệu thiếu**, không phải embedding kém. Cần thêm nguồn dữ liệu (Bài 12+: two-path, SQL cho số).

---

## PDF scan — tại sao khác PDF thông thường

PDF scan = ảnh chụp tài liệu, không có text layer. Các công cụ dựa vào text layer (pdfminer, unstructured fast) trả về rỗng — không có exception, không có warning.

Chỉ các công cụ có OCR engine (pymupdf4llm + Tesseract, LlamaParse) mới đọc được.

**Rủi ro im lặng:** Pipeline nhận 0 chars mà không fail → index rỗng → retrieval không tìm được gì → không ai biết cho đến khi eval chạy và thấy context_recall = 0.

**Giải pháp:** Luôn assert `len(parsed_text) > 1000` sau parse bước. Thêm vào CI.

---

## RAGAS metrics — thứ tự ưu tiên

Từ quan trọng nhất đến ít nhất cho RAG tài chính:

| Thứ tự | Metric | Lý do |
|---|---|---|
| 1 | **faithfulness** | Số liệu tài chính sai = hậu quả nghiêm trọng. Hallucination không chấp nhận được. |
| 2 | **context_recall** | Chunk quan trọng không retrieve được → LLM không thể trả lời dù muốn. Bottleneck của toàn pipeline. |
| 3 | **context_precision** | Chunk nhiễu trong context → nhiễu loạn LLM, tăng xác suất answer sai. |
| 4 | **answer_relevancy** | Ít ưu tiên — có metric artifact với tiếng Việt (xem bên dưới). |

**Nguyên tắc thực hành:** faithfulness đạt 1.000 trước, rồi tập trung kéo context_recall. Đó là thứ tự bài 7 → bài 10-11 (reranking, hybrid search).

---

## Tại sao answer_relevancy thấp với tiếng Việt tài chính

**RAGAS answer_relevancy hoạt động như thế nào:**
1. Lấy câu trả lời của hệ thống
2. Yêu cầu LLM judge sinh N câu hỏi giả từ câu trả lời đó
3. Đo cosine similarity giữa câu hỏi sinh ra và câu hỏi gốc
4. `answer_relevancy = avg(similarity)` — thấp nếu câu trả lời "trôi xa" khỏi trọng tâm câu hỏi

**3 nguyên nhân thấp trong project này:**

1. **RAG trả lời verbose:** Structural chunk lớn → LLM đưa nhiều context vào answer ("theo thuyết minh trang 12, trong bối cảnh...") → câu hỏi sinh từ answer đó rộng hơn câu hỏi gốc → similarity thấp.

2. **Vietnamese embedding trong RAGAS:** Câu hỏi tài chính tiếng Việt cụ thể ("Tổng số nhân viên tại 31/12/2024") → DeepSeek paraphrase → bge-m3 embed → cosine với câu hỏi gốc không tuyệt đối dù nghĩa giống nhau.

3. **DeepSeek sinh câu hỏi lẫn ngôn ngữ:** Judge có thể paraphrase ra tiếng Anh → embedding vs câu hỏi tiếng Việt gốc = giảm cơ học.

**Kết luận:** answer_relevancy thấp ở đây phần lớn là artifact của RAGAS pipeline, không phải lỗi thật của hệ thống. Faithfulness = 1.000 xác nhận model không bịa — đó là bằng chứng hệ thống đang hoạt động đúng.

---

## Bài 10 — Tại sao cần xoá tài liệu & đối chiếu

### Vấn đề cốt lõi: 2 kho dữ liệu hoạt động độc lập

RAG dùng 2 kho song song:

```
Postgres (documents)          Qdrant (vectors)
─────────────────────         ─────────────────
"file X đang active"    ←→   [chunk1, chunk2, chunk3]  ← của file X
"file Y đang active"    ←→   [chunk4, chunk5]           ← của file Y
```

Khi user hỏi → RAG tìm trong **Qdrant**, không hỏi Postgres. Postgres chỉ là sổ sách quản lý. Nếu 2 kho lệch nhau mà không ai biết → hệ thống trả lời từ dữ liệu sai.

### Tại sao cần từng thành phần

**Bảng `documents` — sổ sách kiểm toán**

Không có bảng này → không biết hệ thống đang chứa file nào. Với tài chính, yêu cầu kiểm toán là: *"file X từng tồn tại, bị thu hồi lúc mấy giờ"* → phải giữ record, không xoá dòng, chỉ cập nhật `status='deleted'`.

**`rag/index.py` đăng ký vào Postgres**

Khi index file → ghi vào `documents`. Postgres mới biết "file này đang active". Không làm bước này → Postgres mãi rỗng → reconcile vô nghĩa.

**`soft_delete` — 2 bước phải đồng bộ**

Nếu chỉ xoá Qdrant mà không cập nhật Postgres → sổ sách nói "còn" nhưng không trả lời được → lệch.

Nếu chỉ cập nhật Postgres mà không xoá Qdrant → **LLM vẫn trả lời từ tài liệu đã thu hồi** → nguy hiểm với tài chính:

```
Postgres: status='deleted'    ✓ sổ sách đúng
Qdrant:   chunks vẫn còn     ✗ LLM vẫn dùng → sai
```

**`reconcile` — phát hiện lệch**

Thực tế production: worker chết giữa chừng, deploy lỗi, xoá thủ công nhầm → 2 kho lệch mà không ai biết. Reconcile so sánh:

```
orphan_in_qdrant  = Qdrant có nhưng Postgres không active → rác, xoá đi
missing_in_qdrant = Postgres active nhưng Qdrant không có → mất chunk, cần re-index
```

Không có reconcile → lệch âm thầm cho đến khi user hỏi câu hỏi sai.

### Flow hoàn chỉnh

```
index file        → Qdrant có chunks + Postgres có record (active)
soft_delete       → Postgres: deleted   + Qdrant: chunks xoá
reconcile --fix   → phát hiện & sửa lệch nếu 2 bước trên bị gián đoạn
```

---

## Bài 11 — Cửa lọc chất lượng (data/quality.py)

### Vấn đề cốt lõi: rác index thành công

File rác nguy hiểm **không phải vì bị từ chối** — mà vì được index thành công dưới dạng rác.

```
PDF scan → parse "thành công" → 50 chars
         → không có exception nào
         → lọt vào Qdrant
         → retrieval kéo ra
         → đưa vào context LLM
         → LLM trả lời từ nội dung vô nghĩa
         → không có dấu hiệu lỗi
```

So sánh với PDF bình thường:

```
PDF bình thường (200 trang) → ~500.000 chars → ~2.500 chars/trang
PDF scan (200 trang)        → ~200 chars     → ~1 char/trang
```

Check `chars_per_page < 100` là thứ duy nhất bắt được ca này — `check_char_ratio` không đủ vì ký tự OCR lỗi vẫn "printable".

---

### Giải thích từng hàm trong data/quality.py

#### `check_char_ratio(text)`

```python
readable = sum(1 for c in text if c.isprintable() and not c.isspace())
return readable / len(text)
```

Đếm tỉ lệ ký tự in được (không tính khoảng trắng). PDF scan bị OCR tệ → nhiều ký tự lạ, ký hiệu rác → ratio thấp.

**Ngưỡng `< 0.30`:** dưới 30% ký tự đọc được → chặn.

Giới hạn: file scan với Tesseract tốt có thể ra vài dòng tiếng Việt "đọc được" nhưng số lượng ít → check này bỏ sót. `chars_per_page` là check bổ sung quan trọng hơn.

---

#### `check_chars_per_page(text, num_pages)`

```python
return len(text) / max(num_pages, 1)
```

Chia tổng ký tự cho số trang. Báo cáo tài chính thật có nhiều text + bảng → thường >1.000 chars/trang. PDF scan parse ra vài dòng rác cho 200 trang → cpp < 5.

**Ngưỡng `< 100`:** check này bắt được PDF scan ngay cả khi OCR may mắn cho ra một số ký tự "hợp lệ".

---

#### `check_has_table(text)`

```python
pipe_pattern = re.compile(r"\|.+\|")
number_line = re.compile(r"\d{3,}")
```

Tìm dấu hiệu có bảng số liệu: dòng có ít nhất 2 pipe `|` (markdown table) và có số ≥3 chữ số (tỷ, triệu đồng). Không dùng để chặn — chỉ dùng như tín hiệu bổ sung hoặc để log.

---

#### `check_duplicate_ratio(chunks)`

```python
unique = len(set(chunks))
return 1 - unique / len(chunks)
```

Sau chunking, đo tỉ lệ chunk trùng nhau. >20% trùng = bất thường — có thể file bị copy-paste nhiều lần, hoặc parser lặp lại header trên từng trang.

---

#### `assess_quality(text, num_pages, chunks=None)`

Gọi các check theo thứ tự ưu tiên:

```
1. Nội dung rỗng?            → chặn ngay (ca rõ ràng nhất)
2. chars_per_page < 100?     → chặn (PDF scan — ca nguy hiểm nhất)
3. char_ratio < 0.30?        → chặn (file nhị phân hoặc OCR tệ)
4. duplicate_ratio > 0.20?   → chặn (nếu có chunks)
→ passed = True
```

Trả về `QualityResult(passed, reason, char_ratio, chars_per_page)`. Caller dùng `passed` để quyết định có index không.

---

#### `process_file(file_path)`

Entry point cho pipeline. Làm 4 việc theo thứ tự:

```
1. Tính doc_id = sha256(file bytes)[:16]  → consistent với contracts.py
2. Đếm trang bằng pymupdf
3. Parse text bằng pymupdf4llm
4. Gọi assess_quality()
   ├─ passed=True  → in "OK", trả về kết quả → caller index bình thường
   └─ passed=False → upload_to_quarantine() + log_quarantine() → KHÔNG index
```

Tách `assess_quality` ra khỏi `process_file` để unit test được logic check mà không cần file thật.

---

#### `upload_to_quarantine(file_path, doc_id)`

Upload file vào MinIO bucket `quarantine/`. Tên object = `{timestamp}_{doc_id}.pdf` để tránh trùng và tìm được theo thời gian.

Bucket tự tạo nếu chưa có (`_ensure_quarantine_bucket`). File cách ly vẫn lưu trữ được — nếu sau này muốn re-process thủ công hoặc kiểm tra lại.

---

#### `log_quarantine(...)`

Ghi 1 dòng vào bảng `quarantine_log` trong Postgres:

```
doc_id | source_path | reason | char_ratio | chars_per_page | quarantined_at
```

Postgres là nguồn sự thật — MinIO là kho lưu file. Xem danh sách bằng `--list-quarantine` không cần mở MinIO UI.

---

### Tại sao không ghi vào bảng `documents`

Bảng `documents` (Bài 10) dành cho file đã index thành công — status `active` hoặc `deleted`. File bị cách ly **chưa bao giờ được index** → ghi vào `documents` sẽ làm reconcile nhầm (Postgres nói "active" nhưng Qdrant không có chunk nào).

`quarantine_log` là bảng riêng, không tham gia vào flow index/delete/reconcile.

---

### Thứ tự các bước chạy và lý do

```bash
# 1. Cài MinIO Python SDK
uv add minio
```
SDK để Python nói chuyện với MinIO server. Không có → `upload_to_quarantine` fail khi import.

```bash
# 2. Chạy migration
python data/quality.py --run-migration
```
Tạo bảng `quarantine_log` trong Postgres. Phải làm trước `process_file` vì `log_quarantine` INSERT vào bảng này. Nếu bảng chưa tồn tại → `psycopg2.errors.UndefinedTable`.

```bash
# 3. Thử file HPG thật — phải pass
python data/quality.py --file "evals/docs/HGP/..."
```
Baseline: file BCTC thật phải qua được. Nếu bị chặn → ngưỡng đặt sai, cần điều chỉnh. In ra `char_ratio` và `cpp` thực tế để biết file thật nằm ở đâu so với ngưỡng.

```bash
# 4. Thử 4 file độc hại
python data/quality.py --file scan_test.pdf       # PDF scan → bị chặn
python data/quality.py --file english_report.pdf  # tiếng Anh → tuỳ thiết kế
python data/quality.py --file big_file.pdf        # 500 trang → cảnh báo
python data/quality.py --file non_financial.pdf   # văn bản thường → cần thêm check domain
```
Verify từng ca. Ca quan trọng nhất: **PDF scan phải bị chặn** và phải thấy entry trong `quarantine_log`.

```bash
# 5. Xem danh sách cách ly
python data/quality.py --list-quarantine
```
In bảng từ Postgres: doc_id, nguồn, lý do, các chỉ số đo được, thời gian. Dùng để audit "file nào bị loại vì sao" — không cần mở MinIO UI hay query SQL thủ công.

---

## Đơn vị giá cổ phiếu VN — cái bẫy 1000x

API chứng khoán VN (Fireant, và bảng `ohlcv_daily` sinh ra từ nó) trả giá theo **nghìn đồng**, không phải VND:

```
HPG close = 21.7    → 21,700 VND
VPB close = 27.8    → 27,800 VND
VCB close = 58.9    → 58,900 VND
```

Nhưng API khác lại trả VND thật — VCI price board `foreignBuyValue` là VND nguyên. Cùng một khái niệm "giá trị giao dịch" mà hai nguồn khác nhau 1000 lần.

Hệ quả khi suy ra giá trị từ khối lượng:

```python
# SAI — coi close là VND
buy_value_ty = volume * close / 1e9

# ĐÚNG — close là nghìn đồng, volume × close ra nghìn đồng
buy_value_ty = volume * close / 1e6
```

**Nguyên tắc:** đơn vị phải là phần của *contract*, không phải kiến thức ngầm. Một cột `BIGINT buy_value` không nói nó là VND, nghìn đồng, hay tỷ đồng. Khi contract không được viết ra, mỗi call-site tự đoán — và đoán khác nhau. Cách phòng: đặt tên cột/biến kèm đơn vị (`buy_value_bn`), hoặc normalize ngay tại **boundary** (chỗ data vào hệ thống) rồi bên trong chỉ dùng một đơn vị duy nhất.

Bug này biểu hiện thành `0` chứ không thành số sai lệch nhẹ, vì lỗi xảy ra **hai lần cùng chiều**: ingest chia 1e9 (thay vì 1e6), rồi format lại chia 1e9 lần nữa. Tổng 1e12 → mọi giá trị hợp lý đều làm tròn về 0. Lỗi nhân tính chất lũy — dễ phát hiện hơn lỗi lệch nhẹ, nhưng chỉ khi có người nhìn output thật.

## Nhiều nguồn ghi cùng một bảng — ai được đè ai

Khi hai ingest path viết cùng `(ticker, date)`, `ON CONFLICT DO UPDATE` ở cả hai nghĩa là **job chạy sau thắng** — thứ tự cron quyết định chất lượng data, một cách vô tình.

Trường hợp `foreign_flows`:

```
17:30  VCI price board    → value THẬT (API trả trực tiếp)
18:30  Fireant + ohlcv    → value SUY RA (volume × close)
       ↑ chạy sau, DO UPDATE → đè mất value thật
```

Cách sửa không phải đổi cron (mong manh, phụ thuộc timing) mà là mã hoá **thứ bậc nguồn** vào chính câu SQL:

```sql
-- nguồn chính (value thật): được đè
ON CONFLICT (ticker, date) DO UPDATE SET ...

-- nguồn phụ (value suy ra): chỉ lấp lỗ trống
ON CONFLICT (ticker, date) DO NOTHING
```

`DO NOTHING` biến path phụ thành **gap-filler** idempotent: chạy bao nhiêu lần, chạy lúc nào cũng không phá data tốt. Đổi lại `cur.rowcount` không còn bằng `len(rows)` — phải trả rowcount thật, nếu không log sẽ báo "upserted 248 rows" trong khi thực tế insert 0.

**Nguyên tắc:** khi nhiều nguồn có độ tin cậy khác nhau ghi cùng một bảng, thứ bậc phải nằm trong conflict clause, không nằm trong lịch chạy.

## Ổ rác sống sót sau backfill

Backfill bằng upsert (`--migrate`) chỉ đè được `(ticker, date)` mà **nguồn hiện tại trả về**. Row nào nguồn không có data thì giữ nguyên scale cũ — im lặng sống sót:

| Loại row sống sót | Vì sao nguồn không trả |
|---|---|
| Ngày không phải phiên (thứ Bảy) | Fireant không có data ngày nghỉ |
| Ticker đã bị huỷ/tạm ngừng | Không còn trong response |
| Ticker ngoài `securities.is_active` | `_active_tickers()` không lặp tới |
| Date ngoài window backfill | Không nằm trong range query |
| Chỉ số (VNINDEX) lẫn vào bảng per-ticker | Không thuộc universe cổ phiếu |

Row cuối đặc biệt nguy hiểm: VNINDEX trong `foreign_flows` khiến `SUM(net_value)` toàn thị trường bị **cộng trùng** — chỉ số đã là tổng hợp của các mã thành phần.

**Nguyên tắc:** backfill xong phải chạy invariant check, không chỉ đếm rows upserted. Các check dạng "bất khả thi":

```sql
abs(net_value) > 10000              -- không phiên nào ròng 10,000 tỷ
buy_volume = 0 AND buy_value <> 0   -- không có giá trị mà không có khối lượng
date NOT IN (SELECT date FROM ohlcv_daily)   -- ngày không phải phiên giao dịch
ticker NOT IN (SELECT ticker FROM securities)  -- ticker ngoài universe
```

Mỗi check tốn một query, phát hiện được cả loại lỗi chưa nghĩ tới.

## Live price board không có session date — phantom session

Endpoint dạng "price board" (VCI `price/symbols/getList`) trả **trạng thái hiện tại**, không trả ngày của phiên đang hiển thị. Ngày nghỉ nó vẫn serve số phiên gần nhất — không có cờ nào nói "đây là data cũ".

Client tự đóng dấu `date.today()` lên đó rồi ghi DB:

```
Thứ Bảy 2026-08-29  → board serve số của thứ Sáu 08-28
                    → ghi thành row date = 2026-08-29
                    → "phiên" thứ Bảy xuất hiện trong DB
```

Hệ quả không chỉ là một row rác: mọi aggregate theo phiên đều lệch. Streak "mua ròng N phiên liên tiếp" đếm cả phiên không tồn tại. Trung bình 5 phiên bị pha loãng bằng bản sao.

Hai lớp chặn, rẻ và không cần dependency:

```python
# 1. Weekend — deterministic, chặn trước khi gọi API
if target_date.weekday() >= 5:
    return 0

# 2. Holiday — so volume với phiên đã lưu gần nhất
if matched / compared >= 0.9:   # board chỉ lặp lại phiên trước
    return 0
```

**Tại sao không dùng holiday calendar?** VN có bridge day bất quy tắc quanh Tết và 2/9, công bố từng năm — calendar phải maintain tay và sẽ lệch. So volume tự đúng với mọi ngày nghỉ, kể cả nghỉ đột xuất (sàn treo phiên).

Chọn `buy_volume`/`sell_volume` làm khoá so sánh vì đó là **số nguyên đếm khớp lệnh** — hai phiên thật gần như không thể trùng khít trên hàng trăm mã. Giá trị tiền thì có thể trùng do làm tròn.

Ngưỡng `compared < 10 → không kết luận` là fail-open có chủ ý: thiếu overlap thì thà ghi rồi cleanup sau, hơn là chặn oan một phiên thật.

**Nguyên tắc:** khi nguồn không tự khai thời điểm của data, client phải tự kiểm chứng data có mới không. "Gọi lúc nào thì là data lúc đó" là giả định sai với mọi endpoint dạng snapshot.

## Incremental ingest không lấp được lỗ ở giữa

Incremental kiểu `range_start = MAX(date) + 1` chỉ tiến về phía trước. Một ngày fail ở giữa series thì:

```
DB có:  ... 08-27, 08-28, [thiếu 08-31], 09-03, 09-04
MAX = 09-04  →  range_start = 09-05  →  không bao giờ chạm 08-31
```

Job daily chạy mãi cũng không tự sửa. Chỉ full backfill (`--migrate`, 365 ngày × toàn bộ mã) mới lấp được — quá nặng để chạy vì một ngày.

Cần một đường thứ ba: range tường minh (`--start-date` / `--end-date`) cho phép lấp đúng lỗ. Ba mode phủ ba tình huống khác nhau:

| Mode | Dùng khi |
|---|---|
| incremental (`MAX+1 → today`) | job daily, chi phí thấp nhất |
| explicit range | lấp gap đã xác định |
| full backfill | đổi schema/đơn vị, cần ghi lại toàn bộ |

**Nguyên tắc:** ingest nào có state ("đã chạy tới đâu") thì phải có đường bypass state, nếu không mọi lỗi transient đều thành lỗ vĩnh viễn.

## Time-bomb test — fixture ngày cố định + logic ngày tương đối

Test xanh suốt rồi tự vỡ không do ai sửa code:

```python
base = date(2026, 8, 1)                    # fixture: ngày cố định
fetch_and_upsert("HPG", days=30)           # code: window tương đối today
```

Ngày `today` còn trong 30 ngày kể từ 2026-08-01 thì trùng nhau, test xanh. Qua 2026-08-31 thì window trượt khỏi fixture → rows bị clip hết → fail.

Hai chi tiết làm nó khó tìm:

1. **Chỉ một path clip.** Fireant path không filter theo range, fallback path có. Nên cùng fixture đó, 37 test xanh và đúng 1 test đỏ — trông như bug ở fallback logic.
2. **Fail message vô hại.** `assert 0 == 3` không gợi gì về ngày tháng.

Cách phòng: fixture và code phải cùng hệ quy chiếu. Hoặc fixture sinh theo `today` (`base = today - timedelta(days=5)`), hoặc test truyền range tường minh để không phụ thuộc `today`. Trộn hai hệ là bom hẹn giờ.

Cùng họ với lỗi ở `tests/test_phase2.py`: fixture `date(2026, 8, 25)` + `_is_db_fresh()` so với `date.today()` → sau vài ngày data mock bị coi là stale, tool rơi xuống live path và gọi network thật trong unit test.
