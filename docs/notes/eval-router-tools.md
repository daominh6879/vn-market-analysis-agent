# Eval router + tools — mọi intent

> **Khác với [eval-rag-qa-ragas.md](eval-rag-qa-ragas.md):** file đó đo chất lượng nội dung câu trả lời (RAGAS), chỉ scope `rag_qa`. File này đo **định tuyến (router) đúng không + tool đúng có chạy ra data không**, phủ **tất cả intent** — không dùng RAGAS, không cần `ground_truth` văn bản.

## Mục đích

`evals/eval_router_tools.py` trả lời 3 câu hỏi riêng biệt cho mỗi case, không mock:

1. **Intent đúng?** — `classify_hybrid(question)` (LLM thật) có trả về đúng intent kỳ vọng.
2. **Ticker đúng?** — router có resolve đúng ticker kỳ vọng (hoặc bỏ qua nếu case không assert ticker cụ thể).
3. **Tool đúng có chạy?** — tool được wire cho intent đó trong `agents.graph._get_gather_map()` có thực sự chạy và trả data hợp lệ (không lỗi/rỗng, check qua `agents.graph._is_empty_result`).

Case nào sai (1) thì (3) tự động SKIP — không gọi tool cho intent sai, tránh báo cáo sai lệch.

## Bộ câu hỏi — mục đích từng câu, expect gì

19 câu, copy từ `CLASSIFY_CASES` trong `tests/test_router_intents_cache.py` — mỗi câu chọn có chủ đích để test 1 khía cạnh cụ thể của router/tool, không phải chọn ngẫu nhiên.

