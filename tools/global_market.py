"""
tools/global_market.py — World indices, commodities, crypto, FX, VN gold tools.

All tools:
  get_global_indices()         — S&P500, Dow, Nasdaq, VIX, Nikkei, KOSPI, Shanghai
  get_commodities()            — Gold (XAU), Silver, WTI, Brent
  get_crypto_prices()          — BTC, ETH, XRP, SOL + total market cap
  get_fx_rates()               — USD/VND (VCB buy/sell + central)
  get_vn_gold()                — SJC buy/sell (triệu đồng/lượng) + premium vs world

All return ToolResult. Never raise.
"""

from __future__ import annotations

from typing import Optional

from tools.result import ToolResult
from tracing import instrument_tool


def _yf_filter_as_of(close, as_of_date: Optional[str]):
    """Keep only rows with date STRICTLY BEFORE as_of_date.

    The morning brief for date D reports the previous session's close (D-1).
    Using < (not <=) prevents D's intraday/partial data from bleeding in —
    especially for Asian markets that open around the same time the brief runs.
    """
    if as_of_date is None:
        return close
    try:
        import pandas as _pd
        cutoff = _pd.Timestamp(as_of_date).date()
        mask = _pd.to_datetime(close.index).date < cutoff
        return close[mask]
    except Exception:
        return close


# ── Tool: World equity indices ────────────────────────────────────────────────

@instrument_tool("get_global_indices")
def get_global_indices(as_of_date: Optional[str] = None) -> ToolResult:
    """
    Fetch closing prices + %change for major world indices via yfinance.

    as_of_date: YYYY-MM-DD cap — only rows on or before this date are used.
    The brief for date D should pass D so we show D-1 close, not D partial/future data.
    """
    try:
        import sys
        import yfinance as yf
        from data.global_universe import WORLD_INDICES

        tickers_str = " ".join(WORLD_INDICES.keys())
        raw = yf.download(tickers_str, period="7d", auto_adjust=True, progress=False)

        if raw.empty:
            return ToolResult(status="no_data", data=None,
                              message="yfinance returned no data for world indices.")

        close = raw["Close"] if "Close" in raw else raw.get("close", raw)
        close = close.dropna(how="all")

        if close.empty:
            return ToolResult(status="no_data", data=None,
                              message="Insufficient history for world indices.")

        close = _yf_filter_as_of(close, as_of_date)

        sys.stderr.write(
            f"[get_global_indices] as_of={as_of_date} "
            f"dates_used: {list(close.index.astype(str))}\n"
        )

        results = []
        lines = []
        for yf_ticker, display_name in WORLD_INDICES.items():
            if yf_ticker not in close.columns:
                continue
            col = close[yf_ticker].dropna().tail(2)
            if len(col) < 2:
                continue
            prev = float(col.iloc[-2])
            curr = float(col.iloc[-1])
            if prev == 0:
                continue
            pct = round((curr - prev) / prev * 100, 2)
            sign = "+" if pct >= 0 else ""
            results.append({
                "ticker": yf_ticker,
                "name": display_name,
                "close": round(curr, 2),
                "change_pct": pct,
                "date": str(col.index[-1].date()) if hasattr(col.index[-1], "date") else str(col.index[-1])[:10],
            })
            lines.append(f"• {display_name}: {curr:,.2f} ({sign}{pct:.2f}%)")

        message = "\n".join(lines) if lines else "Không có dữ liệu chỉ số thế giới."
        return ToolResult(status="ok", data=results, message=message)

    except Exception as e:
        return ToolResult(status="upstream_error", data=None,
                          message=f"Lỗi lấy chỉ số thế giới: {e}")


# ── Tool: Commodities (Gold, Silver, WTI, Brent) ────────────────────────────

