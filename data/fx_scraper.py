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

    # --- Source 2: sbv.gov.vn homepage chart (dated series: tyGiaLabels / tyGiaValues) ---
    rate = _fetch_sbv_homepage_chart()
    if rate is not None:
        return rate

    # --- Source 3: tygiausd.org republishes the SBV rate with its date (sbv.gov.vn blocks bots) ---
    return _fetch_central_rate_mirror()


LAST_CENTRAL_RATE_DATE: Optional["date"] = None   # date of the rate last returned by fetch_sbv_central_rate
LAST_CENTRAL_RATE_PREV: Optional[tuple] = None    # (date, rate) of the previous point of the SBV chart, when known


def _fetch_sbv_homepage_chart(max_age_days: int = 4) -> Optional[float]:
    """Latest point of the central-rate chart on www.sbv.gov.vn; None when missing or stale."""
    global LAST_CENTRAL_RATE_DATE
    import re
    from datetime import date, datetime
    try:
        import httpx
        resp = httpx.get("https://www.sbv.gov.vn/", timeout=15, follow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120"})
        resp.raise_for_status()
        html = resp.text
        v = re.search(r"tyGiaValues\s*=\s*\[([^\]]*)\]", html)
        # labels are the dd-mm-yyyy array declared just before tyGiaValues
        labels = re.findall(r'"(\d{2}-\d{2}-\d{4})"', html[: v.start()]) if v else []
        values = re.findall(r"\d{5}(?:\.\d+)?", v.group(1)) if v else []
        if not values or len(labels) < len(values):
            sys.stderr.write("[fx_scraper] SBV homepage chart: labels/values missing or misaligned\n")
            return None
        labels = labels[-len(values):]
        published = datetime.strptime(labels[-1], "%d-%m-%Y").date()
        if (date.today() - published).days > max_age_days:
            sys.stderr.write(f"[fx_scraper] SBV homepage rate is stale ({published}); ignored\n")
            return None
        LAST_CENTRAL_RATE_DATE = published
        global LAST_CENTRAL_RATE_PREV
        LAST_CENTRAL_RATE_PREV = (
            (datetime.strptime(labels[-2], "%d-%m-%Y").date(), float(values[-2])) if len(values) > 1 else None
        )
        return float(values[-1])
    except Exception as e:
        sys.stderr.write(f"[fx_scraper] SBV homepage chart failed: {e}\n")
        return None


def _fetch_central_rate_mirror(max_age_days: int = 4) -> Optional[float]:
    """SBV central rate from a dated mirror page; None when the printed date is stale.

    A stale rate must never be presented as today's, so the page date is checked against today.
    """
    import re
    from datetime import date
    try:
        import httpx
        resp = httpx.get("https://tygiausd.org/", timeout=10, headers={"User-Agent": "Mozilla/5.0"},
                         follow_redirects=True)
        resp.raise_for_status()
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", resp.text))
        m = re.search(r"Tỷ giá trung tâm\s+(\d{2})/(\d{2})/(\d{4})\s+1 Đô la Mỹ\s*=\s*(\d{2})[.,](\d{3})", text)
        if not m:
            return None
        published = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if (date.today() - published).days > max_age_days:
            sys.stderr.write(f"[fx_scraper] central-rate mirror is stale ({published}); ignored\n")
            return None
        global LAST_CENTRAL_RATE_DATE
        LAST_CENTRAL_RATE_DATE = published
        return float(m.group(4) + m.group(5))
    except Exception as e:
        sys.stderr.write(f"[fx_scraper] central-rate mirror failed: {e}\n")
        return None


def midpoint_rate(data: dict) -> float:
    """Average of buy + sell as a rough mid-market rate."""
    return round((data["buy"] + data["sell"]) / 2, 0)


if __name__ == "__main__":
    result = fetch_vcb_usdvnd()
    print(result)
