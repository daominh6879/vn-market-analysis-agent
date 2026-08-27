# Bài 24 — Daily Market Brief: Phase 0 + Phase 3

## Mục tiêu

Xây nền cho bản tin tự động "Đọc gì trước giờ thị trường mở cửa" (như `info/26_08_2026.txt`). Plan đầy đủ tại `docs/plan-daily-market-brief.md`.

---

## Phase 0 — VNINDEX thật (bỏ VN30 proxy)

### Vấn đề cũ

`tools/providers.py` map `VNINDEX → VN30` (proxy) vì nghĩ VCI không có index thật. Hậu quả: mọi số liệu VNINDEX trong agent đều **sai** (VN30 ≈ 1.1x VNINDEX).

### Giải pháp

Thêm `SsiIndexProvider` dùng SSI iBoard API:
```
GET https://iboard-query.ssi.com.vn/v2/stock/second-chart
   ?symbol=VNINDEX&resolution=1D&from=TS&to=TS
```

Response: `{ data: { t: [...], o: [...], h: [...], l: [...], c: [...], v: [...] } }`

SSI iBoard có đủ 5 index: `VNINDEX`, `VN30`, `HNX`, `HNX30`, `UPCOM`.

### Routing mới

| Ticker | Provider cũ | Provider mới |
|--------|-------------|--------------|
| VNINDEX, VN-INDEX, HOSE | VciDirectProvider (VN30 proxy) | **SsiIndexProvider** |
| VN30, HNX, HNX30, UPCOM | VciDirectProvider | **SsiIndexProvider** |
| HPG, VCB, ... (VN stock) | VciDirectProvider | VciDirectProvider (không đổi) |
| ^GSPC, AAPL, ... | YFinanceProvider | YFinanceProvider (không đổi) |

### Artifacts mới

- `tools/providers.py`: `SsiIndexProvider`, cập nhật `resolve_ticker`, `_detect_provider`
- `tools/index_db.py`: query/upsert cho `market_index_daily`
- `ingest/fetch_index.py`: fetch + upsert 5 index vào Postgres
- `infra/migrations/007_market_index_daily.sql`
- `infra/migrations/008_securities.sql` (dùng ở Phase 1)

### Chạy migration + backfill

```bash
python -c "from core.db import run_migration; run_migration('infra/migrations/007_market_index_daily.sql')"
python -c "from core.db import run_migration; run_migration('infra/migrations/008_securities.sql')"
python ingest/fetch_index.py --days 60
```

### DoD verify

```bash
# Kiểm tra VNINDEX ngày 26/08/2026 ra 1791.41 (không phải VN30 ~1450 points)
python -c "
from tools.index_db import query_index_latest
print(query_index_latest('VNINDEX'))
"
```

---

## Phase 3 — World / Commodity / Crypto / FX / VN Gold

### Tools mới

| Tool | Nguồn | Output |
|------|--------|--------|
| `get_global_indices()` | yfinance (`^GSPC`, `^DJI`, `^IXIC`, `^VIX`, `^N225`, `^KS11`, `000001.SS`, `^HSI`) | close + %change |
| `get_commodities()` | yfinance futures (`GC=F`, `SI=F`, `CL=F`, `BZ=F`) | price + %change + unit |
| `get_crypto_prices()` | CoinGecko free API (BTC/ETH/XRP/SOL) | price_usd + 24h change + total mcap |
| `get_fx_rates()` | Vietcombank XML feed | USD/VND buy/sell/transfer |
| `get_vn_gold()` | SJC XML feed | buy/sell triệu đồng/lượng + premium vs thế giới |

### Chuyển đổi vàng

```
1 lượng = 37.5g = 1.20565 troy oz
Premium SJC (triệu đồng/lượng) = SJC_mid - (XAU_USD × USD_VND × 1.20565 / 1_000_000)
```

Ngày 26/08/2026: XAU=4624 USD, USD/VND=26125 → world price ≈ 145.5 triệu/lượng → premium SJC ≈ 2.6 triệu (khớp report).

### Artifacts mới

- `tools/global_market.py`: 5 tools
- `data/global_universe.py`: universe maps (ticker → display name)
- `data/fx_scraper.py`: VCB XML parser
- `data/gold_vn_scraper.py`: SJC XML parser
- `tools/registry.py`: đăng ký 5 tool mới
- `pipeline/assets_index.py`: 2 Dagster assets (`market_index_daily_ingest`, `global_quotes_ingest`)
- `infra/migrations/009_foreign_flows.sql`
- `infra/migrations/010_market_quotes.sql`

### Chạy test

```bash
python -m pytest tests/test_index_provider.py tests/test_global_market.py tests/test_scrapers.py -v
# 39 tests, all pass
```

---

## Kết quả

- **133 tests pass** (39 mới + 94 cũ)
- VNINDEX routing: proxy bỏ hoàn toàn → SsiIndexProvider
- Đủ data source cho 6/15 section bản tin (thế giới, vàng thế giới, vàng SJC, dầu, crypto, FX)
- VNINDEX thật + thanh khoản sàn (matched_value): SSI OHLCV chart **không expose** total exchange value → cột `matched_value` = 0 cho đến khi tìm được endpoint riêng

## Còn thiếu (matched_value)

SSI iBoard chart endpoint không có total matched value của sàn HOSE (~21.400 tỷ trong report). Cần endpoint khác. Ghi nhận vào BLOCKED.md.

## Phase tiếp theo

Phase 1: universe HOSE ~400 mã → `get_market_breadth` thật + `get_top_movers`.