@instrument_tool("get_commodities")
def get_commodities(as_of_date: Optional[str] = None) -> ToolResult:
    """
    Fetch gold, silver, WTI, Brent prices + %change via yfinance futures.

    as_of_date: YYYY-MM-DD cap — only rows on or before this date are used.
    """
    try:
        import yfinance as yf
        from data.global_universe import COMMODITIES

        tickers_str = " ".join(COMMODITIES.keys())
        raw = yf.download(tickers_str, period="7d", auto_adjust=True, progress=False)

        if raw.empty:
            return ToolResult(status="no_data", data=None,
                              message="yfinance returned no commodity data.")

        close = raw["Close"] if "Close" in raw else raw
        close = close.dropna(how="all")

        if close.empty:
            return ToolResult(status="no_data", data=None,
                              message="Insufficient history for commodities.")

        close = _yf_filter_as_of(close, as_of_date)

        results = []
        lines = []
        for yf_ticker, meta in COMMODITIES.items():
            if yf_ticker not in close.columns:
                continue
            col = close[yf_ticker].dropna().tail(2)
            if len(col) < 2:
                continue
            prev = float(col.iloc[-2])
            curr = float(col.iloc[-1])
            if prev == 0:
                continue
            pct = round((curr - prev) / prev * 100, 2)
            sign = "+" if pct >= 0 else ""
            results.append({
                "ticker": yf_ticker,
                "name": meta["name"],
                "unit": meta["unit"],
                "price": round(curr, 2),
                "change_pct": pct,
            })
            lines.append(f"• {meta['name']}: {curr:,.2f} {meta['unit']} ({sign}{pct:.2f}%)")

        message = "\n".join(lines) if lines else "Không có dữ liệu hàng hóa."
        return ToolResult(status="ok", data=results, message=message)

    except Exception as e:
        return ToolResult(status="upstream_error", data=None,
                          message=f"Lỗi lấy giá hàng hóa: {e}")


# ── Tool: Crypto prices ───────────────────────────────────────────────────────

# Binance symbol → (CoinGecko id, display symbol)
_BINANCE_CRYPTO = [
    ("BTCUSDT",  "bitcoin",  "BTC"),
    ("ETHUSDT",  "ethereum", "ETH"),
    ("XRPUSDT",  "ripple",   "XRP"),
    ("SOLUSDT",  "solana",   "SOL"),
]


