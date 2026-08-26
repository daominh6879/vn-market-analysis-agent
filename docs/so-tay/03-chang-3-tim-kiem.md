# CHẶNG 3 · Tìm kiếm (tuần 4–6)

---

### Bài 14 · Tìm theo từ khoá và bài học tách từ tiếng Việt 🔴
**~1 ngày**

**Bối cảnh.** Vector search tìm theo nghĩa — nhưng câu "doanh thu thuần Q3 2024 của FPT" không cần hiểu nghĩa, chỉ cần khớp từ khoá chính xác. BM25 làm điều đó. Bài này cho thấy bằng số cụ thể khi nào từng loại thắng, và dạy một vấn đề đặc thù tiếng Việt: tách từ sai làm mất toàn bộ lợi thế BM25.

**Để hiểu gì.** Vì sao tìm kiếm ngữ nghĩa một mình không đủ — bằng ví dụ cụ thể của chính bạn, không bằng lý thuyết.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Cài dependencies:
   ```
   uv add rank-bm25 underthesea
   ```
2. Kiểm tra underthesea hoạt động đúng:
   ```python
   from underthesea import word_tokenize
   print(word_tokenize("doanh thu thuần quý ba năm 2024", format="text"))
   # Kết quả đúng: "doanh_thu thuần quý_ba năm 2024"
   # Nếu thấy từng chữ riêng lẻ → tách từ đang không nhận diện cụm từ → BM25 sẽ yếu
   ```
3. Tạo file `rag/retrieval_bm25.py` — lớp `BM25Retriever` nhận danh sách chunk text, build index, expose method `search(query, top_k)`.

**Chi tiết từng việc:**

- **Cài BM25 và chạy eval chỉ với nó, ghi số vào `NOTES.md`.**

  Đọc tất cả chunk từ Qdrant, build BM25 index:
  ```python
  from rank_bm25 import BM25Okapi

  tokenized_corpus = [doc.split() for doc in corpus_texts]
  bm25 = BM25Okapi(tokenized_corpus)
  ```
  Chạy eval: `uv run python evals/run.py --retriever bm25 --output evals/bm25_raw.json`

- **Thêm tách từ tiếng Việt trước khi tokenize, chạy lại, ghi số.**

  ```python
  from underthesea import word_tokenize

  def vn_tokenize(text: str) -> list[str]:
      return word_tokenize(text, format="text").split()

  tokenized_corpus = [vn_tokenize(doc) for doc in corpus_texts]
  ```

- **Tìm bằng tay 3 câu từ khoá thắng, 3 câu ngữ nghĩa thắng, ghi vào `NOTES.md`.**

  Lấy 25 câu hỏi từ `evals/golden_hpg.yaml`, thử từng câu với cả BM25 và vector search. So chunk trả về với ground truth.

**Xong khi.**
- [ ] 2 dòng số: BM25 thô vs BM25 + tách từ
- [ ] 6 ví dụ cụ thể (3+3) trong `NOTES.md`

**Tự trả lời được.**
- 6 ví dụ đó — **đây là thứ giá trị nhất của bài này**, cụ thể hơn mọi lý thuyết.
- Vì sao "tài chính" bị tách thành hai token lại gây vấn đề?
- Tìm từ khoá thắng ở nhóm câu hỏi nào?

**Cái bẫy.** Nếu bạn không thấy tìm từ khoá thắng ở nhóm tra số và tra mã cổ phiếu, kiểm tra lại bộ câu hỏi chuẩn có đủ câu nhóm đó chưa.

---

### Bài 15 · Ghép hai loại kết quả 🔴
**~1 ngày**

**Bối cảnh.** Bài 14 cho thấy cả hai loại tìm kiếm đều có điểm mạnh riêng. Bài này ghép chúng lại — nhưng điểm cosine và điểm BM25 nằm trên hai thang khác nhau, không thể cộng trực tiếp.

**Để hiểu gì.** Vì sao không thể cộng thẳng hai loại điểm — và bạn sẽ thấy nó bằng mắt.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Tạo file `rag/fusion.py` với hai hàm: `weighted_sum_fusion` và `rrf_fusion`.
2. Chạy nhanh thử nghiệm tay để thấy vấn đề cộng thẳng:
   ```python
   bm25_score = 12.4
   cosine_score = 0.72
   print(bm25_score + cosine_score)  # → 13.12, BM25 chi phối hoàn toàn
   ```
3. Chạy eval cho cả hai cách ghép rồi so sánh với baseline đơn lẻ từ bài 14.

**Chi tiết từng việc:**

