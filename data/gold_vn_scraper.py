"""
data/gold_vn_scraper.py — Scrape SJC gold buy/sell prices (triệu đồng/lượng).

Primary source : https://giavang.org/  (aggregator — 200 OK, class="gold-price")
Fallback       : SJC XML feed          (https://sjc.com.vn/xml/tygiavang.xml)
                 — as of 2026-08 SJC blocks all IPs with 403; kept as last resort.

Returns dict with keys: buy_vnd, sell_vnd, timestamp (ISO), source.
Non-fatal: returns None on any error.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime
from typing import Optional


def _parse_trieuong(raw: str) -> Optional[float]:
    """
    Parse VN-formatted gold price to triệu đồng/lượng.
    Formats seen:
      "147.000 x1000đ/lượng"  → 147.000 × 1000 đ = 147 triệu
      "147600000"              → raw VND → /1_000_000
      "147,600,000"            → raw VND → /1_000_000
    """
    num_str = re.sub(r'[^\d]', '', raw.split()[0] if raw.split() else raw)
    if not num_str:
        return None
    val = float(num_str)
    # If <= 1000: already in triệu (shouldn't happen, guard)
    if val <= 1_000:
        return val
    # If 3-6 digits with x1000đ unit context → val × 1000 / 1_000_000 = val / 1000
    if val <= 999_999:
        return round(val / 1_000, 1)
    # Otherwise raw VND (9 digits)
    return round(val / 1_000_000, 1)


def _fetch_giavang_org() -> Optional[dict]:
    """
    Scrape https://giavang.org/ for SJC 1-lượng buy/sell.
    HTML structure (stable):
      <span class="gold-price">147.000 <small class="gold-unit">x1000đ/lượng</small></span>
    First occurrence = buy (mua vào), second = sell (bán ra).
    """
    try:
        import httpx
        resp = httpx.get(
            "https://giavang.org/",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            follow_redirects=True,
        )
        resp.raise_for_status()

        prices = re.findall(
            r'<span\s+class="gold-price">\s*([\d\.]+)\s*<small\s+class="gold-unit">x1000',
            resp.text,
        )
        if len(prices) < 2:
            sys.stderr.write(
                f"[gold_vn_scraper] giavang.org: expected ≥2 gold-price spans, got {len(prices)}\n"
            )
            return None

        buy_raw, sell_raw = prices[0], prices[1]
        # "147.000" → 147000 (VN dot = thousands sep); unit = x1000đ → ×1000 / 1e6 = triệu
        buy_vnd = float(buy_raw.replace(".", "")) / 1_000
        sell_vnd = float(sell_raw.replace(".", "")) / 1_000

        return {
            "buy_vnd": round(buy_vnd, 1),
            "sell_vnd": round(sell_vnd, 1),
            "timestamp": datetime.now().isoformat(),
            "source": "giavang.org",
        }
    except Exception as e:
        sys.stderr.write(f"[gold_vn_scraper] giavang.org failed: {e}\n")
        return None


def _fetch_sjc_xml() -> Optional[dict]:
    """
    Fallback: SJC public XML feed.
    Blocked with 403 as of 2026-08 — kept for future recovery.
    """
    try:
        import httpx
        from xml.etree import ElementTree as ET

        resp = httpx.get(
            "https://sjc.com.vn/xml/tygiavang.xml",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        for item in root.findall(".//item"):
            buy_raw = item.get("buy") or item.get("gia_mua") or ""
            sell_raw = item.get("sell") or item.get("gia_ban") or ""
            if not buy_raw or not sell_raw:
                continue
            buy_vnd = float(buy_raw.replace(",", "").replace(".", ""))
            sell_vnd = float(sell_raw.replace(",", "").replace(".", ""))
            if buy_vnd > 1_000_000:
                buy_vnd /= 1_000_000
                sell_vnd /= 1_000_000
            return {
                "buy_vnd": round(buy_vnd, 1),
                "sell_vnd": round(sell_vnd, 1),
                "timestamp": datetime.now().isoformat(),
                "source": "sjc",
            }

        sys.stderr.write("[gold_vn_scraper] SJC XML: no item found\n")
        return None
    except Exception as e:
        sys.stderr.write(f"[gold_vn_scraper] SJC XML failed: {e}\n")
        return None


def _fetch_yfinance_gold_estimate() -> Optional[dict]:
    """
    Fallback: derive VN gold price estimate from world gold (GC=F) + USD/VND (yfinance).
    Returns an estimate labeled clearly as 'world_estimate'.
    """
    try:
        import yfinance as yf
        gc = yf.Ticker("GC=F").history(period="2d")
        if gc.empty:
            return None
        gold_usd_oz = float(gc["Close"].iloc[-1])

        # USD/VND from yfinance
        fx = yf.Ticker("USDVND=X").history(period="2d")
        usd_vnd = float(fx["Close"].iloc[-1]) if not fx.empty else 25_000.0

        from data.global_universe import TROY_OZ_PER_LUONG
        vnd_per_luong = gold_usd_oz * usd_vnd * TROY_OZ_PER_LUONG / 1_000_000  # triệu đồng
        vnd_per_luong = round(vnd_per_luong, 1)
        # SJC premium historically ~5-8 triệu — add 6 triệu as rough estimate
        PREMIUM_ESTIMATE = 6.0
        return {
            "buy_vnd":   round(vnd_per_luong + PREMIUM_ESTIMATE - 0.5, 1),
            "sell_vnd":  round(vnd_per_luong + PREMIUM_ESTIMATE + 0.5, 1),
            "timestamp": datetime.now().isoformat(),
            "source":    "world_estimate (GC=F yfinance)",
        }
    except Exception as e:
        sys.stderr.write(f"[gold_vn_scraper] yfinance fallback failed: {e}\n")
        return None


def fetch_sjc_gold() -> Optional[dict]:
    """
    Public interface — try giavang.org first, SJC XML second, yfinance estimate last.
    Returns:
        {
            "buy_vnd":   float,  # triệu đồng/lượng  e.g. 147.0
            "sell_vnd":  float,  # triệu đồng/lượng  e.g. 150.0
            "timestamp": str,    # ISO datetime
            "source":    str,    # "giavang.org" | "sjc" | "world_estimate (GC=F yfinance)"
        }
    or None if all sources fail.
    """
    result = _fetch_giavang_org()
    if result is not None:
        return result
    result = _fetch_sjc_xml()
    if result is not None:
        return result
    return _fetch_yfinance_gold_estimate()


def gold_vnd_per_oz(gold_world_usd: float, usd_vnd: float) -> float:
    """Convert world gold (USD/oz) to VND/lượng using live FX rate."""
    from data.global_universe import TROY_OZ_PER_LUONG
    vnd_per_oz = gold_world_usd * usd_vnd
    return round(vnd_per_oz * TROY_OZ_PER_LUONG / 1_000_000, 2)  # triệu đồng/lượng


if __name__ == "__main__":
    result = fetch_sjc_gold()
    print(result)
