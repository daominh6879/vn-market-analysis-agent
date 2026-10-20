# Forward test: breakout MID + tự doanh mua ròng

## Mục tiêu
Kiểm tra trên dữ liệu chưa từng thấy xem bộ lọc "tự doanh mua ròng 5 phiên > 1% giá trị giao dịch" có làm breakout MID tốt hơn không (backtest 2022-07 → 2026-10: CAGR +10–20% so với VNINDEX +8.3%, nhưng chỉ 113–179 lệnh và một đường vốn — xem `docs/notes/portfolio-signals.md`).

## Thành phần (đều là Dagster asset, có lịch + bootstrap)
| Thành phần | Vai trò |
|---|---|
| `infra/migrations/041_proprietary_flows_breakout_signals.sql` | bảng `proprietary_flows`, `breakout_signals` |
| `ingest/fetch_proprietary_flows.py` | vnmarket → `proprietary_flows` (đơn vị tỷ đồng); lịch sử trước 300 ngày lấy trực tiếp từ VNDirect vì CafeF chỉ có ~1 năm |
| asset `proprietary_flows_ingest` | T2–T6 19:00; rỗng/under-covered → `data_bootstrap_sensor` chạy backfill từ 2022-05-17; <50% mã có dữ liệu → raise |
| `signals/breakout_live.py` + asset `breakout_signals_daily` | T2–T6 19:30; ghi sự kiện SHORT/MID/LONG của phiên mới nhất + cờ `prop_gate`; raise nếu `proprietary_flows` cũ hơn 4 ngày |
| `signals/breakout_monitor.py` + asset check `gated_mid_beats_ungated` (tên giữ nguyên, nay so mọi loại breakout) | so lợi nhuận vượt universe 20 phiên của sự kiện có gate và không gate (chỉ fail khi mỗi nhóm ≥ 30 sự kiện đã đáo hạn) |

## Quy tắc đánh giá (chốt trước)
- Xét sự kiện breakout của cả ba loại SHORT/MID/LONG, gộp thành 1 sự kiện mỗi mã trong cửa sổ 10 phiên (như backtest `signals/breakout_eval.events`), vào lệnh ở đóng cửa phiên sau, 20 phiên. Quyết định ngày 2026-10-19, trước khi có kết quả live, để tăng gấp đôi số sự kiện (MID từng ~1 sự kiện/phiên; ANY ~1.7/phiên trong backtest); số liệu riêng cho MID vẫn in kèm (`mid_*`).
- Ngưỡng gate (tự doanh 5 phiên > 1% giá trị giao dịch) giữ nguyên.
- Đạt nếu sau khi mỗi nhóm có ≥ 30 sự kiện đáo hạn: nhóm gate vượt universe > nhóm không gate, và vượt universe > 0 với khoảng tin cậy cụm theo tháng loại 0.
- Chưa đạt thì không đưa breakout vào advisor ("Ý tưởng mua mới" vẫn giữ nguồn hiện tại).
- Không đổi ngưỡng 1% / cửa sổ 5 phiên trong lúc theo dõi.

## Xem kết quả
`python -m signals.breakout_monitor`
