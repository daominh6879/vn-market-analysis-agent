# CHẶNG 2 · Dữ liệu (tuần 2–4)

> Nửa đầu của "end-to-end". Bài 12 là bài dạy nhiều nhất về **thiết kế** trong cả sổ tay.

---

### Bài 6 · Parse 1 file PDF và đọ bằng mắt 🔴
**~2 ngày**

**Bối cảnh.** Toàn bộ hệ thống ăn dữ liệu từ PDF tài chính. Bài này là lần đầu bạn nhìn thẳng vào dữ liệu đó — không qua trung gian. Nếu parse hỏng bảng số liệu mà bạn không biết, mọi câu trả lời phía sau đều sai mà không có dấu hiệu lỗi nào.

**Để hiểu gì.** Dữ liệu của bạn hỏng ở đâu — trước khi xây gì lên trên nó. Và vì sao "RAG cho báo cáo tài chính" khó hơn "RAG cho blog".

**Làm gì.**

**Bắt đầu từ đâu:**
1. Chọn 1 file PDF HPG từ `evals/docs/HGP/` — ưu tiên báo cáo thường niên có bảng KQKD và CĐKT. Mở file đó bằng trình xem PDF để biết mình sẽ đối chiếu cái gì.
2. Tạo file `data/parse.py` và cài 3 thư viện:
   ```
   uv add pymupdf4llm unstructured llamaparse
   ```
3. Chạy thử parse nhanh để kiểm tra setup đúng chưa:
   ```python
   import pymupdf4llm
   md = pymupdf4llm.to_markdown("evals/docs/HGP/ten_file.pdf")
   print(md[:2000])
   ```
   Nếu thấy text xuất hiện là setup ổn. Nếu ra chuỗi rỗng hoặc toàn ký tự lạ thì file đó là PDF scan — ghi nhận lại.

**Chi tiết từng việc:**

- **Viết `data/parse.py` với hàm `parse_pdf(path) -> ParsedDoc`.** `ParsedDoc` là dataclass/Pydantic model chứa `content: str` (markdown) và `metadata: dict` (tên file, số trang, ngày parse). Mỗi công cụ có một hàm wrapper riêng:
  ```python
  def parse_with_pymupdf(path: str) -> ParsedDoc: ...
  def parse_with_unstructured(path: str) -> ParsedDoc: ...
  def parse_with_llamaparse(path: str) -> ParsedDoc: ...
  ```

- **Thử 3 công cụ trên cùng 1 file HPG.** Chạy cả 3, xuất ra 3 file markdown riêng:
  ```
  outputs/hpg_pymupdf.md
  outputs/hpg_unstructured.md
  outputs/hpg_llamaparse.md
  ```
  Lệnh chạy:
  ```bash
  python data/parse.py evals/docs/HGP/ten_file.pdf --all-tools
  ```

- **Mở song song PDF gốc và 3 file markdown, đối chiếu bằng mắt.** Tập trung vào:
  - Bảng Kết quả kinh doanh (Doanh thu, Lợi nhuận gộp, LNST)
  - Bảng Cân đối kế toán (Tổng tài sản, Nợ phải trả, Vốn chủ)
  - Chú ý: tiêu đề cột có bị mất không, các ô số có bị lệch sang cột khác không, footnote có bị nhập vào thân bảng không.

- **Ghi vào `NOTES.md`** — mục "Bài 6 — Chất lượng parse":
  - Công cụ nào giữ được cấu trúc bảng
  - Công cụ nào mất tiêu đề cột
  - Công cụ nào trộn text từ nhiều cột thành một chuỗi liên tục
  - Ít nhất 5 vấn đề quan sát được, càng cụ thể càng tốt

**Xong khi.**
- [ ] Chọn 1 công cụ và ghi lý do kèm **ví dụ cụ thể** của cái bị hỏng ở 2 công cụ kia
- [ ] Liệt kê ≥ 5 vấn đề cụ thể bạn thấy

**Tự trả lời được.**
- Cụ thể cái gì hỏng ở mỗi công cụ? *(Nêu ví dụ, không nói chung chung.)*
- Nếu một dòng bảng cân đối kế toán lệch khỏi tiêu đề cột, hệ thống trả lời sai thế nào?

**Cái bẫy.** Bài dễ chìm nhất trong 42 bài. **Timebox cứng 2 ngày.** Bảng tài chính trong PDF không bao giờ parse hoàn hảo — chấp nhận ~70%, ghi phần còn lại vào `BLOCKED.md`, đi tiếp. Chất lượng sẽ được vá ở bài 12 bằng **đường dữ liệu khác**, không bằng cách parse giỏi hơn.

---

### Bài 7 · Chunking: thử 3 cách và **đo** 🔴
**~1.5 ngày**

**Bối cảnh.** Sau parse, văn bản phải được cắt nhỏ để tìm kiếm. Chunk quá nhỏ thì tìm chính xác nhưng thiếu ngữ cảnh cho model trả lời; quá lớn thì ngược lại. Đây là lần đầu bạn dùng bộ đo từ bài 4 để ra quyết định kỹ thuật thay vì đoán.

**Để hiểu gì.** Lần đầu bạn dùng bộ đo để ra quyết định kỹ thuật — cảm giác này cần lặp lại suốt 35 bài còn lại. Và đánh đổi cốt lõi: chunk nhỏ tìm chính xác hơn, chunk lớn đủ ngữ cảnh hơn.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Tạo file `rag/chunking.py`:
   ```
   uv add langchain-text-splitters tiktoken
   ```
