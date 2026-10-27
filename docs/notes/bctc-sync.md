# BCTC sync → Dagster (27/10/2026)

`scripts/sync_bctc.py` (CLI) đã chuyển thành asset Dagster `bctc_sync`; logic nằm ở `ingest/sync_bctc.py` (`run()`), CLI chỉ còn là wrapper.

## Đã làm
- `pipeline/assets_bctc.py`: asset `bctc_sync`, job `bctc_sync_job`, schedule `bctc_sync_weekly` (CN 01:00, `RUNNING`), `RetryPolicy(max_retries=2, delay=600)`. Config: `tickers` (rỗng = tất cả mã active), `report_type`.
- `data_bootstrap_sensor`: thêm check `bctc` — chạy job khi số dòng `documents` active (collection `bctc_structural`) < 50% số mã active.
- Run chỉ fail khi **không mã nào** thành công; mã lỗi lẻ vẫn ghi vào log (`failed=[...]`), run vẫn SUCCESS.
- `Dockerfile.dagster`: thêm `tesseract-ocr`, `-vie`, `-eng`. **Không** cài package `rapidocr`: pymupdf4llm 2.x thấy `rapidocr` là dùng luôn, không bao giờ tới nhánh Tesseract, và RapidOCR làm mất dấu tiếng Việt.
- `rag/index.py`: `OLLAMA_URL` đọc `OLLAMA_BASE_URL` (trước đó hardcode `localhost` → `Connection refused` trong container); payload Qdrant ghi thêm `ticker/sector/year/report_type/source_key` (filter của `tools/rag_query.py` cần các key này).
- `rag/lexical.py` + `_retrieve()` trong `tools/rag_query.py`: vector + lexical (bỏ dấu, bỏ khoảng trắng) hợp nhất theo điểm khớp (`hybrid_rank`).
- `rag/chunking.py::add_table_context`: nhãn báo cáo/ngày/cột cho chunk giữa bảng. Test: `tests/test_rag_lexical.py`, `tests/test_table_context.py`.

## Kết quả chạy thật
| Mã | Kết quả |
|---|---|
| HPG | PDF scan 41 trang, 168 chunk. Chạy lại → `up-to-date` |
| VCB | 287 chunk |
| FPT, VNM | lỗi `404 NOT FOUND` ở link PDF mà CafeF trả về (dry-run vẫn tìm thấy file trong danh sách) |

Run Dagster `7896bbc8` (HPG, đã đồng bộ): SUCCESS, `{'synced': 0, 'up_to_date': 1, 'failed': 0}`. Run `7d880f90` (VCB, FPT, VNM): SUCCESS, `{'synced': 1, 'failed': 2}`.

## Chất lượng retrieval (top_k=5, filter ticker + năm 2025)
Đối chiếu với dòng trong bảng cân đối kế toán đã OCR (đã kiểm tra tổng tài sản = nợ phải trả + vốn chủ sở hữu cho cả HPG và VCB). Mỗi câu hỏi gọi LLM 5 lần.

| Câu hỏi | Chỉ vector (lần đo đầu) | Sau cùng |
|---|---|---|
| HPG tổng tài sản (98.670.778.691.605) | "Không có" | 5/5 |
| HPG nợ phải trả (4.239.852.223.395) | đúng | 5/5 |
| HPG vốn chủ sở hữu cuối năm (94.430.926.468.210) | "Không có" / rỗng | 5/5 |
| HPG vốn chủ sở hữu đầu năm (80.780.186.578.052) | chưa đo | 5/5 |
| VCB tổng tài sản (2.426.649.127 triệu) | "Không có" | 5/5 |
| VCB vốn chủ sở hữu (220.701.017 triệu) | chưa đo | 5/5 |
| VCB tổng nợ phải trả (2.205.948.110 triệu) | đúng | 5/5 |

**Đính chính:** ở lần đo trước tôi ghi VCB tổng tài sản = 2.454.871.437 triệu là "đúng". Sai: đó là tổng trong bảng thuyết minh theo ngành (chunk 271, 279); dòng "TỔNG TÀI SẢN CÓ" của báo cáo tình hình tài chính riêng là **2.426.649.127** (2.205.948.110 + 220.701.017 = 2.426.649.127). Lúc đó chỉ kiểm tra nhất quán nội bộ, không đối chiếu dòng bảng cân đối.

