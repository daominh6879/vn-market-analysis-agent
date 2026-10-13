# Intent model (router huấn luyện, thay LLM khi đủ tin cậy)

Khái niệm và thiết kế: xem EXPLAIN.md ("Router huấn luyện: cascade thay vì thay thế hẳn"). Thư mục: `intent_model/` (prototype, **chưa nối vào agent** — không có file nào ngoài `intent_model/` import nó, trừ `core/tickers.py` được sửa riêng, xem bên dưới).

## Mục tiêu
Phục vụ câu hỏi chắc chắn mà không gọi LLM (giảm độ trễ và chi phí); độ tin cậy thấp thì rơi về `llm_route`. Nhãn đích = các trường router thật trả về: `intent` (14 nhãn trong `agents.classifier.INTENTS`), route type, `time_context`, `market/macro/rag_topics`, `risk_flags`, `language`, ticker/sector, `screening` filters.

## Quyết định
| Trường | Cách làm | Lý do |
|---|---|---|
| intent, route type, language, topics, risk flags | embedding + head tuyến tính (`train.py`) hoặc fine-tune encoder đa nhiệm (`finetune.py`) | ít tham số, đo được độ tin cậy bằng softmax |
| time_context | mô hình đoán `kind` (none/relative/absolute) + `unit`; số/năm/quý đọc bằng regex; `resolve_time` đổi ra ngày theo quy ước router | số liệu là chuỗi ký tự, không cần học |
| ticker/sector | **không mô hình hoá** — dùng `extract_focus_entities`/`extract_focus_sector` | tra bảng `securities` chính xác hơn; router thật cũng ưu tiên hàm này hơn câu trả lời LLM (`_parse_agent_fields`) |
| screening filters | **chưa làm** — cascade sẽ rơi về LLM với `screening` | ghép đúng chỉ báo–toán tử–ngưỡng cho nhiều bộ lọc cần token tagging |

Quy ước thời gian của router (lấy từ `AGENT_RUN_TOOL` trong `agents/conversation_router.py`): hôm qua = 1 ngày, tuần trước = 7, tháng trước = 30, quý trước = 90, năm ngoái = 365, N đơn vị = N × đơn vị. "Phiên" (session) quy ước 1 ngày — prompt router không định nghĩa, đây là giả định của `resolve_time`.

## Dữ liệu
- `data/seed.jsonl` (gold, đánh giá): 155 dòng = 133 gold + 22 silver. Nguồn: `evals/routing_parity_eval.json`, `intents_e2e_eval.json`, `multiturn_routing_eval.json` + viết tay `data/seed_manual.jsonl`, `seed_manual_2.jsonl` (có ticker/time/filters/topics/flags; các eval cũ không có nhãn thời gian). Silver = lấy từ output đã ghi của router ở dòng eval không lỗi.
- `data/synthetic.jsonl` (huấn luyện): **6.022 dòng** do LLM sinh (`generate_synthetic.py`), mỗi dòng có intent, tickers, time, filters, topics, risk flags, language. Sinh theo 8 phong cách (ngắn, trang trọng, không dấu, Anh–Việt, dài, mơ hồ, lỗi chính tả, tên công ty) và có "ranh giới" cho các cặp intent dễ nhầm.
- Lọc nhiễu nhãn lúc nạp (`common.load_synthetic`): còn **5.235/6.022**. Loại: `closing_commentary` có nêu mã 278, `breakout_scan` có mã 275, `screening` có mã 120, `market_brief` có mã 62, gắn "không thời gian" nhưng câu có khoảng thời gian rõ 29, `market_data` nêu mã mà không có topic thị trường 23. Quy tắc lọc là cách đọc `ROUTING_RULES` trong `agents/classifier.py`; chưa chạy router thật để đối chiếu. Hai nhóm ít nhất sau lọc: `closing_commentary` 147, `breakout_scan` 158.

