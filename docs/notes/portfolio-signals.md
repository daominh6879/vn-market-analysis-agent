# Portfolio API + model LightGBM tín hiệu mua/bán

> **Đã gỡ (2026-10-19):** model LightGBM, `signals/{features,labels,train,score,monitor}.py`, `pipeline/assets_signals.py`, migration 040 và 2 bảng `signal_models`/`trade_signals` (migration 042) đã bị xoá; tư vấn nay do AI phán đoán, xem [portfolio-advisor.md](portfolio-advisor.md). File này chỉ còn là lịch sử nghiên cứu.

Kế hoạch gốc: `docs/plans/portfolio-api.md`. File này ghi kết quả đã chạy thật (Phase 1 + Phase 2 + Phase 3).

## Phase 1 — Sổ giao dịch & vị thế

- Migration `infra/migrations/039_portfolio.sql` → bảng `portfolio_transactions` (vị thế suy ra từ giao dịch, không có bảng holdings).
- Code: `portfolio/ledger.py`, router ẩn `api/portfolio.py` (`include_in_schema=False`, không có trong `/openapi.json`).
- `price` trong giao dịch là VND/cp; `ohlcv_daily` lưu nghìn đồng nên khi định giá nhân 1000.
- Kiểm tra thật (API + Postgres, user thử đã xoá sau khi test): nạp 100M, mua HPG 1000 @27.000 và VCB 100 @60.000 →
  cash 66.950.500 (= 100.000.000 − 27.040.500 − 6.009.000), NAV 93.540.500. Bán VCB cùng ngày → 400 (T+2); mua 150 cp → 400 (lô 100);
  mua vượt tiền → 400; xoá khoản nạp đang tài trợ lệnh mua → 400.
- Test: `pytest tests/test_portfolio_ledger.py` → 10 passed.

## Phase 0 — Dữ liệu (đã backfill)

Trước backfill: `foreign_flows` chỉ từ 2025-08-05, `market_index_daily` từ 2025-02-04, và nhiều mã (vd HPG) chỉ có `ohlcv_daily` từ 2025-09.
Provider (Fireant range / SSI) trả được lịch sử sâu, nên đã backfill bằng chính các hàm ingest hiện có (script một lần, không phải job định kỳ):

| Bảng | Sau backfill |
|---|---|
| `ohlcv_daily` | 2021-01-04 → 2026-10-19, 596.074 dòng, 433 mã (gồm VNINDEX, VN30) |
| `foreign_flows` | 2021-01-04 → 2026-10-19, 595.899 dòng, 431 mã |
| `market_index_daily` | VNINDEX/VN30 từ 2019-03-12; HNX/UPCOM từ 2021-07-27 (1900 dòng VNINDEX) |
| `financial_facts` quý (`Qn/YYYY`) | Q1/2020 → Q2/2026, ~406–428 mã mỗi quý (trước đó chỉ từ Q1/2024) |

- HNX30 bỏ qua (chậm do tra foreign-net từng thành viên, model không dùng).
- Kiểm tra đơn vị: 280 dòng `close >= 1000` chỉ thuộc VNINDEX/VN30 (không nằm trong `securities`), không ảnh hưởng. Chỉ 1 bước nhảy giá >50% ở cổ phiếu (CTR 2022-02-16).
- `ratio_*` trong `financial_facts` chỉ có theo **năm** (2018–2025) → feature định giá/ROE dùng báo cáo năm trễ 100 ngày; tăng trưởng doanh thu/LNST dùng YoY theo **quý** trễ 45 ngày.
- P/E, P/B: lấy `ratio_pe/pb` cuối năm × (giá hôm nay / giá cuối năm) vì EPS không điều chỉnh split còn giá thì đã điều chỉnh.

## Phase 2 — Model LightGBM

Code: `signals/{features,labels,train,score}.py`, `pipeline/assets_signals.py`, migration `040_trade_signals.sql`.

- 24 feature số + `sector` (categorical). Label: lợi suất vượt VNINDEX sau 20 phiên; >+5% BUY, <−5% SELL, còn lại HOLD.
- Hai model: classifier 3 lớp (xác suất) + regressor (kỳ vọng excess return). Artifact: `signals/artifacts/{version}_clf.txt`, `_reg.txt`, `{version}.json`.
- Walk-forward mở rộng, purge 20 phiên, 9 fold ×125 phiên (test từ 2022-08-05 đến 2026-09-21), early stopping trên 15% cuối của train.
- Dữ liệu train: 585.717 dòng, 431 mã, nhãn 28,9% SELL / 47,0% HOLD / 24,1% BUY.
- Test: `pytest tests/test_signals_features.py` → 8 passed (không look-ahead: feature tại D không đổi khi xoá dữ liệu sau D; fundamentals chỉ hiện sau ngày công bố).

### Kết quả (gộp 9 fold, 1027 ngày, 425.625 dòng test) — `signal_models.metrics`

| | Top-decile mean excess / 20 phiên | Hit rate (top-decile) | BUY precision |
|---|---|---|---|
| Model (prob_up − prob_down) | −0,12% | 44,1% | 33,4% (BUY rate 2,3%) |
| Regressor | −0,25% | 43,5% | 37,8% |
| Baseline momentum 20 ngày | −0,75% | 41,7% | 24,9% |
| Trung bình toàn universe | −0,42% | – | – |

- Model `lgbm_20261019_1801` được promote vì top-decile (−0,12%) > momentum (−0,75%) theo luật của plan.
- **Nhận xét thật:** edge nhỏ và không ổn định. Top-decile vượt trung bình universe khoảng +0,3%/20 phiên, nhưng vẫn âm so với VNINDEX và hit rate < 50%. Trong 9 fold, model thắng momentum ở 5 fold (1, 3, 4, 7, 8) và kém trung bình universe ở 4 fold (1, 2, 5, 9).
- Early stopping dừng rất sớm (6–94 vòng/fold; model cuối 23 vòng clf, 21 vòng reg) → tín hiệu học được yếu. Gain lớn nhất (clf): `vol_20`, `pb`, `sector`, `dist_high_52w`, `dist_low_52w`, `ret_60`.
- Chưa thử: chuẩn hoá rank theo ngày cho feature, đổi nhãn sang so với median universe. Không tinh chỉnh theo kết quả test để tránh overfit vào walk-forward.

