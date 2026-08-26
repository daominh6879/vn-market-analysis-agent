# Bài 22 · LangGraph Agent — Sequential Graph

## Vấn đề cần giải quyết

### Query phân tích thị trường chung
User có thể hỏi:
- "Phân tích thị trường hôm nay"
- "Nhận định thị trường hôm nay và tuần tới"
- "VNINDEX hôm nay thế nào?"

Đây là **market-level query** (không phải ticker cụ thể).  
Agent cần phân biệt: `ticker query` vs `market query` và xử lý khác nhau.

### Flow cho market query
```
user: "phân tích thị trường hôm nay"
  collect:
    get_ohlcv("VNINDEX", days=30)   → ^VNINDEX via yfinance
    get_ohlcv("VN30", days=30)      → ^VN30 via yfinance
    search_news("VNINDEX", days=1)  → general market news (no ticker filter)
  analyze_technical:
    calculate_indicators(vnindex_df)
    calculate_indicators(vn30_df)
  assess_risk:
    analyze_market_sentiment("VNINDEX", days=3)
  synthesize:
    LLM: tổng hợp → báo cáo có trích nguồn
```

### Flow cho stock query
```
user: "phân tích HPG"
  collect:
    get_ohlcv("HPG", days=60)
    search_news("HPG", days=7)      → ticker-filtered news
  analyze_technical:
    calculate_indicators(hpg_df)
  assess_risk:
    analyze_market_sentiment("HPG", days=7)
  synthesize:
    LLM: tổng hợp
```

## Data source cho market indices (đã fix bài 21)

VCI REST API không hỗ trợ index — chỉ có stock ticker.  
Giải pháp: `_VN_INDEX_ALIASES` trong `tools/providers.py` map sang yfinance:

| Input | yfinance symbol |
|-------|----------------|
| VNINDEX, VN-INDEX, HOSE | ^VNINDEX |
| VN30 | ^VN30 |
| HNX | ^HNX |
| HNX30 | ^HNX30 |
| UPCOM | ^UPCOM |

`resolve_ticker("VNINDEX")` → `"^VNINDEX"`, `_detect_provider("VNINDEX")` → `YFinanceProvider`.  
`get_realtime_price` và `get_historical_ohlcv` đều gọi `resolve_ticker` trước khi fetch.

## Checklist Bài 22

- [ ] Implement `agents/graph.py` — LangGraph StateGraph 4 node
- [ ] Node `collect`: parallel tool calls (OHLCV + news)
- [ ] Node `analyze_technical`: gọi `calculate_indicators`
- [ ] Node `assess_risk`: gọi `analyze_market_sentiment`
- [ ] Node `synthesize`: LLM tổng hợp → structured report
- [ ] Detect market query vs stock query → khác nhau ở `collect` node
- [ ] Test: `"phân tích VNINDEX hôm nay"` → agent dùng yfinance, trả báo cáo
- [ ] Test: `"phân tích HPG"` → agent dùng VCI, filter news theo ticker

## Câu hỏi mở

- Forward-looking ("tuần tới thế nào") cần macro data ngoài OHLCV — LLM sẽ phải caveat
- yfinance `^VNINDEX` có delay ~15 phút, không realtime — cần note trong response
- Nếu OHLCV miss (yfinance không có `^VN30`), agent cần fallback gracefully
