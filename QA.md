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

## Sentiment analysis là gì? Mục đích và ảnh hưởng tới project?

**Q:** Sentiment là gì, dùng để làm gì, có cần thiết cho project này không?

**A:** Sentiment analysis = phân loại cảm xúc của một câu văn bản thành `positive / neutral / negative`.

Trong tài chính (financial sentiment):
- `positive`: "Lợi nhuận HPG tăng mạnh quý 3" → thị trường phản ứng tốt
- `neutral`: "HPG công bố BCTC quý 2" → thông tin trung tính
- `negative`: "HPG lỗ do giá thép giảm" → tín hiệu xấu

**`eval_sentiment.py` dùng dataset Financial PhraseBank** (Malo et al. 2014) — 4846 câu tiếng Anh, đánh giá LLM zero-shot classify đúng bao nhiêu.

**Ảnh hưởng tới project hiện tại: không đáng kể.**  
Pipeline RAG của project tập trung vào retrieval + generation từ BCTC + news — không dùng sentiment score ở bất kỳ bước nào.

**Khi nào sentiment có ích:**
- Tag news articles với label trước khi index → filter "chỉ lấy tin tiêu cực về HPG"
- Tóm tắt xu hướng sentiment thị trường theo thời gian
- Input feature cho mô hình dự báo giá (bài sau)

**Kết luận:** `eval_sentiment.py` là bài học độc lập về LLM classification, không phải core pipeline. Skip nếu không có task sentiment cụ thể.

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

## RAG-Fusion — Multi-Query + RRF

**Q:** RAG-Fusion là gì và tại sao dùng nó thay vì single-query hybrid?

**A:** RAG-Fusion = sinh N sub-queries từ câu gốc bằng LLM → chạy hybrid retrieval song song cho từng câu → gộp tất cả kết quả bằng RRF.

Vấn đề single-query không giải quyết được: câu hỏi tài chính phức tạp như "Phân tích HPG Q1 2024" thực ra cần nhiều góc thông tin cùng lúc — số liệu cụ thể, so sánh kỳ trước, bối cảnh ngành, sự kiện thị trường. Một vector duy nhất không bắt được cả bốn góc. Multi-query dùng LLM để decompose thành 4 sub-queries, mỗi câu đại diện một góc.

Tại sao không dùng reranker thay thế? Reranker chỉ chọn lại từ candidates đã có — không thêm được thông tin mới vào pool. Multi-query mở rộng không gian tìm kiếm trước, reranker thu hẹp sau. Hai cơ chế khác nhau.

---

**Q:** RAG-Fusion ảnh hưởng thế nào lên pipeline HPG?

**A:** Ba thay đổi thực chất:

1. **recall@5 tăng 0.857 → 0.952** — 2 câu `table_lookup` từng miss (q08, q31) nay tìm đúng. Những câu này hỏi số liệu cụ thể nằm sâu trong bảng — sub-query "so sánh kỳ trước" mới bắt được chunk đúng mà single query bỏ qua.

2. **Latency tăng 2.8s → 8.1s p95** — thêm 1 LLM call (sub-query gen ~2s) + N lần retrieval song song (~3s). Không dùng được cho use-case cần phản hồi <3s.

3. **Context đa nguồn** — pipeline giờ kéo Postgres (chỉ số tài chính từ vnstock) + Tavily (tin tức real-time) vào cùng context với RAG corpus. LLM nhận thông tin từ 3 nguồn thay vì 1, có nhãn nguồn rõ ràng (`[BCTC]`, `[GIÁ LỊCH SỬ]`, `[TIN TỨC]`).

---

**Q:** Khi nào multi-query KHÔNG giúp được?

**A:** Câu hỏi tra cứu đơn giản như "HPG ROE 2024 là bao nhiêu?" — chỉ cần 1 chunk duy nhất. Model sinh 4 sub-queries nhưng tất cả xoay quanh cùng một số liệu → 4 lần retrieval trả về phần lớn chunks giống nhau → RRF không thêm được diversity. Tốn thêm 5s và 1 LLM call mà gain = 0.

Dấu hiệu nhận ra: khi sub-queries của model đều hỏi về cùng một số liệu cụ thể, không có góc nhìn khác nhau thực sự.

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

## Bài 19B — Mục đích của tool tin tức và sentiment

**Q:** Tại sao cần 2 tool `search_financial_news` và `analyze_market_sentiment`? Tool giá (bài 19) chưa đủ sao?

**A:** Tool giá trả lời "giá bao nhiêu" — số liệu lịch sử, không có context tại sao. Agent nhận được "HPG = 27,500 VND" nhưng không biết hôm nay thị trường đang nghĩ gì về HPG.

**Vấn đề cụ thể không có 2 tool này:**

