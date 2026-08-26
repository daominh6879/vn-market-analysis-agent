"""
data/fx_scraper.py — Scrape USD/VND exchange rates from Vietcombank.

Source: https://portal.vietcombank.com.vn/Usercontrols/TVPortal.TyGia/pXML.aspx
Returns dict with buy, sell, central_rate (estimated), and delta from previous close.
Non-fatal: returns None on any error.
"""

from __future__ import annotations

import sys
from datetime import datetime
from typing import Optional


def fetch_vcb_usdvnd() -> Optional[dict]:
    """
    Fetch USD/VND from Vietcombank XML feed.
    Returns:
        {
            "buy": float,           # e.g. 25920.0
            "sell": float,          # e.g. 26330.0
            "transfer": float,      # chuyển khoản
            "timestamp": str,
            "source": "vietcombank",
        }
    or None on error.
    """
    try:
        import httpx
        from xml.etree import ElementTree as ET

        resp = httpx.get(
            "https://portal.vietcombank.com.vn/Usercontrols/TVPortal.TyGia/pXML.aspx?b=10",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        for item in root.findall(".//Exrate"):
            code = (item.get("CurrencyCode") or "").strip().upper()
            if code != "USD":
                continue
            buy  = item.get("Buy")  or item.get("buy")  or ""
            sell = item.get("Sell") or item.get("sell") or ""
            transfer = item.get("Transfer") or buy
            if not buy or not sell:
                continue
            buy_f      = float(buy.replace(",", ""))
            sell_f     = float(sell.replace(",", ""))
            transfer_f = float(transfer.replace(",", ""))
            return {
                "buy": buy_f,
                "sell": sell_f,
                "transfer": transfer_f,
                "timestamp": datetime.now().isoformat(),
                "source": "vietcombank",
            }

        sys.stderr.write("[fx_scraper] USD not found in VCB XML\n")
        return None
    except Exception as e:
        sys.stderr.write(f"[fx_scraper] fetch_vcb_usdvnd failed: {e}\n")
        return None


def midpoint_rate(data: dict) -> float:
    """Average of buy + sell as a rough mid-market rate."""
    return round((data["buy"] + data["sell"]) / 2, 0)


if __name__ == "__main__":
    result = fetch_vcb_usdvnd()
    print(result)
