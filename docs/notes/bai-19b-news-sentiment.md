# Bài 19B · Tool Tin Tức và Sentiment

## Kết quả

**60/60 tests xanh** (mock Qdrant và LLM hoàn toàn, không gọi mạng).

## Cấu trúc thêm vào

```
tools/price.py   — thêm search_financial_news, analyze_market_sentiment
tools/cli.py     — thêm subcommand news, sentiment
tests/test_tools.py — thêm TestSearchFinancialNews (9 tests), TestAnalyzeMarketSentiment (7 tests)
```

## Tool functions mới

| Hàm | Nguồn dữ liệu | Output |
|-----|--------------|--------|
| `search_financial_news(ticker, days=7)` | Qdrant `news_chunks` | Text mô tả `[nguồn \| ngày] tiêu đề — tóm tắt` |
| `analyze_market_sentiment(ticker, days=7)` | few-shot LLM từ `data/sentiment_shots_vi.json` | `"Xu hướng TÍCH CỰC — lý do..."` |

## Quyết định kỹ thuật

**Dedup by URL trước khi đưa vào prompt:** Qdrant có thể trả cùng bài báo nhiều lần (nhiều chunk từ 1 URL). Dedup → model không đọc nội dung trùng lặp, không bị bias bởi bài lặp.

**Time-filter bắt buộc:** `search_news_by_text` luôn có `days` filter. Không có filter → agent trả tin cũ như tin mới. Model không tự biết tin nào cũ.

**Few-shot thay zero-shot:** Tài chính Việt Nam có jargon đặc thù (tạm ngừng lò cao, trích lập dự phòng, ESOP...). Zero-shot LLM dễ phân loại sai. Few-shot 5 ví dụ cân bằng (2 positive, 2 negative, 1 neutral) giúp model calibrate đúng ngữ cảnh.

**Không bao giờ raise ra ngoài:** Cả 2 hàm trả string mô tả khi không có tin hoặc LLM lỗi. Agent không bị crash, không loop vô hạn.

## CLI commands

```bash
python -m tools.cli news HPG --days 7
python -m tools.cli sentiment HPG
python -m tools.cli sentiment VNM --days 14
```

## Câu hỏi tự trả lời

**Vì sao dedup theo URL:** Một bài báo dài được chunk thành 3–5 đoạn trong Qdrant. Nếu không dedup, cùng 1 bài chiếm 3–5 slot trong top-5 → model chỉ đọc 1 bài thay vì 5 bài khác nhau.

**Few-shot quan trọng với tài chính tiếng Việt:** Từ `"tạm ngừng lò cao"` zero-shot có thể đánh giá neutral (thông báo kỹ thuật); few-shot với ví dụ negative giúp model hiểu đây là tín hiệu xấu. Domain-specific calibration không thể thiếu.