Câu hỏi *"HPG có đáng mua không?"* → agent có giá, có chỉ báo kỹ thuật, nhưng:
- Không biết hôm qua HPG vừa thông báo tạm ngừng lò cao
- Không biết tuần này 4/5 tin về HPG là tiêu cực
- Kết quả: agent phân tích kỹ thuật trông hợp lý nhưng bỏ qua event thực tế → sai hoàn toàn

**`search_financial_news` giải quyết:** cung cấp context thị trường trong N ngày — agent biết chuyện gì đang xảy ra với ticker, không chỉ biết con số.

**`analyze_market_sentiment` giải quyết:** tổng hợp 5 tin thành 1 nhãn có lý do — agent không cần tự đọc từng bài báo và tự phán xét, đã có signal rõ ràng: "Xu hướng TIÊU CỰC — 3/5 tin đề cập sụt giảm sản lượng".

**Tại sao few-shot thay vì zero-shot:**

Zero-shot với financial tiếng Việt dễ bị lệch: "HPG tạm dừng lò cao để bảo trì theo kế hoạch" — zero-shot có thể đánh giá neutral (bảo trì = bình thường), nhưng trong ngữ cảnh ngành thép Việt Nam đây là tín hiệu tiêu cực (nhu cầu thấp → giảm công suất). Few-shot với ví dụ domain-specific giúp model calibrate đúng.

**Flow trong agent (bài 22+):**

```
user: "Tình hình HPG thế nào?"
→ agent gọi get_realtime_price("HPG")        # giá hiện tại
→ agent gọi calculate_indicators(df)          # kỹ thuật
→ agent gọi search_financial_news("HPG", 7)   # context thị trường
→ agent gọi analyze_market_sentiment("HPG")   # tổng hợp sentiment
→ agent synthesis từ 4 nguồn → trả lời đầy đủ
```

Không có bài 19B: agent chỉ nhìn biểu đồ kỹ thuật, mù với news flow.

---

## Bài 18 — Router prompt cần khai báo data sources, không chỉ mô tả abstract

**Q:** Router phân loại 80% lần đầu — cải thiện bằng cách nào mà không thêm few-shot examples?

**A:** Lỗi chủ yếu do model không biết data sources nào tồn tại. Ví dụ: model biết giá cổ phiếu là "market data" → tự phân loại `ngoài_phạm_vi`, nhưng thực ra `stock_prices` table có trong DB. Fix: khai báo explicit schema trong system prompt (`stock_prices table EXISTS: ticker, trade_date, close_adj`) → model biết đường đi. Nguyên tắc: **router prompt = map của data landscape, không phải tổng quát về loại câu hỏi**.

---

## Bài 18 — Tại sao SQL agent cần 4 lớp bảo mật, không phải chỉ 1?

**Q:** Readonly role đã đủ chưa? Cần thêm sqlglot, LIMIT, timeout làm gì?

**A:** Mỗi lớp chặn thứ khác nhau:
- **Readonly role** chặn DML ngay tại DB — nhưng nếu LLM sinh `SELECT * FROM pg_shadow` thì role không giúp được (pg_shadow là SELECT).
- **sqlglot** chặn forbidden tables, multiple statements, unicode tricks — nhưng không chặn slow query.
- **LIMIT 1000** ngăn full-table dump qua SELECT — nhưng không chặn `pg_sleep(10)`.
- **timeout 5s** kill query chậm — nhưng không phân tích cú pháp SQL.

Không có lớp nào đủ một mình. Defense-in-depth: lớp sau bắt những gì lớp trước bỏ qua.

---

## Bài 20 — Tại sao trả `[]` khiến agent lặp vô hạn, còn `no_data` kèm hướng dẫn thì không?

**Q:** Tôi thấy agent gọi đi gọi lại cùng 1 tool với cùng tham số. Lý do thật sự là gì? Có phải prompt kém không?

**A:** Không phải prompt kém. Lý do là **tool không cho agent thông tin để thoát ra**.

Khi tool trả `[]` hoặc raise exception:
1. Agent nhận tín hiệu "không có kết quả / thất bại"
2. Agent không có hướng dẫn nào → suy luận: *"có thể mạng chậm, thử lại"*
3. Agent gọi lại đúng tool, đúng tham số → cùng kết quả → lặp lại bước 2

Khi tool trả `ToolResult(status="no_data", message="Không có tin tức HPG 7 ngày. **Tăng khoảng thời gian hoặc thử mã khác.**")`:
1. Agent nhận được status + hướng dẫn cụ thể
2. Agent thay đổi chiến lược: thử `days=30` hoặc chuyển sang tool khác
3. Vòng lặp phá vỡ

