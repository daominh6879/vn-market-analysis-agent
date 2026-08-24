# Bài 6 — So sánh công cụ parse PDF (2026-08-18)

**File:** `20260327_-_HPG_-_BCTC_Cong_ty_me_sau_kiem_toan_nam_2025.pdf` — 40 trang scan (không có text layer)

| Công cụ | Kết quả | Output |
|---|---|---|
| pymupdf4llm | OK, 93,302 chars | `outputs/hpg_pymupdf.md` |
| pdfminer / unstructured fast | **RỖNG** (236 chars) | `outputs/hpg_unstructured.md` |
| llamaparse | Cần API key | — |

**Vấn đề quan sát được với pymupdf4llm (English OCR):**

1. **pdfminer/unstructured trả về rỗng** — lỗi im lặng, 0 chars, không exception
2. **Dấu tiếng Việt sai:** "Công ty" → "Cong ty"/"Céng ty", "tiền" → "tién", "khoản" → "khodn"
3. **Header cột BCĐKT corrupt:** "Thuyết minh" → `T\nM4&sé\nhuyét\n minh`, "Mã số" → "M4&sé"
4. **Ký tự nhiễu scan:** `"4 'j iS y 1 a2 XS"`, `"] ] J"` — từ đường viền, logo mờ
5. **Chữ ký bị OCR thành row tài chính:** `|Ng|uyén Diéu Linh<br>Pham<br>ThiKimOanh||NguyenViétTh|ang||`
6. **Mã số KQKD bị cắt:** chỉ tiêu 11 → mã "1" thay vì "11"

**Fix: `ocr_language="vie+eng"` + `force_ocr=True`**

| | English OCR | vie+eng (đã áp dụng) |
|---|---|---|
| Tên công ty | "Céng ty" | "Công ty Cổ phần Tập đoàn Hòa Phát" ✓ |
| Tên bảng | "BANG CAN DOI KE TOAN" | "BANG CÂN ĐỐI KẾ TOÁN" ✓ |
| Khoản mục | "Phai thu ngan han" | "Phải thu ngắn hạn" ✓ |

Vẫn còn: header cột bị split, ký tự nhiễu, chữ ký bị OCR.

**Quyết định:** Dùng **pymupdf4llm** với `vie+eng`. Công cụ duy nhất hoạt động với PDF scan, giữ cấu trúc bảng markdown, số liệu 9-13 chữ số nguyên vẹn.
