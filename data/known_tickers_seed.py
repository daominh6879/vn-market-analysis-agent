"""
data/known_tickers_seed.py — One-shot script to generate data/known_tickers.txt.

Run once to populate the ticker list used by news_scraper.py.

Usage:
    python data/known_tickers_seed.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "data" / "known_tickers.txt"

FALLBACK = [
    "HPG", "VNM", "FPT", "VIC", "MSN", "VHM", "TCB", "MBB", "VCB", "CTG",
    "BID", "VPB", "ACB", "STB", "HDB", "LPB", "SHB", "SSI", "VND", "HCM",
    "MBS", "AGR", "PNJ", "MWG", "DGW", "FRT", "VRE", "KDH", "NLG", "PDR",
    "DIG", "NVL", "CII", "GVR", "PHR", "DPM", "DCM", "GAS", "PLX", "BSR",
    "OIL", "PVS", "PVD", "PVC", "CNG", "REE", "PC1", "TV2", "BCG", "EVF",
    "VIB", "KLB", "NVB", "BVB", "VAB", "ABB", "PGB", "BAB", "HAG", "HNG",
    "TLG", "TNG", "TCM", "VGT", "GMD", "VSC", "HAX", "VTO", "PVT", "SCS",
    "ACV", "SGN", "NCT", "MIG", "BVH", "PTI", "BMI", "PVI", "VNR", "HAH",
]


def main() -> None:
    try:
        from vnstock import Listing
        symbols = Listing().all_symbols()["symbol"].tolist()
        print(f"Loaded {len(symbols)} symbols from vnstock")
    except Exception as e:
        print(f"vnstock unavailable ({e}), using fallback list ({len(FALLBACK)} symbols)")
        symbols = FALLBACK

    tickers = sorted(set(str(s).strip().upper() for s in symbols if s))
    OUT.write_text("\n".join(tickers), encoding="utf-8")
    print(f"Written {len(tickers)} tickers → {OUT}")


if __name__ == "__main__":
    main()