### Chấm điểm (`signals/score.py`)

- argmax thuần cho 2 BUY / 376 HOLD / 11 SELL (HOLD là lớp đa số) → không dùng được. Đổi sang xếp hạng cắt ngang: top 10% theo `prob_up − prob_down` = BUY, bottom 10% = SELL (cùng định nghĩa top-decile khi đánh giá), kèm điều kiện dấu của edge.
- Kết quả 2026-10-19: 389 mã → 10 BUY / 341 HOLD / 38 SELL. Tổng xác suất mỗi dòng = 1; expected excess return trung bình BUY +1,9%, HOLD −0,5%, SELL −1,2%.

## Phase 3 — Advisor (`portfolio/advisor.py`, `POST /portfolio/{user_id}/advise`)

- Python quyết định hành động và khối lượng (lô 100, trong tiền mặt, T+2); LLM chỉ viết các slot: MARKET, R_<MÃ>, IDEAS, RISK. Advisor đọc `trade_signals` bằng SQL, không import lightgbm nên chạy được trong image API.
- Quy tắc: CUT_LOSS khi lỗ ≤ −8% và tín hiệu ≠ BUY; TAKE_PROFIT khi lãi ≥ 15% và tín hiệu SELL; BUY_MORE khi tín hiệu BUY, tỷ trọng < 25%, còn tiền; còn lại HOLD. Ý tưởng mới: BUY, chưa nắm giữ, GTGD TB20 ≥ 5 tỷ, expected excess > 0.
- Chạy thật (user demo, DeepSeek, ~12,5 s, HTTP 200), sau các lần sửa: đủ 4 mục, không thiếu slot (`llm_slots_missing = []`), không lộ mã nội bộ, có disclaimer. Danh mục demo: NVL +22% (SELL) → TAKE_PROFIT 9.000; VIC −13,1% → CUT_LOSS 200; BSR (BUY) → BUY_MORE 1.200; TCB, VHM → HOLD; 3 ý tưởng mua (PNJ, GEL, NAF). Danh mục rỗng → 400.
- Lỗi tìm thấy khi đánh giá output thật và đã sửa: LLM lộ mã lý do/enum (thêm bản dịch tiếng Việt + `_humanize`); HOLD hiện "0 cp"; cờ rủi ro tiếng Anh; ý tưởng có expected excess âm; văn phong quá khứ "đã chốt lời" (đổi thành đề xuất); thiếu slot MARKET ngẫu nhiên (retry 1 lần + fallback từ dữ liệu thị trường); ngành "Unknown".
- Test: 43 passed (`test_portfolio_ledger`, `test_signals_features`, `test_portfolio_advisor`).
- Hạn chế: tín hiệu model yếu (xem Phase 2); báo cáo có ghi chú này. Cải thiện model: `docs/plans/signals-phase2-followup.md`. Dữ liệu demo đã xoá khỏi `portfolio_transactions`.

## Chạy lại

```
.venv\Scripts\activate
python scripts/migrate.py --skip-minio --skip-market-data --skip-audit
python -m signals.train
python -m signals.score
pytest tests/test_portfolio_ledger.py tests/test_signals_features.py tests/test_portfolio_advisor.py
```

## Phase 2 follow-up — code đã có, chưa chạy (chờ số liệu thật)
- Luật promote chặt hơn (C): `signals/train.py::promotion_decision` — beat momentum + universe, thắng momentum ≥60% fold, hit rate >50% hoặc CI95 block-bootstrap (top − universe) > 0. Lý do PASS/FAIL ghi vào `signal_models.metrics.promotion_reason`. Test: `tests/test_signals_train.py`.
- Thử nghiệm B: `python -m signals.experiments` (chỉ fold 1–7); chọn 1 config rồi `python -m signals.experiments --holdout <config>` đúng một lần.
- Giám sát D: `python -m signals.monitor` (hit rate thực theo tuần); asset check `buy_hit_rate_vs_universe` trên `trade_signals_daily`. Advisor in cảnh báo khi model mới nhất không được promote.
- A (Dagster materialize, sensor, image) cần chạy tay — xem `docs/plans/signals-phase2-followup.md`.

### Kết quả thử nghiệm B (fold 1–7, chạy 2026-10-19: `python -m signals.experiments`)
Top-decile = excess 20 phiên vs VNINDEX, gộp 7 fold. CI95 = block-bootstrap của (top-decile − universe).

| config | top-decile | momentum | universe | hit rate | CI95 (top−universe) | thắng momentum |
|---|---|---|---|---|---|---|
| baseline | -0,07% | -0,44% | -0,23% | 43,7% | [-0,62%, +0,97%] | 4/7 |
| rank_norm | -0,21% | -0,44% | -0,23% | 43,6% | [-0,82%, +0,81%] | 3/7 |
| median_label | -0,01% | -0,44% | -0,23% | 44,7% | [-0,49%, +0,96%] | 4/7 |
| no_vol_20 | -0,29% | -0,44% | -0,23% | 42,9% | [-0,62%, +0,43%] | 3/7 |
| no_sector | -0,04% | -0,44% | -0,23% | 44,3% | [-0,49%, +0,89%] | 4/7 |
| no_fundamentals | -0,46% | -0,44% | -0,23% | 43,4% | [-0,88%, +0,35%] | 2/7 |
| regularized | -0,20% | -0,44% | -0,23% | 43,3% | [-0,79%, +0,79%] | 4/7 |
| liquid_only | -0,05% | +0,49% | -0,02% | 43,6% | [-0,79%, +0,66%] | 3/7 |
| horizon_10 | -0,03% | -0,28% | -0,13% | 44,4% | [-0,29%, +0,53%] | 4/7 |
| horizon_40 | -0,98% | -0,78% | -0,50% | 42,1% | [-1,41%, +0,49%] | 4/7 |

