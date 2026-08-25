# Q&A — Kỹ thuật RAG

Tập hợp câu hỏi + giải thích kỹ thuật trong quá trình build RAG pipeline.

---

## Fusion là gì? Mục đích và ảnh hưởng lên pipeline RAG?

**Q:** Fusion là gì, mục đích của nó, ảnh hưởng thế nào tới bài?

**A:** BM25 và vector search có điểm mạnh riêng — không cái nào đủ mọi trường hợp.

- BM25 thắng: tra mã số (`0503000008`), ticker (`HPG`), ngày cụ thể → exact match
- Vector thắng: câu hỏi ngữ nghĩa (`kế toán trưởng ký là ai`), bảng số trong context

**Fusion** = gộp top-k kết quả từ cả hai, tạo một danh sách duy nhất.

**Tại sao không cộng thẳng điểm?**  
BM25 score ~12, cosine score ~0.7 → cộng thẳng = BM25 chi phối 94%. Vector không có tiếng nói.

**Hai giải pháp:**

| | Cách làm | Điểm mạnh |
|---|---|---|
| **Weighted-sum** | Chuẩn hoá cả hai về [0,1] rồi cộng | Tune được alpha |
| **RRF** | Bỏ điểm, chỉ dùng thứ hạng: `1/(60+rank)` | Không cần tune, robust với outlier |

**Ảnh hưởng lên pipeline:**  
Bài 14 cho thấy BM25 MISS `q08` (bảng số), vector có thể tìm được. Fusion top-20 từ cả hai → `hit@20` tăng so với từng retriever đơn lẻ. Bài 16 tiếp tục: lấy top-20 từ fusion, đưa qua reranker để chọn 5 tốt nhất — hai tầng cộng lại mới thành pipeline hoàn chỉnh.

---

## Qdrant là gì?

**Qdrant** là vector database — lưu trữ và tìm kiếm vector (embedding).

### Vector là gì?

Text → embedding model (ở đây: `nomic-embed-text` qua Ollama) → mảng số float, ví dụ 768 chiều.

```
"Doanh thu HPG năm 2025" → [0.12, -0.87, 0.34, ..., 0.91]  # 768 số
```

Vector gần nhau trong không gian = ý nghĩa tương tự nhau.

### Qdrant làm gì?

1. **Lưu** vector + metadata (text gốc, chunk_id, strategy, ...)
2. **Tìm** k vector gần nhất với query vector → ANN search (Approximate Nearest Neighbor)
3. **Trả về** chunks liên quan nhất → đưa vào context cho LLM

### Tại sao không dùng Postgres để lưu vector?

| | Postgres (pgvector) | Qdrant |
|---|---|---|
| Mục đích chính | Relational DB, vector là add-on | Vector-native |
| Tốc độ ANN | Chậm hơn ở scale lớn | HNSW index, rất nhanh |
| Filtering | SQL | Payload filter kết hợp ANN |
| Setup | pgvector extension | Docker image riêng |

Với RAG ở scale nhỏ (< 1M chunks) cả hai đều dùng được. Project này dùng Qdrant để học đúng tool của ngành.

### Tại sao biết cần dùng Qdrant?

RAG pipeline yêu cầu:
1. Embed query lúc runtime → vector
2. Tìm chunks tương tự trong toàn bộ corpus
3. Làm nhanh (< 100ms cho production)

Postgres full-text search không hiểu ngữ nghĩa. SQL `LIKE` không tìm được "doanh thu" khi user hỏi "revenue".

Vector search giải quyết semantic gap — Qdrant là tool phổ biến nhất cho việc này trong Python RAG stack (cạnh tranh với Weaviate, Pinecone, Chroma).

---

## Chunking strategies — tại sao cần chia nhỏ?

LLM có context limit. Không thể nhét cả 93k-char document vào prompt. Chunk nhỏ → embed → chỉ retrieve phần liên quan.

**Trade-off:** chunk nhỏ → precision tốt hơn, recall kém hơn. Hierarchical giải quyết: embed child (nhỏ, precise), return parent (lớn, context đủ).

---

## Tại sao fixed_512 thắng structural và hierarchical? (HPG BCTC)

### Kết quả đo được

