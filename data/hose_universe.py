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
import sys
from pathlib import Path
from typing import Optional

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
    ("FC1", "Xây dựng", "HOSE"),
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
    Return full universe records: [{"ticker", "sector", "index_member"}, ...].
    Merges cache + seed (cache wins for tickers it knows; seed fills gaps).
    """
    seed_map = {t: {"ticker": t, "sector": s, "index_member": m}
                for t, s, m in HOSE_SEED}

    if _CACHE_PATH.exists():
        try:
            data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
            # Cache provides exchange-filtered tickers; use seed for sector info
            result = []
            for row in data:
                if row.get("exchange") != "HOSE":
                    continue
                ticker = row["ticker"]
                entry = seed_map.get(ticker, {
                    "ticker": ticker,
                    "sector": row.get("sector", "Unknown"),
                    "index_member": row.get("index_member", "HOSE"),
                })
                result.append(entry)
            if result:
                return result
        except Exception:
            pass

    return list(seed_map.values())


def fetch_and_save_hose_universe() -> int:
    """
    Fetch full HOSE ticker list from vnstock and save to data/hose_tickers.json.
    Returns count of tickers saved. Non-fatal on error.
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

        seed_map = {t: (s, m) for t, s, m in HOSE_SEED}
        rows = []
        for sym in sorted(hose["symbol"].tolist()):
            sector, index_member = seed_map.get(sym, ("Unknown", "HOSE"))
            rows.append({
                "ticker": sym,
                "exchange": "HOSE",
                "sector": sector,
                "index_member": index_member,
            })

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
