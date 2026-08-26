# Plan — "Đọc gì trước giờ thị trường mở cửa" (Daily Market Brief)

Mục tiêu: sinh tự động bản tin trong `info/*.txt` mỗi sáng ~7:30, chỉ từ data pipeline + LLM, không copy tay.

Nguồn tham chiếu: `info/24_08_2026.txt`, `info/25_08_2026.txtt`, `info/26_08_2026.txt`

---

## 1. Phân rã report → yêu cầu data

Mỗi section của bản tin cần một nguồn cụ thể. Bảng dưới là contract: có gì rồi, thiếu gì.

| # | Section trong report | Data cần | Trạng thái hiện tại | Thiếu |
|---|---|---|---|---|
| 1 | 🌍 Thị trường thế giới | S&P500, Dow, Nasdaq, VIX, Nikkei, KOSPI, Shanghai — close + %change | `YFinanceProvider` (tools/providers.py) đã lấy được ticker Mỹ | Universe map ticker chỉ số (`^GSPC`, `^DJI`, `^IXIC`, `^VIX`, `^N225`, `^KS11`, `000001.SS`); tool `get_global_indices` |
| 2 | 💛 Vàng thế giới | XAU/oz (`GC=F`) — giá + %change + so đỉnh gần nhất | không có | tool `get_commodities` |
| 3 | 💛 Vàng SJC trong nước | mua/bán triệu đ/lượng + chênh lệch vs thế giới | không có | scraper SJC/BTMC + bảng `gold_prices` + công thức quy đổi oz↔lượng |
| 4 | 🛢 Dầu | WTI (`CL=F`), Brent (`BZ=F`) | không có | gộp vào `get_commodities` |
| 5 | ₿ Crypto | BTC/ETH/XRP/SOL + total market cap | không có | CoinGecko client + tool `get_crypto_prices` |
| 6 | 💵 Tỷ giá | tỷ giá trung tâm SBV, VCB mua/bán, delta ngày | không có | scraper SBV + Vietcombank + bảng `fx_rates` |
| 7 | 1️⃣ VN-Index đóng cửa | điểm, ±điểm, %; **thanh khoản khớp lệnh HoSE (tỷ đ)** | OHLCV có nhưng VNINDEX đang dùng **VN30 proxy** (commit 2b73b8c) → điểm số **sai** | VNINDEX thật + giá trị khớp lệnh sàn |
| 8 | 2️⃣ Độ rộng thị trường | số mã tăng/giảm **toàn sàn HOSE** (246/439) | `get_market_breadth` chỉ VN30 (30 mã) | mở rộng universe HOSE ~400 mã |
| 9 | 2️⃣ Trụ đỡ / dẫn dắt thanh khoản | top giá trị giao dịch (VIC 2.300 tỷ), đóng góp điểm index | không có | ranking theo `close*volume`; contribution cần free-float cap |
| 10 | 3️⃣ Khối ngoại mua/bán ròng | net value toàn TT + top mua / top bán | không có | endpoint foreign trading + bảng `foreign_flows` |
| 11 | 4️⃣–5️⃣ Tin & sự kiện | tin FTSE, Quốc hội, GDKHQ, cổ tức | `data/news_scraper.py` (CafeF/VnExpress RSS) + `tavily_news.py` + `news_articles` table | lịch sự kiện quyền (GDKHQ/cổ tức) là data có cấu trúc, RSS không đủ → cần `corporate_events` |
| 12 | 🎯 Nhận định — kỹ thuật | MA50, MA200/EMA200, ADX, Ichimoku Kumo, mẫu nến (Doji), thanh khoản vs TB 20 tuần | `calculate_indicators` chỉ RSI/MACD/MA20/MA50 | thêm MA200, EMA200, ADX, Ichimoku, candle pattern, avg volume 20 tuần |
| 13 | 🎯 Nhận định — quan điểm CTCK | TPS/VCBS/Yuanta target, hỗ trợ/kháng cự | không có | extract từ news bằng LLM → `broker_views` |
| 14 | 🎯 Nhóm ngành đáng chú ý | performance theo ngành | không có | sector map + tool `get_sector_performance` |
| 15 | Toàn bộ bản tin | 1 file .txt đúng format, có emoji, có disclaimer | `agents/graph.py` chỉ báo cáo 1 mã | graph mới `market_brief` + template + scheduler |

**Kết luận gap:** phần VN (7–10, 14) là nặng nhất — cần data sàn thật, không phải VN30 proxy. Phần world/commodity/crypto/FX (1–6) rẻ, chủ yếu là thêm provider.

---

## 2. Nguyên tắc thiết kế (giữ nguyên convention project)

- Mọi tool trả `ToolResult` (`tools/result.py`), không raise → đăng ký trong `tools/registry.py`.
- Mọi fetch đi qua **provider abstraction** (`tools/providers.py`), không import vnstock trong tool.
- **DB-first**: pipeline Dagster ghi Postgres → tool query Postgres, live API chỉ là fallback (giống `get_market_breadth` hiện tại).
- LLM chỉ qua `create_client()` (`llm/factory.py`), provider = DeepSeek từ `.env`.
- Migration đánh số tiếp: `007_` trở đi.
- Identifier + column: **English**. Nội dung report: tiếng Việt.