2. Viết hàm đầu tiên — cắt cố định — và kiểm tra output ngay:
   ```python
   def chunk_fixed(text: str, size: int = 512, overlap: int = 64) -> list[str]:
       from langchain_text_splitters import RecursiveCharacterTextSplitter
       splitter = RecursiveCharacterTextSplitter(chunk_size=size, chunk_overlap=overlap)
       return splitter.split_text(text)

   chunks = chunk_fixed(open("outputs/hpg_pymupdf.md").read())
   print(f"Số chunk: {len(chunks)}, chunk đầu tiên:\n{chunks[0]}")
   ```
3. Index collection này vào Qdrant và chạy eval ngay để có baseline chunking:
   ```bash
   python evals/run.py --collection hpg_fixed_512
   ```

**Chi tiết từng việc:**

- **Viết 3 hàm chunking trong `rag/chunking.py`:**

  *Cắt cố định có chồng lấn* (`size=512`, `overlap=64`):
  ```python
  def chunk_fixed(text: str, size=512, overlap=64) -> list[str]: ...
  ```

  *Cắt theo cấu trúc đoạn* — tách tại `\n\n`, giữ nguyên heading markdown, không phá vỡ giữa câu:
  ```python
  def chunk_structural(text: str, max_size=800) -> list[str]: ...
  ```

  *Cắt hai tầng* — index đoạn nhỏ ~400 token để tìm kiếm, nhưng khi retrieval trả về thì mở rộng ra cả mục chứa đoạn đó (parent chunk ~1200 token) để đưa vào ngữ cảnh model:
  ```python
  def chunk_hierarchical(text: str) -> list[tuple[str, str]]:
      # trả về list (child_chunk, parent_chunk)
      ...
  ```

- **Mỗi cách index vào collection riêng trong Qdrant:**
  ```
  hpg_fixed_512
  hpg_structural
  hpg_hierarchical
  ```
  Chạy `make eval` (hoặc `python evals/run.py`) cho từng collection, ghi số vào `NOTES.md`.

- **Thí nghiệm riêng: gắn metadata vào đầu chunk** trước khi tạo vector:
  ```python
  def prepend_metadata(chunk: str, meta: dict) -> str:
      return f"[HPG | {meta['year']} | {meta['report_type']}]\n{chunk}"
  ```
  Thử bật/tắt với cùng một chiến lược chunking và đo riêng. Thường kết quả của bước này làm người ta ngạc nhiên hơn cả việc đổi chiến lược cắt.

- **Ghi bảng 3 dòng vào `NOTES.md`** — mục "Bài 7 — So sánh chunking":

  | Cách cắt | context_recall | answer_relevancy | Ghi chú |
  |---|---|---|---|
  | fixed_512 | ... | ... | ... |
  | structural | ... | ... | ... |
  | hierarchical | ... | ... | ... |

**Xong khi.**
- [ ] Bảng 3 dòng trong `NOTES.md`
- [ ] Chênh lệch **lớn hơn ngưỡng nhiễu ở bài 5** — nếu không thì chưa kết luận được gì

**Tự trả lời được.**
- Cách nào thắng, thắng bao nhiêu, và có lớn hơn nhiễu không?
- Việc gắn metadata đóng góp bao nhiêu so với đổi chiến lược cắt? *(Kết quả có thể làm bạn ngạc nhiên.)*
- Vì sao cắt hai tầng không phải "thoả hiệp" mà là "tách hai mục tiêu"?

**Cái bẫy.** Nếu chênh lệch nhỏ hơn nhiễu, cần **thêm câu vào bộ câu hỏi chuẩn**, không phải chọn bừa cái cao nhất.

---

### Bài 8 · Chọn embedding model bằng số 🔴
**~1 ngày**

**Bối cảnh.** Embedding model biến văn bản thành vector — nó quyết định "giống nhau về nghĩa" có nghĩa là gì trong hệ thống của bạn. Bảng xếp hạng công khai đo trên dữ liệu tiếng Anh chung chung; dữ liệu tài chính tiếng Việt của bạn có thể cho thứ tự hoàn toàn khác.

**Để hiểu gì.** Vì sao không thể tin bảng xếp hạng công khai, và các đặc tính của embedding model ảnh hưởng tới hệ thống thế nào.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Cài các thư viện cần thiết:
   ```
   uv add sentence-transformers openai
   ```
2. Viết hàm wrapper thống nhất để dễ đổi model:
   ```python
   def get_embedder(model_name: str):
       if model_name.startswith("text-embedding"):
           return OpenAIEmbedder(model_name)
       return SentenceTransformerEmbedder(model_name)
   ```
3. Kiểm tra model đầu tiên (`keepitreal/vietnamese-sbert`) hoạt động chưa bằng cách nhúng một câu thử:
   ```python
   emb = get_embedder("keepitreal/vietnamese-sbert")
   vec = emb.embed("Doanh thu thuần của HPG năm 2024")
   print(f"Chiều vector: {len(vec)}")  # Nếu ra số là chạy được
   ```

**Chi tiết từng việc:**

- **Thử 3 model theo thứ tự:**
  - `keepitreal/vietnamese-sbert` — model tiếng Việt, chạy local hoàn toàn
  - `BAAI/bge-m3` — đa ngôn ngữ, cần kiểm tra tiền tố `query:` / `passage:`
  - `text-embedding-3-small` — OpenAI API, tốn tiền nhưng nhanh

- **Với mỗi model:** index lại toàn bộ dữ liệu HPG vào collection riêng, chạy eval, đo thêm chỉ số retrieval:
  ```python
  def recall_at_k(results: list, gold_page: int, k: int) -> bool:
      return any(r.metadata["page"] == gold_page for r in results[:k])
  ```
  Chạy: `python evals/run.py --collection hpg_vnsbert --recall-at 5 20`