## Nguyên nhân các lỗi trả lời (đã đo)
1. **Câu trả lời rỗng:** API DeepSeek trả đúng nội dung, nhưng `llm/utils.py::strip_thinking` (bật bởi `strip_thinking_output=True` trong `llm/factory.py`) xoá mọi dòng bắt đầu bằng "Theo", "Dựa", "Xem", "Để", "Vậy"... (`_THINKING_LINE_RE`) vì coi là chain-of-thought. Câu trả lời một dòng "Theo bảng…: X" bị xoá hết. Sửa ở `llm/openai_client.py`: giữ text gốc nếu strip ra rỗng. Rỗng 5/8 → 0/8. Heuristic vẫn có thể cắt dòng đầu của câu trả lời nhiều dòng; chưa sửa.
2. **"Không có trong tài liệu" dù số liệu có trong context:** chunk bắt đầu giữa bảng, không có tiêu đề cột/ngày. Chunk chứa dòng vốn chủ sở hữu HPG, 8 lần gọi: không tiêu đề 0/8 đúng, có tiêu đề 8/8. Sửa: `rag/chunking.py::add_table_context` gắn dòng `[Ngữ cảnh: <báo cáo> | <ngày> | Cột: <nhãn cột>]` vào chunk bảng không tự có tiêu đề (gọi trong `rag/index.py::run` cho strategy `structural`). Tên báo cáo lấy từ số mẫu (`Mẫu B01/B02/B03/B09-DN`, `B02/B03/B04/B05/TCTD`) hoặc tiêu đề, vì OCR làm hỏng tiêu đề nhưng số mẫu thường còn đọc được. HPG 63/168 chunk, VCB 113/287 chunk có nhãn.
3. **Chunk đúng không lên top 5 (VCB):** embedding của bảng ngân hàng sau OCR nhiễu — chunk chứa "TỔNG TÀI SẢN CÓ" xếp thứ 65 theo vector nhưng thứ 3 theo khớp chữ (bỏ dấu, bỏ khoảng trắng). Hợp nhất theo thứ hạng (RRF) không đủ vì không thấy độ mạnh của khớp (điểm 18 so với 4–5). `rag/lexical.py::hybrid_rank` cộng thêm số hạng từ điểm khớp chuẩn hoá (best match = 2 hạng vector đầu); khớp bằng nhau thì ưu tiên báo cáo chính hơn thuyết minh; 3 kết quả vector đầu luôn được giữ (xem mục eval).
4. **Hai số khác nhau trong context (VCB):** khi chunk bảng cân đối vào được top 5 nhưng cùng 3 chunk thuyết minh theo ngành, LLM chọn số thuyết minh 3/3 lần. Thêm vào `_SYSTEM` (tools/rag_query.py): ưu tiên báo cáo chính cho chỉ tiêu tổng hợp, nêu số còn lại nếu khác. Sau đó 5/5.

## Quan sát
- OCR Tesseract sửa dấu tiếng Việt nhưng trong bảng vẫn mất khoảng trắng và đọc sai vài ký tự (`181` thay vì `131`, `Ill.` thay vì `III.`).
- Thay đổi gắn nhãn ngữ cảnh làm đổi nội dung chunk nhưng không đổi số chunk (HPG 168, VCB 287); `doc_id` (hash nội dung parse) không đổi nên phải xoá điểm cũ trước khi sync lại (`sync_ticker` coi là `up-to-date`).
- Test không chạy được trong container vì lý do môi trường: `tests/test_idempotent.py` (hardcode `localhost:6333`), `tests/test_llm.py::test_factory_gemini` (image không có package Gemini).
- Không eval nào trong `evals/` chạm `ask_report` (`eval_rag_qa_ragas.py` đi qua `rag/qa.py`, router/intents eval chỉ kiểm tra chọn tool). Đã viết `evals/eval_ask_report.py`, xem mục dưới.