---

## 3. Kế hoạch theo phase

### Phase 0 — Sửa nền tảng VN data (bắt buộc trước mọi thứ)

Không làm phase này thì số VN-Index trong report **sai**, cả bản tin mất giá trị.

1. `tools/providers.py`: thêm `VciIndexProvider` (hoặc mở rộng `VciDirectProvider`) lấy VNINDEX/HNX/UPCOM thật — điểm số, ±, %, giá trị khớp lệnh sàn. Nếu VCI không expose → thử endpoint SSI iBoard / TCBS, ghi vào `BLOCKED.md` nếu cả hai fail.
2. Migration `007_market_index_daily.sql`: `(index_code, date, open, high, low, close, change_pts, change_pct, matched_value, matched_volume, foreign_net)`.
3. `ingest/fetch_index.py` + Dagster asset `market_index_daily`.
4. Bỏ VN30-proxy fallback trong `get_market_performance` khi có index thật.

**DoD:** query `market_index_daily` ngày 26/08 ra đúng `1791.41 / +2.63 / +0.15% / ~21.400 tỷ`.

### Phase 1 — Universe HOSE + breadth thật

1. `data/hose_universe.py`: danh sách mã HOSE + `sector` + `index_membership` (VN30/VN100) + `market_cap`. Seed từ `data/known_tickers_seed.py`, refresh định kỳ.
2. Migration `008_securities.sql`: bảng master `securities (ticker, exchange, sector, industry, listed_shares, free_float)`.
3. `ingest/fetch_ohlcv.py`: mở rộng universe từ 30 → toàn HOSE, dùng `fetch_batch_latest` chia batch (rate-limit + retry).
4. `get_market_breadth()`: bỏ hardcode `_VN30_CONSTITUENTS`, nhận `universe: str = "HOSE"`.
5. Tool mới `get_top_movers(by="value"|"pct", limit=5)` → trụ đỡ / dẫn dắt thanh khoản.

**DoD:** breadth ra 2 con số tăng/giảm cỡ trăm mã, `get_top_movers(by="value")` trả VIC đứng đầu ngày 25/08.

### Phase 2 — Khối ngoại + sector

1. Migration `009_foreign_flows.sql`: `(ticker, date, buy_value, sell_value, net_value)`.
2. `ingest/fetch_foreign_flows.py` + asset. Nguồn: endpoint foreign của VCI/TCBS; nếu không có → scrape CafeF trang "GD khối ngoại" (ghi rõ nguồn trong NOTES.md).
3. Tool `get_foreign_flows(days=1)` → net toàn TT + top mua/top bán.
4. Tool `get_sector_performance(period="day")` — JOIN `ohlcv_daily × securities.sector`, weighted theo market cap.

**DoD:** ngày 25/08 ra "mua ròng ~188 tỷ, tâm điểm HPG/VIC/VPB".

### Phase 3 — World / commodity / crypto / FX

Rẻ nhất, làm song song được với Phase 1–2.

1. `tools/global_market.py`:
   - `get_global_indices()` — yfinance batch, universe trong `data/global_universe.py`.
   - `get_commodities()` — `GC=F`, `CL=F`, `BZ=F`.
   - `get_crypto_prices()` — CoinGecko `/simple/price` + `/global` (total mcap). Không cần API key ở free tier.
2. `data/gold_vn_scraper.py` — SJC/BTMC; `data/fx_scraper.py` — SBV tỷ giá trung tâm + Vietcombank.
3. Migration `010_market_quotes.sql`: bảng chung `(symbol, asset_class, date, value, change_abs, change_pct, unit, source)` — dùng cho cả vàng/dầu/crypto/FX, tránh 4 bảng gần giống nhau.
4. Assets Dagster: `global_quotes_daily`, `vn_gold_fx_daily`.
5. Đăng ký cả 5 tool vào `TOOL_REGISTRY` (`cost_hint: "low"`, `timeout: 15`).

**DoD:** một lệnh CLI in ra đủ block 🌍 💛 ₿ 💵 với số khớp report.

### Phase 4 — Indicator + nhận định

1. `calculate_indicators`: thêm `MA200`, `EMA200`, `ADX(14)`, Ichimoku (`ta.ichimoku` → vị trí giá vs Kumo), so sánh volume phiên vs TB 20 tuần (100 phiên).
2. `detect_candle_pattern(df)` — Doji / Marubozu / Hammer, dùng `pandas_ta.cdl_pattern` nếu TA-Lib có, không thì rule thuần.
3. `tools/levels.py`: `find_support_resistance(df)` — swing high/low + mốc tâm lý tròn (1.800).

**DoD:** với data 26/08, output nêu được "trên MA50/MA200, thanh khoản thấp hơn TB 20 tuần ~24%".

### Phase 5 — Broker views + corporate events (LLM extraction)