- **Ghi bảng 3 model × 6 cột vào `NOTES.md`:**

  | Model | recall@5 | recall@20 | context_recall | Chiều vector | Thời gian index | Local? |
  |---|---|---|---|---|---|---|

- **Kiểm tra 2 thứ dễ bỏ sót:**

  *Giới hạn độ dài đầu vào* — mỗi model có max token khác nhau (ví dụ `vietnamese-sbert` thường 256, `bge-m3` là 8192). Đo chunk size thực tế của bạn:
  ```python
  import tiktoken
  enc = tiktoken.get_encoding("cl100k_base")
  token_counts = [len(enc.encode(c)) for c in chunks]
  print(f"Max: {max(token_counts)}, P95: {sorted(token_counts)[int(len(token_counts)*0.95)]}")
  ```
  Chunk size phải nhỏ hơn giới hạn model. Nếu không — chunk bị cắt ngầm, không có lỗi nào báo.

  *Tiền tố query/passage* cho `bge-m3`:
  ```python
  query_vec = model.encode("query: Doanh thu HPG 2024 là bao nhiêu?")
  passage_vec = model.encode("passage: Doanh thu thuần năm 2024 đạt 165.000 tỷ đồng")
  ```

**Xong khi.**
- [ ] Bảng 3 model × 6 cột
- [ ] Biết giới hạn độ dài của model đã chọn, và chunk size nhỏ hơn nó

**Tự trả lời được.**
- Model nào thắng trên **dữ liệu của bạn**, có trùng thứ tự bảng xếp hạng công khai không?
- Nếu chunk vượt giới hạn độ dài, chuyện gì xảy ra? *(Gợi ý: không có lỗi nào báo.)*
- Vì sao "nằm trong 20 đoạn" quan trọng hơn "nằm trong 5 đoạn" ở giai đoạn này?

**Cái bẫy.** Nhiều model cần tiền tố `query:` / `passage:`. Quên là mất chất lượng mà không có dấu hiệu gì.

---

### Bài 9 · Chạy 3 lần, index phải giống hệt 🔴
**~1 ngày**

**Bối cảnh.** Pipeline index sẽ chạy lại nhiều lần — khi có file mới, khi đổi embedding model, khi debug. Nếu mỗi lần chạy lại nhân đôi số chunk trong Qdrant, retrieval bị nhiễu và kết quả eval dao động lạ. Bài này đảm bảo chạy lại bao nhiêu lần cũng an toàn.

**Để hiểu gì.** Khác biệt giữa "script" và "pipeline". Và một khái niệm bạn dùng suốt sự nghiệp: **idempotent** — chạy nhiều lần cho cùng kết quả.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Tạo file `data/contracts.py` để định nghĩa schema Pydantic cho từng tầng:
   ```python
   from pydantic import BaseModel, validator

   class ParsedDoc(BaseModel):
       doc_id: str       # sha256 của nội dung file
       content: str
       source_path: str
       parsed_at: str

       @validator("content")
       def content_not_empty(cls, v):
           assert len(v.strip()) > 0, "Content rỗng"
           return v
   ```
2. Tính `doc_id` từ hash file:
   ```python
   import hashlib
   def compute_doc_id(path: str) -> str:
       return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]
   ```
3. Chạy pipeline một lần, ghi số vector trong Qdrant, chạy lần 2, so sánh — phải bằng nhau.

**Chi tiết từng việc:**

- **Tính ID xác định từ nội dung, không từ tên file hay thời gian:**
  ```python
  doc_id = sha256(file_content)[:16]
  chunk_id = f"{doc_id}_{chunk_index:04d}"
  ```

- **Ghi vào Qdrant bằng upsert theo id, không insert.** Trước khi ghi chunk mới của một tài liệu, xoá chunk cũ của tài liệu đó:
  ```python
  qdrant_client.delete(
      collection_name="hpg_chunks",
      points_selector=Filter(must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))])
  )
  qdrant_client.upsert(
      collection_name="hpg_chunks",
      points=[PointStruct(id=chunk_id, vector=vec, payload=meta)]
  )
  ```

- **Tách 3 tầng, không cho nhảy tầng:**
  - Tầng 1: file PDF gốc lưu MinIO — không bao giờ sửa
  - Tầng 2: markdown đã parse lưu Postgres (`parsed_docs` table)
  - Tầng 3: chunk + vector lưu Qdrant

- **Viết `tests/test_idempotent.py`:**
  ```python
  def test_idempotent():
      run_full_pipeline("evals/docs/HGP/")
      count_1 = get_vector_count()
      ids_1 = get_all_ids()

      run_full_pipeline("evals/docs/HGP/")  # Chạy lần 2
      count_2 = get_vector_count()
      ids_2 = get_all_ids()

      assert count_1 == count_2
      assert ids_1 == ids_2
  ```

**Xong khi.**
- [ ] Test idempotency xanh
- [ ] Đổi 1 ký tự trong PDF → `doc_id` đổi → coi là tài liệu mới
- [ ] Chunk rỗng bị chặn bởi schema

**Tự trả lời được.**
- Nếu không idempotent, chuyện gì xảy ra khi một message trong queue bị xử lý hai lần?
- Vì sao tách tầng "đã parse" ra riêng tiết kiệm rất nhiều thời gian và tiền khi đổi chiến lược chunking?

**Cái bẫy.** Chunk rỗng hoặc chỉ có khoảng trắng rất dễ lọt. Chúng làm nhiễu kết quả mà không gây lỗi nào.

---

### Bài 10 · Xoá tài liệu và đối chiếu 🟡
**~1 ngày**