## Eval `ask_report` (evals/eval_ask_report.py, 27/10/2026)
14 câu hỏi (10 số liệu bảng cân đối, 3 chính sách trong thuyết minh, 1 câu đối chứng âm) trên HPG và VCB năm 2025; mỗi câu có "gold" (chuỗi đã bỏ dấu phải nằm chung 1 chunk, được kiểm tra còn trong index trước khi chấm). Báo cáo riêng `ctx` (gold có trong top 5 không) và `ans` (câu trả lời đúng, 3 lần gọi LLM/câu). Kết quả lưu `evals/ask_report_eval.json`.

| | ctx (gold trong context) | ans số liệu | ans chính sách | ans đối chứng âm | câu rỗng |
|---|---|---|---|---|---|
| vector (top_k=5) | 8/14 | 15/30 | 6/12 | 3/3 | 0 |
| hybrid (top_k=7, 3 anchor) | 14/14 | 29/30 | 10/12 | 3/3 | 0 |

Số chính sách của vector dao động 6/12–9/12 giữa các lần chạy, hybrid 10/12–12/12. Các câu hybrid trượt đều có chunk vàng trong context: câu trả lời đúng nhưng thiếu một fact bắt buộc (ví dụ không nhắc "một năm" hay "nghĩa vụ").

Hybrid bản đầu (top_k=5, không anchor) có ctx 13/14 và chính sách 6/12 (vector 8/12 ở lần đo đó). Các lỗi đã điều tra:
- `vcb_provisioning_policy` (3/3 → 0/3): câu hỏi rộng nên hàng chục chunk khớp "dự phòng rủi ro tín dụng" gần như bằng nhau (điểm 155–171, chunk vàng "dự phòng rủi ro chung 0,75%" chỉ 59); phần thưởng lexical của đám đông đẩy chunk vector xếp hạng 3 ra khỏi top 5. IDF theo n-gram và giảm trọng số lexical đều không sửa được. Không có thống kê phía câu hỏi nào tách được hai nhóm (số chunk khớp ≥70% điểm tốt nhất: `vcb_equity` 10, `vcb_trading_securities` 19, `vcb_provisioning_policy` 6). Sửa: `hybrid_rank(anchors=3)` luôn giữ 3 kết quả vector đầu, `HYBRID_TOP_K=6`. Với top_k=5, anchor chỉ đổi lỗi này sang `vcb_trading_securities`; từ top_k=6 với 3 anchor đạt 14/14 và giữ nguyên ở 7, 8.
- `hpg_unearned_revenue` (hybrid 1/3): chunk chính sách có trong context nhưng LLM trả số dư "Mã số 318". Nguyên nhân là quy tắc `_SYSTEM` "ưu tiên số báo cáo chính" quá rộng; giới hạn lại chỉ áp dụng khi câu hỏi hỏi con số, và câu hỏi chính sách/"như thế nào" thì trả nội dung thuyết minh. Sau đó 3/3.

- `HYBRID_TOP_K`: lúc đầu chọn 6 (14/14), nhưng sau khi sync lại với nhãn ngữ cảnh mới (đổi văn bản được embed) `vcb_trading_securities` rơi khỏi top 6 → 13/14 (chunk vàng xếp hạng 6 theo vector, hạng 15 theo chữ trong 15 chunk gần bằng nhau). Đo lại bằng `--no-answer` trên index hiện tại: top_k=6 → 13/14; top_k=7/8/10 với 3 anchor → 14/14; ở index trước k=6..8 đều 14/14. Chọn 7 là mức nhỏ nhất đúng ở cả hai trạng thái. Đây là ca biên, nhạy với thay đổi nhỏ của embedding.

Lưu ý khi đọc kết quả: top_k, số anchor và câu chữ prompt được chọn trên chính 14 câu này nên 14/14 là số trên tập tinh chỉnh, không phải ước lượng độc lập; mẫu nhỏ (2 mã, 3 lần chạy). Cùng một ca vector dao động 2/3 ↔ 3/3 giữa hai lần chạy (`hpg_unearned_revenue`), nên chênh 1 lần gọi không đáng tin. Cần thêm câu hỏi (mã khác, báo cáo kết quả kinh doanh/lưu chuyển tiền tệ) trước khi coi là ổn định.

