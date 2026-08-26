# Sổ tay thực hành AI Engineer

RAG pipeline cho tài liệu tài chính tiếng Việt (HPG BCTC). 45 bài, ~16 tuần.

**Stack:** Python · Qdrant · Postgres · Redis · MinIO · Ollama · RAGAS · Dagster · LangGraph · FastAPI · Streamlit · feedparser

---

## Mục lục

| File | Nội dung | Bài |
|---|---|---|
| [00-intro.md](00-intro.md) | Giới thiệu, triết lý, cách dùng sổ tay | — |
| [01-chang-1-do-luong.md](01-chang-1-do-luong.md) | Đo lường trước khi xây | 1–5 |
| [02-chang-2-du-lieu.md](02-chang-2-du-lieu.md) | Data pipeline | 6–12, 12B, 13 |
| [03-chang-3-tim-kiem.md](03-chang-3-tim-kiem.md) | Search & retrieval | 14–18 |
| [04-chang-4-tool.md](04-chang-4-tool.md) | Tool design | 19, 19B, 20–21 |
| [05-chang-5-agent.md](05-chang-5-agent.md) | Agent architecture | 22–30 |
| [06-chang-6-van-hanh.md](06-chang-6-van-hanh.md) | Production operations | 31–36 |
| [07-chang-7-nhin-vao-ben-trong.md](07-chang-7-nhin-vao-ben-trong.md) | Observability & optimization | 37–39 |
| [08-chang-8-phong-van.md](08-chang-8-phong-van.md) | Portfolio & interview prep | 40–42 |

---

## Ba đường dữ liệu (bức tranh tổng)

```
                    ┌─────────────────────────────────────────────────┐
                    │              Câu hỏi người dùng                  │
                    └──────────────────┬──────────────────────────────┘
                                       │ router (bài 18)
              ┌────────────────────────┼────────────────────────┐
              ▼                        ▼                        ▼
   ① Số liệu (SQL)         ② Văn bản BCTC             ③ Tin tức
   financial_facts          vector search              vector search
   stock_prices             hpg_chunks (Qdrant)        news_chunks (Qdrant)
   (Postgres)               ← bài 6–13                ← bài 12B, 19B
   ← bài 12
```

Vector không đảm bảo số chính xác → ① SQL.
BCTC không có tin thời sự → ③ News.
Cả 3 đều phải có.

---

## Thứ tự ưu tiên

🔴 bắt buộc · 🟡 nên làm · ⚪ nếu còn thời gian

Luôn chạy được code đơn giản nhất trước, đo số, rồi mới thêm phức tạp. Bài 26 (so 3 kiến trúc agent) là bài dạy nhiều nhất toàn sổ tay.

---

## Thứ tự ưu tiên

🔴 bắt buộc · 🟡 nên làm · ⚪ nếu còn thời gian

Luôn chạy được code đơn giản nhất trước, đo số, rồi mới thêm phức tạp. Bài 26 (so 3 kiến trúc agent) là bài dạy nhiều nhất toàn sổ tay.
