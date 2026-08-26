"""
data/hose_universe.py — HOSE universe: tickers, sector, index membership.

Two layers:
  1. HOSE_SEED — hardcoded ~180 major tickers with sector tags (always available).
  2. fetch_and_save_hose_universe() — calls VCI to get full ~400-ticker list,
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
    Fetch full HOSE ticker list from VCI API and save to data/hose_tickers.json.
    Returns count of tickers saved. Non-fatal on error.

    VCI screening endpoint returns all listed HOSE stocks.
    """
    try:
        import httpx

        url = "https://trading.vietcap.com.vn/api/market/MarketIntraday/fetch-data-for-iboard"
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/json",
        }
        payload = {"exchange": "HOSE", "pageSize": 500, "pageIndex": 1}
        resp = httpx.post(url, json=payload, headers=headers, timeout=20)
        resp.raise_for_status()
        raw = resp.json()

        # Response shape varies — try common patterns
        items = raw if isinstance(raw, list) else raw.get("data", raw.get("items", []))
        rows = []
        for item in items:
            ticker = (item.get("stockCode") or item.get("ticker") or item.get("symbol") or "").upper()
            if not ticker or len(ticker) > 5:
                continue
            rows.append({
                "ticker": ticker,
                "exchange": "HOSE",
                "sector": item.get("industry") or item.get("sector") or "Unknown",
                "index_member": "HOSE",
            })

        if rows:
            _CACHE_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
            return len(rows)

        sys.stderr.write("[hose_universe] VCI returned empty list; cache not updated\n")
        return 0

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