Nguyên nhân cốt lõi: agent không có ý chí riêng — nó tối đa hoá "hoàn thành task". Nếu không có signal nào nói "đường này dead end", nó cứ thử lại. `message` là **thứ duy nhất** model đọc để quyết định bước tiếp theo.

---

**Q:** Nếu `message` viết là "có lỗi xảy ra" thì model làm gì?

**A:** Model làm chính xác những gì message nói — tức là không làm gì có ích.

"Có lỗi xảy ra" không trả lời được câu hỏi nào mà agent cần: *Lỗi gì? Tạm thời hay vĩnh viễn? Thử lại ngay hay chờ? Đổi tham số gì? Dùng tool nào thay thế?*

Model không có thêm thông tin nào ngoài message → tiếp tục tìm cách "giải quyết lỗi" bằng cách thử lại với cùng input → loop.

So sánh:

| Message | Agent làm gì tiếp |
|---|---|
| `"có lỗi xảy ra"` | Retry ngay, loop |
| `"Timeout. Thử lại sau 1–2 phút."` | Pause, retry 1 lần |
| `"Không có dữ liệu cho 'XXXX'. Kiểm tra mã CK. Thử mã khác."` | Xác nhận ticker, thử ticker khác |
| `"429 Rate limited. Chờ 60 giây. Đừng gọi lại ngay."` | Chuyển sang tool khác trong khi chờ |

**Quy tắc viết message:** nói rõ *đừng làm gì* và *nên làm gì thay thế*. Viết như đang nhắn tin cho đồng nghiệp mới đang bị stuck, không phải ghi log lỗi cho dev.

---

**Q:** `_map_upstream_error` dùng string matching để phân biệt loại lỗi — có brittle không?

**A:** Có, nhưng đây là đánh đổi có chủ ý.

Vấn đề: `VnstockProvider` và `YFinanceProvider` raise exception với message không nhất quán — không có enum lỗi chuẩn, mỗi library format khác nhau. Parse exception message là cách duy nhất không cần sửa provider.

```python
# vnstock có thể raise: "HTTP Error 429 for url..."
# yfinance có thể raise: "Too Many Requests. Rate limited."
# requests có thể raise: "429 Client Error: Too Many Requests"
# → tất cả đều chứa "429" hoặc "rate" → match được
```

Brittle ở đâu: nếu provider nâng cấp và đổi format message hoàn toàn → có thể map sai sang `upstream_error` thay vì `rate_limited`. Hệ quả: agent retry ngay thay vì chờ 60s — không nguy hiểm, chỉ kém optimal.

Fix đúng hơn: wrapper riêng cho từng provider, convert exception thành enum `ProviderError`. Nhưng với 2 provider hiện tại, string matching đủ dùng và ít code hơn nhiều.

---

**Q:** Tại sao `data: Any | None` thay vì generic `ToolResult[T]`?

**A:** Generic `ToolResult[T]` (Pydantic v2 generic model) yêu cầu khai báo type tại call site:

```python
# Generic — verbose, cần biết type trước khi gọi
result: ToolResult[float] = get_realtime_price("HPG")
result: ToolResult[pd.DataFrame] = get_historical_ohlcv("HPG", 30)
```

Với `data: Any | None`, caller chỉ cần check `status == "ok"` rồi dùng `result.data` — IDE và runtime vẫn biết type qua function signature return type. Ngoài ra, `pd.DataFrame` trong generic model cần `ConfigDict(arbitrary_types_allowed=True)` dù sao, và agent code thường không cần strict typing cho `data` vì nó đọc `message` nhiều hơn.

Trade-off chấp nhận được: mất type safety cho `data`, đổi lại interface đơn giản hơn, không cần bọc DataFrame vào TypeVar.

---

**Q:** `tools/registry.py` dùng để làm gì thực tế? Không thấy code nào đọc nó.

**A:** Registry hiện tại là **metadata chờ dùng** — không có code tự động đọc nó, nhưng nó phục vụ 3 use case sắp tới:

1. **Bài 21 (MCP server):** khi expose tool qua MCP, server cần `timeout` để set HTTP deadline, `version` để version the endpoint. Không có registry → hardcode trong mcp_server.py → khó sync với tool thật.

2. **Agent orchestrator (bài 22+):** agent cần biết `cost_hint` để quyết định gọi tool nào trước (gọi `free` tool trước, chỉ gọi `medium` khi cần) và `side_effect` để biết tool nào an toàn retry (side_effect=False → retry vô hại; side_effect=True → không retry mù).

3. **Monitoring:** log mỗi tool call kèm metadata → biết tool nào tốn tiền nhiều nhất, tool nào timeout thường xuyên.

Pattern này là **self-documenting infrastructure** — metadata viết một lần, dùng ở nhiều chỗ. Giống schema migration: không có tác dụng ngay nhưng là prerequisite cho nhiều thứ sau.

---