| Chiến lược | faithfulness | refusal_pass_rate | Vấn đề |
|---|---|---|---|
| fixed_512 | **0.931** | **1.000** | — |
| hierarchical | 0.867 | 0.800 | refusal regression |
| structural | 0.615 | 1.000 | faithfulness tệ nhất |

Chỉ faithfulness gap (fixed vs structural = 0.316) đủ lớn để kết luận thật. Các metric khác chênh < 0.04 — có thể là nhiễu.

### Nguyên lý cốt lõi

```
Faithfulness ∝ 1 / (số thông tin không liên quan trong context)
```

LLM bịa khi context **mơ hồ hoặc thiếu nhất quán** — model lấp chỗ trống bằng prior knowledge.

### Tại sao structural thất bại?

Structural cắt theo markdown boundary (headers, `|`, dòng trống). Với PDF scan:

- OCR corrupt boundary: `##` bị mất, header bảng thành `T\nM4&sé\nhuyét\nminh`
- Boundary sai → chunk cắt giữa bảng → 1 chunk chứa nửa BCĐKT + nửa thuyết minh
- Chunk size không đồng đều (200–2000 chars) → chunk to chứa nhiều số không liên quan → model confused, bịa để "kết nối" các số

fixed_512 cắt mù, đều → mỗi chunk nhất quán về mật độ thông tin → ít khoảng trống logic để bịa.

### Tại sao hierarchical thất bại?

Hierarchical: embed child (400 chars, precise) nhưng return **parent** (1200 chars) cho LLM.

Tốt cho prose (Wikipedia, sách). Tệ cho bảng số tài chính:

```
[parent 1200 chars]
Phải thu ngắn hạn .... 1,234,567
Phải trả ngắn hạn .... 2,345,678   ← liên quan query
Vay dài hạn .......... 3,456,789   ← không liên quan
Thuế TNDN hoãn lại ... 4,567,890   ← không liên quan
```

LLM nhận parent → nhiều con số → dễ lấy nhầm số sai từ cùng block → faithfulness giảm.

**Nghịch lý:** parent "context" trong tài liệu tài chính là nhiều dòng số rời rạc, không phải câu văn cung cấp ngữ cảnh.

### Tổng kết

| Strategy | Noise trong context | Lý do |
|---|---|---|
| fixed_512 | Thấp | Chunk nhỏ, đồng đều |
| structural | Cao | Boundary sai do OCR corrupt |
| hierarchical | Trung bình-cao | Parent quá rộng cho financial data |

fixed_512 không phải "tốt hơn mọi mặt" — thắng vì faithfulness cao nhất + không có regression.

---

## MinIO — Object Storage cho PDF

**Q:** MinIO là gì, dùng để làm gì trong pipeline này?

**A:** MinIO là S3-compatible object storage chạy local. Pipeline dùng MinIO làm nguồn PDF — thay vì để file trên disk, PDF được upload lên MinIO, pipeline download về xử lý. Lợi ích: nhiều máy cùng truy cập, sensor phát hiện file mới tự động, không cần SSH vào server để copy file.

---

**Q:** Cách upload PDF lên MinIO?

**A:** Dùng MinIO Console (UI) hoặc `mc` CLI:

```bash
# Cài mc (MinIO Client)
# Windows: winget install MinIO.MinIO  hoặc download từ https://min.io/download

# Kết nối đến MinIO local
mc alias set local http://localhost:9001 minioadmin minioadmin

# Tạo bucket cho ticker (nếu chưa có)
mc mb local/hpg-docs

# Upload 1 file
mc cp outputs/hpg_pymupdf.pdf local/hpg-docs/2024/hpg_q4_bctc.pdf

# Upload cả thư mục
mc cp --recursive outputs/2024/ local/hpg-docs/2024/

# Xem danh sách file
mc ls local/hpg-docs/
```

Hoặc dùng MinIO Console tại `http://localhost:9001` (login: minioadmin / minioadmin):
1. Vào **Buckets** → **Create Bucket** → đặt tên `hpg-docs`
2. Vào bucket → **Upload** → kéo thả PDF

---

**Q:** Tại sao bucket tên là `hpg-docs` chứ không phải `financial-docs`?

**A:** Pipeline Option B — mỗi ticker có bucket riêng. Bucket tự sinh từ ticker:
```
ticker=HPG  →  hpg-docs
ticker=VCB  →  vcb-docs
ticker=MWG  →  mwg-docs
```
Lý do: rebuild 1 ticker không đụng ticker khác. Drop collection `hpg_structural` → full_rebuild → không ảnh hưởng `vcb_structural`.

