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
17 câu hỏi (13 số liệu bảng cân đối, 3 chính sách trong thuyết minh, 1 câu đối chứng âm) trên HPG, VCB và VHM năm 2025 (VHM: số đọc trực tiếp từ ảnh PDF, vì OCR của VHM bỏ sót dòng tổng tài sản và đọc sai chữ số); mỗi câu có "gold" (chuỗi đã bỏ dấu phải nằm chung 1 chunk, được kiểm tra còn trong index trước khi chấm). Báo cáo riêng `ctx` (gold có trong top 5 không) và `ans` (câu trả lời đúng, 3 lần gọi LLM/câu). Kết quả lưu `evals/ask_report_eval.json`.

| | ctx (gold trong context) | ans số liệu | ans chính sách | ans đối chứng âm | câu rỗng |
|---|---|---|---|---|---|
| vector (top_k=5) | 8/14 | 15/30 | 6/12 | 3/3 | 0 |
| hybrid (top_k=7, 3 anchor) | 14/14 | 29/30 | 10/12 | 3/3 | 0 |

Số chính sách của vector dao động 6/12–9/12 giữa các lần chạy, hybrid 10/12–12/12. Các câu hybrid trượt đều có chunk vàng trong context: câu trả lời đúng nhưng thiếu một fact bắt buộc (ví dụ không nhắc "một năm" hay "nghĩa vụ").

Sau khi thêm 3 câu VHM và nạp 14 mã (run cuối, hybrid top_k=7): gold trong context 17/17 (vector 10/17), số liệu 38/39 (vector 21/39), chính sách 12/12 (vector 6/12), đối chứng âm 3/3. Câu trượt duy nhất: `vhm_total_assets` 2/3, một lần trả lời "Không có trong tài liệu" vì dòng "Tổng cộng tài sản" bị OCR bỏ và số 590.372.684 chỉ còn trên dòng "Tổng cộng nguồn vốn". `vhm_equity` đúng 3/3 dù OCR đọc sai một trong hai dòng vốn chủ sở hữu.

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
- **MBB, SAB: lớp chữ ẩn của PDF không dùng được (đã sửa, xem mục dưới).**
- **VHM:** dòng "270 Tổng cộng tài sản" bị OCR bỏ sót; tôi đọc trực tiếp ảnh trang PDF: tổng tài sản là 590.372.684 (triệu VND), cùng số với dòng "Tổng cộng nguồn vốn" mà OCR vẫn giữ. OCR còn đọc sai chữ số (dòng 400 ghi `172.731.896`, đúng là `172.731.696`; ở trang kia `16.578.452` thay vì `16.575.452`). Đây là giới hạn của OCR trên bản scan độ phân giải thấp. Thử `ocr_dpi` khác 150 không giúp: tỉ lệ nhận đúng số trên 2 trang (85 số đối chiếu bằng mắt) dao động 44–88% không theo quy luật (120 → 88%, 150 → 79%, 165 → 85%, 180 → 51%, 300 → 47%), nên không đổi tham số.

