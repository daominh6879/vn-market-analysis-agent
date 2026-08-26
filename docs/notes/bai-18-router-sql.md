# Bài 18 · Phân loại câu hỏi + sinh SQL an toàn

## Thiết kế

**Vấn đề cốt lõi:** "Top 5 mã ROE cao nhất 2024" — vector search không thể trả lời về mặt nguyên tắc vì nó tìm theo đoạn văn, không tổng hợp toàn bộ dữ liệu có cấu trúc.

**Giải pháp:** Router phân loại câu hỏi → SQL agent sinh truy vấn an toàn.

---

## Kiến trúc bảo mật SQL (4 lớp)

| Lớp | Cơ chế | Chặn cái gì |
|-----|--------|------------|
| 1 | Postgres role `rag_readonly` | INSERT/UPDATE/DELETE dù code bị bypass hoàn toàn |
| 2 | sqlglot AST parse | DML/DDL, bảng ngoài danh sách cho phép, unicode lookalike, comment injection |
| 3 | Inject LIMIT 1000 | Dump toàn bộ bảng |
| 4 | `SET statement_timeout = '5s'` | pg_sleep, cross-join chậm, DoS |

**Tại sao không dùng regex?** Regex trên chuỗi SQL bị qua mặt bởi:
- Comment: `SELECT/*DROP*/ticker FROM financial_facts`
- Unicode: `ＤＲＯＰ TABLE financial_facts`
- Newline trong keyword

sqlglot parse token stream → tất cả trick này vô hình với nó.

---

## Router — 4 nhãn

| Nhãn | Khi nào dùng | Đường xử lý |
|------|-------------|------------|
| `diễn_giải` | Thông tin định tính, tra cứu văn bản | hybrid_rerank |
| `số_liệu` | Tổng hợp, xếp hạng, tính toán trên toàn DB | SQL agent |
| `cả_hai` | Cần cả số từ DB + diễn giải từ văn bản | SQL + RAG, LLM tổng hợp |
| `ngoài_phạm_vi` | Ngoài phạm vi dữ liệu tài chính | Từ chối |

---

## 10 prompt tấn công — kết quả

`pytest tests/test_sql_agent.py -v` → **16/16 PASSED** (10 attack + 4 safe + 2 limit)

| # | Loại tấn công | SQL | Bị chặn bởi |
|---|--------------|-----|------------|
| 1 | Bảng không được phép | `SELECT * FROM users` | Lớp 2: table check |
| 2 | INSERT DML | `INSERT INTO financial_facts ...` | Lớp 2: stmt type |
| 3 | UPDATE DML | `UPDATE financial_facts SET value = 0` | Lớp 2: stmt type |
| 4 | DELETE DML | `DELETE FROM financial_facts` | Lớp 2: stmt type |
| 5 | DROP DDL | `DROP TABLE financial_facts` | Lớp 2: stmt type |
| 6 | TRUNCATE DDL | `TRUNCATE TABLE financial_facts` | Lớp 2: stmt type |
| 7 | Nhiều statement | `SELECT ...; DROP TABLE ...` | Lớp 2: multiple stmts |
| 8 | Comment + statement | `SELECT ...-- comment\n; DELETE ...` | Lớp 2: multiple stmts |
| 9 | Bảng hệ thống | `SELECT * FROM pg_shadow` | Lớp 2: table check |
| 10 | Subquery bảng cấm | `SELECT * FROM (SELECT * FROM pg_tables) s` | Lớp 2: table check |

Prompt gần lọt nhất: **#8 (comment + statement)** — nếu dùng regex `;\s*DELETE` thì bị qua mặt bởi newline `\n` trước `;`. sqlglot parse thành 2 statement → bị chặn ngay.

---

## Benchmark — thêm dòng router/SQL

`python evals/run.py --collection hpg_b7_structural_meta --retriever router_sql --skip-ragas`

| Cấu hình | Đoạn đúng top-5 | Ghi chú | refusal p95 |
|---|---|---|---|
| Model trần, không RAG | ~2-3/21 | Hallucinate số liệu | ~2s |
| Chỉ vector (bge-m3) | 7/21 | Baseline | ~2.5s |
| + BM25 fusion weighted_sum | 13/21 | Winner từ bài 15-16 | ~5s |
| + reranker CrossEncoder | 11/21 | Kém hơn fusion | ~20s |
| + filter metadata | *(bài 17)* | | |
| **+ router/SQL** | *(RAGAS todo)* | refusal 4/5 (0.800) | ~25s |