---

**Q:** Sensor phát hiện file mới như thế nào?

**A:** Sensor poll 5 phút/lần, so sánh sorted list PDF key trong bucket với cursor (lần poll trước). Key mới xuất hiện → kick off `ingestion_job` cho file đó. Cursor format: `"HPG:2024/a.pdf,2024/b.pdf|VCB:2024/c.pdf"` — phân biệt theo từng ticker.

---

**Q:** Update hoặc xóa file trong MinIO thì pipeline xử lý thế nào?

**A:**

**Update (upload lại cùng key):** MinIO cấp etag mới (MD5 của content). Sensor so sánh `key→etag` với cursor lần trước — etag đổi → trigger `ingestion_job` lại. `index_run()` đã idempotent: xóa chunk cũ theo `doc_id` trước khi upsert mới.

**Delete (xóa file khỏi MinIO):** Sensor phát hiện key biến mất khỏi bucket → trigger `delete_job` → `delete_doc` asset: tìm `doc_id` trong Postgres theo `source_uri`, xóa chunk khỏi Qdrant, soft-delete record trong `documents` (`status='deleted'`, `deleted_at=NOW()`). Không xóa hard để giữ audit trail.

Cursor format sau khi fix: `"HPG:2024/a.pdf=etag1,2024/b.pdf=etag2|VCB:2024/c.pdf=etag3"` — track cả key lẫn etag.

---

**Q:** Thêm ticker mới cần làm gì?

**A:** 3 bước:
1. Thêm vào `.env`: `TICKERS=HPG,VCB`
2. Tạo bucket trên MinIO: `mc mb local/vcb-docs`
3. Upload PDF vào bucket

Sensor tự scan bucket mới, pipeline tự tạo collection `vcb_structural` trong Qdrant khi chạy lần đầu.

---

## Dagster — Orchestrator

**Q:** Dagster là gì?

**A:** Dagster là orchestrator — công cụ biến các script rời thành pipeline có thứ tự, có retry, có lịch chạy, và theo dõi được từng bước. Tương tự Airflow hay Prefect nhưng tập trung vào data assets thay vì task graph.

Khái niệm cốt lõi:
- **Asset**: một tập dữ liệu có tên (`raw_pdf`, `parsed_doc`). Dagster track trạng thái: đã materialize chưa, lần cuối chạy lúc nào.
- **Op**: đơn vị tính toán tạo ra asset. Khi dùng `@asset`, Dagster tạo op ngầm bên trong.
- **Job**: tập hợp các asset/op chạy cùng nhau.
- **Schedule**: lịch cron chạy job.
- **Sensor**: poll liên tục, detect sự kiện bên ngoài → trigger job.

---

**Q:** Dagster khác cron + script thế nào?

**A:** Cron + script không có:
- **Retry theo bước**: script A → script B → B fail → cron chạy lại từ A. Dagster retry đúng bước B, A không chạy lại.
- **Dependency rõ ràng**: `parsed_doc` chạy sau `raw_pdf` và nhận output của nó — Dagster enforce thứ tự và truyền data.
- **Audit trail**: Dagster lưu mỗi lần materialize: chạy lúc nào, input là gì, output là gì, log từng dòng.
- **Visibility**: UI tại `http://localhost:3000` — xem asset graph, click vào từng run, đọc log theo từng bước.

Với file 200 trang: parse mất 3 phút, embed mất 20 phút. Nếu embed fail thì chỉ retry embed, không parse lại.

---

**Q:** Tại sao project này cần Dagster?

**A:** Bài 6–12 là các script chạy tay. Thực tế cần:
1. **File mới tự động index** — không thể yêu cầu người dùng `python index.py` mỗi khi có PDF mới.
2. **Retry khi lỗi** — Ollama timeout, Qdrant bận → retry đúng bước, không chạy lại từ đầu.
3. **Audit trail** — biết chunk trong Qdrant đến từ file nào, run nào, lúc mấy giờ.
4. **Hai luồng song song** — `embeddings` và `financial_facts` cùng nhận `parsed_doc`, chạy song song, không parse PDF hai lần.