- **Chuẩn hoá rồi cộng có trọng số:**
  ```python
  def weighted_sum_fusion(bm25_results, vector_results, alpha=0.5) -> list[str]:
      def normalize(scores):
          min_s, max_s = min(scores), max(scores)
          if max_s == min_s:
              return [0.0] * len(scores)
          return [(s - min_s) / (max_s - min_s) for s in scores]
      ...
  ```

- **Ghép bằng thứ hạng (RRF, `k=60`) — bỏ điểm, chỉ dùng vị trí:**
  ```python
  def rrf_fusion(bm25_results: list[str], vector_results: list[str], k=60) -> list[str]:
      scores: dict[str, float] = {}
      for rank, doc in enumerate(bm25_results, start=1):
          scores[doc] = scores.get(doc, 0) + 1 / (k + rank)
      for rank, doc in enumerate(vector_results, start=1):
          scores[doc] = scores.get(doc, 0) + 1 / (k + rank)
      return sorted(scores, key=scores.get, reverse=True)
  ```

- **Kiểm tra "đoạn đúng nằm trong top-20" của kết quả ghép có cao hơn cả hai phương pháp đơn lẻ không.**

**Xong khi.**
- [ ] 2 dòng số, chọn 1 cách và ghi lý do
- [ ] Số "đoạn đúng nằm trong top-20" cao hơn cả hai phương pháp đơn lẻ

**Tự trả lời được.**
- Nhìn vào điểm thô: nếu cộng thẳng thì **bên nào chi phối** và vì sao?
- Ghép bằng thứ hạng có ưu điểm gì mà số liệu không thể hiện?

**Cái bẫy.** Nếu cộng có trọng số thắng trên dữ liệu của bạn, đừng ép chọn cách kia vì "sách nói vậy". Ghi trung thực.

---

### Bài 16 · Reranker — và bảng số hoàn chỉnh 🔴
**~1 ngày**

**Bối cảnh.** Top-20 kết quả từ bài 15 thường còn nhiễu. Reranker đọc câu hỏi và từng đoạn cùng lúc (thay vì so vector riêng lẻ) để chọn ra 5 đoạn thực sự liên quan.

**Để hiểu gì.** Hai cách so khớp câu hỏi với tài liệu khác nhau ở đâu, và vì sao kiến trúc hai tầng (tìm rộng rồi chấm lại) là chuẩn công nghiệp.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Cài và tải model reranker (~500 MB):
   ```python
   from sentence_transformers import CrossEncoder
   reranker = CrossEncoder("BAAI/bge-reranker-v2-m3")
   ```
2. Thử với 3 đoạn text để xác nhận model đang chạy.
3. Đo thời gian ngay từ đầu — reranker chạy trên CPU.

**Chi tiết từng việc:**

- **Cài `BAAI/bge-reranker-v2-m3`, đưa 20 đoạn từ bài 15 qua, giữ 5 đoạn:**
  ```python
  def rerank(query: str, candidates: list[str], top_k=5) -> list[str]:
      pairs = [(query, doc) for doc in candidates]
      scores = reranker.predict(pairs)
      ranked = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
      return [doc for doc, _ in ranked[:top_k]]
  ```

- **Đặt đoạn điểm cao nhất gần cuối prompt, ngay trước câu hỏi.** Lý do: LLM có xu hướng chú ý hơn vào phần cuối context (lost-in-the-middle effect).

- **Đo thời gian riêng của bước reranker** cho p95 — chạy eval 25 câu, lấy percentile 95.

- **Hoàn thiện bảng so sánh** — điền đủ 5 dòng:

  | Cấu hình | Đoạn đúng trong top-5 | Ngữ cảnh chính xác | Có bịa không | Thời gian p95 |
  |---|---|---|---|---|
  | Model trần, không RAG | | | | |
  | Chỉ tìm ngữ nghĩa | | | | |
  | + tìm từ khoá, ghép lại | | | | |
  | + reranker | | | | |
  | + filter theo metadata | | | | |

**Xong khi.**
- [ ] Bảng 5 dòng đầy đủ
- [ ] Nói được một câu: *"Ngữ cảnh chính xác tăng từ X lên Y, đổi lại thêm Z ms ở p95"*

**Tự trả lời được.**
- Reranker chính xác hơn ở đâu, và **vì sao nó không thể chạy trên cả corpus?**
- Nó tốn thêm bao nhiêu ms? Con số đó có làm bạn đổi thiết kế không?

**Cái bẫy.** Reranker trên CPU tốn 200–800ms. Đây là con số thật đầu tiên trong đó **chất lượng và thời gian xung đột trực tiếp** — dừng lại và cảm nhận sự đánh đổi đó.