- Không config nào có CI > 0, không config nào đạt hit rate > 50% hay thắng momentum ≥ 60% fold → không config nào qua luật promote C. Holdout (fold 8–9) **chưa dùng**.
- Bỏ fundamentals làm xấu nhất (-0,46%), cho thấy fundamentals đóng góp một phần; `median_label` và `horizon_10` chỉ nhỉnh hơn baseline trong sai số (CI rộng ±1%).
- Kết luận: edge thống kê chưa phân biệt được với 0. Advisor tiếp tục coi tín hiệu là độ nghiêng rất yếu.

### Phase 2 follow-up — mục A (chạy 2026-10-19) và B7
- **A1 Dagster:** `signal_model_job` và `trade_signals_job` chạy thành công qua `execute_in_process` (CLI `dagster asset materialize --select` lỗi do antlr4 trên Python 3.14, nên dùng job API). Log: `signal_model lgbm_20261019_1958: promoted=False (...)`; `trade_signals_daily` ghi 389 dòng (BUY 10 / HOLD 341 / SELL 38) bằng model `lgbm_20261019_1801`. Schedule `signal_model_weekly`, `trade_signals_daily_1915` = RUNNING. Asset check `buy_hit_rate_vs_universe` = passed (chưa có tín hiệu đủ 20 phiên).
- Model mới `lgbm_20261019_1958` không được promote: qua 2 điều kiện (hơn momentum, hơn universe), trượt 2 (thắng momentum 5/9 fold < 60%; hit rate 44,1% và CI95 [-0,36%, +0,97%] chứa 0). Model `_1801` (promote theo luật cũ) vẫn active.
- **A2 sensor:** đặt `is_active=FALSE` → sensor tạo `signal_model_job`; xoá `trade_signals` phiên mới nhất → tạo `trade_signals_job`; trạng thái cả hai đã khôi phục (389 dòng, model active).
- **A3 image:** build `Dockerfile.api` xong; image API không có lightgbm, `import api.portfolio` thành công. (Dagster chạy local ngoài docker nên không có image Dagster riêng để build.)
- **A4 universe nhỏ:** phiên 14–19/10 chỉ 374–391 mã vs 429. 38 mã HOSE (ABR, ASG, VNX, …) có dòng ngày 13/10 nhưng thiếu 19/10. Fireant và nguồn dự phòng đều không có dữ liệu những ngày đó (ABR chỉ khớp 100–300 cp/phiên; VNX không giao dịch từ tháng 4) → các ngày thiếu là do mã không giao dịch, không phải lỗi pipeline.
- **B7 (`extra_features`: flow_60, vol/pe/pb tương đối so với ngành), fold 1–7:** top-decile -0,05%, hit rate 44,0%, CI95 [-0,53%, +0,89%], thắng momentum 4/7 — không khác baseline (-0,07%). Không config nào qua luật promote; holdout vẫn chưa dùng.

### Hướng mới: rank IC + universe giao dịch được + feature đặc thù VN (chạy 2026-10-19)
Thêm vào `signals/`: rank IC (Spearman theo ngày, t-stat điều chỉnh chồng lấn = IC/std·√(n_dates/horizon)), feature `VN_FEATURES` (ret_1, ret_3, gap, intraday, chuỗi trần/sàn 10 phiên, Amihud, momentum ngành), `rank_target`, lọc thanh khoản `min_value_bn` (nhãn tính trên toàn bộ panel rồi mới lọc để không look-ahead). Lệnh: `python -m signals.experiments --only ...` (fold 1–7).

| config | IC clf (t) | IC reg (t) | IC momentum | top-decile | universe | hit | CI95 top−universe |
|---|---|---|---|---|---|---|---|
| baseline (toàn universe, 20 phiên) | +0,036 (1,4) | +0,032 (1,6) | -0,021 | -0,07% | -0,23% | 43,7% | [-0,62%, +0,97%] |
| liquid_only | +0,002 (0,1) | +0,008 (0,3) | +0,005 | -0,50% | -0,21% | 41,2% | [-1,01%, +0,43%] |
| liquid_vn | +0,010 (0,4) | +0,015 (0,8) | +0,005 | -0,40% | -0,21% | 43,4% | [-1,04%, +0,52%] |
| liquid_vn_h10 (rank target) | +0,036 (1,9) | +0,003 (0,2) | +0,003 | -0,06% | -0,16% | 44,7% | [-0,28%, +0,45%] |
| liquid_vn_h5 (rank target) | +0,034 (2,4) | +0,036 (2,9) | -0,004 | +0,32% | -0,07% | 47,4% | [+0,20%, +0,61%] |

- Tín hiệu IC ≈ +0,03 của baseline nằm ở các mã kém thanh khoản: lọc GTGD TB20 ≥ 5 tỷ thì IC về ≈ 0 (20 phiên). Edge của model cũ phần lớn không giao dịch được.
- Chỉ `liquid_vn_h5` (horizon 5, universe thanh khoản, feature VN, rank target) có CI95 > 0 trên fold 1–7. Đây là 1 trong ~20 cấu hình đã thử (nguy cơ chọn trúng may mắn) → chạy holdout đúng một lần.
- **Holdout (fold 8–9, `--holdout liquid_vn_h5`):** top-decile +0,03% (2026-02-04..08-07, momentum -0,44%, hit 45,3%) và +0,05% (08-10..10-12, momentum -0,15%, hit 47,7%) mỗi 5 phiên. Dấu dương và hơn momentum, nhưng nhỏ hơn rất nhiều so với +0,32% lúc tìm kiếm.
- Chi phí giao dịch VN (phí ~0,15–0,25%/chiều + thuế bán 0,1%, khoảng 0,4–0,5% cả vòng) lớn hơn mức vượt trội 5 phiên ở cả hai giai đoạn → chưa có bằng chứng lãi ròng. Chưa promote/deploy cấu hình này.

