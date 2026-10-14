"""
data/gold_vn_scraper.py — Scrape SJC gold buy/sell prices (triệu đồng/lượng).

Primary source : vnmarket macro.gold("sjc") (SJC, else SJC rows on PNJ/BTMC boards)
Fallback       : world-price estimate (GC=F yfinance)

Returns dict with keys: buy_vnd, sell_vnd, timestamp (ISO), source.
Non-fatal: returns None on any error.
"""

from __future__ import annotations

import sys
from datetime import datetime
from typing import Optional


def _fetch_sjc_vnmarket() -> Optional[dict]:
    """SJC bar (Vàng SJC 1L, 10L, 1KG, HCM) via vnmarket; VND/lượng -> triệu đồng/lượng."""
    try:
        from core.vnmarket_client import get_client

        df = get_client().macro.gold("sjc")
        bars = df[df["type"].str.contains("SJC 1L", na=False)]
        if bars.empty:
            bars = df
        hcm = bars[bars["branch"] == "Hồ Chí Minh"]
        row = (hcm if not hcm.empty else bars).iloc[0]
        return {
            "buy_vnd": round(float(row["buy"]) / 1_000_000, 1),
            "sell_vnd": round(float(row["sell"]) / 1_000_000, 1),
            "timestamp": datetime.now().isoformat(),
            "source": str(df.attrs.get("source", "sjc")),
        }
    except Exception as e:
        sys.stderr.write(f"[gold_vn_scraper] vnmarket gold failed: {e}\n")
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
    Public interface — vnmarket (SJC, else PNJ/BTMC SJC rows) first, yfinance estimate last.
    Returns:
        {
            "buy_vnd":   float,  # triệu đồng/lượng  e.g. 147.0
            "sell_vnd":  float,  # triệu đồng/lượng  e.g. 150.0
            "timestamp": str,    # ISO datetime
            "source":    str,    # "sjc" | "pnj" | "btmc" | "world_estimate (GC=F yfinance)"
        }
    or None if all sources fail.
    """
    result = _fetch_sjc_vnmarket()
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