## Sửa lỗi sau lần chạy 10 mã (27/10/2026)
1. **CafeF 404 (đã sửa):** link trong danh sách CafeF nằm trên `cafefnew.mediacdn.vn`, host này trả 404 cho một số file, nhưng cùng đường dẫn trả 200 trên `cafef1.mediacdn.vn` và `s.cafef.vn` (kiểm tra bằng HEAD cho SSI, GAS và các mã khác; `cafef.vn` thì 404). `ingest/sync_bctc.download_pdf` thử lần lượt các host. 6 mã từng lỗi 404 (ACB, SSI, GAS, MSN, FPT, VNM) đều nạp được.
2. **Chọn báo cáo (đã sửa):** `annual_candidates()` xếp hạng: năm mới nhất trước, rồi loại báo cáo được yêu cầu, rồi bản đã kiểm toán; chỉ lùi về năm cũ hơn khi mọi host đều lỗi. MBB chỉ có BCTC hợp nhất cho FY2025 (không có công ty mẹ), nên giờ lấy hợp nhất FY2025 thay vì công ty mẹ FY2024; payload `report_type` ghi loại thực sự được nạp, `bctc_sync` ghi chú trong log (`notes`).
3. **Lớp chữ ẩn của PDF scan (đã sửa, `core/parse.py`):** `pymupdf4llm` với `force_ocr=True` là chế độ `FORCE_KEEP_OLD` (chỉ OCR trang chưa có chữ). Hai trường hợp lọt:
   - lớp OCR sẵn của máy scan không có dấu (MBB: `Ngan hang ThU'ong mc;:1i`, SAB, ACB; tỉ lệ chữ có dấu 0,0–2%, văn bản tiếng Việt thật khoảng 14–25%);
   - trang scan chỉ có vài ký tự rác (chữ ký số, chú thích QR): GAS có 118 trang nhưng 116 trang chỉ có ~24 ký tự chữ nên không trang nào được OCR, kết quả 14,6k ký tự / 20 chunk (nay 518 chunk).
   Giải pháp: `text_layer_problem()` phát hiện, `_rasterize_pdf()` dựng bản chỉ có ảnh (150 dpi, đúng độ phân giải OCR của pymupdf4llm) rồi OCR như scan thường. `FORCE_DROP_OLD` đã thử nhưng giữ lại cả chữ cũ (mỗi ô thành `cũ<br>mới`). PDF không có lớp chữ (HPG, VCB, TCB, MWG, VHM, PLX) không bị ảnh hưởng, đã kiểm tra detector trả `None` cho cả 6.
4. **Run `ca46822f` (601 s):** SAB, MBB, GAS, ACB, MSN, FPT, VNM → 7/7. Hiện 14 tài liệu active (HPG, VCB, MBB, TCB, MWG, VHM, PLX, SAB, ACB, SSI, GAS, MSN, FPT, VNM), tất cả FY2025 (MBB hợp nhất). Tỉ lệ chữ có dấu 22–25% (GAS 12%, VHM 11%, PLX 17%).

Còn lại: OCR GAS có số sai rõ ràng (`B7.190,620.643.636` ở dòng tổng tài sản, chưa đối chiếu PDF); chưa đối chiếu số liệu 12 mã còn lại với PDF; chưa chạy bootstrap toàn bộ 431 mã.

## Chạy thêm 15 mã (27/10/2026, run `195c73d4`)
BID, CTG, VPB, HDB, VIC, KDH, NVL, VJC, VND, HCM, DGC, GVR, REE, PNJ, BSR: SUCCESS sau 1521 s, `{'synced': 15, 'failed': 0, 'with_notes': 0}`. Không còn lỗi 404; không cần lùi năm hoặc đổi loại báo cáo. **101 s mỗi mã** (gồm OCR và embed); 402 mã còn lại × 101 s ≈ 11 giờ nếu mọi mã thành công. Hiện 29 mã trong `bctc_structural`.

Các PDF cần dựng lại bản ảnh trước khi OCR (`text_layer_problem`): "no diacritics" cho BID, HDB, NVL, GVR, REE (cùng SAB, ACB đã gặp trước đó); "stray text only" cho BSR (cùng GAS). Tức khoảng 1/3 số mã có lớp chữ ẩn không dùng được, nên bước này cần thiết cho bootstrap.