**Bối cảnh.** Trong thực tế, báo cáo tài chính bị thu hồi, cập nhật, hoặc xoá nhầm. Nếu không có cơ chế xoá đúng và đối chiếu định kỳ, hệ thống có thể trả lời từ tài liệu đã thu hồi mà không ai biết. Với sản phẩm tài chính, đây là yêu cầu kiểm toán, không phải tính năng tuỳ chọn.

**Để hiểu gì.** Rằng index **sẽ** lệch khỏi nguồn ở quy mô lớn — worker chết giữa việc, deploy lỗi, xoá không hoàn tất. Và cách phát hiện.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Tạo bảng `documents` trong Postgres:
   ```sql
   CREATE TABLE documents (
       doc_id       TEXT PRIMARY KEY,
       status       TEXT NOT NULL DEFAULT 'active',
       source_uri   TEXT NOT NULL,
       indexed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
       deleted_at   TIMESTAMPTZ
   );
   ```
2. Tạo file `data/delete.py` với hàm `soft_delete(doc_id)`.
3. Chạy thử: xoá 1 file HPG, tìm kiếm câu liên quan, xác nhận không còn chunk nào.

**Chi tiết từng việc:**

- **Bảng `documents` là nguồn sự thật.** Không xoá record — chỉ cập nhật `status` và `deleted_at`. Lý do: kiểm toán cần biết "file này từng tồn tại, bị xoá lúc mấy giờ, ai xoá".

- **Viết `data/delete.py`** với 2 bước atomic:
  ```python
  def soft_delete(doc_id: str):
      with db.transaction():
          db.execute("UPDATE documents SET status='deleted', deleted_at=NOW() WHERE doc_id=%s", [doc_id])
          qdrant_client.delete(
              collection_name="hpg_chunks",
              points_selector=Filter(must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))])
          )
  ```

- **Viết `data/reconcile.py`** — so sánh 2 nguồn sự thật:
  ```python
  def reconcile(fix: bool = False):
      pg_ids = set(db.fetchall("SELECT doc_id FROM documents WHERE status='active'"))
      qdrant_ids = set(scroll_all_doc_ids("hpg_chunks"))

      orphan_in_qdrant = qdrant_ids - pg_ids
      missing_in_qdrant = pg_ids - qdrant_ids

      print(f"Orphan in Qdrant: {len(orphan_in_qdrant)}")
      print(f"Missing in Qdrant: {len(missing_in_qdrant)}")

      if fix:
          for doc_id in orphan_in_qdrant:
              qdrant_client.delete(...)
  ```

- **Tự tay gây lệch để kiểm tra:** dùng Qdrant Dashboard xoá thủ công vài vector, sau đó chạy reconcile và xác nhận nó phát hiện đúng.

**Xong khi.**
- [ ] Xoá 1 file → search câu liên quan → không còn chunk nào của nó
- [ ] **Tự tay gây lệch** (xoá vài vector thủ công) → `reconcile` phát hiện đúng và sửa được

**Tự trả lời được.**
- Vì sao **không xoá record** khỏi Postgres khi xoá tài liệu?
- Nếu không có job đối chiếu, làm sao bạn biết mình đang trả lời từ dữ liệu nào?

**Cái bẫy.** Với sản phẩm tài chính bạn phải trả lời được "tài liệu này từng tồn tại và bị thu hồi ngày nào" — đó là yêu cầu kiểm toán, không phải tuỳ chọn.

---

### Bài 11 · Thả file rác vào pipeline 🟡
**~1 ngày**

**Bối cảnh.** File rác nguy hiểm không phải vì bị từ chối — mà vì được index thành công dưới dạng rác. PDF scan không có lớp text sẽ parse ra vài dòng vô nghĩa, lọt vào vector DB, rồi được retrieval kéo ra và đưa vào ngữ cảnh model. Hệ thống trả lời sai không có dấu hiệu lỗi nào.

**Để hiểu gì.** Rủi ro thật không phải file bị từ chối — mà là **file được index thành rác mà không ai biết**, rồi agent trả lời sai từ nó.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Chuẩn bị 4 file độc hại trước khi viết code: PDF scan (chụp ảnh), file 500 trang, file tiếng Anh, file không phải báo cáo tài chính.
2. Tạo `data/quality.py`. Viết check đầu tiên — tỉ lệ ký tự đọc được:
   ```python
   def check_char_ratio(text: str) -> float:
       readable = sum(1 for c in text if c.isprintable() and not c.isspace())
       return readable / max(len(text), 1)
   ```

**Chi tiết từng việc:**

- **Viết `data/quality.py`** với các hàm kiểm tra:
  ```python
  def check_char_ratio(text: str) -> float:
      """Tỉ lệ ký tự đọc được. PDF scan thường < 0.3"""

  def check_chars_per_page(text: str, num_pages: int) -> float:
      """Số ký tự trung bình mỗi trang. < 100 là nghi ngờ scan"""

  def check_duplicate_ratio(chunks: list[str]) -> float:
      """Tỉ lệ chunk trùng lặp — > 0.2 là bất thường"""
  ```

- **Kết quả kiểm tra → quyết định:**
  ```python
  def assess_quality(parsed: ParsedDoc) -> QualityResult:
      if parsed.chars_per_page < 100:
          return QualityResult(passed=False, reason="Nghi ngờ PDF scan — < 100 ký tự/trang")
      if parsed.char_ratio < 0.3:
          return QualityResult(passed=False, reason=f"Quá nhiều ký tự không đọc được")
      return QualityResult(passed=True)
  ```
  File không qua: chuyển vào thư mục cách ly trên MinIO (`quarantine/`), ghi lý do vào Postgres, **không vào index**.

