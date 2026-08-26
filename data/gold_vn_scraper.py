"""
data/gold_vn_scraper.py — Scrape SJC gold buy/sell prices (VND/lượng).

Source: https://sjc.com.vn/xml/tygiavang.xml
Returns dict with keys: buy_vnd, sell_vnd, timestamp (ISO), source.
Non-fatal: returns None on any error.
"""

from __future__ import annotations

import sys
from datetime import datetime
from typing import Optional


def fetch_sjc_gold() -> Optional[dict]:
    """
    Fetch SJC gold prices from public XML feed.
    Returns:
        {
            "buy_vnd": float,   # triệu đồng/lượng (e.g. 147.6)
            "sell_vnd": float,  # triệu đồng/lượng (e.g. 150.6)
            "timestamp": str,   # ISO datetime
            "source": "sjc",
        }
    or None on error.
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

        # SJC XML structure: <root><item type="SJC" khu_vuc="TP.HCM" buy="..." sell="..."/></root>
        # Find HCM SJC 1-lượng row
        for item in root.findall(".//item"):
            name = (item.get("name") or item.get("type") or "").lower()
            buy_raw  = item.get("buy")  or item.get("gia_mua") or ""
            sell_raw = item.get("sell") or item.get("gia_ban") or ""
            if not buy_raw or not sell_raw:
                continue
            # SJC returns prices in VND (e.g. "147,600,000" or "147600000")
            buy_vnd = float(buy_raw.replace(",", "").replace(".", ""))
            sell_vnd = float(sell_raw.replace(",", "").replace(".", ""))
            # Convert to triệu đồng
            if buy_vnd > 1_000_000:
                buy_vnd /= 1_000_000
                sell_vnd /= 1_000_000
            return {
                "buy_vnd": round(buy_vnd, 1),
                "sell_vnd": round(sell_vnd, 1),
                "timestamp": datetime.now().isoformat(),
                "source": "sjc",
            }

        sys.stderr.write("[gold_vn_scraper] No matching SJC item found in XML\n")
        return None
    except Exception as e:
        sys.stderr.write(f"[gold_vn_scraper] fetch_sjc_gold failed: {e}\n")
        return None


def gold_vnd_per_oz(gold_world_usd: float, usd_vnd: float) -> float:
    """Convert world gold (USD/oz) to VND/lượng using live FX rate."""
    from data.global_universe import TROY_OZ_PER_LUONG
    vnd_per_oz = gold_world_usd * usd_vnd
    return round(vnd_per_oz * TROY_OZ_PER_LUONG / 1_000_000, 2)  # triệu đồng/lượng


if __name__ == "__main__":
    result = fetch_sjc_gold()
    print(result)
