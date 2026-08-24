# Bài 14 — BM25 + tách từ tiếng Việt (2026-08-24)

**Pipeline:** structural + bge-m3 + không metadata → collection `hpg_structural`  
**Script:** `evals/run.py --retriever bm25 --collection hpg_structural`

## Cài đặt

```
uv add rank-bm25 underthesea
```

## Kiểm tra underthesea

```python
from underthesea import word_tokenize
print(word_tokenize("doanh thu thuần quý ba năm 2024", format="text"))
# Kết quả thực tế: "doanh_thu thuần quý ba năm 2024"
# "doanh_thu" → nối đúng
# "quý ba" → KHÔNG nối — underthesea bỏ sót cụm tài chính domain-specific
```

**Quan sát:** underthesea xử lý được cụm phổ thông (`doanh_thu`, `lợi_nhuận`) nhưng bỏ sót cụm tài chính (`quý ba`, `quý I`, `lãi suất`). Ảnh hưởng: query "quý 3" vs corpus "quý ba" → miss khi cần khớp chính xác.

## Kết quả eval (skip-ragas)

| Cấu hình | refusal_pass_rate | Ghi chú |
|---|---|---|
| Vector bge-m3 (baseline bài 8) | 1.000 | fixed_512, RAGAS đầy đủ |
| BM25 raw (split) | 0.800 | q23 trả về chuỗi rỗng |
| BM25 + VN tokenize | **1.000** | q23 từ chối đúng |

*Chưa chạy RAGAS đầy đủ — bảng điểm RAGAS sẽ bổ sung sau.*

## Phân tích per-question (10 câu indexed)

| q | Nhóm | BM25 raw ctx hit | BM25 vn ctx hit | Ghi chú |
|---|---|---|---|---|
| q08 | table_lookup | ❌ | ❌ | Lấy chunk định nghĩa, không lấy bảng số |
| q09 | text_interp | ✅ | ✅ | "131 người (2024: 154)" — exact match |
| q10 | text_interp | ✅ | ✅ | "hoạt động chính...cho thuê văn phòng" |
| q11 | text_interp | ✅ | ✅ | "mã HPG từ ngày 15 tháng 11 năm 2007" |
| q12 | text_interp | ❌ | ❌ | Lấy heading "KẾ TOÁN TRƯỞNG", không có tên |
| q13 | text_interp | ✅ | ✅ | "số 0503000008" — exact code match |
| q26 | text_interp | partial | partial | Lấy được 154, không có 127 (ở 2024 PDF) |
| q27 | text_interp | ❌ | ❌ | Nhầm 2025 doc, không phân biệt năm |
| q28 | text_interp | ❌ | ❌ | 2024 PDF không có trong corpus |
| q29 | table_lookup | ❌ | ❌ | Chunk bảng số dài hạn không lọt top-5 |
| q30 | table_lookup | ✅* | ❌ | **raw thắng vn** — xem bên dưới |

*q30 raw: chunk "10.247.400.472.100" nằm ở ctx[2] nhưng model trả lời rỗng (lỗi model, không phải retrieval).  
*q30 vn: `lợi_nhuận` compound thay đổi BM25 scoring → chunk đúng bị đẩy ra khỏi top-5.

## 6 ví dụ BM25 thắng vs vector thắng

### BM25 thắng (từ khoá chính xác)

1. **q13** — "Giấy Chứng nhận Đăng ký Kinh doanh lần đầu số 0503000008"  
   BM25 khớp chính xác chuỗi số `0503000008`, đưa đúng chunk vào top-1.  
   Vector search cần hiểu nghĩa "giấy đăng ký lần đầu" mới tìm đúng đoạn này.

2. **q11** — "mã chứng khoán HPG từ ngày 15 tháng 11 năm 2007"  
   Ticker `HPG` + ngày cụ thể → BM25 match chính xác. Câu hỏi không cần hiểu ngữ nghĩa.

3. **q30** — "Lợi nhuận sau thuế TNDN năm 2024 = 10.247.400.472.100 VND"  
   BM25 raw khớp "lợi nhuận sau thuế" trực tiếp với bảng KQKD, đưa chunk số vào top-5.  
   BM25 vn bị hurt vì `lợi_nhuận` compound làm thay đổi ranking (chunk đúng rớt khỏi top-5).

### Vector thắng (ngữ nghĩa)

1. **q12** — "Kế toán trưởng ký báo cáo là ai?"  
   BM25 lấy đúng heading "HỘI ĐỒNG QUẢN TRỊ...KẾ TOÁN TRƯỞNG" nhưng tên người ký nằm ở chunk khác (trang chữ ký). Vector search hiểu "người ký = tên dưới chữ ký" → có thể tìm đúng chunk hơn.

2. **q08** — "Đầu tư tài chính dài hạn 31/12/2025 = 97.018 tỷ"  
   BM25 lấy chunk định nghĩa khái niệm "đầu tư vào công ty con" (nhiều lần xuất hiện từ khoá). Số thực nằm trong bảng ở chunk khác. Vector search nhờ context embedding của bảng số có thể xếp hạng tốt hơn.

3. **q27** — "Hoạt động kinh doanh chính 2024 (có mua bán thép)"  
   BM25 không phân biệt năm — cả 2025 và 2024 đều có "hoạt động kinh doanh chính". Kết quả: trả về câu trả lời của 2025. Vector search với metadata năm hoặc semantic context có thể phân biệt tốt hơn.

## VN tokenize thắng/thua

**Thắng:** refusal_pass_rate 0.8 → 1.0 (q23 trả về rỗng → từ chối đúng)  
**Thua:** q30 — `lợi_nhuận` compound làm chunk đúng rớt khỏi top-5  
**Kết luận:** VN tokenize ổn định hơn trên refusal nhưng có thể hurt trên câu hỏi số cụ thể khi compound tokenization thay đổi IDF scoring.

## Lệnh chạy

```bash
# BM25 raw — không tách từ
make eval-bm25
# hoặc: uv run python evals/run.py --retriever bm25 --collection hpg_structural --skip-ragas --out evals/bm25_raw.json

# BM25 + VN tokenize
make eval-bm25-vn
# hoặc: uv run python evals/run.py --retriever bm25 --collection hpg_structural --vn-tokenize --skip-ragas --out evals/bm25_vn.json
```

## Cái bẫy gặp phải

**Cả hai PDF đều đã được index** — hpg_structural có 293 points, 2 doc_id:
- `f52ce0b29b0193c6` — 2024 PDF (sample: "31/12/2024|1/1/2024")
- `9bc110e242aeda01` — 2025 PDF

Vì vậy "2024 PDF chưa index" là **sai**. Root cause thật sự:

**BM25 không phân biệt được năm** — hai file cùng công ty, dùng y hệt từ khoá ("lợi nhuận sau thuế", "kế toán trưởng", "nhân viên"). BM25 không có khái niệm về metadata năm → chunk 2025 và chunk 2024 có score tương đương, 2025 thường thắng vì corpus lớn hơn hoặc may mắn hơn trong IDF. 

Ví dụ q27: query "hoạt động kinh doanh 2024" → BM25 trả về chunk 2025 (cùng từ khoá "hoạt động kinh doanh chính") thay vì chunk 2024 (có thêm "mua bán các sản phẩm thép"). Không có cách nào BM25 biết phải ưu tiên năm nào nếu không có metadata filter.

**Đây là lý do cần filter metadata theo năm** ở bài 17+ — BM25 và vector đều có vấn đề này, nhưng BM25 đặc biệt dễ bị vì không có context embedding để phân biệt ngữ cảnh năm.