| # | Intent kỳ vọng | Câu hỏi | Ticker kỳ vọng | Mục đích (test cái gì) | Tool được gọi | Expect |
|---|---|---|---|---|---|---|
| 1 | `price_action` | Khối ngoại mua bán ròng HPG hôm nay thế nào? | HPG | Case chuẩn nhất của `price_action` — có ticker rõ, từ khóa "khối ngoại mua bán ròng" đặc trưng. Baseline trước khi xét case mơ hồ. | `price_action.gather_data` | intent=`price_action`, ticker=`HPG`, tool trả data giá/dòng tiền không rỗng. |
| 2 | `price_action` | Khối lượng giao dịch HPG có đột biến không? | HPG (không assert cứng — `_SKIP`) | Từ khóa "khối lượng giao dịch" dễ nhầm sang `technical_analysis` (khối lượng cũng là chỉ báo kỹ thuật) — test router phân biệt đúng theo ngữ cảnh câu hỏi (hỏi hiện tượng thị trường, không hỏi chỉ báo). | `price_action.gather_data` | intent=`price_action` (không check ticker cụ thể). |
| 3 | `technical_analysis` | RSI và MACD của FPT đang như thế nào? | FPT | Ticker KHÁC HPG (FPT) — test router/ticker-extraction không bị lệ thuộc case HPG-only quen thuộc; tên chỉ báo kỹ thuật rõ (RSI/MACD). | `technical.gather_data` | intent=`technical_analysis`, ticker=`FPT`. |
| 4 | `technical_analysis` | Vùng hỗ trợ kháng cự của VNM ở đâu? | VNM | Ticker thứ 3 khác (VNM) + từ khóa "hỗ trợ kháng cự" không chứa tên chỉ báo trực tiếp (khác câu #3) — test router nhận diện technical qua khái niệm, không chỉ qua từ khóa RSI/MACD cứng. | `technical.gather_data` | intent=`technical_analysis`, ticker=`VNM`. |
| 5 | `technical_analysis` | Phân tích kỹ thuật HPG hôm nay | HPG | Câu ngắn, chung ("phân tích kỹ thuật") không nêu chỉ báo cụ thể nào — test router route đúng nhóm intent khi câu hỏi mơ hồ/tổng quát. | `technical.gather_data` | intent=`technical_analysis`, ticker=`HPG`. |
| 6 | `valuation` | P/E của HPG hiện tại so với trung bình ngành thế nào? | HPG | Có "so sánh với trung bình ngành" — test router không bị kéo sang `macro_sector` (có chữ "ngành") dù trọng tâm câu hỏi vẫn là P/E của 1 mã cụ thể. | `fundamentals.gather_data` | intent=`valuation` (không lệch `macro_sector`), ticker=`HPG`. |
| 7 | `valuation` | ROE của VCB năm ngoái là bao nhiêu? | VCB (không assert ticker) | Case gây nhầm thực tế đã gặp: "ROE" dễ tưởng là `rag_qa` (tra cứu số liệu BCTC) nhưng đúng ra thuộc `valuation` (chỉ số định giá). Đây chính là case đã sửa sai ở `eval_rag_qa_ragas.py` trước đó. | `fundamentals.gather_data` | intent=`valuation` — **không phải `rag_qa`**. |
| 8 | `rag_qa` | Doanh thu HPG năm 2024 là bao nhiêu? | HPG | Câu tra cứu số liệu BCTC gốc — đối chứng với case #7 để khẳng định router phân biệt được "doanh thu" (rag_qa, tra bảng) vs "ROE" (valuation, chỉ số tính toán). | `rag.qa.retrieve_only` | intent=`rag_qa`, ticker=`HPG`. |
| 9 | `rag_qa` | Doanh thu và lợi nhuận HPG năm 2024 là bao nhiêu? | HPG | 2 metric trong 1 câu — test router vẫn nhận đúng `rag_qa` khi câu hỏi phức tạp hơn (không chỉ 1 con số). | `rag.qa.retrieve_only` | intent=`rag_qa`, ticker=`HPG`. |
| 10 | `macro_sector` | Tỷ giá USD/VND hôm nay ảnh hưởng gì đến FPT? | (không assert ticker) | Có nêu tên ticker (FPT) nhưng trọng tâm câu hỏi là yếu tố vĩ mô (tỷ giá) — test router không bị "thấy ticker là route sang intent theo-ticker" (price_action/technical/valuation), phải nhận macro là trọng tâm. | `macro_sector.gather_data` | intent=`macro_sector`. |
| 11 | `macro_sector` | Giá thép HRC thế giới tăng ảnh hưởng HPG thế nào? | (không assert ticker) | Tương tự #10 nhưng yếu tố vĩ mô là giá nguyên liệu ngành (thép HRC) — test router nhận diện macro qua nhiều loại yếu tố khác nhau (tỷ giá, giá hàng hóa), không chỉ 1 dạng. | `macro_sector.gather_data` | intent=`macro_sector`. |
| 12 | `news_sentiment` | Tin tức về HPG trong 3 ngày gần nhất | HPG | Case chuẩn — từ khóa "tin tức" rõ, có mốc thời gian ("3 ngày gần nhất") — test router + time_context parsing đơn giản nhất. | `news_sentiment.gather_data` | intent=`news_sentiment`, ticker=`HPG`. |
| 13 | `news_sentiment` | Sentiment của cộng đồng về VNM như thế nào? | VNM (không assert) | Không có chữ "tin tức" — dùng "sentiment"/"cộng đồng" — test router nhận diện qua khái niệm (cảm xúc thị trường), không chỉ qua từ khóa "tin tức" cố định. | `news_sentiment.gather_data` | intent=`news_sentiment`. |
| 14 | `investment_case` | HPG có nên mua không? | HPG | Câu quyết định đầu tư rõ nhất ("có nên mua") — baseline cho intent tổng hợp (decompose nhiều sub-task). | `investment_case.gather_data` | intent=`investment_case`, ticker=`HPG`. |
| 15 | `investment_case` | Khuyến nghị VCB lúc này: mua bán hay nắm giữ? | VCB | Cách hỏi khác #14 ("khuyến nghị", "mua bán hay nắm giữ") + ticker khác — test router nhận diện investment_case qua nhiều cách diễn đạt, không chỉ mẫu câu "có nên mua". | `investment_case.gather_data` | intent=`investment_case`, ticker=`VCB`. |
| 16 | `screening` | Lọc cổ phiếu có ROE > 20% | (không ticker — quét toàn thị trường) | Không có ticker cụ thể, có điều kiện lọc số (ROE > 20%) — test router nhận diện `screening` (quét nhiều mã) khác với `valuation`/`rag_qa` (hỏi 1 mã), và tool nhận đúng filter `{indicator, op, threshold}`. | `screening.gather_data` (kèm `sf=` filter) | intent=`screening`, tool chạy với filter đúng, không lỗi. |
| 17 | `breakout_scan` | Quét cổ phiếu đang breakout tạo đỉnh mới | None | Không ticker, không filter số như screening — thuần "quét kỹ thuật toàn thị trường" — test router phân biệt `breakout_scan` với `screening` (cả 2 đều "quét nhiều mã" nhưng khác cơ chế: breakout dựa tín hiệu kỹ thuật, screening dựa điều kiện số tùy chọn). | `breakout.gather_data` | intent=`breakout_scan`. |
| 18 | `market_brief` | Thị trường chứng khoán hôm nay thế nào? | None | Hỏi tổng quan thị trường (VNINDEX/toàn cảnh), không hỏi mã cụ thể — test router nhận diện đúng intent "brief" khác mọi intent theo-mã ở trên. | Không có tool riêng — route thẳng, marker tĩnh. | intent=`market_brief`. |
| 19 | `conversation` | Xin chào bạn tên gì | None | Câu chào hỏi thuần xã giao, không liên quan tài chính — test router KHÔNG lỡ route sang bất kỳ intent tài chính nào (âm tính giả). | Không có tool — trả lời trực tiếp. | intent=`conversation`. |

**Logic chọn bộ câu hỏi:** mỗi cặp câu trong cùng intent (VD #1-#2, #3-#4-#5, #10-#11, #14-#15) cố tình khác nhau về **cách diễn đạt** hoặc **ticker**, không lặp lại mẫu câu — để bộ test không chỉ pass vì router học "khớp từ khóa cố định". Case #7 (ROE) và #8 (Doanh thu) đặt cạnh nhau có chủ đích: đây là ranh giới `valuation` vs `rag_qa` dễ nhầm nhất trong hệ thống (đã có bug thật xảy ra ở bước trước khi thêm case này vào `eval_rag_qa_ragas.py`).

## Cách chạy

```bash
.venv\Scripts\activate
python evals/eval_router_tools.py
python evals/eval_router_tools.py --out evals/router_tools_eval.json
```

Yêu cầu: đủ hạ tầng cho mọi tool (Postgres, Qdrant, VCI/yfinance nếu có, `.env` đủ key) — vì script gọi tool thật cho từng intent, không mock riêng lẻ như test unit.

## Đọc kết quả

Output in 3 cột `intent=OK/FAIL ticker=OK/FAIL tool=OK/FAIL` mỗi case + accuracy tổng theo từng chiều, cộng danh sách case fail ở cuối. Lưu chi tiết vào `evals/router_tools_eval.json` (bao gồm `tool_note` — lý do fail cụ thể: tool raise exception, trả rỗng, hay bị skip vì router không resolve ticker).

## Kết quả lần chạy gần nhất (2026-09-09)

| Chiều đo | Kết quả |
|---|---|
| intent accuracy | 19/19 = 100% |
| ticker accuracy | 19/19 = 100% |
| tool ok | 19/19 = 100% (sau khi sửa false positive — xem bug dưới) |

Tất cả 19 case pass cả 3 chiều — router + tool wiring đúng cho mọi intent ở bộ câu hỏi hiện tại.

### Bug đã gặp + đã sửa: `_is_empty_result()` false positive trong check "tool ok"

Lần chạy đầu: 5/19 case báo `tool=FAIL` (`macro_sector` ×2, `news_sentiment` ×1, `investment_case` ×2) dù xem preview thấy rõ data hợp lệ (tỷ giá, tin tức, giá cổ phiếu). Verify tay bằng cách gọi trực tiếp `agents.graph._get_gather_map()['macro_sector'](...)`:

```
[VĨ MÔ & NGÀNH]

Tỷ giá: Vietcombank — mua: 25,680 | bán: 26,090 | CK: 25,710 VND

Hàng hóa (thế giới): • Gold: 4,451.80 USD/oz ...

Ngành: Không có dữ liệu ngành.
```

Data chính (tỷ giá, hàng hóa) đầy đủ — chỉ phần "Ngành" hợp lệ không có data (đúng thực tế, không phải bug gather). Nhưng `agents.graph._is_empty_result()` — vốn dùng trong production để quyết định replan (`route_after_subqueries`) — check **substring containment** ("không có dữ liệu" xuất hiện ở BẤT KỲ đâu trong chuỗi → coi là empty toàn bộ), nên bị false positive ở đây.

**Đã sửa trong `eval_router_tools.py`:** không dùng `_is_empty_result()` nữa cho việc check "tool có chạy ra data không" — thay bằng check "output không rỗng VÀ không phải TOÀN BỘ chuỗi chỉ là 1 câu lỗi/no-data" (`stripped.lower().startswith(...)` thay vì `in`).

**Chưa sửa — finding cho production:** `_is_empty_result()` ở `agents/graph.py` vẫn dùng containment check. Nếu 1 sub-task trong `run_subqueries_node` trả về data phần lớn tốt nhưng có lẫn 1 câu "không có dữ liệu X" cho 1 khía cạnh nhỏ, `route_after_subqueries` có thể tính nhầm `sub_results_empty_ratio` cao hơn thực tế → **replan oan** dù data thực ra vẫn dùng được. Chưa verify mức độ ảnh hưởng thật trong production (cần xem log `traces/latest.jsonl` số lần replan thực tế), chỉ mới xác nhận cơ chế false-positive tồn tại.
