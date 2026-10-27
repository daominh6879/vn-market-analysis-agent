"""rag/chunking.add_table_context — column/date context for chunks that start mid-table (scanned BCTC)."""
from rag.chunking import _header_cells, _title_of, add_table_context

DOC = """BẢNG CÂN ĐỐI KẾ TOÁN

Tại ngày 31 tháng 12 năm 2025
Đơn vị: VND

|TAISAN|Mã số|Thuyết<br>minh|Sốcuối năm|Sốđầu năm|
|---|---|---|---|---|
|A.|TÀI SẢN NGẮN HẠN|100||1.383|1.015|

|8.|Quỹ khen thưởng|322|17|878|938|
|D.|VỐN CHỦ SỞ HỮU|400||94.430|80.780|

Tiền và các khoản tương đương tiền được ghi nhận theo giá gốc.

BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH

|1.|Doanh thu|01|5|100|90|
"""


def test_title_and_header_detection_tolerate_ocr_damage():
    assert _title_of("BẢNG CÂN ĐỐI KẾ TOÁN") == "BẢNG CÂN ĐỐI KẾ TOÁN"
    assert _title_of("BANG CAN ĐOI KE TOAN") is not None
    assert _title_of("Tiền và các khoản tương đương tiền được ghi nhận") is None
    assert _title_of("|Báo cáo tình hình tài chính riêng|6-8|") is None
    assert _title_of("được ghi nhận vào báo cáo kết quả hoạt động riêng theo phương pháp") is None
    assert _title_of("CÁC KHOẢN MỤC NGOÀI BẢNG CÂN ĐỐI KẾ TOÁN") is None
    assert _header_cells("|TAISAN|Mã số|Thuyết<br>minh|Sốcuối năm|Sốđầu năm|") == [
        "TAISAN", "Mã số", "Thuyết minh", "Sốcuối năm", "Sốđầu năm"]
    assert _header_cells("|A.|TÀI SẢN NGẮN HẠN|100||1.383|1.015|") is None


def test_mid_table_chunk_gets_title_date_and_columns():
    chunk = "|8.|Quỹ khen thưởng|322|17|878|938|\n|D.|VỐN CHỦ SỞ HỮU|400||94.430|80.780|"
    first, = add_table_context(DOC, [chunk])
    assert first.startswith("[Ngữ cảnh: BẢNG CÂN ĐỐI KẾ TOÁN | Tại ngày 31 tháng 12 năm 2025")
    assert "Cột: TAISAN | Mã số | Thuyết minh | Sốcuối năm | Sốđầu năm]" in first
    assert first.endswith(chunk)


def test_chunks_with_own_header_or_no_rows_are_unchanged():
    with_header = DOC[DOC.index("|TAISAN"):DOC.index("|8.")].strip()
    prose = "Tiền và các khoản tương đương tiền được ghi nhận theo giá gốc."
    assert add_table_context(DOC, [with_header, prose]) == [with_header, prose]


def test_new_statement_does_not_inherit_previous_columns():
    chunk = "|1.|Doanh thu|01|5|100|90|"
    out, = add_table_context(DOC, [chunk])
    assert "KẾT QUẢ HOẠT ĐỘNG" in out and "Sốcuối năm" not in out


def test_chunk_not_found_in_text_is_kept():
    assert add_table_context(DOC, ["|x|y|"]) == ["|x|y|"]


def test_form_number_and_running_header_identify_statement():
    assert _title_of("MẪU SỐ B 09-DN") == "THUYẾT MINH BÁO CÁO TÀI CHÍNH"
    assert _title_of("CÔNGTY CO PHAN TAP DOAN HÒA PHAT THUYET MINH BAO CÁO TAI CHINH RIENG (Tiép theo)") == "THUYẾT MINH BÁO CÁO TÀI CHÍNH"
    assert _title_of("Mẫu B02/TCTD (Ban hành theo Thông tư số 49/2014)") == "BÁO CÁO TÌNH HÌNH TÀI CHÍNH"
    assert _title_of("Mẫu B04/TCTD (Ban hành theo Thông tư số") == "BÁO CÁO LƯU CHUYỂN TIỀN TỆ"
    assert _title_of("MAU SỐ B 03-DN") == "BÁO CÁO LƯU CHUYỂN TIỀN TỆ"