## Phát hiện (đã kiểm chứng)
1. **Nhãn ticker do LLM sinh không đáng tin.** Lần đo trên 6.022 dòng: LLM để trống ở 473 dòng mà `extract_focus_entities` tìm được mã (hầu hết câu nhiều mã: "so sánh định giá HPG VNM FPT"); prompt ban đầu định nghĩa `ticker` là một chuỗi và dặn để trống khi có nhiều mã. Đã đổi thành danh sách `tickers`; `fix_tickers.py` ghi lại `tickers` = kết quả `extract_focus_entities`, giữ nhãn LLM trong `gen_tickers`.
2. **Nhiễu nhãn intent** (bảng trên) là nguyên nhân gây nhầm `market_data` ↔ `price_action` ở lần train đầu (xem kết quả dưới).
3. **Lỗi `extract_focus_entities`/`core/tickers.py`** (đã sửa, có test `tests/test_ticker_extraction.py`):
   - `VND` bị loại hẳn khỏi mã → "compare SSI and VND" ra `['SSI']`. Giờ VND là tiền tệ chỉ khi trong cặp tỷ giá ("USD/VND", "usd vnd") hoặc sau số tiền ("20.000 VND", "5 tỷ VND"); còn lại là mã VNDirect.
   - Câu không có chữ hoa: từ thường bị chuyển hoa thành mã ("oil and gas" → OIL, GAS; "cá tra" → TRA; "has", "dat"). Giờ `TRA, HAS, DAT, TRC, OIL, GAS` chỉ tính là mã khi người dùng viết hoa.
   - Câu có từ chữ hoa không phải mã ("MACD ssi tuần trước", "ROE của hpg") không còn che mất mã chữ thường.
   - `_normalize` không đổi `đ → d` (NFKD không tách "đ") nên tên công ty có "đ" gõ không dấu không khớp alias ("the gioi di dong" → MWG).
   - Chưa sửa: `SBV` (cũng là mã Siam Brothers, không phân biệt được với Ngân hàng Nhà nước chỉ bằng từ vựng); "VN milk" (đã thêm alias vào `TICKER_ENRICHMENT` trong `data/hose_universe.py`; DB cần seed lại `securities` mới có hiệu lực).

## Kết quả
**Baseline: bge-m3 đông cứng + logistic regression** (`train.py`), chạy trên **1.831 dòng synthetic chưa lọc nhiễu** và seed 155 dòng (thời điểm đó chưa có bản sửa `extract_tickers`):
- Dev split synthetic: intent 85,2%, route type 96,4%, language 96,4%, time kind 95,1%; time_context khớp ngày 92,9%; topics F1 micro: market 0,60, macro 0,76, rag 0,79, risk flags 0,67.
- Seed gold: intent 78,7% (n=155). Ngưỡng tin cậy → độ phủ / độ chính xác phần được phục vụ:

| Ngưỡng | Phủ | Chính xác |
|---|---|---|
| 0,5 | 78% | 88,4% |
| 0,6 | 75% | 90,5% |
| 0,7 | 64% | 93,9% |
| 0,8 | 50% | 97,4% |
| 0,9 | 34% | 100% |
| 0,95 | 19% | 100% |

- time_context trên seed: 105/108 khớp ngày. 3 sai: "dư luận về HPG gần đây" (đoán 1 ngày, nhãn: không thời gian), "lãi suất ngân hàng tháng này" (đoán 1 tháng, nhãn: không), "triển vọng VN30 tuần tới" (đoán n=30 tuần do regex đọc "30" của VN30 — đã sửa regex, nhãn "không" vì router chỉ định nghĩa quá khứ).
- Ticker bằng `extract_focus_entities`: 133/133 dòng gold đúng hoàn toàn (trước khi sửa lỗi ở trên; các dòng đó không chứa VND/chữ hoa lẫn thường).
- Yếu nhất: `market_data` recall 0,48, `price_action` precision 0,58 (dev split).

**Fine-tune (`finetune.py`, multilingual-e5-small, 6 epoch):** chạy trên dữ liệu đã lọc, **chưa có kết quả** tại thời điểm viết. Cập nhật mục này khi có số.

## Tái hiện
```
.venv\Scripts\activate
python intent_model/build_seed.py
python intent_model/generate_synthetic.py --append --per-intent 300 --workers 6   # thêm dữ liệu
python intent_model/fix_tickers.py            # tickers := extract_focus_entities (chạy SAU generate)
python intent_model/train.py                  # baseline đông cứng
python intent_model/finetune.py --model intfloat/multilingual-e5-small --epochs 6
pytest tests/test_ticker_extraction.py tests/test_focus.py
```
`generate_synthetic.py` ghi đè cả file khi kết thúc → chạy `fix_tickers.py` sau khi nó xong, không chạy song song.

## Việc tiếp theo
Phân tích ma trận nhầm `market_data`/`price_action`; so sánh mô hình với `llm_route` trên 155 câu seed (cũng để xác nhận các nhãn viết tay còn tranh cãi); thêm dữ liệu `closing_commentary`/`breakout_scan`; nối cascade vào `llm_route` chỉ sau khi có ngưỡng đo được.