- **Xem danh sách cách ly:**
  ```bash
  python data/quality.py --list-quarantine
  ```

- **Chạy cả 4 file độc qua pipeline và kiểm tra từng cái** bị chặn đúng không.

**Xong khi.**
- [ ] Cả 4 file bị chặn hoặc xử lý đúng, **không file nào lọt vào index dưới dạng rác**
- [ ] Xem được danh sách cách ly kèm lý do

**Tự trả lời được.**
- PDF scan "parse thành công" nhưng cho ra vài dòng vô nghĩa — check nào bắt được nó?
- Vì sao đây là ca nguy hiểm nhất trong 4 file?

**Cái bẫy.** Không có check "số ký tự mỗi trang" thì PDF scan lọt, và bạn sẽ debug rất lâu ở tầng retrieval trong khi lỗi ở tầng nạp dữ liệu.

---

### Bài 12 · Hai đường dữ liệu — số **không** vào vector DB 🔴
**~2 ngày**

**Bối cảnh.** Vector search tìm ngữ nghĩa tốt nhưng không thể đảm bảo số tài chính chính xác — model có thể bịa hoặc nhầm đơn vị. Bài này tách số liệu (doanh thu, ROE, P/E...) ra một con đường riêng qua SQL, nơi số là số thật từ DB, không đi qua model lần nào. Đây là bài thiết kế quan trọng nhất trong cả 42 bài.

**Để hiểu gì.** Bài dạy nhiều nhất về **thiết kế** trong cả 42 bài. Bài học: khi một công cụ liên tục thất bại ở một loại dữ liệu, câu trả lời không phải tinh chỉnh công cụ đó, mà là **dùng công cụ khác cho loại dữ liệu đó**.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Tạo bảng `financial_facts` trong Postgres:
   ```sql
   CREATE TABLE financial_facts (
       id              SERIAL PRIMARY KEY,
       ticker          TEXT NOT NULL,
       ky              TEXT NOT NULL,
       loai_bao_cao    TEXT NOT NULL,
       ma_chi_tieu     TEXT NOT NULL,
       gia_tri         NUMERIC NOT NULL,
       don_vi          TEXT NOT NULL,
       nguon_file      TEXT NOT NULL,
       nguon_trang     INT NOT NULL
   );
   ```
2. Viết `data/extract_facts.py` với function trích xuất dùng structured output.
3. Chạy thử trên 1 đoạn markdown từ file HPG đã parse, kiểm tra con số với PDF gốc.

**Chi tiết từng việc:**

- **Trích xuất số liệu dùng structured output** — không để model trả lời tự do. Dùng tool use của Claude hoặc `response_format` của OpenAI để ép đầu ra đúng schema.

- **Ba kiểm tra nghiệp vụ sau khi trích:**
  ```python
  def validate_facts(facts: list[FinancialFact]) -> list[ValidationError]:
      # 1. Tổng tài sản = Nợ phải trả + Vốn chủ sở hữu (chênh lệch < 1%)
      # 2. Không trộn riêng lẻ với hợp nhất trong cùng một tập facts
      # 3. Chỉ tiêu nhất quán giữa các kỳ (không thay đổi > 500%)
  ```

- **Giá cổ phiếu vào bảng riêng, dùng giá đã điều chỉnh** (`close_adj`). Nguồn dữ liệu: `vnstock` (`uv add vnstock`).

- **Kiểm tra `SELECT` ra đúng con số.** Mở PDF gốc tại trang `nguon_trang`, đối chiếu bằng mắt. Phải khớp.

- **Cố tình đưa số sai vào để test validator:**
  ```python
  bad_fact = FinancialFact(ma_chi_tieu="tong_tai_san", gia_tri=999999, ...)
  errors = validate_facts([bad_fact, ...])
  assert any(e.type == "balance_sheet_mismatch" for e in errors)
  ```

**Xong khi.**
- [ ] `SELECT` lấy doanh thu thuần 2024 của HPG, **con số khớp PDF gốc**
- [ ] Cố tình đưa vào một số sai → validator bắt được
- [ ] Ghi số: trích được bao nhiêu % chỉ tiêu, bao nhiêu % bị đánh dấu

**Tự trả lời được.**
- Vì sao *"số không đi qua model, chỉ có câu SQL đi qua"* là câu nói về **độ tin cậy**, không phải hiệu năng?
- Nếu dùng giá chưa điều chỉnh, chỉ báo kỹ thuật ở bài 19 sai **ở đâu**?
- Đường dữ liệu này giải quyết được phần nào của vấn đề parse ở bài 6?

**Cái bẫy.** Giá chưa điều chỉnh làm chỉ báo sai **đúng tại ngày chia cổ tức** — nhìn vào code không thấy gì sai. Loại lỗi này dạy bạn: hiểu dữ liệu quan trọng hơn hiểu code.

---

### Bài 12B · Tin tức tài chính — cào, lưu, index, sentiment 🔴
**~2.5 ngày**

**Bối cảnh.** Bài 12 tách số liệu ra SQL. Nhưng câu "Tại sao HPG giảm hôm nay?" hay "Có tin gì ảnh hưởng đến giá thép tuần này?" không thể trả lời từ BCTC — cần tin tức thời sự. Bài này thêm **đường dữ liệu thứ ba**: tin tức CafeF/VnExpress, lưu Postgres, index Qdrant collection riêng, kèm Financial PhraseBank làm eval cho sentiment.