### Backtest quy tắc advisor, không dùng tín hiệu (2026-10-19: `python -m portfolio.backtest_rules`)
4.000 vị thế ngẫu nhiên (mã GTGD TB20 ≥ 5 tỷ, vào lệnh 2022-01..2026-04, nắm tối đa 120 phiên), mọi quy tắc chạy trên **cùng** điểm vào với "giữ". Phí 0,15%/chiều + thuế bán 0,1%, bán được từ T+2, khớp ở giá đóng cửa phiên sau khi chạm ngưỡng, mã bị khoá sàn (≤ −6,5%/phiên) không bán được. CI95 = bootstrap theo cụm tháng vào lệnh.

| quy tắc | lợi suất TB | p5 lợi suất | max drawdown TB | p5 drawdown | khác biệt vs giữ (CI95) |
|---|---|---|---|---|---|
| giữ | +0,32% | -43,3% | -27,6% | -60,2% | — |
| cắt lỗ -5% | +0,18% | -15,5% | -14,5% | -30,9% | -0,15% [-3,2%, +3,0%] |
| cắt lỗ -8% | +0,20% | -18,2% | -17,3% | -33,2% | -0,12% [-2,6%, +2,6%] |
| cắt lỗ -12% | -0,32% | -21,5% | -20,0% | -34,7% | -0,64% [-2,9%, +1,6%] |
| cắt lỗ -20% | -0,41% | -27,8% | -23,1% | -38,9% | -0,73% [-2,3%, +0,8%] |
| chốt lời +10% | -0,37% | -40,0% | -17,2% | -52,7% | -0,69% [-3,5%, +2,0%] |
| chốt lời +15% | -0,36% | -41,6% | -20,2% | -55,3% | -0,69% [-3,0%, +1,7%] |
| chốt lời +25% | -0,14% | -43,1% | -23,5% | -57,9% | -0,46% [-2,0%, +1,1%] |
| advisor cũ (-8, +15) | -0,12% | -17,3% | -11,7% | -23,8% | -0,45% [-4,1%, +3,5%] |

- Cắt lỗ −5%/−8% giảm mạnh rủi ro đuôi (p5 lợi suất −43% → −18%, drawdown TB −28% → −17%) mà lợi suất trung bình gần như không đổi (khác biệt không phân biệt được với 0). Cắt lỗ muộn hơn (−12%, −20%) giảm rủi ro ít hơn và lợi suất TB thấp hơn.
- Chốt lời không giảm rủi ro đuôi (p5 −40% đến −43%) mà làm lợi suất TB thấp hơn 0,5–0,7 điểm do cắt phần lãi (CI chứa 0). Gộp (−8, +15) đạt drawdown nhỏ nhất chủ yếu vì 94% vị thế bị thoát sớm ra tiền mặt, không phải nhờ kỹ năng. → Đã **bỏ TAKE_PROFIT** khỏi advisor, giữ CẮT LỖ −8%.
- Giới hạn: giai đoạn 2022–2026 gồm đợt giảm mạnh 2022 nên lợi ích cắt lỗ có thể lớn hơn một giai đoạn thuần tăng; mỗi vị thế được mô phỏng độc lập, chưa tính tiền mặt tái đầu tư.

### Model rủi ro (2026-10-19: `python -m signals.risk`)
Mục tiêu trong 20 phiên tới: biến động thực hiện (hồi quy log) và xác suất đóng cửa giảm ≥10% so với hôm nay (phân loại). Chỉ mã thanh khoản (GTGD TB20 ≥ 5 tỷ), walk-forward 9 fold, một cấu hình cố định (không tinh chỉnh nên báo cáo mọi fold). So với baseline "biến động tháng sau = biến động 20 phiên vừa rồi" (`vol_20`).

| chỉ số (TB 9 fold, theo ngày) | LightGBM | baseline vol_20 |
|---|---|---|
| tương quan hạng với biến động thực hiện | 0,588 | 0,568 |
| AUC giảm sâu ≥10% (bộ phân loại) | 0,644 | 0,712 |
| AUC giảm sâu ≥10% (điểm từ model biến động) | 0,722 | 0,712 |

- Rủi ro dự đoán được nhưng gần như toàn bộ nằm sẵn trong `vol_20`: decile biến động cao nhất của baseline có 37,8% lần giảm ≥10% trong 20 phiên so với 8,2% ở decile thấp nhất (trung bình ~23%). LightGBM chỉ cải thiện thêm ~0,01–0,02; bộ phân loại trực tiếp còn kém baseline vì học mức rủi ro theo chế độ thị trường (tỷ lệ giảm sâu theo fold từ 8,6% đến 50%) thay vì xếp hạng chéo mã. → **Không triển khai model rủi ro**; advisor dùng thẳng `vol_20`.
- Advisor (`portfolio/advisor.py::_vol_percentiles`): thêm cờ `vol_high` và dòng "Biến động cao" cho mã đang giữ có biến động 20 phiên thuộc top 10% mã thanh khoản, kèm tỷ lệ giảm sâu từ bảng trên (38% vs 23%). Chạy thật trên danh mục thử (NVL −23,3% → CẮT LỖ; VIC, HPG → GIỮ; NVL đúng ở phân vị 90): cờ và số liệu đúng. Lần chạy này slot MARKET bị LLM bỏ sót (`llm_slots_missing = ['MARKET']`) nên dùng văn bản dự phòng thô — lỗi có sẵn từ trước, chưa sửa.

### Đánh giá bộ phát hiện breakout (`tools/breakout.py`) có thay được tín hiệu LightGBM không (2026-10-19)
Cài lại vectorized các luật SHORT/MID/LONG (`signals/breakout_eval.py`, 2 biến thể ngưỡng khối lượng: strict/loose vì scanner thật chọn ngưỡng theo MACD và khối ngoại), mã thanh khoản (GTGD TB20 ≥ 5 tỷ), vào ở giá đóng cửa phiên sau tín hiệu, mỗi sự kiện = tín hiệu đầu tiên trong cửa sổ 10 phiên, CI = bootstrap theo cụm tháng.

