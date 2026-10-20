# Plan — hoàn thiện Phase 2 (model tín hiệu LightGBM) sau này

Bối cảnh: Phase 2 đã chạy end-to-end (`docs/notes/portfolio-signals.md`): dữ liệu 2021→2026, 9 fold walk-forward, model `lgbm_20261019_1801` được promote theo luật của plan nhưng edge yếu
(top-decile −0,12%/20 phiên vs momentum −0,75%, universe −0,42%; hit rate 44%; thắng momentum 5/9 fold; early stopping rất sớm).
Phase 3 (advisor) dùng tín hiệu này như một **độ nghiêng yếu**, không phải căn cứ chính. Plan này liệt kê việc còn lại để model đáng tin hơn.

## Quy tắc chạy (giống plan gốc)
- Không sửa theo kết quả fold test rồi chọn cấu hình tốt nhất trên chính các fold đó. Dành **giai đoạn giữ lại (holdout)**: 2026-02-05 → 2026-09-21 (fold 8–9) không dùng để chọn cấu hình; mọi thử nghiệm chỉ dùng fold 1–7.
- Mỗi thử nghiệm ghi vào `docs/notes/portfolio-signals.md` (số liệu thật, lệnh chạy lại). Người dùng/agent chạy `python -m signals.train`.

## A. Việc chưa kiểm chứng của Phase 2 (làm trước, rẻ)
1. Materialize `signal_model` và `trade_signals_daily` **trong Dagster** (đến giờ mới chạy bằng `python -m signals.train/score`). Xác nhận: run thành công, log có model_version, schedule `signal_model_weekly` / `trade_signals_daily_1915` ở trạng thái RUNNING.
2. Thử `data_bootstrap_sensor`: đặt `is_active=FALSE` cho model (hoặc DB trống) → sensor phải tạo run `signal_model_job`; xoá `trade_signals` của phiên mới nhất → tạo run `trade_signals_job`.
3. Build image Dagster có `lightgbm`; xác nhận image API **không** có lightgbm và `api/portfolio.py` vẫn import được (advisor chỉ đọc `trade_signals` bằng SQL).
4. Phiên mới nhất có ít mã hơn phiên trước (374–391 vs ~429 mã ở 14–19/10). Kiểm tra pipeline ohlcv hằng ngày có bỏ sót mã không (không phải lỗi của model nhưng làm lệch universe lúc chấm điểm).

## B. Cải thiện chất lượng model (thử từng bước, đo bằng fold 1–7)
Thứ tự theo kỳ vọng/độ rẻ:
1. **Rank-normalize feature theo ngày** (mỗi feature số → phân vị cắt ngang trong ngày). Lý do: raw feature trôi theo chế độ thị trường (vol, ret chung), model học mức thay vì xếp hạng. Giữ `idx_ret_20` như feature riêng.
2. **Nhãn tương đối với median universe** thay vì VNINDEX (VNINDEX bị chi phối bởi vài mã vốn hoá lớn nên universe_mean_excess luôn âm ≈ −0,4%). Giữ nhãn VNINDEX làm metric báo cáo.
3. **Ablation**: bỏ `vol_20` (gain lớn nhất, có thể chỉ chọn mã biến động), bỏ `sector`, bỏ nhóm fundamentals → xem edge đến từ đâu.
4. **Regularization/đơn giản hoá**: `num_leaves` 7–15, `min_data_in_leaf` 2000, `learning_rate` 0.02 (early stopping hiện dừng 6–94 vòng → mô hình đang overfit nhiễu).
5. **Lọc thanh khoản** khi train (GTGD 20 phiên ≥ ngưỡng, cùng ngưỡng với advisor) vì mã thanh khoản kém làm nhãn nhiễu.
6. **Horizon 10/40 phiên** để xem tín hiệu bền hơn ở khung nào (chỉ giữ 20 nếu không có lợi rõ rệt).
7. Feature mới có lý do: khối ngoại cộng dồn dài hơn (60), biến động tương đối so với ngành, mức thay đổi định giá so với median ngành.

## C. Luật promote chặt hơn (sửa `promotion_decision` trong `signals/train.py`)
Hiện chỉ cần top-decile > momentum. Đề xuất promote khi **đồng thời**:
- top-decile mean excess > momentum **và** > trung bình universe;
- thắng momentum ở ≥ 60% số fold;
- hit rate top-decile > 50% hoặc khoảng tin cậy bootstrap (theo ngày) của (top-decile − universe) không chứa 0.
Model không đạt → không thay model đang active; advisor hiển thị cảnh báo "tín hiệu chưa đạt chuẩn".

## D. Giám sát sau triển khai
- Bảng/asset nhỏ so sánh `trade_signals` cũ với lợi suất thực 20 phiên sau (realized hit rate theo tuần) → đưa vào `docs/notes` hoặc dashboard Bài 26.
- Cảnh báo (asset check) khi hit rate thực 3 tháng gần nhất < universe.

## Xong khi
- A hoàn tất và có log Dagster thật.
- Có bảng so sánh các thử nghiệm B trên fold 1–7, một cấu hình được chọn **trước** khi nhìn holdout, sau đó báo cáo holdout một lần.
- Luật promote C được code + test; `signal_models.metrics` ghi lý do promote/không promote.