Chạy: `docker cp evals/eval_ask_report.py dagster-daemon:/app/evals/ && docker exec dagster-daemon python evals/eval_ask_report.py --runs 3` (thêm `--no-answer` để chỉ đo retrieval, không gọi LLM).

## Chạy thử 10 mã qua Dagster (27/10/2026, run `0d51d902`)
`bctc_sync_job` với ACB, MBB, TCB, MWG, VHM, SSI, GAS, PLX, SAB, MSN: SUCCESS sau 480 s, `{'synced': 6, 'failed': 4}`.

| Mã | Kết quả |
|---|---|
| MBB, TCB, MWG, VHM, PLX, SAB | đã nạp: 336, 306, 102, 713, 246, 218 chunk |
| ACB, SSI, GAS, MSN | `404 NOT FOUND` ở link PDF CafeF trả về |

Tổng đã thử 14 mã: 8 nạp được, 6 lỗi 404 (FPT, VNM, ACB, SSI, GAS, MSN). Mẫu do tôi chọn, không ngẫu nhiên. Toàn universe có 431 mã active; hiện có 8 tài liệu trong `bctc_structural`. Thời gian: khoảng 80 s mỗi mã nạp được (gồm OCR) nên một lần bootstrap đầy đủ có thể kéo dài hàng giờ; đây là ước lượng từ mẫu nhỏ, chưa đo.

Vấn đề thấy trong dữ liệu đã nạp:
- **Nhãn ngữ cảnh sai (đã sửa; sync lại 8 mã, run `9e98762e`, 601 s, 8/8):** MBB 137/336 chunk nhãn "Báo cáo của Ban điều hành", VHM 222/713 chunk nhãn "lưu chuyển tiền tệ", SAB 18 chunk nhãn "báo cáo kiểm toán". Nguyên nhân: (a) tiêu đề chung chung (báo cáo của Ban điều hành/kiểm toán) trở thành nhãn thay vì xoá nhãn; (b) `_title_of` khớp chuỗi con nên dòng "THÔNG TIN BỔ SUNG VỀ BÁO CÁO LƯU CHUYỂN TIỀN TỆ" trong thuyết minh bị coi là tiêu đề báo cáo; (c) tiêu đề MBB (`BAO CAO TINH Hi NH TAI CHiNH Rl~NG tc;1i ngay…`) và số mẫu `B02/TCTD` (không có chữ "Mẫu") không được nhận ra. Sau sửa: tiêu đề phải đứng đầu dòng (hoặc sau tên công ty), kiểm tra chữ hoa trên 28 chữ cái đầu, tiêu đề lạ xoá nhãn, hàng có 2 ngày `dd/mm/yyyy` được nhận là hàng cột. Nhãn lệch còn lại của MBB: 10 chunk chỉ có nhãn cột (có ngày kỳ báo cáo).
- **Văn bản MBB hỏng từ gốc:** mã hoá font sai nên "Tiền mặt" thành `Ti~nm~t`, "Dự phòng rủi ro" thành `Dl,l'ph6ng rui ro`. Truy vấn chữ trên MBB sẽ kém; chưa xử lý.
- **MBB năm 2024:** payload `year` = '2024' (các mã khác 2025), nên lọc `year='2025'` sẽ không thấy MBB. Chưa xác định CafeF chưa có BCTC 2025 của MBB hay nguyên nhân khác.
- **VHM:** không tìm thấy dòng "Tổng tài sản" có số trong 713 chunk (các mã khác đều có). Chưa điều tra.
- Chưa đối chiếu số liệu của 6 mã mới với PDF.

## Cách chạy lại
```
docker exec dagster-daemon sh -c 'cd /app && dagster job launch -f pipeline/assets.py -d /app -j bctc_sync_job -c /tmp/run.yaml'
# /tmp/run.yaml:  ops: {bctc_sync: {config: {tickers: [HPG]}}}
```
`dagster job execute` (chạy in-process) lỗi `No module named 'ingest'`, nguyên nhân chưa tìm ra; `job launch` qua daemon chạy đúng.