| loại | n sự kiện | 5 phiên vs universe | 10 phiên | 20 phiên (CI95) | hit 20 phiên |
|---|---|---|---|---|---|
| SHORT | ~1.900 | +0,01% | +0,24% | +0,46% [-0,15%, +1,17%] | 46% |
| MID (loose) | 1.186 | +0,37% (CI>0) | +0,51% (CI>0) | +0,78% [+0,13%, +1,48%] | 46% |
| LONG | 6 | không đủ mẫu | | | |
| tất cả | ~2.060 | +0,12% | +0,30% | +0,57% [-0,02%, +1,29%] | 47% |

- Theo năm (tất cả, 20 phiên, so với universe): 2021 −0,3%, 2022 +3,1% (n=142), 2023 +0,7%, 2024 +0,3%, 2025 +0,06% (hit 35%, excess vs VNINDEX −1,6%), 2026 +1,7% → không đều.
- Kiểm tra thực tế hơn (`python -m signals.breakout_vs_random`): sự kiện MID vs 5 mã thanh khoản ngẫu nhiên cùng ngày, mô phỏng đủ phí/thuế/T+2/khoá sàn, 20 phiên: lợi suất ròng +1,3–1,4% so với +0,7–0,8% (chênh +0,4–0,6%, CI95 [−0,1%, +1,4%] chạm 0); p5 lợi suất không tốt hơn (−14,3% vs −14,6%); thêm cắt lỗ −8% đưa p5 về −12% nhưng chênh lợi suất còn +0,4–0,5% (CI chứa 0).
- Kết luận: tốt hơn LightGBM (holdout chỉ +0,03–0,05%/5 phiên) và đơn giản hơn (luật cố định, không fit), nhưng lợi thế sau phí vẫn nhỏ và chưa có ý nghĩa thống kê chắc chắn. Chỉ MID có CI>0 ở thước đo "vs universe" trước phí; không giảm rủi ro đuôi.

### Scanner thật + dùng breakout làm feature cho LightGBM (2026-10-19)
- Chạy `tools.breakout.scan_all` thật trên thị trường ngày 2026-10-19: 2 SHORT (BCM, CCI), 2 MID (CCI, PHC), 5 MID_PRE, 0 LONG. Bản vector hóa trong `signals/breakout_eval.py` cùng ngày tìm thấy 2 trong 4 tín hiệu SHORT/MID/LONG của scanner thật, không có tín hiệu thừa → là xấp xỉ bảo thủ (thiếu phần ngưỡng khối lượng thích ứng theo MACD/khối ngoại).
- Thêm `BO_FEATURES` (cờ brk_short/mid/long, khoảng cách tới pivot 10/40/100, độ chặt nền 20/40/100, ma20/ma60, số phiên phân phối 20/40) vào `signals/features.py` (tuỳ chọn, không đổi `FEATURES` đang phục vụ). Kết quả fold 1–7 (`python -m signals.experiments --only bo_features liquid_bo_h20 liquid_bo_h10 liquid_bo_vn_h5`):

| config | IC clf (t) | IC reg (t) | top-decile | hit | CI95 top−universe |
|---|---|---|---|---|---|
| bo_features (toàn universe, 20 phiên) | +0,030 (1,2) | +0,025 (1,3) | -0,35% | 42,5% | [-0,73%, +0,42%] |
| liquid_bo_h20 | +0,002 (0,1) | +0,013 (0,7) | -0,77% | 40,3% | [-1,28%, +0,16%] |
| liquid_bo_h10 | +0,024 (1,2) | +0,034 (1,9) | -0,12% | 43,4% | [-0,46%, +0,53%] |
| liquid_bo_vn_h5 | +0,030 (2,1) | +0,027 (2,1) | +0,28% | 47,2% | [+0,13%, +0,61%] |

- Không cải thiện so với không có breakout feature: liquid_bo_vn_h5 (+0,28%, IC 0,030) tương đương liquid_vn_h5 (+0,32%, IC 0,034); liquid_only (-0,50%) vs liquid_bo_h20 (-0,77%) không khá hơn. Thông tin của breakout đã nằm trong feature giá/khối lượng sẵn có. Không chạy holdout (không có cấu hình mới đáng chọn).
- Huấn luyện model trực tiếp trên sự kiện breakout (meta-labeling) không khả thi: chỉ ~2.000 sự kiện (1.200 MID) trong 5 năm, gộp theo cụm tháng thì quá ít để có fold walk-forward đáng tin.

## Backtest danh mục breakout (signals/breakout_portfolio.py)

Mô phỏng một tài khoản: tối đa 10 vị thế, mỗi vị thế 1/10 vốn, tín hiệu ở đóng cửa t → mua đóng cửa t+1 (bỏ qua nếu kịch trần ≥ +6.5% hoặc volume 0), ưu tiên mã có return 63 phiên cao nhất. Thoát: cắt lỗ −8% (bán được từ T+2, khớp phiên kế tiếp, không bán được khi giá sàn) hoặc sau 20 phiên. Phí 0.15%/chiều + thuế bán 0.1%, tiền nhàn rỗi không sinh lãi. Baseline "random": cùng số lệnh mỗi ngày nhưng chọn mã thanh khoản ngẫu nhiên, 30 seed, cùng quy tắc thoát. Giai đoạn 2022-01-18 → 2026-10-19. Chạy: `python -m signals.breakout_portfolio`.

| Chiến lược | Lệnh | Thắng | Tổng | CAGR | Sharpe | MaxDD | Random CAGR (TB, p5–p95) |
|---|---|---|---|---|---|---|---|
| MID | 417 | 45.6% | +9.5% | +1.9% | 0.20 | −25% | −7.0% (−11.6…−1.4) |
| MID+SHORT | 486 | 45.3% | −11.7% | −2.6% | −0.07 | −35% | −6.4% (−13.0…−1.0) |
| VNINDEX mua-giữ | | | +17.4% | +3.4% | 0.27 | −40% | |