**Để hiểu gì.** RAG tài chính đầy đủ cần 3 đường song song: (1) số liệu → SQL, (2) văn bản báo cáo → vector search, (3) tin tức realtime → vector search có time-filter. Thiếu đường 3, agent không giải thích được biến động giá.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Tạo migration và xác nhận bảng tồn tại:
   ```bash
   psql $DATABASE_URL -f infra/migrations/005_news_articles.sql
   psql $DATABASE_URL -c "\d news_articles"
   ```
2. Lấy danh sách ticker HOSE/HNX từ vnstock — cần trước khi viết scraper:
   ```python
   from vnstock import Listing
   symbols = Listing().all_symbols()["symbol"].tolist()
   # Lưu vào data/known_tickers.txt, một mã mỗi dòng
   ```
3. Kiểm tra RSS hoạt động:
   ```python
   import feedparser
   feed = feedparser.parse("https://cafef.vn/rss/chung-khoan.rss")
   print(len(feed.entries), feed.entries[0].title)
   ```
   Thấy title tiếng Việt là OK. Nếu 403/empty → dùng VnExpress RSS trước.

**Chi tiết từng việc:**

- **`infra/migrations/005_news_articles.sql`** — đã tạo. Kiểm tra lại: URL là UNIQUE, `indexed_at` mặc định NULL (incremental logic dựa vào đây).

- **Tạo Qdrant collection `news_chunks` trước khi upsert lần đầu.** Dùng cùng dimension với `hpg_chunks` (phải cùng embedding model):
  ```python
  from qdrant_client.models import Distance, VectorParams

  if not qdrant_client.collection_exists("news_chunks"):
      qdrant_client.create_collection(
          collection_name="news_chunks",
          vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
      )
  ```
  `EMBED_DIM` lấy từ config — cùng giá trị với `hpg_chunks`, nếu khác thì 2 collection không thể dùng cùng search logic.

- **Viết `data/news_scraper.py`** — RSS từ 2 nguồn:
  - `https://cafef.vn/rss/chung-khoan.rss`
  - `https://vnexpress.net/rss/kinh-doanh.rss`

  **Chuẩn hóa `published_at` về UTC ngay khi parse** — không lưu string raw:
  ```python
  from datetime import datetime, timezone

  def parse_published(entry) -> str:
      if hasattr(entry, "published_parsed") and entry.published_parsed:
          dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
          return dt.isoformat()
      # fallback: thời điểm scrape
      return datetime.now(timezone.utc).isoformat()
  ```

  **Clean HTML trước khi lưu** — RSS body thường có `<p>`, `&amp;`, `&nbsp;`:
  ```python
  import html, re

  def clean_html(text: str) -> str:
      text = html.unescape(text)
      text = re.sub(r"<[^>]+>", " ", text)
      text = re.sub(r"\s+", " ", text).strip()
      return text
  ```

  **Tối thiểu body 80 ký tự** — một số RSS entry chỉ có tiêu đề lặp lại trong summary:
  ```python
  body = clean_html(e.get("summary", ""))
  if len(body) < 80:
      body = clean_html(e.title)   # dùng title thay thế
  ```

  **Cấu trúc hàm chính:**
  ```python
  RSS_SOURCES = [
      ("https://cafef.vn/rss/chung-khoan.rss", "cafef"),
      ("https://vnexpress.net/rss/kinh-doanh.rss", "vnexpress"),
  ]

  def scrape_rss(url: str, source: str) -> list[dict]:
      try:
          feed = feedparser.parse(url)
      except Exception as e:
          print(f"[WARN] scrape_rss failed for {source}: {e}")
          return []
      results = []
      for e in feed.entries:
          body = clean_html(e.get("summary", ""))
          if len(body) < 80:
              body = clean_html(e.title)
          results.append({
              "url": e.link,
              "title": clean_html(e.title),
              "body": body,
              "source": source,
              "published_at": parse_published(e),
              "tickers": extract_tickers(e.title),
          })
      return results
  ```

- **Trích xuất ticker từ tiêu đề — pattern matching, không LLM:**
  ```python
  import re
  from pathlib import Path

  KNOWN_TICKERS: set[str] = set(
      Path("data/known_tickers.txt").read_text().splitlines()
  )
  TICKER_RE = re.compile(r'\b([A-Z]{2,4})\b')

  def extract_tickers(title: str) -> list[str]:
      return [m for m in TICKER_RE.findall(title) if m in KNOWN_TICKERS]
  ```
  `data/known_tickers.txt` lấy từ bước khởi động (vnstock `Listing().all_symbols()`).

- **Upsert vào Postgres + UPDATE `indexed_at` sau khi Qdrant thành công:**
  ```python
  def save_article(conn, article: dict) -> bool:
      """Trả True nếu mới, False nếu đã tồn tại."""
      cur = conn.cursor()
      cur.execute("""
          INSERT INTO news_articles (url, title, body, source, published_at, tickers)
          VALUES (%s, %s, %s, %s, %s, %s)
          ON CONFLICT (url) DO NOTHING
          RETURNING id
      """, (article["url"], article["title"], article["body"],
            article["source"], article["published_at"], article["tickers"]))
      return cur.fetchone() is not None

  def mark_indexed(conn, url: str):
      conn.execute(
          "UPDATE news_articles SET indexed_at = NOW() WHERE url = %s", (url,)
      )
  ```
  Gọi `mark_indexed` **sau khi** Qdrant upsert thành công. Nếu Qdrant fail, `indexed_at` giữ NULL → lần chạy sau tự retry.