def _fetch_crypto_binance() -> Optional[list[dict]]:
    """Binance public 24h ticker — no API key, better uptime than CoinGecko free."""
    try:
        import httpx
        import json as _json
        syms = _json.dumps([p[0] for p in _BINANCE_CRYPTO])
        resp = httpx.get(
            "https://api.binance.com/api/v3/ticker/24hr",
            params={"symbols": syms},
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        data = resp.json()
        sym_map = {p[0]: (p[1], p[2]) for p in _BINANCE_CRYPTO}
        results = []
        for item in data:
            sym = item.get("symbol", "")
            if sym not in sym_map:
                continue
            cg_id, display = sym_map[sym]
            price = float(item["lastPrice"])
            change_pct = float(item["priceChangePercent"])
            results.append({
                "symbol": display,
                "cg_id": cg_id,
                "price_usd": round(price, 2),
                "change_24h_pct": round(change_pct, 2),
                "market_cap_usd": 0,
            })
        return results if len(results) == len(_BINANCE_CRYPTO) else None
    except Exception:
        return None


@instrument_tool("get_crypto_prices")
def get_crypto_prices() -> ToolResult:
    """
    Fetch BTC/ETH/XRP/SOL prices + 24h change + total market cap.
    Primary: CoinGecko free API. Fallback: Binance public ticker.
    """
    try:
        import httpx
        from data.global_universe import CRYPTO_IDS

        ids = ",".join(CRYPTO_IDS.keys())
        params = {
            "ids": ids,
            "vs_currencies": "usd",
            "include_24hr_change": "true",
            "include_market_cap": "true",
        }
        resp = httpx.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params=params,
            timeout=12,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        data = resp.json()

        # CoinGecko rate-limits return empty {} — detect and fall back
        if not data or not any(cg_id in data for cg_id in CRYPTO_IDS):
            raise ValueError("CoinGecko returned empty payload (likely rate-limited)")

        results = []
        lines = []
        for cg_id, symbol in CRYPTO_IDS.items():
            if cg_id not in data:
                continue
            item = data[cg_id]
            price = item.get("usd", 0.0)
            change_24h = item.get("usd_24h_change", 0.0)
            mcap = item.get("usd_market_cap", 0.0)
            sign = "+" if change_24h >= 0 else ""
            results.append({
                "symbol": symbol,
                "cg_id": cg_id,
                "price_usd": round(price, 2),
                "change_24h_pct": round(change_24h, 2),
                "market_cap_usd": mcap,
            })
            lines.append(f"• {symbol}: {price:,.2f} USD ({sign}{change_24h:.2f}%)")

        # Total market cap
        total_mcap_t = None
        try:
            global_resp = httpx.get(
                "https://api.coingecko.com/api/v3/global",
                timeout=8,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if global_resp.status_code == 200:
                gdata = global_resp.json().get("data", {})
                total_mcap = gdata.get("total_market_cap", {}).get("usd", 0)
                total_mcap_t = round(total_mcap / 1e12, 2)
                lines.append(f"• Total market cap: ~{total_mcap_t} nghìn tỷ USD")
        except Exception:
            pass

        message = "\n".join(lines) if lines else "Không có dữ liệu crypto."
        return ToolResult(
            status="ok",
            data={"coins": results, "total_market_cap_trillion_usd": total_mcap_t},
            message=message,
        )

    except Exception:
        # Fallback: Binance public ticker
        import sys
        sys.stderr.write("[get_crypto_prices] CoinGecko failed, trying Binance fallback\n")
        binance = _fetch_crypto_binance()
        if binance:
            lines = []
            for r in binance:
                sign = "+" if r["change_24h_pct"] >= 0 else ""
                lines.append(f"• {r['symbol']}: {r['price_usd']:,.2f} USD ({sign}{r['change_24h_pct']:.2f}%)")
            message = "\n".join(lines)
            return ToolResult(
                status="ok",
                data={"coins": binance, "total_market_cap_trillion_usd": None},
                message=message,
            )
        return ToolResult(status="upstream_error", data=None,
                          message="Lỗi lấy giá crypto (CoinGecko + Binance đều thất bại)")


# ── Tool: FX rates (USD/VND) ──────────────────────────────────────────────────

@instrument_tool("get_fx_rates")
def get_fx_rates() -> ToolResult:
    """
    Fetch USD/VND exchange rates: SBV central rate + Vietcombank commercial rates.
    Returns buy, sell, transfer, and central_rate (None if SBV unavailable).
    """
    try:
        from data.fx_scraper import fetch_vcb_usdvnd, fetch_sbv_central_rate
        data = fetch_vcb_usdvnd()
        if data is None:
            return ToolResult(status="no_data", data=None,
                              message="Không lấy được tỷ giá USD/VND từ Vietcombank.")

        central = fetch_sbv_central_rate()
        data["central_rate"] = central

        lines = []
        if central:
            lines.append(f"Tỷ giá trung tâm (SBV): {central:,.0f} VND/USD")
        lines.append(
            f"Vietcombank — mua: {data['buy']:,.0f} | bán: {data['sell']:,.0f} | "
            f"CK: {data['transfer']:,.0f} VND"
        )
        msg = "\n".join(lines)
        return ToolResult(status="ok", data=data, message=msg)
    except Exception as e:
        return ToolResult(status="upstream_error", data=None,
                          message=f"Lỗi lấy tỷ giá: {e}")


# ── Tool: VN Gold (SJC) ───────────────────────────────────────────────────────

@instrument_tool("get_vn_gold")
def get_vn_gold() -> ToolResult:
    """
    Fetch SJC gold buy/sell prices (triệu đồng/lượng).
    Also computes premium vs world gold (requires get_commodities + get_fx_rates).
    """
    try:
        from data.gold_vn_scraper import fetch_sjc_gold, gold_vnd_per_oz

        sjc = fetch_sjc_gold()
        if sjc is None:
            return ToolResult(status="no_data", data=None,
                              message="Không lấy được giá vàng SJC.")

        # Compute premium vs world (best-effort; non-fatal if FX/commodity unavailable)
        premium_note = ""
        try:
            from data.fx_scraper import fetch_vcb_usdvnd
            import yfinance as yf
            gold_hist = yf.Ticker("GC=F").history(period="2d")
            fx = fetch_vcb_usdvnd()
            if not gold_hist.empty and fx:
                xau_usd = float(gold_hist["Close"].iloc[-1])
                usd_vnd = (fx["buy"] + fx["sell"]) / 2
                world_vnd = gold_vnd_per_oz(xau_usd, usd_vnd)
                mid_sjc = (sjc["buy_vnd"] + sjc["sell_vnd"]) / 2
                premium = round(mid_sjc - world_vnd, 1)
                premium_note = f" (chênh vs thế giới ~{premium:.1f} triệu đồng/lượng)"
        except Exception:
            pass

        msg = (
            f"Vàng SJC: mua {sjc['buy_vnd']:.1f} – bán {sjc['sell_vnd']:.1f} triệu đồng/lượng"
            f"{premium_note}"
        )
        return ToolResult(status="ok", data=sjc, message=msg)

    except Exception as e:
        return ToolResult(status="upstream_error", data=None,
                          message=f"Lỗi lấy giá vàng SJC: {e}")