Kiểm tra nhất quán số liệu (heuristic thô: lấy số đầu tiên trên dòng "Tổng (cộng) tài sản" và dòng "Tổng (cộng) nguồn vốn" / "nợ phải trả và vốn chủ sở hữu", hai số phải bằng nhau), 29 mã:
- khớp: 18 (HPG, VCB, MWG, SAB, ACB, MSN, FPT, VNM, BID, VPB, VIC, KDH, NVL, VJC, HCM, DGC, REE, BSR);
- **lệch chữ số (OCR đọc sai):** PLX `…742…` vs `…712…`, CTG `2735488878` vs `2735388878`, PNJ `…815635…` vs `…818638…` (cùng một tổng, hai chỗ đọc khác nhau). Đây là lỗi thật, ít nhất 3/29 mã;
- không kết luận được: MBB, TCB (lệch có thể do dòng "Tổng tài sản" của bảng thuyết minh theo ngành), GVR (một số bị cắt), VHM (đã biết, không có dòng tổng tài sản);
- không thấy dòng nguồn vốn để so: SSI, GAS, HDB; VND dòng tổng tài sản là `000000000` (rác OCR).
Phép kiểm tra chỉ thấy lỗi ở hai dòng tổng; lỗi ở các dòng khác không được phát hiện, nên tỉ lệ lỗi chữ số thực tế cao hơn.

Ảnh hưởng: số liệu đi qua `sql_query` (DB vnstock) chứ không qua OCR; `ask_report` dành cho câu hỏi định tính (router: rủi ro, chiến lược, chính sách). Số trong câu trả lời của `ask_report` có thể sai chữ số với một phần mã.

## Kiểm tra cân đối lúc index (migration 044, 27/10/2026)
OCR đọc sai chữ số, nên mỗi tài liệu được kiểm tra: "Tổng (cộng) tài sản" phải bằng "Tổng (cộng) nguồn vốn" (ngân hàng: "Tổng tài sản" = "Tổng nợ phải trả và vốn chủ sở hữu"). Chỉ gắn cờ, không chặn index.
- `core/balance_check.py::balance_check(markdown)` → `ok` / `mismatch` / `unverified`. Nhãn dòng lấy từ các ô đứng trước số đầu tiên (bỏ ô "mã số"), cho phép nhiễu OCR tối đa 6 ký tự trước và 3 chữ cái sau từ khoá, dòng có hơn 3 số bị bỏ (bảng theo ngành), nên "Tổng tài sản thuế thu nhập hoãn lại" và bảng thuyết minh theo ngành không bị đọc nhầm là tổng. `mismatch` ghi số chữ số khác nhau khi hai số cùng độ dài. 8 test dùng dòng thật của HPG, VCB, VHM, PLX, VNM.
- `ingest/sync_bctc.ensure_quality()`: chạy cho tài liệu mới nạp và cho tài liệu đã nạp từ trước (đọc markdown đã parse trong MinIO, không OCR lại); ghi `documents.quality`/`quality_detail` (migration `044_documents_quality.sql`) và payload `quality` trên mọi chunk Qdrant. Ghi chú đi vào log và tóm tắt asset (`balance_mismatch`, `balance_unverified`). Cột chưa có (chưa chạy migration) thì chỉ cảnh báo, không ảnh hưởng việc đăng ký tài liệu.
- Kết quả trên 29 mã (run `0496aeae`, 20 s, không OCR): 19 `ok`, 4 `mismatch`, 6 `unverified`. Mismatch: TCB (`1125765759` vs `4125765759`), PLX, CTG, PNJ, đều lệch 1–2 chữ số. TCB trước đó tôi nhầm là "không kết luận được"; bản kiểm tra chặt hơn loại được bảng theo ngành và thấy lỗi thật. Unverified (thiếu hoặc không đọc được dòng tổng): MBB, VHM, SSI, GAS, VND, GVR. Qdrant: 4992 chunk `ok`, 1346 `mismatch`, 2725 `unverified`.
- Giới hạn: chỉ phát hiện lỗi ở hai dòng tổng; lỗi ở dòng khác, hoặc hai lỗi trùng nhau, không thấy. `unverified` không có nghĩa là đúng. `ask_report` dùng cờ: khi context có chunk của tài liệu `mismatch` và câu trả lời có số lớn (dạng `53.336.709.742.044`), nó thêm một dòng "⚠ Lưu ý" nêu mã bị cờ và ghi `Có cảnh báo OCR` vào `message`; `unverified` không cảnh báo (6/29 mã, sẽ thành nhiễu); câu trả lời không có số lớn (chính sách) không cảnh báo. Thử trên index thật: PLX hỏi tổng tài sản → có cảnh báo; HPG → không; PLX hỏi chính sách → không; PLX + HPG → cảnh báo chỉ nêu PLX. `tests/test_rag_query_quality.py`: 6 test.
- **Câu hỏi nhiều mã (đã sửa):** "So sánh tổng tài sản của PLX và HPG" trả lời lẫn lộn ("tài liệu chỉ có số liệu của một đơn vị") vì chunk đưa vào LLM không ghi thuộc mã nào (`prepend_metadata` chỉ dùng khi embed, không lưu vào text). `_label_hits()` gắn `[MÃ | năm | loại báo cáo]` vào đầu mỗi chunk trong context của `ask_report`, `_SYSTEM` dặn dùng đúng nhãn từng công ty. Eval dùng cùng context đó (`_retrieve_hits` + `_label_hits`) và có thêm 2 câu hai-mã (HPG–VCB, HPG–MWG): hybrid 3/3 cả hai, vector-only 0/3. Lần chạy cuối, 19 câu: gold trong context 17/17 (vector 10/17), số liệu 44/45 (vector 21/45), chính sách 12/12 (6/12), đối chứng âm 3/3; câu trượt duy nhất vẫn là `vhm_total_assets` 2/3.
- **Bản kiểm tra cân đối được làm chịu nhiễu hơn (`CHECK_VERSION = 2`):** nhãn lấy từ phần trước số đầu tiên (kể cả khi nhãn và số cùng một ô), cho phép một chữ cái đọc sai trong từ khoá ("NGUỒNVỐM"), nhiễu quanh mã "(z70=100+200)", số mất dấu chấm ("45.814.910047405"), dấu `**` chen trong số, và nhãn "Tổng cộng nợ phải trả và vốn chủ sở hữu" của công ty chứng khoán; vẫn loại "Tổng tài sản dài hạn", "… thuế thu nhập hoãn lại". Kết quả trên 29 mã: **21 ok, 5 mismatch, 3 unverified** (trước đó 19/4/6): GVR và SSI chuyển sang ok, GAS sang mismatch (số OCR hỏng thật: `7190620643636` vs `643636`). Còn unverified: MBB, VHM (OCR bỏ dòng "Tổng tài sản"), VND (dòng tổng là rác OCR). Phiên bản của luật được lưu trong `quality_detail.version`; `ensure_quality` tự kiểm tra lại tài liệu lưu bởi phiên bản cũ (từ markdown trong MinIO, không OCR), nên lần sau đổi luật chỉ cần tăng `CHECK_VERSION`. Làm mới 29 mã mất 40 s (run `0fff0cf7`).

