"""core/balance_check.py — assets = capital cross-check on OCR'd balance sheets (rows taken from real indexed BCTC)."""
from core.balance_check import balance_check

HPG_ASSETS = "||TONGCỘNGTÀISAN(270=100+200)|270||98.670.778.691.605|81.793.076.515.644||"
HPG_CAPITAL = "|440|TỔNG CỘNG NGUỒN VỐN (440=300+400)||98.670.778.691.605|81.793.076.515.644|"
VCB_ASSETS = "||TONGTAISANCO||2.426.649.127|2.075.778.940|"
VCB_CAPITAL = "||TONGNOPHAITRAVÀVONCHỦSỞHỮU||2.426.649.127|2.075.778.940|"
PLX_ASSETS = "|TONG CONG TÀI SAN<br>(270=100+200)|270||53.336.709.742.044<br>|—50.355.675.330.691|"
PLX_CAPITAL = "|TONG CONG NGUON VON<br>(440=300+400)|440||53.336.709.712.044|50.355.675.330.691|"
VHM_CAPITAL = "|440||TONGCONG NGUONVON<br>||]|590.372.684|436.738.860|"
SEGMENT_ROW = "||Tongtai sản|9.475.985|3.385.855|716.336.031|435327847|572604642|315509778|402231299|2.454.871.437|"
DEFERRED_TAX_ROW = "|Tổng tai sảnthué thunhập hoãn lại|119.890.569.552|104.466.336.169|"


def test_matching_totals_are_ok():
    r = balance_check("\n".join(["BẢNG CÂN ĐỐI KẾ TOÁN", HPG_ASSETS, "|x|1|2|", HPG_CAPITAL]))
    assert r.status == "ok" and r.assets == ["98670778691605"] and r.capital == ["98670778691605"]


def test_bank_form_total_assets_co_and_liabilities_plus_equity():
    assert balance_check(f"{VCB_ASSETS}\n{VCB_CAPITAL}").status == "ok"


def test_one_misread_digit_is_a_mismatch_and_says_so():
    # PLX: the same total read differently in two places
    r = balance_check(f"{PLX_ASSETS}\n{PLX_CAPITAL}")
    assert r.status == "mismatch" and "1 digit(s) differ" in r.detail and "likely OCR" in r.detail


def test_dropped_total_assets_row_is_unverified_not_mismatch():
    # VHM: OCR lost "270 Tổng cộng tài sản" but kept the capital row
    r = balance_check(VHM_CAPITAL)
    assert r.status == "unverified" and "total assets" in r.detail and r.capital == ["590372684"]


def test_rows_of_other_tables_do_not_count():
    # segment table (many numbers) and "Tổng tài sản thuế thu nhập hoãn lại" must not be read as the total
    assert balance_check(f"{SEGMENT_ROW}\n{DEFERRED_TAX_ROW}\n{HPG_CAPITAL}").status == "unverified"
    assert balance_check(f"{SEGMENT_ROW}\n{HPG_CAPITAL}").assets == []


def test_ocr_flattened_number_without_dots_is_read():
    r = balance_check("|270|TỔNG CỘNG TÀI SẢN||590372684|436738860|\n" + VHM_CAPITAL)
    assert r.status == "ok"


def test_any_matching_pair_is_enough_when_a_document_has_several_balance_sheets():
    # separate + consolidated statements in one PDF: one consistent pair is evidence enough
    other = "||TONGCỘNGTÀISAN(270=100+200)|270||111.111.111.111|2.222.222|"
    assert balance_check(f"{other}\n{HPG_ASSETS}\n{HPG_CAPITAL}").status == "ok"


def test_text_without_a_balance_sheet_is_unverified():
    assert balance_check("Báo cáo của Ban Tổng Giám đốc ...").status == "unverified"
