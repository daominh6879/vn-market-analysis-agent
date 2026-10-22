# Độ tươi dữ liệu thị trường ngày — kiểm tra, chặn, tự chạy lại (2026-10-21)

## Sự cố (đã kiểm chứng)
- `dagster_home/history/runs.db`: không có run nào từ 19/10 15:39 đến 21/10 13:32 (UTC). Trên máy không có process Dagster
  → schedule 17:30/18:00/18:30 không chạy hai phiên 20–21/10.
- Chạy bù thủ công 21/10: `ohlcv_ingest_job` bị CANCELED lúc 14:17 UTC, run c5e40587 kẹt ở STARTED.
- Kết quả trên DB (phiên kỳ vọng 21/10): `market_index_daily` đủ, `foreign_flows` 431/431, `ohlcv_daily` 270/429 mã —
  132 mã thiếu 20–21/10 (VIC, TCB, VHM, VPB, SHB, SSI, VIX, STB, VCB, VCI, VND, VJC, TPB, VNM…).
- `/advisor/advise` vẫn trả lời bình thường trên dữ liệu 19/10 và ghi ngày 21/10. Chỉ `technical.gather_data` tự lấy live khi DB
  cũ → cùng một mã có hai giá trong prompt. Chi tiết: `vn-portfolio/docs/notes/advisor-eval.md`.

## Cơ chế
| Lớp | Ở đâu | Làm gì |
|---|---|---|
| Kiểm tra | `portfolio/freshness.py` | Phiên kỳ vọng = hôm nay nếu ngày giao dịch và ≥ 19:00 VN, ngược lại phiên giao dịch trước. Mỗi bảng phải có phiên đó và ≥ 90% số dòng trung bình 10 phiên trước. |
| Chặn | `portfolio/evidence.py`, `api/advisor.py` | `gather_evidence_for` gọi `require_fresh()` đầu tiên; dữ liệu cũ → `/advisor/advise` trả **503 `stale_data: …`**, không gọi LLM. |
| Báo | `POST /advisor/freshness` | Trả `{expected_session, fresh, datasets, problems, checked_at}`. vn-portfolio hỏi mỗi 30 phút và báo Telegram. |
| Chạy lại | `pipeline/assets_market_data.py` `data_freshness_sensor` | Mỗi 15 phút: dataset nào hỏng → chạy lại job tương ứng (tối đa 1 lần/giờ/job/phiên, bỏ qua nếu job đang chạy < 1 giờ). OHLCV `days_back = gap+3` (≥ 5); index `days = gap+5` (≥ 30); `foreign_flows` phiên đã qua → `ohlcv_ingest_job` (bảng live chỉ có hôm nay). |
| Lỗi một phần | `pipeline/assets_ohlcv.py` | > 10% mã lỗi → raise để `RetryPolicy` chạy lại thay vì báo thành công. |
| Một giá | `portfolio/evidence.py` `reprice` | Giá vị thế và ứng viên lấy cùng nguồn với phần kỹ thuật (`get_historical_ohlcv`); NAV, tỷ trọng, lãi/lỗ tính lại theo giá đó. Prompt và báo cáo ghi rõ "Dữ liệu thị trường: phiên X · giá các mã: phiên Y". |

Giới hạn: sensor chỉ chạy khi Dagster daemon chạy. Daemon chết thì chỉ còn lớp "Chặn" và "Báo" — vn-portfolio nhắc sau 2 giờ
rằng nhiều khả năng daemon không chạy.

## Kiểm chứng (chạy thật 21/10 tối)
- pytest 4 file trên: 64 passed.
- Sau rebuild `api`, 23:11: `/advisor/freshness` → `fresh=false`, `ohlcv_daily phiên 2026-10-21 chỉ có 270/429 dòng`;
  `/advisor/advise` → **503** `stale_data: …` trong 0,16 s (không gọi LLM).
- Khởi động `dagster dev` (DAGSTER_HOME=`C:\Users\HungDao\dagster_home`) 23:12:57 → tick đầu của `data_freshness_sensor`
  23:12:59 ghi "stale: ohlcv_daily …" và tạo `ohlcv_ingest_job` ba44270f → SUCCESS 23:29 (15m43s), 252 dòng, 1 mã lỗi
  (FC1: không nguồn nào có dữ liệu).
- Sau backfill: `fresh=true`, ohlcv 399/429 (93%). 30 mã còn thiếu đều có giá trị khớp ≈ 0 (không khớp lệnh phiên đó) → ngưỡng
  90% thay vì 100% là đúng.
- `/advisor/advise` với danh mục t1 (HPG 500, VCB 100): 200 trong 19,7 s; `data_session = price_session = 2026-10-21`; báo cáo
  ghi "Dữ liệu thị trường: phiên 2026-10-21 · giá các mã: phiên 2026-10-21"; bối cảnh = số 21/10 (VNINDEX +2,76% 1.738,67,
  ngoại +168 tỷ); HPG một giá 21.150 (bảng và phần kỹ thuật), lãi/lỗ −21,8%.
- Lưu ý: tiến trình `dagster dev` hiện do tay khởi động, khởi động lại máy thì mất → cần chạy như dịch vụ.