## OCR hai engine cho trang báo cáo chính (27/10/2026)
**Nhận định sai trước đó:** tôi từng ghi VHM/MBB/VND "dòng bị OCR bỏ, không sửa được" và GAS "scan quá hỏng". Sai: ảnh nhúng trong PDF chỉ khoảng 90–102 dpi (VHM 90, GAS 102) nên Tesseract (thiết kế cho ~300 dpi) đọc số kém, nhưng engine khác đọc được. Đo trên 85 số của 2 trang cân đối VHM đã đối chiếu bằng mắt: Tesseract tốt nhất 75/85 (88%; psm 6, 150 dpi; ở 300 dpi chỉ 54%), **RapidOCR 85/85 (100%)** ở 150 dpi, 84/85 ở 300 dpi. Qua `pymupdf4llm`, RapidOCR lấy lại dòng "270 TỔNG CỘNG TÀI SẢN 590.372.684" và sửa vốn chủ sở hữu thành `172.731.696`. Nhược điểm: làm hỏng một phần dấu tiếng Việt (290 chữ có dấu / 2380 chữ cái, thường ~24%), nên không dùng cho cả tài liệu.
- `core/parse.parse_with_pymupdf`: pass chính bằng Tesseract (chỉ định tường minh, vì khi cài `rapidocr` thì `pymupdf4llm` tự chọn nó cho mọi trang); nếu `balance_check` ≠ ok thì OCR lại các trang báo cáo chính (`statement_pages`: tiêu đề/mẫu B01–B03 DN, B02–B04 TCTD trong 500 ký tự đầu trang, bỏ trang thuyết minh) bằng RapidOCR, ghép, và chỉ giữ bản ghép nếu nó qua kiểm tra. `Dockerfile.dagster`: `rapidocr` + `opencv-python-headless` (bản GUI cần libGL). 
- Chạy lại 8 tài liệu chưa đạt (run `ae806ca4`, 1141 s, 8/8): **PLX, VHM, VND → ok** (VHM lấy lại dòng tổng tài sản, PLX sửa chữ số, VND từng là rác OCR); TCB, CTG, PNJ, GAS vẫn mismatch, MBB vẫn unverified. Toàn bộ 29 tài liệu: 24 ok, 4 mismatch, 1 unverified.
- CTG (đã gỡ lỗi): RapidOCR đọc đúng tổng tài sản trang 10 (`2735388878`) nhưng ở trang 11 không trả về dòng tổng nguồn vốn đọc được, trong khi Tesseract đọc đúng dòng đó; thay nguyên trang làm mất dòng nên bản ghép thành `unverified` và bị loại.
- **Ghép nguyên trang là cách thô, nên đổi sang sửa từng số.** Qua `pymupdf4llm`, RapidOCR dựng lại bảng kém hơn Tesseract: CTG trang 10 chỉ 19/26 dòng, trang 11 chỉ 11/20; PNJ có trang ra 0 dòng; TCB mất dòng nhãn "tổng". Nhưng số nó đọc thì đúng. `repair_numbers()` giữ nguyên văn bản và bảng của Tesseract, chỉ thay một số (từ 6 chữ số) khi nó **không có trong tập số RapidOCR đọc được trên ảnh trang** và có **đúng một** số RapidOCR cùng độ dài lệch 1–2 chữ số (nhầm 8/5, 3/8, 1/4); giữ nguyên dấu phân cách. `_rapidocr_numbers()` đọc thẳng ảnh trang, không dựng bảng, ~11–20 s mỗi tài liệu. Chỉ chạy khi `balance_check` ≠ ok, và bản sửa chỉ được giữ nếu qua kiểm tra; nếu không thì thử phương án thay trang (lấy lại dòng bị bỏ như VHM); nếu cũng không được thì giữ nguyên Tesseract.
- **Đo trên đáp án thật:** 2 trang cân đối VHM, 79 số ≥ 6 chữ số đã đối chiếu bằng mắt: Tesseract 63/79 đúng, sau khi sửa 73/79; 10 số được sửa, cả 10 đúng; không số nào đang đúng bị làm hỏng; không xuất hiện số dài lạ. 6 số còn lại không có số gần giống để đối chiếu.
- **Kết quả (run `8fb4a66f`, 380 s, CTG/TCB/PNJ):** cả ba → ok (`ok` nghĩa là hai dòng tổng khớp; từng số được sửa còn lại không được kiểm tra riêng, ngoài phép đo VHM ở trên). Toàn bộ 29 tài liệu: **27 ok, 1 mismatch (GAS), 1 unverified (MBB)**.
- **Còn lại và lý do:** GAS: Tesseract đọc dòng tổng thành rác (`B7.190,620.643.636`; sửa 82 số khác nhưng tổng vẫn hỏng), nên không còn số hoàn chỉnh để đối chiếu; MBB: Tesseract không có dòng "Tổng tài sản" nào, mà sửa số không thêm được dòng, và thay trang bằng RapidOCR cũng không lấy lại được. Cả hai cần phương án khác (ví dụ OCR riêng vùng dòng tổng), chưa làm.