- **Index vào `news_chunks`** — embed `title + body`, dedup bằng `url_hash`:
  ```python
  import hashlib, uuid

  def url_to_uuid(url: str) -> str:
      h = hashlib.md5(url.encode()).hexdigest()
      return str(uuid.UUID(h))

  def index_article(article: dict, embed_fn) -> None:
      vec = embed_fn(f"{article['title']}\n{article['body']}")
      qdrant_client.upsert(
          collection_name="news_chunks",
          points=[PointStruct(
              id=url_to_uuid(article["url"]),
              vector=vec,
              payload={
                  "source":       article["source"],
                  "published_at": article["published_at"],
                  "tickers":      article["tickers"],
                  "url":          article["url"],
                  "title":        article["title"],
              }
          )]
      )
  ```

  **Tìm kiếm có time-filter:**
  ```python
  from datetime import datetime, timedelta, timezone

  def search_news(query_vec, days: int, limit: int = 5) -> list:
      cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
      return qdrant_client.search(
          collection_name="news_chunks",
          query_vector=query_vec,
          query_filter=Filter(must=[
              FieldCondition(key="published_at", range=DatetimeRange(gte=cutoff))
          ]),
          limit=limit,
      )
  ```
  **Không cần BM25 cho `news_chunks`** — tin tức ngắn, thường truy vấn theo chủ đề + thời gian, time-filter + vector là đủ. BM25 chỉ giúp khi cần khớp chính xác từ khoá như tên mã trong câu dài; với tiêu đề ngắn, vector đã đủ.

- **Data retention — tránh tăng vô hạn:** Xóa bài cũ hơn 90 ngày định kỳ (chạy trong Dagster):
  ```python
  def purge_old_articles(conn, days_to_keep: int = 90):
      conn.execute("""
          DELETE FROM news_articles
          WHERE published_at < NOW() - INTERVAL '%s days'
      """, (days_to_keep,))
  ```
  Đồng thời xóa điểm Qdrant tương ứng bằng filter `published_at < cutoff`. Không cần xóa ngay lập tức — chạy weekly là đủ.

- **Financial PhraseBank — eval sentiment:**

  ⚠️ **Đúng tên dataset**: `financial_phrasebank` (không phải `ProsusAI/finbert` — cái đó là model).
  ```python
  from datasets import load_dataset
  ds = load_dataset("financial_phrasebank", "sentences_allagree")
  # 2264 câu tiếng Anh, nhãn: 0=negative, 1=neutral, 2=positive
  ```
  Viết `evals/eval_sentiment.py` — feed 200 câu ngẫu nhiên qua LLM zero-shot, đo accuracy. Ngưỡng chấp nhận: ≥ 0.70.

  **Tạo `data/sentiment_shots_vi.json`** — 30 câu tiếng Việt, 10 mỗi nhãn, kiểm tra tay:
  ```json
  [
    {
      "text": "Doanh thu thuần của HPG tăng 23% so với cùng kỳ, vượt kỳ vọng thị trường.",
      "label": "positive"
    },
    {
      "text": "Lợi nhuận sau thuế giảm mạnh do chi phí nguyên vật liệu tăng đột biến.",
      "label": "negative"
    },
    {
      "text": "Ban lãnh đạo HPG cho biết sẽ họp vào tháng tới để thảo luận kế hoạch năm sau.",
      "label": "neutral"
    }
  ]
  ```
  30 câu phải **đa dạng**: tin KQKD, tin ngành, tin vĩ mô, tin nhân sự. Không toàn câu về HPG.

- **Thêm 5 câu hỏi tin tức vào `evals/golden_hpg.yaml`** — để đo đường 3 có hoạt động không:
  ```yaml
  - question: "Có tin tức gì về HPG trong 30 ngày gần nhất?"
    answer: ""          # không có đáp án cố định — chỉ kiểm tra sources_used có [TIN TỨC]
    group: "news"
    check: "sources_used_contains_news"
  - question: "Ngành thép Việt Nam đang đối mặt với thách thức gì?"
    answer: ""
    group: "news"
    check: "sources_used_contains_news"
  ```
  Cập nhật `evals/run.py` để nhận dạng `check: sources_used_contains_news` và đánh giá đúng.

- **Tích hợp vào Bài 13 (Dagster)** — thêm 2 asset và 1 job purge:
  ```python
  @asset(retry_policy=RetryPolicy(max_retries=3, delay=60))
  def news_raw(): ...          # scrape RSS → upsert news_articles (save_article)

  @asset(deps=[news_raw])
  def news_indexed(): ...      # index_article + mark_indexed cho URL chưa indexed

  @asset
  def news_purge(): ...        # purge_old_articles(days_to_keep=90)
  ```
  Lịch `news_raw` + `news_indexed`: mỗi 6 giờ. Lịch `news_purge`: weekly.

- **Wiring `news_chunks` vào pipeline chính** — đây là bước nối cuối cùng. Nếu bỏ qua, `news_chunks` có dữ liệu nhưng pipeline `rag/rag_fusion_graph.py` vẫn không dùng đến. Sửa `make_multi_retrieve_node`:
  ```python
  async def _gather():
      tasks = [
          _retrieve_one(sq, "hpg_chunks", embed_model, bm25_retriever)
          for sq in sub_queries
      ]
      tasks += [
          _retrieve_news(sq, days=30)
          for sq in sub_queries[:2]   # top 2, tránh over-fetch
      ]
      return await asyncio.gather(*tasks)
  ```
  `_retrieve_news(query, days)` — query `news_chunks` có `DatetimeRange` filter, tag `[TIN TỨC YYYY-MM-DD]` qua `tag_source`.

  **Kiểm chứng**: chạy pipeline câu liên quan tin tức → `state["sources_used"]` phải có entry prefix `[TIN TỨC`. Nếu không → wiring chưa hoạt động.