def test_notes_tables_after_cash_flow_are_not_labelled_cash_flow():
    doc = ("BÁO CÁO LƯU CHUYỂN TIỀN TỆ\n\n|Mã số|Chỉ tiêu|Năm nay|Năm trước|\n|01|Lợi nhuận|5|4|\n\n"
           "CÔNG TY CỔ PHẦN HÒA PHÁT THUYẾT MINH BÁO CÁO TÀI CHÍNH RIÊNG (Tiếp theo)\nMẪU SỐ B 09-DN\n\n"
           "|1.|Tiền mặt|10|20|\n")
    out, = add_table_context(doc, ["|1.|Tiền mặt|10|20|"])
    assert out.startswith("[Ngữ cảnh: THUYẾT MINH BÁO CÁO TÀI CHÍNH]") and "LƯU CHUYỂN" not in out


def test_continuation_page_keeps_columns_when_form_header_repeats():
    doc = ("MẪU SỐ B 01-DN\n\nTại ngày 31 tháng 12 năm 2025\n\n|Mã số|Sốcuối năm|Sốđầu năm|\n|---|---|---|\n|100|1|2|\n\n"
           "MẪU SỐ B 01-DN\n\n|8.|Quỹ|322|3|4|\n")
    out, = add_table_context(doc, ["|8.|Quỹ|322|3|4|"])
    assert "Tại ngày 31 tháng 12 năm 2025" in out and "Cột: Mã số | Sốcuối năm | Sốđầu năm" in out


# ── cases taken from real parsed BCTC (MBB, VHM, SAB) ───────────────────────────────────────────────

def test_garbled_mixed_case_title_is_still_found():
    # MBB: only the first words are upper case, the rest is "tc;1i ngay 31 thang 12 nam 2024"
    assert _title_of("BAO CAO TINH Hi NH TAI CHiNH Rl~NG tc;1i ngay 31 thang 12 nam 2024 ") == "BÁO CÁO TÌNH HÌNH TÀI CHÍNH"


def test_form_number_without_the_word_mau_counts_only_at_line_start():
    assert _title_of("B02/TCTD _(?an hanh theo Thong fll' s6 49/2014/TT-NHNN ngay 31 thang 12 nam 2014)_") == "BÁO CÁO TÌNH HÌNH TÀI CHÍNH"
    assert _title_of("Các số liệu được trình bày theo Mẫu B01-DN ban hành kèm theo Thông tư 200") is None


def test_notes_subheading_that_mentions_a_statement_is_not_a_title():
    # VHM: this line relabelled 222 later chunks as the cash-flow statement
    assert _title_of("THONG TIN BO SUNG VE BAO CÁO LƯU CHUYEN TIEN TE RIENG") is None
    assert _title_of("CÁC KHOẢN MỤC NGOÀI BẢNG CÂN ĐỐI KẾ TOÁN") is None
    assert _title_of("CÔNG TY CỔ PHẦN VINHOMES BẢNG CÂN ĐỐI KẾ TOÁN RIÊNG (Tiếp theo)") == "BẢNG CÂN ĐỐI KẾ TOÁN"


def test_header_row_with_garbled_labels_but_two_dates():
    cells = _header_cells("||_Thuy/;t_|_31/12/2024_|_31/12/2023_|")
    assert cells and "31/12/2024" in cells and "31/12/2023" in cells
    assert _header_cells("|Tiền mặt|5|3.266.448|3.637.930|") is None


def test_unrecognised_heading_clears_the_label_instead_of_inheriting_the_previous_statement():
    doc = ("BẢNG CÂN ĐỐI KẾ TOÁN\n\n|Mã số|Sốcuối năm|Sốđầu năm|\n|100|1|2|\n\n"
           "BÁO CÁO CỦA BAN TỔNG GIÁM ĐỐC\n\n|1.|Hội đồng quản trị|ông A|\n")
    chunk = "|1.|Hội đồng quản trị|ông A|"
    assert add_table_context(doc, [chunk]) == [chunk]


def test_notes_subheading_does_not_relabel_the_notes():
    doc = ("MẪU SỐ B 09-DN\n\nTHÔNG TIN BỔ SUNG VỀ BÁO CÁO LƯU CHUYỂN TIỀN TỆ RIÊNG\n\n|a|1|2|\n")
    out, = add_table_context(doc, ["|a|1|2|"])
    assert out.startswith("[Ngữ cảnh: THUYẾT MINH BÁO CÁO TÀI CHÍNH]")