## Bootstrap toàn bộ 431 mã (27–28/10/2026)

- Lần chạy đầu: 6 run song song (402 mã) bị `run_monitoring` cắt ở 240 phút → 354 tài liệu. Bộ chạy tiếp (idempotent) chạy thêm các vòng cho phần thiếu.
- Sau các vòng: 424/431. 7 mã (ACL, BTT, CIG, CII, SGT, SJD, TTA) lỗi giống nhau: Ollama `/api/embeddings` trả 500 `the input length exceeds the context length`.
- Nguyên nhân (đã đo trên SJD, BTT): `add_table_context` nối nhãn "[Ngữ cảnh: ...]" quá dài — dòng trang thuyết minh bị OCR dồn thành một dòng bị nhận là tiêu đề cột (SJD: chunk 8.563 ký tự), và các dòng ngày nối dồn (BTT: 7.099 ký tự). Chunk gốc từ splitter luôn ≤ 799 ký tự.
- Sửa trong `rag/chunking.py`: dòng tiêu đề dài hơn 600 ký tự không được coi là tiêu đề cột (`_MAX_HEADER_LINE`); tổng nhãn tối đa 500 ký tự (`_MAX_LABEL`). Chunk lớn nhất: SJD 892, BTT 1.312 ký tự. Test: `tests/test_table_context.py`.
- Chạy lại 7 mã: cả 7 nạp được → **431/431**.
- Chất lượng (kiểm tra tổng tài sản = tổng nguồn vốn): ok 329, mismatch 29, unverified 73.
- Các ca mismatch "số bị cắt" (SVD, VFG, VSH, VVS) không phải lỗi bộ kiểm tra: văn bản OCR của hàng tổng thật sự hỏng (SVD nguồn vốn `45431338`; VFG `2.288,A55:444.855`; VSH `8.180 07858 298` thiếu một chữ số). Cần sửa ở bước OCR.
- Còn mở: GAS, MBB, VHM chưa sửa được; 73 tài liệu unverified chưa điều tra nguyên nhân.