Không có orchestrator: 4 điểm trên phải tự code — retry logic, dependency management, logging, scheduling. Dagster cho sẵn tất cả.

---

**Q:** Cách dùng Dagster trong project này?

**A:**

```bash
# Khởi động UI
dagster dev -f pipeline/assets.py
# Mở http://localhost:3000
```

**Materialize thủ công** (test từng asset):
- UI → **Assets** → click `raw_pdf` → **Materialize** → xem log

**Chạy toàn bộ pipeline**:
- UI → **Jobs** → `ingestion_job` → **Launch Run**

**Sensor tự động** (detect PDF mới trong MinIO):
- UI → **Automation** → `minio_new_pdf_sensor` → **Start**
- Thả PDF vào MinIO bucket → 5 phút sau sensor trigger → pipeline tự chạy

**Config per-run** (chạy cho ticker khác):
- Launch Run → **Config** → sửa `ticker: VCB`, `ky: 2023`

**Xem audit trail của 1 chunk**:
- Qdrant payload có `dagster_run_id` → UI → **Runs** → paste run_id → xem log → thấy file gốc

---

**Q:** Asset graph trông như thế nào?

**A:**
```
raw_pdf ──→ parsed_doc ──→ embeddings
                       └──→ financial_facts
```
Dagster vẽ graph này trong UI. Click vào node → xem code, config, lịch sử materialize.

---

## BM25 — Sparse retrieval

**Q:** BM25 là gì, khác vector search thế nào?

**A:** BM25 (Best Match 25) là thuật toán tìm kiếm **từ khoá chính xác** dựa trên thống kê. Không cần embedding, không cần GPU.

Công thức cốt lõi: mỗi từ trong query được score theo 2 yếu tố:
- **TF (Term Frequency):** từ xuất hiện nhiều lần trong chunk → score cao hơn, nhưng có saturation (sau ngưỡng k₁ ≈ 1.5 thì không tăng thêm)
- **IDF (Inverse Document Frequency):** từ hiếm trong toàn corpus → score cao hơn; từ phổ biến ("là", "của") → gần 0

```
BM25(q, d) = Σ IDF(t) × TF(t,d) × (k₁+1) / (TF(t,d) + k₁×(1-b+b×|d|/avgdl))
```

| | BM25 | Vector search |
|---|---|---|
| Tìm theo | Từ khoá chính xác | Ngữ nghĩa / ý nghĩa |
| Query "quý 3 năm 2024" | Match đúng chunk chứa "quý 3 năm 2024" | Match chunk nói về "kết quả quý" dù khác từ |
| Query "revenue" vs "doanh thu" | Miss (khác token) | Match (cùng semantic space) |
| Tốc độ | Nhanh (in-memory index) | Nhanh (ANN search Qdrant) |
| Cần embed model | Không | Có |
| Thất bại khi | Synonym, paraphrase, đa ngôn ngữ | Số cụ thể, mã cổ phiếu, tên riêng chính xác |

**Tại sao dùng BM25 trong project này:**
- HPG BCTC có nhiều truy vấn từ khoá chính xác: "quý I/2024", "1,234,567 triệu đồng", "HOSE"
- Vector search đôi khi "hiểu quá rộng" → trả về chunk đúng chủ đề nhưng sai số
- BM25 làm baseline để so sánh: nếu BM25 thắng → corpus có structure rõ ràng; nếu vector thắng → query cần semantic understanding

---

**Q:** BM25 tích hợp vào pipeline thế nào?

**A:** Project dùng `rank_bm25` (Python lib). BM25Retriever load **toàn bộ chunks từ Qdrant** vào RAM khi khởi tạo, build index, rồi search hoàn toàn in-memory — không query Qdrant lúc search.

```python
# Khởi tạo: scroll all chunks từ Qdrant → build BM25Okapi index
retriever = BM25Retriever(collection="hpg_structural", use_vn_tokenize=False)

# Search: tokenize query → BM25 score → rank → trả top_k text
results = retriever.search("doanh thu thuần quý 3", top_k=5)
```

Trade-off: index không update real-time (cần restart để pick up chunks mới). Chấp nhận được cho eval — production cần hybrid có Qdrant handle freshness.

---

## VN Tokenization (underthesea)

**Q:** Tại sao cần tách từ tiếng Việt, split() không đủ?