**Quan sát:** Golden questions (`golden_hpg.yaml`) toàn `table_lookup` và `text_interpretation` — không có câu `số_liệu` (aggregation). Router route tất cả sang `diễn_giải` → hybrid_rerank. Câu refusal q21 fail vì router classify "Doanh thu từ mảng thép xây dựng riêng lẻ" thành `diễn_giải` thay vì `ngoài_phạm_vi` → không từ chối.

**Để thấy router_sql thực sự cải thiện:** cần thêm câu `số_liệu` vào golden (ví dụ: "Kỳ nào HPG có lợi nhuận cao nhất trong DB?") — câu đó hybrid_rerank sẽ bịa, router_sql sẽ trả đúng từ SQL.

---

## Router accuracy

`python evals/eval_router.py` → **30/30 = 100%** ✅

| Tổng câu | Đúng | Accuracy | Đạt ≥ 90%? |
|----------|------|----------|-----------|
| 30 | 30 | 100% | ✅ |

**Prompt version 1** (80% = 24/30) — 6 lỗi:
- `stock_prices` table tồn tại nhưng model không biết → nhầm giá cổ phiếu thành `ngoài_phạm_vi`
- Đếm từ văn bản (nhân viên, công ty con) → nhầm thành `số_liệu`
- Dữ liệu vận hành (lò cao, sản lượng thép) → nhầm thành `diễn_giải`

**Fix**: thêm vào system prompt: (1) khai báo `stock_prices` table tồn tại, (2) phân biệt "stated in document" vs "DB aggregation", (3) liệt kê explicit dữ liệu vận hành = `ngoài_phạm_vi`.

**Prompt version 2** → 30/30 = 100%.

---

## Lệnh chạy

```bash
# 1. Tạo readonly role (chạy 1 lần, cần superuser)
psql -U postgres -d ragdb -f infra/migrations/004_readonly_role.sql

# 2. Security + unit tests (không cần DB/LLM)
uv run pytest tests/test_sql_agent.py -v
# → 16/16 PASSED

# 3. Integration tests — cần DB + rag_readonly role
uv run pytest tests/test_sql_agent.py -m integration -v
# → 4/4 PASSED
# Xác nhận: DELETE bị từ chối, execute_safe() trả rows, pg_sleep bị kill <8s

# 4. Router accuracy eval (cần LLM)
uv run python evals/eval_router.py
# → 30/30 = 100%

# 5. Eval với router_sql retriever
uv run python evals/run.py --collection hpg_b7_structural_meta --retriever router_sql --skip-ragas
```

---

## Câu trả lời — tự kiểm tra

**Tại sao vector search không thể trả lời "top 5 mã ROE cao nhất"?**
Vector search tìm đoạn văn gần nghĩa nhất với query — nó trả về tối đa `top_k` đoạn văn từ cùng hoặc khác bộ tài liệu. Không có cơ chế nào để nó đọc TẤT CẢ giá trị `roe` trong DB rồi sắp xếp và lấy top 5. Đây là bài toán aggregation, không phải retrieval.

**Prompt tấn công nào gần lọt nhất?**
Attack #8 — comment injection + statement chaining:
```sql
SELECT ticker FROM financial_facts -- safe query
; DELETE FROM financial_facts
```
Regex `;\s*DELETE` bị qua mặt vì `\n` nằm giữa comment và `;`. sqlglot parse ra 2 statement → chặn ở lớp 2 (multiple stmts check). Lớp 1 (readonly role) cũng chặn được dù lớp 2 fail — đây là lý do cần defense-in-depth.

**Tại sao không dùng regex?**
Regex khớp chuỗi văn bản — dễ bị qua mặt bằng comment SQL (`-- comment\n; DELETE`), unicode lookalike (`ＤＲＯＰ`), whitespace tricks. sqlglot phân tích token stream (AST) — mọi trick trên trở nên vô hình với nó. Test #8 chứng minh: `SELECT ticker -- safe\n; DELETE FROM financial_facts` sẽ qua regex `;\s*DELETE` nhưng sqlglot parse ra 2 statement → bị chặn.