Theo năm (MID / random / VNINDEX): 2022 −16% / −20% / −30%; 2023 +33% / +13% / +8%; 2024 +5% / −0.5% / +12%; 2025 −0.6% / −8% / +41%; 2026 −6% / −13% / −5.5%.

Quan sát:
- MID thắng random ở cả 30/30 seed (CAGR +1.9% vs −7%), nhưng chỉ thắng nhờ random thua nặng; tuyệt đối MID vẫn thấp hơn VNINDEX mua-giữ (+1.9% vs +3.4%) và thua rõ ở 2024–2025.
- MID+SHORT âm sau phí; thêm SHORT làm xấu đi. ANY trùng MID+SHORT (LONG không thêm lệnh nào qua bộ lọc thanh khoản).
- Chênh MID so với random tập trung ở 2023 (+20 điểm); các năm khác chênh 4–8 điểm, không đủ để kết luận có lợi thế ổn định. Mới có 1 đường vốn nên chưa có khoảng tin cậy cho chênh lệch MID − VNINDEX.
- Lỗi đã sửa trong lúc viết: lần chạy đầu cho vốn về −100% do `j, price = p["j"], close[d, j]` lấy giá theo mã của vòng lặp trước; đã có test hồi quy `tests/test_breakout_portfolio.py`.

### Thử cải tiến 1: tiền nhàn rỗi nằm trong VNINDEX (`park` trong `run_portfolio`)

Tiền chưa dùng được giữ trong chỉ số (phí mỗi lần vào/ra, thuế bán khi rút ra mua cổ phiếu). Cùng giai đoạn và quy tắc như bảng trên.

| Chiến lược | CAGR | Sharpe | MaxDD | Random cùng quy tắc (CAGR TB) |
|---|---|---|---|---|
| MID (tiền mặt) | +1.9% | 0.20 | −25% | −7.0% |
| MID + tiền nhàn rỗi vào VNINDEX | +2.1% | 0.21 | −42% | −6.6% |
| MID+SHORT + VNINDEX | −1.5% | 0.04 | −37% | −6.9% |
| VNINDEX mua-giữ | +3.4% | 0.27 | −40% | |

Năm 2025 (VNINDEX +40.5%): MID tiền mặt −0.6%, MID + VNINDEX +12.2%. Kết luận: cải thiện không đáng kể (+0.2 điểm CAGR) và MaxDD xấu đi gần bằng chỉ số; phần cổ phiếu (~7 vị thế đồng thời, 417 lệnh × 20 phiên / ~1180 phiên) chiếm phần lớn vốn và đi sau chỉ số, nên giữ phần còn lại trong chỉ số không bù được. Hướng 1 không giúp MID vượt VNINDEX.

### Thử cải tiến 2: quy tắc thoát (signals/breakout_exits.py)

Giao thức cố định trước khi chạy: xếp hạng 48 cấu hình (hold {20,40,60,120} × trailing stop {không,10%,15%,20%} × thoát khi thủng MA {không,20,50}; luôn có stop −8% và tiền nhàn rỗi vào VNINDEX) trên TRAIN (đến 2024-12-31) theo CAGR vượt VNINDEX; cấu hình đứng đầu được xét 1 lần trên HOLDOUT (2025-01 → 2026-10). "Dùng được" nếu trên HOLDOUT: CAGR > VNINDEX, MaxDD nhỏ hơn VNINDEX, thắng random cùng quy tắc ≥ 90% seed.

- VNINDEX: TRAIN CAGR −4.2% (MaxDD −40.2%); HOLDOUT CAGR +17.3% (MaxDD −18.1%).
- Cấu hình thắng TRAIN: hold 60, trailing 20%, không MA → TRAIN +13.5%/năm (vượt chỉ số +17.7 điểm), HOLDOUT +7.5% (thua chỉ số 9.8 điểm), MaxDD HOLDOUT −24.2% (xấu hơn chỉ số −18.1%), thắng random 90% seed (random TB +0.2%). Theo năm: 2023 +33%, 2024 +47%, 2025 +29% (chỉ số +40%), 2026 −11% (chỉ số −5.5%). → KHÔNG đạt tiêu chí.
- Giữ lệnh lâu hơn (60–120 phiên) cải thiện rõ trên TRAIN (train chỉ số âm nên chiến lược bán sớm/ít vị thế được lợi), nhưng thứ hạng không giữ được qua HOLDOUT; vài cấu hình có HOLDOUT ≥ chỉ số (vd hold 60 + trailing 15% + MA50: +18.0%, hold 120 không trailing: +22.2%) nhưng đều xếp giữa/thấp trên TRAIN. Cột HOLDOUT của cả 48 cấu hình đã được in ra nên HOLDOUT không còn "sạch" để chọn thêm; chọn tiếp trên đó là overfit.

### Dữ liệu bổ sung có tách được breakout tốt/xấu không? (signals/breakout_enrich.py)

Dữ liệu trong DB đã kiểm tra: `foreign_flows` (2021-01 → nay, 431 mã, `net_value` đơn vị tỷ VND), `financial_facts` (lợi nhuận theo quý từ Q1/2020, 374 mã có lợi nhuận cổ đông công ty mẹ), `news_articles` (chỉ từ 2025-04-25, 78/11,956 bài có sentiment → không dùng được để backtest). `stock_ratios` chỉ là ảnh chụp hiện tại (430 dòng) nên không dùng cho lịch sử.

Phép thử: gắn thuộc tính (biết tại ngày tín hiệu) cho 2,058 sự kiện ANY và 1,182 sự kiện MID (loose, thanh khoản, horizon 20), so lợi nhuận vượt universe cùng ngày theo nhóm, tách TRAIN (≤2024) / HOLDOUT (2025+). Lợi nhuận quý: Q1–Q3 coi là công bố sau 45 ngày, Q4 sau 90 ngày.