---

### Bài 16b · RAG-Fusion: sinh nhiều truy vấn, hợp nhất bằng RRF 🔴
**~1.5 ngày**

**Bối cảnh.** Bài 15 gộp BM25 + vector cho *cùng một truy vấn*. RAG-Fusion đi xa hơn: sinh N truy vấn con từ câu gốc, chạy retrieval song song cho từng câu, rồi gộp *tất cả* kết quả bằng RRF. Câu "Phân tích HPG quý 1 2024" có thể cần thông tin về doanh thu Q1, về ngành thép Q1, về sự kiện thị trường Q1 — một truy vấn đơn không bắt được cả ba.

**Để hiểu gì.** Tại sao câu hỏi phức tạp về tài chính không bao giờ có một "góc nhìn" duy nhất — và cách multi-query giải quyết điều đó mà không cần thêm reranker.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Cài dependencies:
   ```
   uv add vnstock langgraph
   ```
2. Tạo file `rag/multi_query.py`. Viết hàm sinh sub-queries:
   ```python
   def generate_sub_queries(query: str, n: int = 4) -> list[str]:
       prompt = f"""Sinh {n} cách hỏi khác nhau về cùng một thông tin từ câu gốc.
   Mỗi câu nhấn vào một khía cạnh: số liệu · so sánh · ngữ cảnh ngành · sự kiện gần đây.
   Trả về JSON array of strings. Câu gốc: {query}"""
       ...
   ```

**Chi tiết từng việc:**

- **Ba nguồn dữ liệu và cách gán nhãn nguồn:**

  | Nguồn | Dùng cho | Lấy từ |
  |---|---|---|
  | Corpus RAG (Qdrant) | Báo cáo phân tích, BCTC đã index | `hybrid_search()` từ bài 15 |
  | DB lịch sử (Postgres) | Giá cổ phiếu, chỉ số tài chính | bảng từ bài 12 |
  | Web search | Tin tức real-time | Gọi API lúc chạy |

  Gán nhãn nguồn trước khi đưa vào LLM:
  ```python
  def tag_source(chunk: str, metadata: dict) -> str:
      src = metadata.get("source_type", "unknown")
      if src == "news":
          return f"[TIN TỨC {metadata.get('date', '')}] {chunk}"
      elif src == "financial_report":
          return f"[BCTC {metadata.get('period', '')}] {chunk}"
      elif src == "historical_price":
          return f"[GIÁ LỊCH SỬ] {chunk}"
      return chunk
  ```

- **Luồng LangGraph — decompose → multi-retrieve → RRF-fuse → analyze → report.**

  `multi_retrieve_node` chạy song song tất cả sub-queries bằng `asyncio.gather`. Guard bắt buộc trong `analyze_node`: KHÔNG đưa lời khuyên mua, bán, hoặc nắm giữ bất kỳ cổ phiếu nào.

- **Đo: multi-query có thực sự giúp không?**

  Ghi vào `NOTES.md`:
  | Cấu hình | recall@5 | context_recall | Thời gian p95 | Chi phí LLM/câu |
  |---|---|---|---|---|
  | Single query | | | | |
  | Multi-query (N=4) + RRF | | | | |

**Xong khi.**
- [ ] LangGraph flow 5 bước chạy end-to-end trên ≥ 3 câu hỏi HPG thật, `result["report"]` có bảng số liệu và trích nguồn
- [ ] `multi_retrieve_node` chạy song song — xác nhận bằng cách đo thời gian
- [ ] Bảng 2 dòng (single vs multi-query) trong `NOTES.md`
- [ ] Guard từ chối lời khuyên đầu tư hoạt động

**Tự trả lời được.**
- Multi-query thắng bao nhiêu điểm recall? Tốn thêm bao nhiêu tiền mỗi câu?
- Với câu hỏi đơn giản ("HPG ROE 2024 là bao nhiêu?"), multi-query có giúp gì không?

**Cái bẫy.** LLM sinh sub-queries có thể drift xa câu gốc — đặc biệt với câu ngắn. Thêm ràng buộc vào prompt ("tất cả sub-queries phải hỏi về cùng công ty và cùng kỳ") và in sub-queries ra kiểm tra trước khi chạy retrieval.

---

### Bài 17 · Nhiều khách hàng dùng chung: tự tấn công chính mình 🟡
**~1.5 ngày**

**Bối cảnh.** Khi nhiều tổ chức dùng chung hệ thống, dữ liệu của công ty A không được lọt sang công ty B. Bài này không chỉ thêm cách ly mà còn tự tấn công để chứng minh nó kín: kể cả cache cũng phải cách ly.