### Phân loại 73 tài liệu unverified và sửa bộ kiểm tra (28/10/2026)

- Đo trên văn bản đã parse trong MinIO: 31 tài liệu không có nhãn "Tổng cộng tài sản"/"Tổng cộng nguồn vốn" ở một hoặc cả hai hàng (OCR làm mất nhãn); 42 tài liệu có nhãn nhưng bộ kiểm tra không đọc được hàng.
- Một nguyên nhân của nhóm thứ hai (đã kiểm chứng trên ANT, CCL, DRL): OCR gộp hàng tổng với hàng bên cạnh trong cùng ô qua `<br>`, nhãn và số nằm ở các dòng con khác nhau. `core/balance_check.py` nay tách ô theo `<br>` thành các hàng ảo (căn từ trên và từ dưới). `CHECK_VERSION` = 3.
- Chạy lại trên 431 tài liệu: không tài liệu `ok` nào bị đổi; 4 unverified → ok (APS, CCL, DRL, TCT), 3 unverified → mismatch (BMC, ECO, OCB). Sau khi áp dụng vào chỉ mục: ok 333, mismatch 32, unverified 66.
- 66 tài liệu còn lại không sửa được ở bộ kiểm tra: bước parse đã thử sửa số và đổi trang bằng RapidOCR khi kiểm tra ≠ ok, nên chúng cần cách OCR khác (ví dụ OCR theo vùng hàng tổng) — chưa làm.

## Cách chạy lại
```
docker exec dagster-daemon sh -c 'cd /app && dagster job launch -f pipeline/assets.py -d /app -j bctc_sync_job -c /tmp/run.yaml'
# /tmp/run.yaml:  ops: {bctc_sync: {config: {tickers: [HPG]}}}
```
`dagster job execute` (chạy in-process) lỗi `No module named 'ingest'`, nguyên nhân chưa tìm ra; `job launch` qua daemon chạy đúng.
