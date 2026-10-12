"""
data/hose_universe.py — HOSE universe: tickers, sector, index membership.

Two layers:
  1. HOSE_SEED — hardcoded ~405 HOSE tickers with sector tags (always available).
  2. fetch_and_save_hose_universe() — calls vnstock to refresh full list,
     saves to data/hose_tickers.json. Run periodically.
  3. load_hose_tickers() — reads json if exists, falls back to HOSE_SEED.

Sector tags follow GICS level-1 approximation (in Vietnamese).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_CACHE_PATH = Path(__file__).parent / "hose_tickers.json"

# ── Seed universe ─────────────────────────────────────────────────────────────

# (ticker, sector, index_member)  — index_member: "VN30"|"VN100"|"HOSE"
HOSE_SEED: list[tuple[str, str, str]] = [
    # ── Ngân hàng ──────────────────────────────────────────────────────────────
    ("VCB", "Ngân hàng", "VN30"),
    ("BID", "Ngân hàng", "VN30"),
    ("CTG", "Ngân hàng", "VN30"),
    ("TCB", "Ngân hàng", "VN30"),
    ("MBB", "Ngân hàng", "VN30"),
    ("ACB", "Ngân hàng", "VN30"),
    ("VPB", "Ngân hàng", "VN30"),
    ("HDB", "Ngân hàng", "VN30"),
    ("STB", "Ngân hàng", "VN30"),
    ("VIB", "Ngân hàng", "VN30"),
    ("SSB", "Ngân hàng", "VN30"),
    ("TPB", "Ngân hàng", "VN30"),
    ("SHB", "Ngân hàng", "VN30"),
    ("LPB", "Ngân hàng", "VN100"),
    ("EIB", "Ngân hàng", "VN100"),
    ("MSB", "Ngân hàng", "VN100"),
    ("OCB", "Ngân hàng", "VN100"),
    ("PGB", "Ngân hàng", "HOSE"),
    ("VAB", "Ngân hàng", "HOSE"),
    ("ABB", "Ngân hàng", "HOSE"),
    # ── Bất động sản ──────────────────────────────────────────────────────────
    ("VIC", "Bất động sản", "VN30"),
    ("VHM", "Bất động sản", "VN30"),
    ("NVL", "Bất động sản", "VN100"),
    ("PDR", "Bất động sản", "VN30"),
    ("KDH", "Bất động sản", "VN100"),
    ("NLG", "Bất động sản", "VN100"),
    ("DIG", "Bất động sản", "VN100"),
    ("TCH", "Bất động sản", "HOSE"),
    ("DXS", "Bất động sản", "HOSE"),
    ("CEO", "Bất động sản", "HOSE"),
    ("HDG", "Bất động sản", "VN100"),
    ("CII", "Bất động sản", "VN100"),
    ("DPG", "Bất động sản", "HOSE"),
    ("SZC", "Bất động sản", "HOSE"),
    ("AGG", "Bất động sản", "HOSE"),
    ("TDH", "Bất động sản", "HOSE"),
    # ── Vật liệu xây dựng / Thép ─────────────────────────────────────────────
    ("HPG", "Vật liệu", "VN30"),
    ("HSG", "Vật liệu", "VN100"),
    ("NKG", "Vật liệu", "VN100"),
    ("BMP", "Vật liệu", "HOSE"),
    ("VGC", "Vật liệu", "HOSE"),
    ("SCG", "Vật liệu", "HOSE"),
    # ── Hàng tiêu dùng / Bán lẻ ──────────────────────────────────────────────
    ("VNM", "Thực phẩm & Đồ uống", "VN30"),
    ("MSN", "Thực phẩm & Đồ uống", "VN30"),
    ("SAB", "Thực phẩm & Đồ uống", "VN30"),
    ("MCH", "Thực phẩm & Đồ uống", "HOSE"),
    ("QNS", "Thực phẩm & Đồ uống", "HOSE"),
    ("KDC", "Thực phẩm & Đồ uống", "HOSE"),
    ("VHC", "Thực phẩm & Đồ uống", "HOSE"),
    ("ANV", "Thực phẩm & Đồ uống", "HOSE"),
    ("IDI", "Thực phẩm & Đồ uống", "HOSE"),
    ("MWG", "Bán lẻ", "VN30"),
    ("FRT", "Bán lẻ", "VN100"),
    ("DGW", "Bán lẻ", "VN100"),
    ("PNJ", "Bán lẻ", "VN100"),
    ("VRE", "Bán lẻ", "VN100"),
    # ── Công nghệ ─────────────────────────────────────────────────────────────
    ("FPT", "Công nghệ", "VN30"),
    ("CMG", "Công nghệ", "VN100"),
    ("ELC", "Công nghệ", "HOSE"),
    ("ITD", "Công nghệ", "HOSE"),
    # ── Chứng khoán ───────────────────────────────────────────────────────────
    ("SSI", "Chứng khoán", "VN30"),
    ("VND", "Chứng khoán", "VN100"),
    ("HCM", "Chứng khoán", "VN100"),
    ("MBS", "Chứng khoán", "VN100"),
    ("VCI", "Chứng khoán", "VN100"),
    ("AGR", "Chứng khoán", "VN100"),
    ("BSI", "Chứng khoán", "HOSE"),
    ("CTS", "Chứng khoán", "HOSE"),
    ("APS", "Chứng khoán", "HOSE"),
    ("ORS", "Chứng khoán", "HOSE"),
    # ── Dầu khí / Năng lượng ─────────────────────────────────────────────────
    ("GAS", "Dầu khí", "VN30"),
    ("PLX", "Dầu khí", "VN30"),
    ("POW", "Tiện ích", "VN30"),
    ("PVS", "Dầu khí", "VN100"),
    ("PVD", "Dầu khí", "VN100"),
    ("BSR", "Dầu khí", "HOSE"),
    ("OIL", "Dầu khí", "HOSE"),
    ("PVC", "Dầu khí", "HOSE"),
    ("CNG", "Dầu khí", "HOSE"),
    ("PGD", "Dầu khí", "HOSE"),
    # ── Điện ──────────────────────────────────────────────────────────────────
    ("REE", "Tiện ích", "VN100"),
    ("PC1", "Tiện ích", "VN100"),
    ("TV2", "Tiện ích", "HOSE"),
    ("BCG", "Tiện ích", "HOSE"),
    ("NT2", "Tiện ích", "HOSE"),
    ("SBA", "Tiện ích", "HOSE"),
    ("VSH", "Tiện ích", "HOSE"),
    ("GEX", "Tiện ích", "VN100"),
    ("EVF", "Tài chính", "HOSE"),
    # ── Cao su / Nông nghiệp ──────────────────────────────────────────────────
    ("GVR", "Nông nghiệp", "VN30"),
    ("PHR", "Nông nghiệp", "VN100"),
    ("DPR", "Nông nghiệp", "HOSE"),
    ("TRC", "Nông nghiệp", "HOSE"),
    ("HAG", "Nông nghiệp", "HOSE"),
    ("HNG", "Nông nghiệp", "HOSE"),
    ("HVN", "Hàng không", "HOSE"),
    # ── Hoá chất / Phân bón ───────────────────────────────────────────────────
    ("DPM", "Hóa chất", "VN100"),
    ("DCM", "Hóa chất", "VN100"),
    ("CSV", "Hóa chất", "HOSE"),
    ("BFC", "Hóa chất", "HOSE"),
    # ── Dệt may / May mặc ─────────────────────────────────────────────────────
    ("TLG", "Dệt may", "HOSE"),
    ("TNG", "Dệt may", "HOSE"),
    ("TCM", "Dệt may", "HOSE"),
    ("VGT", "Dệt may", "HOSE"),
    ("STK", "Dệt may", "HOSE"),
    # ── Cảng / Vận tải / Logistics ────────────────────────────────────────────
    ("GMD", "Logistics", "VN100"),
    ("VSC", "Logistics", "HOSE"),
    ("HAX", "Logistics", "HOSE"),
    ("VTO", "Logistics", "HOSE"),
    ("PVT", "Logistics", "HOSE"),
    ("SCS", "Logistics", "HOSE"),
    ("ACV", "Logistics", "HOSE"),
    ("SGN", "Logistics", "HOSE"),
    ("NCT", "Logistics", "HOSE"),
    ("HAH", "Logistics", "HOSE"),
    ("TMS", "Logistics", "HOSE"),
    ("VTP", "Logistics", "HOSE"),
    # ── Bảo hiểm ──────────────────────────────────────────────────────────────
    ("BVH", "Bảo hiểm", "VN30"),
    ("MIG", "Bảo hiểm", "HOSE"),
    ("PTI", "Bảo hiểm", "HOSE"),
    ("BMI", "Bảo hiểm", "HOSE"),
    ("PVI", "Bảo hiểm", "HOSE"),
    ("VNR", "Bảo hiểm", "HOSE"),
    # ── Xây dựng / Hạ tầng ───────────────────────────────────────────────────
    ("BCM", "Xây dựng", "VN30"),
    ("CTD", "Xây dựng", "VN100"),
    ("HBC", "Xây dựng", "HOSE"),
    ("LCG", "Xây dựng", "HOSE"),
    ("VCG", "Xây dựng", "HOSE"),
    ("TV4", "Xây dựng", "HOSE"),
    # ── Y tế / Dược ───────────────────────────────────────────────────────────
    ("DVN", "Y tế", "HOSE"),
    ("IMP", "Y tế", "HOSE"),
    ("DHG", "Y tế", "HOSE"),
    ("DMC", "Y tế", "HOSE"),
    ("TRA", "Y tế", "HOSE"),
    # ── Du lịch / Khách sạn ───────────────────────────────────────────────────
    ("VJC", "Hàng không", "VN30"),
    ("VNG", "Công nghệ", "HOSE"),
    ("TCT", "Du lịch", "HOSE"),
    # ── Khác ──────────────────────────────────────────────────────────────────
    ("VNX", "Thực phẩm & Đồ uống", "HOSE"),
    ("CMF", "Thực phẩm & Đồ uống", "HOSE"),
    ("SBT", "Thực phẩm & Đồ uống", "HOSE"),
    ("LSS", "Thực phẩm & Đồ uống", "HOSE"),
    ("SVI", "Công nghiệp", "HOSE"),
    ("VCS", "Công nghiệp", "HOSE"),
    ("LHG", "Bất động sản", "HOSE"),
    ("IJC", "Bất động sản", "HOSE"),
    ("TDC", "Bất động sản", "HOSE"),
    ("DTL", "Bất động sản", "HOSE"),
    # ── Additional HOSE stocks (sector unknown — sourced from vnstock) ─────────
    ("AAA", "Unknown", "HOSE"),
    ("AAM", "Unknown", "HOSE"),
    ("AAN", "Unknown", "HOSE"),
    ("AAT", "Unknown", "HOSE"),
    ("ABR", "Unknown", "HOSE"),
    ("ABS", "Unknown", "HOSE"),
    ("ABT", "Unknown", "HOSE"),
    ("ACC", "Unknown", "HOSE"),
    ("ACG", "Unknown", "HOSE"),
    ("ACL", "Unknown", "HOSE"),
    ("ADG", "Unknown", "HOSE"),
    ("ADP", "Unknown", "HOSE"),
    ("ADS", "Unknown", "HOSE"),
    ("AFX", "Unknown", "HOSE"),
    ("ANT", "Unknown", "HOSE"),
    ("APG", "Unknown", "HOSE"),
    ("APH", "Unknown", "HOSE"),
    ("ASG", "Unknown", "HOSE"),
    ("ASM", "Unknown", "HOSE"),
    ("ASP", "Unknown", "HOSE"),
    ("AST", "Unknown", "HOSE"),
    ("BAF", "Thực phẩm & Đồ uống", "HOSE"),
    ("BCE", "Xây dựng", "HOSE"),
    ("BHN", "Thực phẩm & Đồ uống", "HOSE"),
    ("BIC", "Bảo hiểm", "HOSE"),
    ("BKG", "Bất động sản", "HOSE"),
    ("BMC", "Unknown", "HOSE"),
    ("BRC", "Unknown", "HOSE"),
    ("BTP", "Tiện ích", "HOSE"),
    ("BTT", "Unknown", "HOSE"),
    ("BVB", "Ngân hàng", "HOSE"),
    ("BWE", "Tiện ích", "HOSE"),
    ("C32", "Xây dựng", "HOSE"),
    ("C47", "Xây dựng", "HOSE"),
    ("CCC", "Unknown", "HOSE"),
    ("CCI", "Unknown", "HOSE"),
    ("CCL", "Bất động sản", "HOSE"),
    ("CDC", "Bất động sản", "HOSE"),
    ("CHP", "Tiện ích", "HOSE"),
    ("CIG", "Unknown", "HOSE"),
    ("CKG", "Bất động sản", "HOSE"),
    ("CLC", "Unknown", "HOSE"),
    ("CLL", "Logistics", "HOSE"),
    ("CLW", "Tiện ích", "HOSE"),
    ("CMV", "Unknown", "HOSE"),
    ("CMX", "Unknown", "HOSE"),
    ("COM", "Unknown", "HOSE"),
    ("CRC", "Unknown", "HOSE"),
    ("CRE", "Bất động sản", "HOSE"),
    ("CRV", "Unknown", "HOSE"),
    ("CSM", "Vật liệu", "HOSE"),
    ("CTF", "Unknown", "HOSE"),
    ("CTI", "Xây dựng", "HOSE"),
    ("CTR", "Công nghệ", "HOSE"),
    ("CVT", "Vật liệu", "HOSE"),
    ("D2D", "Bất động sản", "HOSE"),
    ("DAH", "Du lịch", "HOSE"),
    ("DAT", "Bất động sản", "HOSE"),
    ("DBC", "Thực phẩm & Đồ uống", "HOSE"),
    ("DBD", "Y tế", "HOSE"),
    ("DBT", "Unknown", "HOSE"),
    ("DC4", "Xây dựng", "HOSE"),
    ("DCL", "Y tế", "HOSE"),
    ("DGC", "Hóa chất", "HOSE"),
    ("DHA", "Unknown", "HOSE"),
    ("DHC", "Unknown", "HOSE"),
    ("DHM", "Vật liệu", "HOSE"),
    ("DLG", "Bất động sản", "HOSE"),
    ("DMX", "Unknown", "HOSE"),
    ("DQC", "Unknown", "HOSE"),
    ("DRC", "Unknown", "HOSE"),
    ("DRH", "Du lịch", "HOSE"),
    ("DRL", "Tiện ích", "HOSE"),
    ("DSC", "Chứng khoán", "HOSE"),
    ("DSE", "Unknown", "HOSE"),
    ("DSN", "Unknown", "HOSE"),
    ("DTA", "Unknown", "HOSE"),
    ("DTT", "Unknown", "HOSE"),
    ("DVP", "Logistics", "HOSE"),
    ("DXG", "Bất động sản", "HOSE"),
    ("DXV", "Vật liệu", "HOSE"),
    ("EVE", "Dệt may", "HOSE"),
    ("EVG", "Bất động sản", "HOSE"),
    ("FCM", "Unknown", "HOSE"),
    ("FCN", "Xây dựng", "HOSE"),
    ("FDC", "Bất động sản", "HOSE"),
    ("FIR", "Bất động sản", "HOSE"),
    ("FIT", "Unknown", "HOSE"),
    ("FMC", "Thực phẩm & Đồ uống", "HOSE"),
    ("FTS", "Chứng khoán", "HOSE"),
    ("GDT", "Unknown", "HOSE"),
    ("GEE", "Unknown", "HOSE"),
    ("GEG", "Tiện ích", "HOSE"),
    ("GEL", "Unknown", "HOSE"),
    ("GHC", "Tiện ích", "HOSE"),
    ("GIL", "Dệt may", "HOSE"),
    ("GMH", "Unknown", "HOSE"),
    ("GSP", "Logistics", "HOSE"),
    ("GTA", "Unknown", "HOSE"),
    ("HAP", "Unknown", "HOSE"),
    ("HAR", "Bất động sản", "HOSE"),
    ("HAS", "Unknown", "HOSE"),
    ("HCD", "Unknown", "HOSE"),
    ("HDC", "Bất động sản", "HOSE"),
    ("HHP", "Unknown", "HOSE"),
    ("HHS", "Bất động sản", "HOSE"),
    ("HHV", "Xây dựng", "HOSE"),
    ("HID", "Unknown", "HOSE"),
    ("HII", "Unknown", "HOSE"),
    ("HMC", "Vật liệu", "HOSE"),
    ("HNA", "Unknown", "HOSE"),
    ("HPA", "Unknown", "HOSE"),
    ("HPX", "Xây dựng", "HOSE"),
    ("HQC", "Bất động sản", "HOSE"),
    ("HRC", "Nông nghiệp", "HOSE"),
    ("HSL", "Unknown", "HOSE"),
    ("HT1", "Vật liệu", "HOSE"),
    ("HTG", "Unknown", "HOSE"),
    ("HTI", "Unknown", "HOSE"),
    ("HTL", "Unknown", "HOSE"),
    ("HTN", "Xây dựng", "HOSE"),
    ("HTV", "Unknown", "HOSE"),
    ("HU1", "Unknown", "HOSE"),
    ("HUB", "Unknown", "HOSE"),
    ("HVH", "Unknown", "HOSE"),
    ("ICT", "Công nghệ", "HOSE"),
    ("ILB", "Unknown", "HOSE"),
    ("ITC", "Bất động sản", "HOSE"),
    ("JVC", "Y tế", "HOSE"),
    ("KBC", "Bất động sản", "VN100"),
    ("KHG", "Bất động sản", "HOSE"),
    ("KHP", "Tiện ích", "HOSE"),
    ("KLB", "Ngân hàng", "HOSE"),
    ("KMR", "Dệt may", "HOSE"),
    ("KOS", "Unknown", "HOSE"),
    ("KSB", "Vật liệu", "HOSE"),
    ("L10", "Xây dựng", "HOSE"),
    ("LAF", "Thực phẩm & Đồ uống", "HOSE"),
    ("LBM", "Vật liệu", "HOSE"),
    ("LDG", "Bất động sản", "HOSE"),
    ("LGC", "Tiện ích", "HOSE"),
    ("LGL", "Bất động sản", "HOSE"),
    ("LIX", "Unknown", "HOSE"),
    ("LM8", "Xây dựng", "HOSE"),
    ("LPS", "Unknown", "HOSE"),
    ("MCM", "Unknown", "HOSE"),
    ("MCP", "Unknown", "HOSE"),
    ("MDG", "Unknown", "HOSE"),
    ("MHC", "Unknown", "HOSE"),
    ("MSH", "Dệt may", "HOSE"),
    ("MZG", "Unknown", "HOSE"),
    ("NAB", "Ngân hàng", "HOSE"),
    ("NAF", "Thực phẩm & Đồ uống", "HOSE"),
    ("NAV", "Unknown", "HOSE"),
    ("NBB", "Bất động sản", "HOSE"),
    ("NHA", "Bất động sản", "HOSE"),
    ("NHH", "Unknown", "HOSE"),
    ("NHT", "Unknown", "HOSE"),
    ("NNC", "Vật liệu", "HOSE"),
    ("NO1", "Unknown", "HOSE"),
    ("NSC", "Nông nghiệp", "HOSE"),
    ("NTC", "Logistics", "HOSE"),
    ("NTL", "Bất động sản", "HOSE"),
    ("NVT", "Bất động sản", "HOSE"),
    ("OGC", "Unknown", "HOSE"),
    ("OPC", "Y tế", "HOSE"),
    ("PAC", "Unknown", "HOSE"),
    ("PAN", "Nông nghiệp", "HOSE"),
    ("PDN", "Logistics", "HOSE"),
    ("PDV", "Unknown", "HOSE"),
    ("PET", "Unknown", "HOSE"),
    ("PGC", "Dầu khí", "HOSE"),
    ("PGI", "Bảo hiểm", "HOSE"),
    ("PGV", "Tiện ích", "HOSE"),
    ("PHC", "Xây dựng", "HOSE"),
    ("PIT", "Unknown", "HOSE"),
    ("PJT", "Logistics", "HOSE"),
    ("PLP", "Unknown", "HOSE"),
    ("PMG", "Unknown", "HOSE"),
    ("PNC", "Unknown", "HOSE"),
    ("PPC", "Tiện ích", "HOSE"),
    ("PTB", "Vật liệu", "HOSE"),
    ("PTC", "Xây dựng", "HOSE"),
    ("PTL", "Bất động sản", "HOSE"),
    ("PVP", "Dầu khí", "HOSE"),
    ("QCG", "Bất động sản", "HOSE"),
    ("QNP", "Unknown", "HOSE"),
    ("RAL", "Unknown", "HOSE"),
    ("RYG", "Unknown", "HOSE"),
    ("S4A", "Tiện ích", "HOSE"),
    ("SAM", "Công nghệ", "HOSE"),
    ("SAV", "Unknown", "HOSE"),
    ("SBG", "Unknown", "HOSE"),
    ("SBV", "Unknown", "HOSE"),
    ("SC5", "Xây dựng", "HOSE"),
    ("SCR", "Bất động sản", "HOSE"),
    ("SFC", "Dầu khí", "HOSE"),
    ("SFG", "Hóa chất", "HOSE"),
    ("SFI", "Logistics", "HOSE"),
    ("SGR", "Du lịch", "HOSE"),
    ("SGT", "Công nghệ", "HOSE"),
    ("SHA", "Du lịch", "HOSE"),
    ("SHI", "Unknown", "HOSE"),
    ("SHP", "Tiện ích", "HOSE"),
    ("SIP", "Bất động sản", "HOSE"),
    ("SJD", "Tiện ích", "HOSE"),
    ("SJS", "Bất động sản", "HOSE"),
    ("SKG", "Logistics", "HOSE"),
    ("SMA", "Unknown", "HOSE"),
    ("SMB", "Thực phẩm & Đồ uống", "HOSE"),
    ("SMC", "Vật liệu", "HOSE"),
    ("SPM", "Unknown", "HOSE"),
    ("SRC", "Unknown", "HOSE"),
    ("SRF", "Unknown", "HOSE"),
    ("SSC", "Nông nghiệp", "HOSE"),
    ("ST8", "Xây dựng", "HOSE"),
    ("STG", "Logistics", "HOSE"),
    ("SVC", "Unknown", "HOSE"),
    ("SVD", "Unknown", "HOSE"),
    ("SVT", "Unknown", "HOSE"),
    ("SZL", "Bất động sản", "HOSE"),
    ("TAL", "Unknown", "HOSE"),
    ("TBC", "Tiện ích", "HOSE"),
    ("TCI", "Unknown", "HOSE"),
    ("TCL", "Unknown", "HOSE"),
    ("TCO", "Unknown", "HOSE"),
    ("TCR", "Unknown", "HOSE"),
    ("TCX", "Unknown", "HOSE"),
    ("TDG", "Unknown", "HOSE"),
    ("TDM", "Tiện ích", "HOSE"),
    ("TDP", "Unknown", "HOSE"),
    ("TDW", "Tiện ích", "HOSE"),
    ("TEG", "Tiện ích", "HOSE"),
    ("THG", "Xây dựng", "HOSE"),
    ("TIP", "Logistics", "HOSE"),
    ("TIX", "Bất động sản", "HOSE"),
    ("TLD", "Unknown", "HOSE"),
    ("TLH", "Vật liệu", "HOSE"),
    ("TMP", "Tiện ích", "HOSE"),
    ("TMT", "Unknown", "HOSE"),
    ("TN1", "Unknown", "HOSE"),
    ("TNC", "Dệt may", "HOSE"),
    ("TNH", "Y tế", "HOSE"),
    ("TNI", "Unknown", "HOSE"),
    ("TNT", "Unknown", "HOSE"),
    ("TPC", "Hóa chất", "HOSE"),
    ("TSA", "Unknown", "HOSE"),
    ("TSC", "Nông nghiệp", "HOSE"),
    ("TTA", "Tiện ích", "HOSE"),
    ("TTE", "Tiện ích", "HOSE"),
    ("TTF", "Unknown", "HOSE"),
    ("TVB", "Chứng khoán", "HOSE"),
    ("TVS", "Chứng khoán", "HOSE"),
    ("TVT", "Unknown", "HOSE"),
    ("TYA", "Dệt may", "HOSE"),
    ("UIC", "Bất động sản", "HOSE"),
    ("VBB", "Ngân hàng", "HOSE"),
    ("VCA", "Unknown", "HOSE"),
    ("VCF", "Thực phẩm & Đồ uống", "HOSE"),
    ("VCK", "Unknown", "HOSE"),
    ("VDP", "Unknown", "HOSE"),
    ("VDS", "Chứng khoán", "HOSE"),
    ("VFG", "Hóa chất", "HOSE"),
    ("VID", "Unknown", "HOSE"),
    ("VIP", "Logistics", "HOSE"),
    ("VIX", "Chứng khoán", "HOSE"),
    ("VMD", "Y tế", "HOSE"),
    ("VNL", "Logistics", "HOSE"),
    ("VNS", "Unknown", "HOSE"),
    ("VOS", "Logistics", "HOSE"),
    ("VPD", "Unknown", "HOSE"),
    ("VPG", "Unknown", "HOSE"),
    ("VPH", "Bất động sản", "HOSE"),
    ("VPI", "Bất động sản", "HOSE"),
    ("VPL", "Bất động sản", "HOSE"),
    ("VPS", "Chứng khoán", "HOSE"),
    ("VPX", "Xây dựng", "HOSE"),
    ("VRC", "Bất động sản", "HOSE"),
    ("VSI", "Xây dựng", "HOSE"),
    ("VTB", "Unknown", "HOSE"),
    ("VVS", "Unknown", "HOSE"),
    ("YBM", "Vật liệu", "HOSE"),
    ("YEG", "Unknown", "HOSE"),
]


# ── Enrichment (data-driven replacement for core.tickers.SECTOR_ALIAS) ─────────

# sector value → broad synonym tags, safe for EVERY ticker in that sector. These are the
# alias labels that don't equal the sector column directly ("năng lượng" spans "Dầu khí" +
# "Tiện ích", "tiêu dùng" spans F&B + retail, "vận tải" spans logistics + aviation).
_SECTOR_TAGS: dict[str, tuple[str, ...]] = {
    "Dầu khí": ("năng lượng", "dầu thô"),
    "Tiện ích": ("năng lượng", "điện"),
    "Thực phẩm & Đồ uống": ("tiêu dùng", "thực phẩm", "đồ uống"),
    "Bán lẻ": ("tiêu dùng",),
    "Logistics": ("vận tải",),
    "Hàng không": ("vận tải",),
}

# Per-ticker curated metadata for the liquid universe. `name_aliases` = lowercased company
# / brand names (resolved diacritic-insensitively); `tags` = precision sub-sector labels
# (subsets of a sector, e.g. "thép" ⊂ "Vật liệu") + product/theme keywords. `industry`
# optional — defaults to the sector value.
TICKER_ENRICHMENT: dict[str, dict] = {
    # ── Banks ──
    "VCB": {"name_aliases": ["vietcombank", "ngân hàng ngoại thương", "ngân hàng ngoại thương việt nam"]},
    "BID": {"name_aliases": ["bidv", "ngân hàng đầu tư và phát triển", "ngân hàng đầu tư phát triển việt nam"]},
    "CTG": {"name_aliases": ["vietinbank", "ngân hàng công thương", "ngân hàng công thương việt nam"]},
    "TCB": {"name_aliases": ["techcombank", "ngân hàng kỹ thương", "ngân hàng kỹ thương việt nam"]},
    "MBB": {"name_aliases": ["mbbank", "ngân hàng quân đội", "ngân hàng tmcp quân đội"]},
    "ACB": {"name_aliases": ["ngân hàng á châu", "ngân hàng á châu việt nam"]},
    "VPB": {"name_aliases": ["vpbank", "ngân hàng việt nam thịnh vượng", "ngân hàng thịnh vượng", "thịnh vượng"]},
    "HDB": {"name_aliases": ["hdbank", "ngân hàng phát triển tp.hcm", "ngân hàng phát triển thành phố hồ chí minh"]},
    "STB": {"name_aliases": ["sacombank", "ngân hàng sài gòn thương tín"]},
    "VIB": {"name_aliases": ["ngân hàng quốc tế", "ngân hàng quốc tế việt nam"]},
    "SSB": {"name_aliases": ["seabank", "ngân hàng đông nam á"]},
    "TPB": {"name_aliases": ["tpbank", "ngân hàng tiên phong"]},
    "SHB": {"name_aliases": ["ngân hàng sài gòn hà nội"]},
    "LPB": {"name_aliases": ["lienvietpostbank", "ngân hàng bưu điện liên việt", "liên việt"]},
    "EIB": {"name_aliases": ["eximbank", "ngân hàng xuất nhập khẩu", "ngân hàng xuất nhập khẩu việt nam"]},
    "MSB": {"name_aliases": ["ngân hàng hàng hải", "ngân hàng tmcp hàng hải"]},
    "OCB": {"name_aliases": ["ngân hàng phương đông"]},
    "NAB": {"name_aliases": ["nam a bank", "ngân hàng nam á"]},
    "KLB": {"name_aliases": ["kienlongbank", "ngân hàng kiên long"]},
    "BVB": {"name_aliases": ["ngân hàng bản việt", "vietcapital bank"]},
    "ABB": {"name_aliases": ["ngân hàng an bình"]},
    "VAB": {"name_aliases": ["vietabank", "ngân hàng việt á"]},
    "VBB": {"name_aliases": ["vietbank", "ngân hàng việt nam thương tín"]},
    "PGB": {"name_aliases": ["pgbank", "ngân hàng xăng dầu petrolimex"]},
    # ── Real estate ──
    "VIC": {"name_aliases": ["vingroup", "tập đoàn vingroup"]},
    "VHM": {"name_aliases": ["vinhomes"]},
    "NVL": {"name_aliases": ["novaland", "tập đoàn novaland"]},
    "PDR": {"name_aliases": ["phát đạt", "bất động sản phát đạt"]},
    "KDH": {"name_aliases": ["khang điền", "nhà khang điền"]},
    "NLG": {"name_aliases": ["nam long", "đầu tư nam long"]},
    "DIG": {"name_aliases": ["dầu khí miền đông", "dic corp", "tổng công ty cổ phần đầu tư phát triển xây dựng"]},
    "DXG": {"name_aliases": ["đất xanh", "tập đoàn đất xanh"]},
    "HDG": {"name_aliases": ["hà đô", "tập đoàn hà đô"]},
    "CII": {"name_aliases": ["hạ tầng kỹ thuật tphcm", "đầu tư hạ tầng kỹ thuật"]},
    # ── Steel / materials ──
    "HPG": {"name_aliases": ["hòa phát", "tập đoàn hòa phát"], "tags": ["thép"]},
    "HSG": {"name_aliases": ["hoa sen", "tập đoàn hoa sen"], "tags": ["thép"]},
    "NKG": {"name_aliases": ["nam kim", "thép nam kim"], "tags": ["thép"]},
    "TLH": {"name_aliases": ["thép tiến lên"], "tags": ["thép"]},
    "HT1": {"name_aliases": ["xi măng hà tiên", "hà tiên 1"], "tags": ["xi măng"]},
    "BCC": {"name_aliases": ["xi măng bỉm sơn"], "tags": ["xi măng"]},
    "BMP": {"name_aliases": ["nhựa bình minh"]},
    "VGC": {"name_aliases": ["viglacera", "tổng công ty viglacera"]},
    # ── Food & beverage ──
    "VNM": {"name_aliases": ["vinamilk", "công ty sữa việt nam", "sữa việt nam"], "tags": ["sữa"]},
    "MSN": {"name_aliases": ["masan", "tập đoàn masan"]},
    "SAB": {"name_aliases": ["sabeco", "bia sài gòn", "tổng công ty bia rượu nước giải khát sài gòn"]},
    "MCH": {"name_aliases": ["masan consumer", "masan tiêu dùng"]},
    "QNS": {"name_aliases": ["đường quảng ngãi", "vinasoy"], "tags": ["sữa"]},
    "KDC": {"name_aliases": ["kido", "tập đoàn kido"]},
    "SBT": {"name_aliases": ["mía đường thành thành công", "ttc agris"]},
    "BHN": {"name_aliases": ["habeco", "bia hà nội", "tổng công ty bia rượu nước giải khát hà nội"]},
    "LSS": {"name_aliases": ["mía đường lam sơn"]},
    "VCF": {"name_aliases": ["vinacafé biên hòa", "vinacafe"]},
    # ── Retail ──
    "MWG": {"name_aliases": ["thế giới di động", "đầu tư thế giới di động"]},
    "FRT": {"name_aliases": ["fpt retail", "bán lẻ fpt", "long châu"]},
    "DGW": {"name_aliases": ["digiworld", "thế giới số"]},
    "PNJ": {"name_aliases": ["phú nhuận jewelry", "vàng bạc đá quý phú nhuận"]},
    "VRE": {"name_aliases": ["vincom retail", "vincom"]},
    # ── Technology ──
    "FPT": {"name_aliases": ["fpt corporation", "tập đoàn fpt", "công ty cổ phần fpt"]},
    "CMG": {"name_aliases": ["cmc", "tập đoàn công nghệ cmc"]},
    "VNG": {"name_aliases": ["vng", "công ty cổ phần vng"]},
    "CTR": {"name_aliases": ["viettel construction", "công trình viettel"]},
    "ELC": {"name_aliases": ["điện lực miền trung", "công nghệ điện lực miền trung"]},
    "SAM": {"name_aliases": ["sacom", "cáp sài gòn"]},
    # ── Securities ──
    "SSI": {"name_aliases": ["chứng khoán ssi", "công ty chứng khoán ssi"]},
    "VND": {"name_aliases": ["chứng khoán vndirect", "vndirect"]},
    "HCM": {"name_aliases": ["chứng khoán hsc", "hsc"]},
    "MBS": {"name_aliases": ["chứng khoán mb", "mbs"]},
    "VCI": {"name_aliases": ["chứng khoán vietcap", "vietcap"]},
    "AGR": {"name_aliases": ["chứng khoán agribank", "agriseco"]},
    "FTS": {"name_aliases": ["chứng khoán fpt", "fp t securities"]},
    "VIX": {"name_aliases": ["chứng khoán vix"]},
    "VDS": {"name_aliases": ["chứng khoán rồng việt", "rồng việt"]},
    "DSC": {"name_aliases": ["chứng khoán dsc"]},
    # ── Oil & gas / energy ──
    "GAS": {"name_aliases": ["pv gas", "tổng công ty khí việt nam", "petrovietnam gas"]},
    "PLX": {"name_aliases": ["petrolimex", "tập đoàn xăng dầu việt nam"]},
    "POW": {"name_aliases": ["tổng công ty điện lực dầu khí", "pv power"]},
    "PVS": {"name_aliases": ["dịch vụ kỹ thuật dầu khí", "petrovietnam services"]},
    "PVD": {"name_aliases": ["khoan và dịch vụ khoan dầu khí", "pv drilling"]},
    "BSR": {"name_aliases": ["lọc hóa dầu bình sơn", "bình sơn"]},
    "OIL": {"name_aliases": ["tổng công ty dầu việt nam", "pv oil"]},
    "PVC": {"name_aliases": ["hóa chất và dịch vụ dầu khí", "petrovietnam chemicals"]},
    "CNG": {"name_aliases": ["khí tự nhiên cng"]},
    "PGD": {"name_aliases": ["phân phối khí thấp áp dầu khí"]},
    # ── Utilities ──
    "REE": {"name_aliases": ["cơ điện lạnh", "ree corporation"]},
    "PC1": {"name_aliases": ["xây lắp điện 1", "pc1 group"]},
    "GEX": {"name_aliases": ["gelex", "tập đoàn gelex"]},
    "NT2": {"name_aliases": ["điện lực dầu khí nhơn trạch 2"]},
    "BCG": {"name_aliases": ["bamboo capital", "tập đoàn bamboo capital"]},
    # ── Rubber / agriculture ──
    "GVR": {"name_aliases": ["tập đoàn cao su việt nam", "cao su việt nam"], "tags": ["cao su"]},
    "PHR": {"name_aliases": ["cao su phước hòa"], "tags": ["cao su"]},
    "DPR": {"name_aliases": ["cao su đồng phú"], "tags": ["cao su"]},
    "TRC": {"name_aliases": ["cao su tây ninh"], "tags": ["cao su"]},
    "PAN": {"name_aliases": ["tập đoàn pan", "pan group", "nông nghiệp pan"]},
    "HAG": {"name_aliases": ["hoàng anh gia lai", "hagl"]},
    # ── Insurance ──
    "BVH": {"name_aliases": ["bảo việt", "tập đoàn bảo việt"]},
    "MIG": {"name_aliases": ["bảo hiểm quân đội", "mic"]},
    "BMI": {"name_aliases": ["bảo minh", "tổng công ty bảo minh"]},
    "PVI": {"name_aliases": ["bảo hiểm pvi", "pvi"]},
    # ── Construction / infrastructure ──
    "BCM": {"name_aliases": ["becamex", "tổng công ty đầu tư và phát triển công nghiệp"]},
    "CTD": {"name_aliases": ["coteccons", "xây dựng coteccons"]},
    "HBC": {"name_aliases": ["hòa bình", "tập đoàn xây dựng hòa bình"]},
    "VCG": {"name_aliases": ["vinaconex", "tổng công ty xây dựng vinaconex"]},
    # ── Logistics / aviation ──
    "GMD": {"name_aliases": ["gemadept", "cảng gemadept"]},
    "VSC": {"name_aliases": ["cảng xanh vip", "cảng vip"]},
    "PVT": {"name_aliases": ["vận tải dầu khí", "pv trans"]},
    "ACV": {"name_aliases": ["tổng công ty cảng hàng không việt nam", "cảng hàng không việt nam"]},
    "VJC": {"name_aliases": ["vietjet", "vietjet air", "hàng không vietjet"]},
    "HVN": {"name_aliases": ["vietnam airlines", "hàng không quốc gia việt nam"]},
    # ── Chemicals / fertilizer ──
    "DPM": {"name_aliases": ["đạm phú mỹ", "phân bón và hóa chất dầu khí"], "tags": ["phân bón"]},
    "DCM": {"name_aliases": ["đạm cà mau", "phân bón dầu khí cà mau"], "tags": ["phân bón"]},
    "DGC": {"name_aliases": ["hóa chất đức giang", "đức giang"]},
    # ── Textile ──
    "TCM": {"name_aliases": ["dệt may thành công"]},
    "TNG": {"name_aliases": ["dệt may tng", "đầu tư và thương mại tng"]},
    "VGT": {"name_aliases": ["dệt may việt tiến", "tổng công ty việt thắng"]},
    "STK": {"name_aliases": ["sợi thế kỷ"]},
    "MSH": {"name_aliases": ["may sông hồng"]},
    # ── Healthcare / pharma ──
    "DHG": {"name_aliases": ["dược hậu giang"]},
    "IMP": {"name_aliases": ["dược imexpharm", "imexpharm"]},
    "TRA": {"name_aliases": ["dược trà vinh", "traphaco"]},
    "DBD": {"name_aliases": ["dược bến tre", "bidiphar"]},
    "OPC": {"name_aliases": ["dược opc", "opc pharmaceutical"]},
    # ── Seafood ──
    "VHC": {"name_aliases": ["vĩnh hoàn", "thủy sản vĩnh hoàn"], "tags": ["thủy sản"]},
    "ANV": {"name_aliases": ["nam việt", "thủy sản nam việt"], "tags": ["thủy sản"]},
    "IDI": {"name_aliases": ["i.d.i", "đầu tư và phát triển đa quốc gia"], "tags": ["thủy sản"]},
    "FMC": {"name_aliases": ["sao ta", "thực phẩm sao ta"], "tags": ["thủy sản"]},
}


# Curated corrections to the sector / ICB industry that the seed list and vnstock produce.
# Applied last (load + fetch + seed), so they survive a `--refresh` of the universe.
#   VRE: owns shopping malls (real estate), not a retailer.
#   EVF: a finance company, vnstock's ICB industry "Ngân hàng" put it in every bank list.
#   PNJ: jewelry/gold maker-retailer, kept out of the generic "bán lẻ" (electronics/grocery) group.
#   HDG: diversified; kept under Tiện ích (power generation) instead of Bất động sản — judgment call,
#        edit here if you prefer the other classification.
SECTOR_OVERRIDES: dict[str, dict] = {
    "VRE": {"sector": "Bất động sản", "industry": "Bất động sản", "tags": ["bất động sản", "trung tâm thương mại"]},
    "EVF": {"sector": "Tài chính", "industry": "Dịch vụ tài chính", "tags": ["công ty tài chính"]},
    "PNJ": {"sector": "Hàng tiêu dùng", "industry": "Trang sức", "tags": ["trang sức", "vàng bạc", "tiêu dùng"]},
    "HDG": {"sector": "Tiện ích", "industry": "Điện", "tags": ["điện", "thủy điện", "năng lượng"]},
}


def _apply_override(rec: dict) -> dict:
    ov = SECTOR_OVERRIDES.get(rec.get("ticker", ""))
    return {**rec, **ov} if ov else rec


def _enrich(ticker: str, sector: str) -> dict:
    """Merge sector-derived tags + curated per-ticker metadata into one record."""
    enr = TICKER_ENRICHMENT.get(ticker, {})
    tags = sorted(set(_SECTOR_TAGS.get(sector, ())) | set(enr.get("tags", ())))
    return {
        "name_aliases": list(enr.get("name_aliases", ())),
        "tags": tags,
        "industry": enr.get("industry") or sector,
    }


def _seed_record(ticker: str, sector: str, index_member: str) -> dict:
    """Full-record shape for one HOSE_SEED entry (company_name filled by vnstock later)."""
    enr = _enrich(ticker, sector)
    return _apply_override({
        "ticker": ticker,
        "sector": sector,
        "index_member": index_member,
        "company_name": "",
        "industry": enr["industry"],
        "name_aliases": enr["name_aliases"],
        "tags": enr["tags"],
    })


def load_hose_tickers() -> list[str]:
    """Return list of HOSE tickers: from cache file if exists, else HOSE_SEED."""
    if _CACHE_PATH.exists():
        try:
            data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
            tickers = [row["ticker"] for row in data if row.get("exchange") == "HOSE"]
            if tickers:
                return tickers
        except Exception as e:
            sys.stderr.write(f"[hose_universe] cache read error: {e}\n")
    return [t for t, _, _ in HOSE_SEED]


def load_hose_universe() -> list[dict]:
    """
    Return full universe records: [{"ticker", "sector", "index_member", "company_name",
    "industry", "name_aliases", "tags"}, ...]. The cache (hose_tickers.json, built by
    fetch_and_save_hose_universe) is authoritative; HOSE_SEED is only a fallback when
    the cache is missing.
    """
    if _CACHE_PATH.exists():
        try:
            data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
            result = []
            for row in data:
                if row.get("exchange") != "HOSE":
                    continue
                result.append(_apply_override({
                    "ticker": row["ticker"],
                    "sector": row.get("sector", "Unknown"),
                    "index_member": row.get("index_member", "HOSE"),
                    "company_name": row.get("company_name", ""),
                    "industry": row.get("industry", "Unknown"),
                    "name_aliases": row.get("name_aliases", []),
                    "tags": row.get("tags", []),
                }))
            if result:
                return result
        except Exception:
            pass

    return [_seed_record(t, s, m) for t, s, m in HOSE_SEED]


# vnstock `industry_name` → coarse `securities.sector` bucket. Fills the sector gap for
# HOSE tickers not classified in HOSE_SEED (microcaps). Only a data-build mapping, never
# used at query time.
_INDUSTRY_TO_SECTOR: dict[str, str] = {
    "Ngân hàng": "Ngân hàng",
    "Chứng khoán": "Chứng khoán",
    "Tài chính khác": "Tài chính",
    "Bảo hiểm": "Bảo hiểm",
    "Bất động sản": "Bất động sản",
    "Xây dựng": "Xây dựng",
    "Vật liệu xây dựng": "Vật liệu",
    "Khai khoáng": "Vật liệu",
    "SX Nhựa - Hóa chất": "Hóa chất",
    "Sản phẩm cao su": "Nông nghiệp",
    "Nông - Lâm - Ngư": "Nông nghiệp",
    "Chế biến Thủy sản": "Thực phẩm & Đồ uống",
    "Thực phẩm - Đồ uống": "Thực phẩm & Đồ uống",
    "Bán lẻ": "Bán lẻ",
    "Bán buôn": "Bán lẻ",
    "Công nghệ và thông tin": "Công nghệ",
    "Tiện ích": "Tiện ích",
    "Vận tải - kho bãi": "Logistics",
    "Chăm sóc sức khỏe": "Y tế",
    "Dịch vụ lưu trú, ăn uống, giải trí": "Du lịch",
    "Dịch vụ tư vấn, hỗ trợ": "Công nghiệp",
    "SX Hàng gia dụng": "Công nghiệp",
    "SX Phụ trợ": "Công nghiệp",
    "SX Thiết bị, máy móc": "Công nghiệp",
    "Thiết bị điện": "Công nghiệp",
}

_COMPANY_PREFIXES = (
    "công ty cổ phần", "ctcp", "công ty tnhh", "công ty trách nhiệm hữu hạn",
    "tổng công ty", "tập đoàn", "ngân hàng tmcp", "ngân hàng thương mại cổ phần",
)


def _derive_core_alias(company_name: str) -> str:
    """Derive a searchable core name from a legal company name.

    "CTCP Tập đoàn Hòa Phát" → "hòa phát", "Ngân hàng TMCP Ngoại thương VN" →
    "ngoại thương vn". Falls back to "" when nothing meaningful remains.
    """
    n = (company_name or "").lower().strip()
    if not n:
        return ""
    for p in _COMPANY_PREFIXES:
        if n.startswith(p):
            n = n[len(p):].strip()
            break
    n = re.sub(r"\s*[-–]\s*(ctcp|cp|jsc|corp|group)\b.*$", "", n).strip()
    n = re.sub(r"\s*\(.*?\)\s*$", "", n).strip()
    return n


def fetch_and_save_hose_universe() -> int:
    """
    Fetch full HOSE ticker list + company name + industry from vnstock and save a
    COMPLETE universe (ticker/exchange/sector/industry/company_name/name_aliases/tags)
    to data/hose_tickers.json. Returns count of tickers saved. Non-fatal on error.
    """
    try:
        import warnings
        warnings.filterwarnings("ignore")
        from vnstock import Listing

        lst = Listing()
        df = lst.symbols_by_exchange()
        hose = df[(df["exchange"].str.upper() == "HOSE") & (df["type"].str.lower() == "stock")]
        if hose.empty:
            sys.stderr.write("[hose_universe] vnstock returned empty HOSE list; cache not updated\n")
            return 0

        # Company name from the exchange frame; fine-grained industry from the ICB frame.
        org = dict(zip(hose["symbol"], hose["organ_name"].fillna("").astype(str)))
        try:
            ind_df = lst.symbols_by_industries(lang="vi")
            ind = dict(zip(ind_df["symbol"], ind_df["industry_name"].fillna("").astype(str)))
        except Exception:
            ind = {}

        seed_map = {t: (s, m) for t, s, m in HOSE_SEED}
        rows = []
        for sym in sorted(hose["symbol"].tolist()):
            seed_sector, index_member = seed_map.get(sym, ("Unknown", "HOSE"))
            industry = ind.get(sym, "")
            sector = seed_sector if seed_sector != "Unknown" else _INDUSTRY_TO_SECTOR.get(industry, "Công nghiệp")
            if not industry:
                industry = sector  # vnstock has no industry for this symbol — mirror sector
            enr = _enrich(sym, sector)

            aliases = list(enr["name_aliases"])
            core = _derive_core_alias(org.get(sym, ""))
            if core and len(core) >= 4 and core not in aliases:
                aliases.append(core)

            tags = list(enr["tags"])
            if industry and industry.lower() not in tags:
                tags.append(industry.lower())

            rows.append(_apply_override({
                "ticker": sym,
                "exchange": "HOSE",
                "sector": sector,
                "index_member": index_member,
                "company_name": org.get(sym, ""),
                "industry": industry,
                "name_aliases": sorted(set(aliases)),
                "tags": sorted(set(tags)),
            }))

        _CACHE_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        return len(rows)

    except Exception as e:
        sys.stderr.write(f"[hose_universe] fetch_and_save_hose_universe failed: {e}\n")
        return 0


def get_vn30_tickers() -> list[str]:
    """Return VN30 constituent tickers from seed."""
    return [t for t, _, m in HOSE_SEED if m == "VN30"]


if __name__ == "__main__":
    n = fetch_and_save_hose_universe()
    print(f"Saved {n} HOSE tickers to {_CACHE_PATH}")
    if n == 0:
        print("Using seed fallback:", len(load_hose_tickers()), "tickers")
