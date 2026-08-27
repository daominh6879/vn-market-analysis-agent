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


def fetch_sbv_central_rate() -> Optional[float]:
    """
    Fetch SBV (NHNN) official USD/VND central rate (tỷ giá trung tâm).

    Tries two sources in order:
      1. VCB XML — checks for a CentralRate/Reference attribute (rarely present).
      2. SBV website HTML — parses the exchange rate table for the USD row.

    Returns float (e.g. 25615.0) or None on failure.
    """
    import re

    # --- Source 1: VCB XML central rate attribute ---
    try:
        import httpx
        from xml.etree import ElementTree as ET

        resp = httpx.get(
            "https://portal.vietcombank.com.vn/Usercontrols/TVPortal.TyGia/pXML.aspx?b=10",
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for item in root.findall(".//Exrate"):
            if (item.get("CurrencyCode") or "").strip().upper() != "USD":
                continue
            for attr in ("CentralRate", "centralRate", "central_rate", "Reference"):
                val = item.get(attr)
                if val:
                    cleaned = val.replace(",", "").replace(".", "")
                    if cleaned.isdigit() and len(cleaned) >= 5:
                        return float(cleaned)
    except Exception as e:
        sys.stderr.write(f"[fx_scraper] fetch_sbv_central_rate VCB source failed: {e}\n")

    # --- Source 2: SBV website HTML ---
    try:
        import httpx
        resp = httpx.get(
            "https://www.sbv.gov.vn/webcenter/portal/vi/menu/fm/tghh",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
            follow_redirects=True,
        )
        text = resp.text
        # Find USD section then extract the first 5-digit rate number
        usd_idx = text.find("USD")
        if usd_idx == -1:
            usd_idx = text.find("Đô la Mỹ")
        if usd_idx != -1:
            snippet = text[usd_idx: usd_idx + 600]
            matches = re.findall(r"\b(2[4-6]\s*[.,]\s*\d{3}(?:[.,]\d{1,2})?)\b", snippet)
            for m in matches:
                cleaned = re.sub(r"[\s.,]", "", m)
                if len(cleaned) == 5 and cleaned.isdigit():
                    return float(cleaned)
    except Exception as e:
        sys.stderr.write(f"[fx_scraper] fetch_sbv_central_rate SBV source failed: {e}\n")

    return None


def midpoint_rate(data: dict) -> float:
    """Average of buy + sell as a rough mid-market rate."""
    return round((data["buy"] + data["sell"]) / 2, 0)


if __name__ == "__main__":
    result = fetch_vcb_usdvnd()
    print(result)