- Dòng tiền ngoại 20 phiên / 5 phiên: mua ròng >2% không tốt hơn bán ròng <−2% (MID, vs universe: +1.1% vs +1.4% với 20 phiên; +0.3% vs +1.1% với 5 phiên); mọi CI cụm theo tháng cắt 0 hoặc dấu đảo giữa TRAIN/HOLDOUT.
- Tăng trưởng lợi nhuận YoY: nhóm >20% không tốt hơn (MID −0.3%, CI −1.5%…+1.0%); nhóm âm đảo dấu giữa TRAIN (+1.2%) và HOLDOUT (−2.2%). Nhóm duy nhất có CI loại 0 là "n/a" (năm trước lỗ hoặc thiếu dữ liệu, n=411, +1.8%), đây là nhóm không ý nghĩa kinh tế rõ ràng (cổ phiếu hồi phục/đầu cơ, ngân hàng thiếu chỉ tiêu).
- Độ rộng thị trường (tỷ lệ mã trên MA50), volume tương đối, quy mô thanh khoản: không có nhóm nào cùng dấu và CI loại 0 ở cả hai giai đoạn trừ "value mid" (MID, +1.3%, CI +0.1%…+2.7%; TRAIN +1.0%, HOLDOUT +2.0%), mà "value high" và "value low" không theo thứ tự đơn điệu nên coi là nhiễu.
- Đã kiểm ~40 ô (6 thuộc tính × 3 nhóm × 2 loại sự kiện) nên 1–3 ô CI loại 0 là điều kỳ vọng do ngẫu nhiên. Kết luận: các nguồn dữ liệu hiện có không tách được breakout tốt/xấu; không có bộ lọc nào đáng đưa vào backtest danh mục.
- Lỗi đã sửa khi viết: lần chạy đầu `foreign_flows.net_value` (tỷ VND) chia cho giá trị giao dịch (VND) nên mọi sự kiện rơi vào nhóm "flat".

### Dữ liệu ngoài DB: tự doanh theo mã (signals/fetch_proprietary.py, prop_eval.py, breakout_prop_portfolio.py)

Nguồn: `vnmarket` `market.proprietary` (chuỗi CafeF → VNDirect). CafeF chỉ trả 1 năm gần nhất (từ 2025-10-20); VNDirect trả từ 2022-05-17 (ngày HOSE công bố lại số liệu tự doanh) → dùng VNDirect gọi trực tiếp. Tải 417/431 mã (14 mã NoData: AAN, ANT, CCC, CMF, CRV, ECO, LPS, NO1, PDV, QNP, SBG, TSA, VNX…), 100,864 dòng, cache `outputs/proprietary_vnd.csv` (chỉ để nghiên cứu, chưa có asset Dagster). Margin theo mã không có nguồn công khai (chỉ có tổng theo quý từ báo cáo các công ty chứng khoán).

Kết quả (từ 2022-07-01; ratio = tự doanh ròng / giá trị giao dịch cùng cửa sổ):
- Toàn bộ cổ phiếu thanh khoản (174,890 stock-day): rank IC với lợi nhuận vượt universe 20 phiên là +0.014 (5 phiên, t hiệu chỉnh +0.96) và +0.013 (20 phiên, t +0.94); chênh decile cao − thấp chỉ +0.04% đến +0.18% / 20 phiên. Tự doanh tự nó không dự báo lợi nhuận.
- Trong sự kiện breakout MID (n=1,020): nhóm tự doanh mua ròng >1% (5 phiên: n=205; 20 phiên: n=195) vượt universe +1.75% (CI +0.5%…+3.2%) và +1.68% (CI +0.7%…+3.0%), cả TRAIN (+0.8%) và HOLDOUT (+3.8%/+3.5%) dương; nhóm flat +0.3%/+0.5%, nhóm bán ròng +0.9%/+0.5% (CI cắt 0, không đơn điệu). Với ANY (n=1,744), chỉ ô 5 phiên mua ròng có CI loại 0 (+1.1%).
- Backtest danh mục (tối đa 10 vị thế, tiền nhàn rỗi vào VNINDEX, phí+thuế, T+2, khóa sàn/trần), 2022-07 → 2026-10, VNINDEX CAGR +8.3%, MaxDD −29%:

| Chiến lược | Thoát | Lệnh | Thắng | CAGR | Hơn VNINDEX | Sharpe | MaxDD | Thắng random |
|---|---|---|---|---|---|---|---|---|
| MID | hold20/stop8 | 388 | 45% | +5.2% | −3.1 | 0.35 | −33% | 97% |
| MID | hold60/trail20 | 186 | 38% | +15.3% | +7.0 | 0.79 | −31% | 100% |
| MID + prop5>1% | hold20/stop8 | 179 | 54% | +10.1% | +1.8 | 0.59 | −31% | 100% |
| MID + prop5>1% | hold60/trail20 | 113 | 50% | +19.9% | +11.6 | 1.00 | −31% | 100% |
| MID + prop20>1% | hold20/stop8 | 169 | 54% | +9.2% | +0.9 | 0.55 | −30% | 90% |
| MID + prop20>1% | hold60/trail20 | 115 | 42% | +13.4% | +5.1 | 0.73 | −28% | 100% |

  Theo năm (MID+prop5, hold60/trail20 / VNINDEX): 2022 −18% / −16%; 2023 +24% / +8%; 2024 +36% / +12%; 2025 +39% / +40%; 2026 +8% / −5.5%.
- Giới hạn: một đường vốn, 113–179 lệnh, 4.3 năm; 6 cấu hình đã thử (cấu hình thoát hold60/trail20 chọn từ phép thử trên 2022–2024 trước đó nên không hoàn toàn độc lập); MaxDD không tốt hơn chỉ số; tự doanh ròng của mã cụ thể do công ty chứng khoán công bố theo ngày, chưa kiểm tra độ trễ công bố so với đóng cửa (dùng tín hiệu cuối phiên t, mua đóng cửa t+1). Đây là kết quả đầu tiên hứa hẹn nhưng chưa phải bằng chứng.