**Xong khi.**
- [ ] Postgres có ≥ 100 bài, không bài nào duplicate, `published_at` đều là UTC
- [ ] Qdrant `news_chunks` tồn tại, tìm được với time-filter 7 ngày
- [ ] `mark_indexed` hoạt động — chạy lại Dagster job, số bài `indexed_at IS NULL` về 0
- [ ] `eval_sentiment.py` chạy trên `financial_phrasebank`, accuracy ≥ 0.70, ghi vào `NOTES.md`
- [ ] `data/sentiment_shots_vi.json` — 30 câu, 10 mỗi nhãn, đã kiểm tra tay
- [ ] 5 câu tin tức trong `golden_hpg.yaml`, `run.py` pass với check `sources_used_contains_news`
- [ ] **Pipeline chính có `[TIN TỨC]` trong `sources_used`** khi hỏi câu liên quan tin tức

**Tự trả lời được.**
- Vì sao URL là dedup key tốt hơn title?
- Nếu không chuẩn hóa UTC, time-filter sai thế nào?
- Vì sao `indexed_at` phải update **sau** Qdrant upsert thành công, không phải trước?
- Vì sao không cần BM25 cho `news_chunks` nhưng lại cần cho `hpg_chunks`?
- Vì sao 30 câu tiếng Việt riêng thay vì dùng thẳng `financial_phrasebank` tiếng Anh?
- Vì sao `news_chunks` cần wiring riêng vào `multi_retrieve_node` thay vì chỉ có tool `search_financial_news` ở bài 19B?

**Cái bẫy.**
1. `e.get("published", "")` lưu raw string — timezone CafeF và VnExpress khác nhau → `DatetimeRange` filter sai âm thầm. Phải dùng `entry.published_parsed`.
2. **Build `news_chunks` xong nhưng quên wire vào pipeline** — tool `search_financial_news` (bài 19B) cho agent gọi thủ công; wiring `_retrieve_news` vào `multi_retrieve_node` mới làm tin tức tự động xuất hiện trong mọi phân tích.
3. `ProsusAI/finbert` là model, không phải dataset. Dataset đúng: `load_dataset("financial_phrasebank", "sentences_allagree")`.
4. Không check `collection_exists` trước khi upsert → crash `KeyError` lần đầu chạy trên môi trường mới.

---

### Bài 13 · Biến script thành pipeline tự chạy 🟡
**~2 ngày**

**Bối cảnh.** Bài 6–12 là các script chạy tay. Thực tế, file mới xuất hiện liên tục và một bước có thể thất bại giữa chừng (parse xong nhưng chưa embed). Bài này dùng Dagster để biến các script rời rạc thành pipeline có retry, có lịch, có thể audit được từng chunk đến từ file nào qua bước nào.

**Để hiểu gì.** Vì sao cần orchestrator thay vì cron + script: retry theo từng bước, chạy lại một khoảng thời gian, và truy vết nguồn gốc dữ liệu.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Cài Dagster:
   ```
   uv add dagster dagster-webserver dagster-postgres dagster-aws
   ```
2. Tạo file `pipeline/assets.py`, định nghĩa asset đầu tiên `raw_pdf`, chạy Dagster UI:
   ```bash
   dagster dev -f pipeline/assets.py
   # Mở http://localhost:3000
   ```
3. Materialize thử asset `raw_pdf` từ UI, xem log trước khi viết thêm.

**Chi tiết từng việc:**

- **Chuyển bài 6–12 thành Dagster assets:**
  ```python
  @asset
  def raw_pdf(): ...           # Quét MinIO

  @asset(deps=[raw_pdf])
  def parsed_doc(): ...        # parse_pdf() + quality check

  @asset(deps=[parsed_doc])
  def chunks(): ...            # chunking

  @asset(deps=[chunks])
  def embeddings(): ...        # embedding, upsert vào Qdrant (idempotent)

  @asset(deps=[parsed_doc])
  def financial_facts(): ...   # extract_facts
  ```
  Hai nhánh `embeddings` và `financial_facts` chạy song song từ `parsed_doc`.

- **Cấu hình retry và lịch chạy:**
  ```python
  @asset(retry_policy=RetryPolicy(max_retries=3, delay=30))
  def embeddings(): ...

  daily_schedule = ScheduleDefinition(job=ingestion_job, cron_schedule="0 6 * * *")
  ```

- **Sensor phát hiện file mới trong MinIO.**

- **Phân biệt 2 chế độ chạy:** `full_rebuild` | `incremental`.

- **Viết `data/RUNBOOK.md`** — tài liệu vận hành khi đổi embedding model:
  - Tạo collection mới → full_rebuild → kiểm tra eval → đổi alias → xoá cũ sau 24h.
  - **Chạy thật runbook này ít nhất 1 lần**, không chỉ viết ra.

- **Kiểm tra audit trail:** Từ Dagster UI, click vào một chunk cụ thể trong Qdrant, truy ngược về asset run nào tạo ra nó, từ file HPG nào. Nếu không làm được thì pipeline chưa xong.

**Xong khi.**
- [ ] **Thả 3 PDF mới vào MinIO bằng tay** → 5 phút sau truy vấn được, không chạm gì nữa
- [ ] Mở Dagster UI, click vào một chunk, thấy nó đến từ file nào qua bước nào
- [ ] Đã **thử chạy runbook index lại một lần**, không chỉ viết ra

**Tự trả lời được.**
- Một bước fail — chỉ bước đó retry, hay chạy lại từ đầu? Với file 200 trang thì khác biệt là gì?
- Vì sao đổi embedding model bắt buộc phải index lại toàn bộ?

**Cái bẫy.** Đừng để Dagster chạy embedding không giới hạn số việc song song — nó sẽ tự bắn lỗi quá tải vào chính API embedding của bạn.
