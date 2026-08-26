# Bài 19 · Ba Tool Giá Chứng Khoán

## Kết quả

**44/44 tests xanh** (không cần mạng, mock hoàn toàn).

## Cấu trúc

```
tools/
  __init__.py      — export 5 hàm public
  price.py         — PriceProvider, VnstockProvider, YFinanceProvider, 5 tool functions
  cli.py           — CLI: price, ohlcv, indicators, price-intl, ohlcv-intl, indicators-intl
tests/
  test_tools.py    — 44 tests, mock cả 2 provider
```

## Tool functions

| Hàm | Provider | Đơn vị |
|-----|----------|--------|
| `get_realtime_price(ticker)` | VnstockProvider | VND |
| `get_historical_ohlcv(ticker, days)` | VnstockProvider | VND |
| `calculate_indicators(df, currency)` | — | tag trong output |
| `get_realtime_price_intl(ticker)` | YFinanceProvider | USD |
| `get_historical_ohlcv_intl(ticker, days)` | YFinanceProvider | USD |

## Quyết định kỹ thuật

**Trả text mô tả thay vì số thô:** `calculate_indicators` trả string như `"RSI(14) = 65.3 → vùng trung tính"`. Model không phải tự interpret con số, tránh sai ngữ cảnh.

**Currency tag trong output:** `[Đơn vị: VND]` / `[Đơn vị: USD]` ở đầu mỗi output. Tránh model so sánh giá VND và USD trong cùng prompt.

**`_detect_provider(ticker)`:** ≤4 ký tự không có dấu chấm → VnstockProvider; có dấu chấm hoặc >4 ký tự → YFinanceProvider.

**pandas-ta 0.4.71b trên Python 3.14:** `df.ta.macd()` trả lại original df (không phải None) khi thiếu dữ liệu. Fix: kiểm tra `"MACD_12_26_9" not in macd_df.columns` thay vì `macd_df is None or macd_df.empty`. Tương tự RSI/SMA: dùng `float(series.iloc[-1])` trong try/except thay vì `pd.isna(series)` trực tiếp.

**pandas-ta cài `--no-deps`:** numba không hỗ trợ Python 3.14. pandas-ta hoạt động bình thường không cần numba.

## CLI commands

```bash
python -m tools.cli price FPT
python -m tools.cli ohlcv HPG 30
python -m tools.cli indicators VNM
python -m tools.cli price-intl AAPL
python -m tools.cli ohlcv-intl TSLA 30
python -m tools.cli indicators-intl NVDA
```

## Câu hỏi tự trả lời

**Mã mới lên sàn < 14 phiên:** `calculate_indicators` trả `"RSI(14): không đủ dữ liệu (cần ít nhất 14 phiên)"` + tương tự cho MACD/MA. Không crash, không NaN trong output.

**Text mô tả tốt hơn số thô:** Model không biết RSI 72 có nghĩa gì nếu không có context. Text `"quá mua"` là signal rõ ràng. Số thô NaN trong prompt khiến model bịa.

**VnstockProvider và YFinanceProvider dùng cùng interface `PriceProvider`:** Cùng 2 method `fetch_price` và `fetch_history`, cùng output schema (float và DataFrame). Nhưng khác implementation (vnstock API vs yfinance API), khác đơn vị tiền (VND vs USD). Interface đảm bảo code gọi không cần biết provider nào đang chạy.