1. Migration `011_corporate_events.sql`: `(ticker, event_type, ex_date, record_date, ratio, note)`; scrape lịch sự kiện CafeF/Vietstock.
2. Migration `012_broker_views.sql`: `(broker, ticker_or_index, published_at, stance, target, support, resistance, source_url)`.
3. `ingest/extract_broker_views.py` — dùng lại pattern `ingest/extract_facts.py`: LLM + Pydantic schema + validate, chạy trên `news_articles` mới.

**DoD:** ngày 25/08 extract được TPS→1.900, VCBS→hỗ trợ 1.760, Yuanta→1.820.

### Phase 6 — Report agent + template + schedule

1. `agents/market_brief_graph.py` — LangGraph, node fan-out song song rồi synthesize:

```
                ┌─ collect_world      (indices, commodities, crypto, fx)
 start ────────>├─ collect_vn         (index, breadth, movers, foreign, sector)
                ├─ collect_news       (news_articles + corporate_events + broker_views)
                └─> analyze_technical ─> compose_outlook ─> render_report ─> END
```

   - `state.py` riêng, chỉ giữ **path/dict nhỏ**, không giữ DataFrame (giống `agents/state.py` hiện tại).
   - `compose_outlook`: 1 LLM call duy nhất cho phần 🎯 NHẬN ĐỊNH.
   - `render_report`: **template Python thuần** cho phần số liệu (không để LLM đọc/viết lại số → tránh hallucinate), LLM chỉ viết văn phần nhận định.
2. Template `agents/templates/market_brief.txt` — copy đúng khung 3 file trong `info/`, kể cả emoji, disclaimer, hashtag.
3. `agents/run_brief.py --date YYYY-MM-DD --out info/{DD_MM_YYYY}.txt`.
4. Dagster schedule 07:15 ICT: ingest world/VN/news → build brief.
5. Guardrail: field nào thiếu data → in `(không có dữ liệu)`, **không** để LLM tự điền. Log field thiếu.

**DoD:** `python agents/run_brief.py --date 2026-08-26` ra file gần khớp `info/26_08_2026.txt`; mọi số truy được về Postgres.

### Phase 7 — Eval + monitoring

1. `evals/eval_market_brief.py`: so file sinh ra vs file người viết trong `info/` — chấm 3 trục: (a) độ chính xác số liệu (exact match có tolerance), (b) độ phủ section (15/15), (c) chất lượng phần nhận định (LLM-as-judge).
2. Dashboard ToolResult + tool-call error (đã ghi trong memory là việc của bài 26) — thêm tab freshness data: bảng nào stale > 1 ngày.

---

## 4. Thứ tự thực thi đề xuất

```
Phase 0  ──> Phase 1 ──> Phase 2 ──┐
Phase 3  (song song, độc lập)      ├──> Phase 6 ──> Phase 7
Phase 4  (song song, chỉ cần P0)   │
Phase 5  (song song, cần news có sẵn)
```

Ưu tiên nếu cần bản tin chạy được sớm: **Phase 0 + 3 + 6** → ra bản tin đủ block thế giới/vàng/dầu/crypto/FX + VN-Index đúng, các block VN chi tiết in `(không có dữ liệu)`, rồi bồi dần Phase 1/2/4/5.

---

## 5. Rủi ro / điểm cần quyết trước khi code

1. **Nguồn VNINDEX thật** — chưa verify VCI có endpoint index. Đây là blocker của cả plan. Cần thử trước khi code Phase 0.
2. **Foreign flows** — có thể chỉ scrape được, dễ vỡ. Chấp nhận degrade.
3. **Contribution điểm index** (VIC đóng góp bao nhiêu điểm) cần free-float market cap — nếu không lấy được thì chỉ báo cáo top thanh khoản, bỏ contribution.
4. **Rate limit** khi mở universe 30 → 400 mã: cần batch + backoff, đo lại thời gian chạy asset.
5. **Số liệu vs LLM** — quyết định cứng: số liệu render bằng template, LLM không được sinh số. Không thương lượng.

---

## 6. Tổng kết artifact mới

**Migrations:** `007_market_index_daily` · `008_securities` · `009_foreign_flows` · `010_market_quotes` · `011_corporate_events` · `012_broker_views`

**Ingest:** `fetch_index.py` · `fetch_foreign_flows.py` · `fetch_global.py` · `extract_broker_views.py` · `fetch_corporate_events.py`

**Data/scrapers:** `hose_universe.py` · `global_universe.py` · `gold_vn_scraper.py` · `fx_scraper.py`

**Tools:** `get_global_indices` · `get_commodities` · `get_crypto_prices` · `get_fx_rates` · `get_vn_gold` · `get_top_movers` · `get_foreign_flows` · `get_sector_performance` · `find_support_resistance` · `detect_candle_pattern` (+ mở rộng `calculate_indicators`, `get_market_breadth`, `get_market_performance`)

**Agent:** `agents/market_brief_graph.py` · `agents/run_brief.py` · `agents/templates/market_brief.txt`

**Eval:** `evals/eval_market_brief.py`