**Để hiểu gì.** Cách ly dữ liệu phải làm **trong lúc tìm kiếm**, không lọc sau. Và chỗ rò rỉ dễ bị bỏ qua nhất không nằm ở vector DB.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Tạo fixture 2 tenant với tài liệu chồng lấp — dùng file HPG thật cho tenant A, bản copy có sửa vài con số cho tenant B.
2. Kiểm tra `tenant_id` đã có trong payload Qdrant chưa.
3. Tạo `tests/test_tenant_isolation.py` với 3 test case.

**Chi tiết từng việc:**

- **Thêm `tenant_id` vào payload mọi chunk, mọi row Postgres, mọi cache key.**

  Redis cache key — bắt buộc prefix bằng tenant:
  ```python
  cache_key = f"{tenant_id}:{hashlib.md5(query.encode()).hexdigest()}"
  ```
  Nếu key chỉ là hash của query, tenant B nhận nguyên câu trả lời của tenant A.

- **Filter `tenant_id` tại query time trong Qdrant — không filter sau:**
  ```python
  results = qdrant_client.search(
      collection_name="chunks",
      query_vector=query_embedding,
      query_filter=Filter(must=[FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))]),
      limit=20,
  )
  ```
  Thử bỏ `query_filter` đi, chạy lại — sẽ thấy kết quả lẫn lộn của cả hai tenant.

- **Viết 3 test:** `test_a_cannot_see_b_chunks`, `test_a_searching_b_content_returns_nothing`, `test_cache_is_isolated`.

- **Thử lọc sau khi search và ghi lại vấn đề** (cố tình làm sai để hiểu).

**Xong khi.**
- [ ] 3 test xanh
- [ ] Bạn đã **thử lọc sau khi search** và thấy vấn đề

**Tự trả lời được.**
- Lọc sau gây hai vấn đề gì? *(Một về bảo mật, một về chất lượng.)*
- Chỗ rò rỉ dễ bị bỏ qua nhất là gì?

**Cái bẫy.** Nếu cache key chỉ là mã băm câu hỏi, tenant B nhận nguyên câu trả lời của tenant A. Đây là lỗi thật, không phải giả định.

---

### Bài 18 · Phân loại câu hỏi + sinh SQL an toàn 🔴
**~2 ngày**

**Bối cảnh.** "Top 5 mã ROE cao nhất 2024" là câu hỏi cần tính toán trên toàn bộ dữ liệu — vector search không thể làm điều đó về nguyên tắc. Bài này xây router tự phân loại câu hỏi và SQL agent sinh truy vấn an toàn — không thể bị lừa để đọc bảng nhạy cảm hoặc sửa dữ liệu.

**Để hiểu gì.** "Chống model bịa số" là bài toán **kiến trúc**, không phải bài toán prompt.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Cài `uv add sqlglot`.
2. Tạo Postgres user chỉ đọc ngay bây giờ — **trước khi viết một dòng agent nào:**
   ```sql
   CREATE ROLE rag_readonly LOGIN PASSWORD 'readonly_pass';
   GRANT CONNECT ON DATABASE ragdb TO rag_readonly;
   GRANT USAGE ON SCHEMA public TO rag_readonly;
   GRANT SELECT ON financial_facts TO rag_readonly;
   GRANT SELECT ON companies TO rag_readonly;
   ```
   Kết nối thử bằng role này, chạy `DELETE FROM financial_facts LIMIT 1` — phải báo lỗi.

**Chi tiết từng việc:**

- **`rag/router.py`: phân câu hỏi thành 4 nhãn** (`diễn_giải`, `số_liệu`, `cả_hai`, `ngoài_phạm_vi`) bằng model nhỏ với structured output.

- **`rag/sql_agent.py`: model sinh SQL qua 4 lớp chặn:**
  1. Role chỉ đọc.
  2. Phân tích cú pháp SQL bằng `sqlglot` — chặn INSERT/UPDATE/DELETE/DROP và bảng không được phép.
  3. Giới hạn số dòng: thêm `LIMIT 1000` nếu SQL không có LIMIT.
  4. Timeout 5 giây: `conn.execute("SET statement_timeout = '5s'")`.

- **Tự tấn công với 10 prompt** (bảng không được phép, multiple statements, comment injection, unicode lookalike, subquery với bảng cấm...), ghi kết quả vào `NOTES.md`.

**Xong khi.**
- [ ] "Top 5 mã ROE cao nhất 2024" trả lời đúng, con số khớp Postgres
- [ ] 10 prompt tấn công đều bị chặn, ghi kết quả
- [ ] Router phân loại đúng ≥ 90% trên 30 câu test
- [ ] Bảng benchmark có thêm dòng "+ router/SQL", nhóm câu tra số cải thiện rõ