### Triển khai forward test (2026-10-19)
Kế hoạch: `docs/plans/prop-flow-forward-test.md`. Đã áp migration 041 và chạy bootstrap bằng Dagster (`execute_in_process`):
- `proprietary_flows_job`: thành công sau 758 giây, bảng có 100,864 dòng, 417 mã, 2022-05-17 → 2026-10-16 (khớp file cache nghiên cứu); 14 mã đang hoạt động không có dữ liệu (NoData). Kiểm chứng chéo: HPG 2026-10-16 tự doanh ròng +64.53 tỷ, khối ngoại −23.92 tỷ.
- `breakout_signals_job`: thành công sau 17 giây, phiên 2026-10-19 ghi 1 sự kiện (SHORT, BCM, chưa qua gate).
- Dữ liệu tự doanh phiên 2026-10-19 chưa có tại thời điểm chạy (mới đến 2026-10-16), nên tỷ lệ tự doanh 5 phiên của phiên mới nhất coi ngày thiếu là 0; asset chỉ raise khi dữ liệu cũ hơn 4 ngày.
- `signals.breakout_monitor` hiện trả 0 sự kiện đáo hạn (chưa đủ 20 phiên); sensor bootstrap cho hai bảng mới trả False (đã đầy).
- Test: 51 passed trên các bộ test signals/portfolio/breakout/proprietary.

### Dọn code (2026-10-19)
Đã xóa các script nghiên cứu cho hướng không dùng hoặc đã được thay thế; kết quả của chúng vẫn nằm trong các mục trên của file này:
- `signals/experiments.py` (thử cấu hình LightGBM), `signals/risk.py` (mô hình rủi ro, advisor dùng cờ `vol_20`), `signals/breakout_vs_random.py` (thay bằng `breakout_portfolio.py`), `signals/breakout_exits.py` và `signals/breakout_enrich.py` (kết quả âm tính), `signals/prop_eval.py` + `signals/fetch_proprietary.py` + `outputs/proprietary_vnd.csv` (thay bằng bảng `proprietary_flows` và `signals/breakout_live.prop_ratio`).
- `signals/features.py`: bỏ các nhóm feature thử nghiệm (B7/VN/BO, `rank_normalize`) và cột `open`; mô hình đang chạy chỉ dùng `FEATURES`. Chấm điểm lại 2026-10-19 cho kết quả giống hệt (389 mã, trùng từng dòng). `signals/labels.forward_excess_median` cũng bỏ.
- Mô hình `lgbm_20261019_1958` (đào tạo xong nhưng không được promote, chưa có tín hiệu nào) bị xóa khỏi `signal_models` và `signals/artifacts/`. Mô hình active `lgbm_20261019_1801` giữ nguyên.
- Còn lại: `breakout_eval.py` (luật breakout), `breakout_portfolio.py` + `breakout_prop_portfolio.py` (backtest danh mục, để chạy lại kết quả chính), `breakout_live.py` + `breakout_monitor.py` (forward test).

### Chỉ dùng độ biến động để chọn mã (signals/lowvol_eval.py)
Danh mục cân bằng trọng số, tái cân bằng hằng tháng, chia 5 nhóm theo độ biến động 60 phiên trong các mã thanh khoản (≥5 tỷ/phiên), có phí + thuế bán, 2022-01-18 → 2026-10-19:

| Danh mục | CAGR | Sharpe | MaxDD |
|---|---|---|---|
| VNINDEX | +3.4% | 0.27 | −40% |
| Toàn bộ mã thanh khoản (cân bằng) | −4.5% | −0.07 | −60% |
| Nhóm biến động thấp nhất (20%) | +2.4% | 0.23 | −36% |
| Nhóm biến động cao nhất (20%) | −14.6% | −0.31 | −72% |
| Bỏ 10% biến động cao nhất | −3.4% | −0.03 | −58% |

Theo năm, nhóm biến động cao nhất: 2022 −53%, 2023 +34%, 2024 −2%, 2025 −4%, 2026 −23%. Kết luận: biến động tách rất rõ rủi ro (nhóm cao nhất thua nặng gần như mọi năm trừ 2023) nên là bộ lọc loại trừ tốt; nhóm biến động thấp xấp xỉ VNINDEX (lợi nhuận thấp hơn 1 điểm, sụt giảm nhỏ hơn 4 điểm) chứ không vượt chỉ số. Lưu ý chuẩn so sánh khó: toàn bộ mã thanh khoản cân bằng trọng số thua VNINDEX 8 điểm/năm vì chỉ số bị dẫn dắt bởi vài mã vốn hóa lớn.

### Tìm thêm dữ liệu ngoài (2026-10-19)
- Margin theo mã, lệnh mua/bán chủ động theo ngày, nắm giữ quỹ mở: không tìm thấy nguồn miễn phí có lịch sử (margin chỉ có tổng theo quý từ báo cáo công ty chứng khoán; lệnh chủ động có ở FiinQuant, trả phí, chưa kiểm tra giá). `vnmarket prices.intraday` có cột `side` (b/s/unknown) nhưng chỉ trả 100 tick cuối và chỉ trong ngày hiện tại (HPG 14:27–14:45, tick ATC ghi `unknown` chiếm phần lớn khối lượng) nên không dựng được dữ liệu lệnh chủ động cả phiên.
- Giao dịch thỏa thuận (`ptVolume`/`ptValue`, VNDirect `stock_prices`, 465,309 dòng, 431 mã, từ 2022-05): tỷ trọng thỏa thuận trên tổng giá trị = 0 ở trung vị các mã thanh khoản. Rank IC với lợi nhuận vượt universe 20 phiên: −0.016 (5 phiên, t −1.34), −0.030 (20 phiên, t −2.61; TRAIN −0.043, HOLDOUT −0.010). Trong sự kiện breakout, nhóm có thỏa thuận cao/thấp không nhất quán giữa TRAIN/HOLDOUT, CI đều cắt 0 (nhóm không có thỏa thuận chiếm 85% sự kiện). Không có tín hiệu để dùng; không đưa vào pipeline.
- Tổng kết: trong các nguồn đã thử, chỉ tự doanh (xem mục trên) có dấu hiệu bổ sung thông tin cho breakout.