**A:** Tiếng Việt là ngôn ngữ **đa âm tiết** — đơn vị nghĩa là **từ ghép** nhiều âm tiết, không phải âm tiết đơn lẻ.

```
"doanh thu" → 1 đơn vị nghĩa (revenue)
"thu"       → âm tiết thứ 2 của "doanh thu", không phải "thu" (collect/autumn)

split() → ["doanh", "thu"]     # 2 token riêng, mất nghĩa gốc
vn_tokenize → ["doanh_thu"]    # 1 token compound, giữ nghĩa
```

Với BM25, token là đơn vị match. Nếu query "doanh thu" bị split thành `["doanh", "thu"]` và chunk cũng bị split tương tự → match ngẫu nhiên trên từng âm tiết thay vì match cụm.

**underthesea** là thư viện NLP tiếng Việt — dùng CRF model để nhận diện ranh giới từ.

---

**Q:** underthesea có hoàn hảo không?

**A:** Không. Observed trong project:

```python
word_tokenize("doanh thu thuần quý ba năm 2024", format="text")
# Output: "doanh_thu thuần quý ba năm 2024"
# ✓ "doanh_thu" đúng
# ✗ "quý ba" không được nối thành "quý_ba"
# ✗ "doanh thu thuần" nên là 1 cụm nhưng "thuần" tách riêng
```

underthesea train trên corpus phổ thông — thiếu domain-specific financial terms như:
- `quý I`, `quý ba`, `quý 3` (Roman và Arabic numeral)
- `lãi suất`, `lãi gộp`, `lãi ròng`
- `HOSE`, `HNX`, `tỷ lệ P/E`

Hệ quả: query "quý 3" vs corpus chứa "quý ba" → vẫn miss dù đã tokenize. Cần custom dictionary hoặc normalization step nếu muốn giải quyết triệt để.

---

**Q:** BM25 raw split vs BM25 + VN tokenize — khi nào cái nào tốt hơn?

**A:** Chưa có kết quả đo từ project. Hypothesis dựa trên lý thuyết:

| Loại query | raw split | vn_tokenize |
|---|---|---|
| Số cụ thể: "1,234,567" | Tương đương | Tương đương |
| Cụm phổ thông: "doanh thu" | Tệ hơn (match từng âm tiết) | Tốt hơn |
| Cụm tài chính: "quý ba" | Tệ hơn | Không cải thiện (underthesea miss) |
| Tên riêng: "HPG", "HOSE" | Tốt (không bị tách) | Tốt |

→ Chờ eval thực tế (`evals/bm25_raw.json` vs `evals/bm25_vn.json`) để kết luận.

---

## Bài 10 — Xoá tài liệu & kiểm toán

**Q:** Vì sao không xoá record khỏi Postgres khi xoá tài liệu?

**A:** Xoá record = mất bằng chứng. Kiểm toán cần trả lời được: *"báo cáo HPG 2024 từng tồn tại, bị thu hồi lúc mấy giờ ngày nào"*. Nếu xoá dòng → câu hỏi đó không trả lời được. `status='deleted'` + `deleted_at` giữ audit trail đầy đủ mà không ảnh hưởng retrieval.

---

**Q:** Nếu không có job đối chiếu, làm sao biết mình đang trả lời từ dữ liệu nào?

**A:** Không biết. Qdrant là hộp đen — không có API "liệt kê tất cả tài liệu đang active". Chỉ biết "có bao nhiêu vector", không biết "vector đó từ file nào, file đó còn valid không". Nếu worker chết giữa soft_delete (Postgres đã `deleted` nhưng Qdrant chưa xoá) → LLM vẫn trả lời từ tài liệu đã thu hồi, không có dấu hiệu lỗi nào. Reconcile là cơ chế duy nhất phát hiện tình huống này.

---

**Q:** Tại sao yêu cầu kiểm toán là bắt buộc với sản phẩm tài chính?

**A:** Nếu HPG thu hồi báo cáo 2024 vì số liệu sai → cơ quan quản lý hỏi *"hệ thống anh đã dừng trả lời từ báo cáo đó từ khi nào?"* — phải trả lời được bằng timestamp cụ thể. `deleted_at` quan trọng hơn "tiết kiệm 1 dòng trong DB". Đây là yêu cầu pháp lý, không phải tính năng tuỳ chọn.

---