**Tự trả lời được.**
- Vì sao tìm kiếm ngữ nghĩa **về nguyên tắc** không thể trả lời "top 5 mã ROE cao nhất"?
- Prompt tấn công nào gần lọt nhất, lớp nào chặn nó?
- Vì sao **không được chặn bằng regex**?

**Cái bẫy.** Nếu 10 prompt của bạn đều bị chặn ngay từ lần đầu, bạn chưa tấn công đủ mạnh — thử comment SQL, unicode, truy vấn lồng.

---

## Tổng kết Chặng 3 · Tìm kiếm

### Hành trình qua 6 bài

Chặng 3 bắt đầu từ một câu hỏi đơn giản — *"tìm kiếm ngữ nghĩa có đủ không?"* — và kết thúc ở một hệ thống biết mình không đủ và biết khi nào cần dùng công cụ khác.

| Bài | Câu hỏi được trả lời | Kết quả thực đo |
|-----|---------------------|----------------|
| 14 · BM25 | Vector search bỏ sót câu nào? | 3 nhóm từ khoá thắng rõ (tra số, mã cổ phiếu, ngày tháng) |
| 15 · Fusion | Cộng điểm hai loại tìm kiếm thế nào? | hit@5 tăng từ 7 lên **13/21** (+86%) với weighted_sum |
| 16 · Reranker | Thêm tầng đọc lại có giúp không? | Reranker 11/21 — *kém hơn fusion*, chậm 4× |
| 16b · RAG-Fusion | Một truy vấn có đủ không? | recall@5 0.952 (+11%), chi phí +1 LLM call/câu |
| 17 · Multi-tenant | Cách ly dữ liệu đặt ở đâu? | Filter tại query time; cache key phải prefix tenant_id |
| 18 · Router + SQL | RAG có thể trả lời mọi câu không? | Không — nhóm "top N / xếp hạng" cần SQL, không phải RAG |

### Ba bài học cốt lõi

**1. Không có retriever nào thắng mọi câu hỏi.**
BM25 thắng tra số chính xác. Vector thắng câu ngữ nghĩa. Fusion lấy điểm mạnh của cả hai. Reranker không giúp được khi chunks đã đủ tốt. Đây là kết quả thực nghiệm, không phải lý thuyết.

**2. Kiến trúc quan trọng hơn prompt.**
Ba ví dụ trong chặng này:
- Lọc tenant *sau* retrieval → lỗ hổng bảo mật + mất chất lượng. Phải lọc *bên trong* Qdrant.
- Regex chặn SQL injection → bị qua mặt bởi comment và unicode. Phải dùng AST (sqlglot).
- Prompt cẩn thận không ngăn được model bịa số → phải dùng SQL + readonly role.

**3. Biết giới hạn của công cụ mình đang dùng.**
Vector search tìm theo *nghĩa* — không tổng hợp dữ liệu cấu trúc. Câu "top 5 mã ROE cao nhất" không phải câu khó, mà là câu sai kiến trúc nếu đưa vào vector search. Router không phải tính năng tùy chọn — đây là điều kiện để hệ thống trả lời đúng loại câu hỏi đúng.

### Pipeline cuối chặng 3

```
Câu hỏi
    │
    ▼
Router (4 nhãn)
    │
    ├─ diễn_giải ──→ BM25 + Vector ──→ Fusion ──→ Reranker ──→ LLM
    │
    ├─ số_liệu ───→ SQL agent (readonly, sqlglot, LIMIT, timeout) ──→ kết quả
    │
    ├─ cả_hai ────→ SQL + RAG ──→ LLM tổng hợp
    │
    └─ ngoài_phạm_vi ──→ từ chối
```

### Số đo tích lũy

| Cấu hình | hit@5 | Ghi chú |
|---|---|---|
| Vector đơn lẻ | 7/21 | Baseline bài 14 |
| + BM25 fusion | **13/21** | +86%, winner của chặng |
| + Reranker | 11/21 | Không cải thiện trên data này |
| + RAG-Fusion | recall@5 0.952 | Câu phức tạp, tốn thêm 1 LLM call |
| + Router/SQL | nhóm số_liệu đúng | Câu aggregation không còn bị bịa |

### Chặng tiếp theo

Chặng 4 chuyển từ *tìm kiếm* sang *hệ thống hoàn chỉnh*: API, streaming, feedback loop, và monitoring. Pipeline ở cuối chặng 3 là nền tảng — chặng 4 bọc nó thành sản phẩm có thể vận hành.
