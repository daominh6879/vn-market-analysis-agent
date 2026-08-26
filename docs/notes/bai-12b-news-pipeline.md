# Bài 12B — News Pipeline

## Trạng thái
- [x] Bước 1: `known_tickers.txt` đã generate — 1524 tickers từ vnstock
- [x] Bước 2: RSS scrape lần đầu — ghi số bài bên dưới
- [x] Bước 3: Qdrant `news_chunks` collection đã tạo (dim=1024, bge-m3)
- [x] Bước 4: Indexer chạy, `indexed_at IS NULL` = 0
- [ ] Bước 5: Pipeline test — `sources_used` chứa `TIN TỨC` (cần Ollama local)
- [x] Bước 6: `eval_sentiment.py` chạy, accuracy=0.965 ≥ 0.70 ✅
- [x] Bước 7: `eval_sentiment.py --vi` chạy, accuracy=0.900
- [ ] Bước 8: `run.py` news check pass (cần full RAG pipeline)
- [x] Bước 9: `eval_news_quality.py` — 9/9 pass ✅

## Kết quả thực tế

### Scrape (2026-08-26)
| Source | Bài fetch | Bài mới | Tổng trong DB |
|--------|-----------|---------|---------------|
| cafef  | 50        | 14      | 64            |
| vnexpress | 60     | 6       | 66            |
| vneconomy | 50     | 6       | 56            |
| tinnhanhchungkhoan | 50 | 32 | 82            |
| **Tổng** | **210** | **58** | **268**      |

### Indexing (2026-08-26)
- Tổng bài trong `news_articles`: 268
- Số điểm trong `news_chunks`: 268 (dim=1024, bge-m3)
- `indexed_at IS NULL` sau index: 0 (idempotent ✅)

### Pipeline check
```
sources_used = [cần chạy demo_rag_fusion.py khi Ollama local available]
```
Câu hỏi test: "Có tin tức gì liên quan đến HPG trong 30 ngày gần nhất?"

### Sentiment eval (Financial PhraseBank) — 2026-08-26
- Sample size: 200
- Accuracy: **0.965** (193/200)
- Per-class: positive=1.000 (n=50), neutral=0.943 (n=122), negative=1.000 (n=28)
- Pass/Fail (ngưỡng 0.70): ✅ PASS
- Ghi chú: max_tokens cần ≥500 với deepseek-v4-flash (reasoning model)

### Sentiment eval (Vietnamese) — 2026-08-26
- N: 30 câu (10 positive, 10 negative, 10 neutral)
- Accuracy: **0.900** (27/30)
- 3 sai: TCB ESOP, GAS MOU, VHM bàn giao → model classify positive thay vì neutral
- Pass/Fail: ✅ (no hard threshold cho Vietnamese)

### News quality eval (eval_news_quality.py) — 2026-08-26

| Test | Mô tả | Kết quả |
|------|-------|---------|
| A1 | Temporal conflict — 2 bài, kết luận ngược, 25 ngày cách nhau | ✅ PASS |
| A2 | Source conflict — CafeF vs VnExpress cùng ngày | ✅ PASS |
| A3 | Stale-as-current — bài 20 ngày, không được gọi "gần đây" | ✅ PASS |
| A4 | Dedup — title gần giống, chỉ giữ 1 | ✅ PASS |
| B1 | Empty news warn — collection missing, không crash | ✅ PASS |
| B3b | Model isolation — DEFAULT_EMBED_MODEL từ news_index | ✅ PASS |
| B4 | Time filter — days=0 trả 0 kết quả | ✅ PASS |
| C1 | Source attribution — phân biệt BCTC vs tin tức | ✅ PASS |
| C3 | Additive value — có news tốt hơn chỉ BCTC | ✅ PASS |

## Ghi chú kỹ thuật

<!-- Điền sau khi chạy -->
