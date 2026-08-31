"""
tools/breakout.py — Breakout detection engine ported from stock-vn-v2.

Signal types: SHORT (20d base), MID (40d), LONG (100d), MID_PRE, LONG_PRE
Uses ohlcv_daily + foreign_flows tables.
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


@dataclass
class BreakoutSignal:
    ticker: str
    signal_type: str          # SHORT | MID | LONG | MID_PRE | LONG_PRE
    date: str
    price: float
    volume: int
    pivot: float
    target1: float
    target2: float
    stop_loss: float
    rs: float
    distribution_days: int
    macd_bullish: bool
    foreign_net_ratio: float
    tight_range_pct: float
    vol_ratio: float = 0.0
    pct_to_pivot: float = 0.0  # only for PRE signals


def _simple_ma(arr: list[float], period: int) -> Optional[float]:
    if len(arr) < period:
        return None
    return sum(arr[-period:]) / period


def _ema(arr: list[float], period: int) -> list[float]:
    if len(arr) < period:
        return []
    k = 2 / (period + 1)
    result = [sum(arr[:period]) / period]
    for price in arr[period:]:
        result.append(price * k + result[-1] * (1 - k))
    return result


def _count_distribution_days(closes: list[float], volumes: list[float], period: int) -> int:
    if len(closes) < period + 1:
        return 0
    c = closes[-(period + 1):]
    v = volumes[-(period + 1):]
    avg_vol = sum(v) / len(v) if v else 0
    count = 0
    for i in range(1, len(c)):
        if c[i - 1] > 0:
            pct = (c[i] - c[i - 1]) / c[i - 1]
            if pct <= -0.002 and v[i] > avg_vol:
                count += 1
    return count


def _detect_macd_bullish(closes: list[float]) -> bool:
    if len(closes) < 35:
        return False
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    min_len = min(len(ema12), len(ema26))
    if min_len < 9:
        return False
    macd_line = [ema12[i + len(ema12) - min_len] - ema26[i + len(ema26) - min_len]
                 for i in range(min_len)]
    signal_line = _ema(macd_line, 9)
    min2 = min(len(macd_line), len(signal_line))
    if min2 < 3:
        return False
    hist = [macd_line[-min2 + i] - signal_line[i] for i in range(min2)]
    # Signal 1: cross in last 5
    for i in range(max(1, len(hist) - 5), len(hist)):
        if hist[i - 1] < 0 <= hist[i]:
            return True
    # Signal 2: zero cross upward
    if hist[-1] > 0 >= hist[-2]:
        return True
    # Signal 3: accelerating histogram (last 3 rising)
    if len(hist) >= 3 and hist[-1] > hist[-2] > hist[-3]:
        return True
    return False


def _relative_strength(stock_closes: list[float], market_closes: list[float], period: int = 63) -> float:
    if len(stock_closes) < period + 1 or len(market_closes) < period + 1:
        return 1.0
    sr = (stock_closes[-1] - stock_closes[-period - 1]) / stock_closes[-period - 1]
    mr = (market_closes[-1] - market_closes[-period - 1]) / market_closes[-period - 1]
    if mr == 0:
        return 1.0
    return sr / mr


def _get_foreign_dominance(ticker: str, days: int = 20) -> dict:
    _empty = {"accumulating_in_base": False, "strong_signal": False, "net_ratio_today": 0.0, "net_total": 0.0}
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT buy_volume, sell_volume, net_volume
                    FROM foreign_flows
                    WHERE ticker = %s
                    ORDER BY date DESC
                    LIMIT %s
                    """,
                    (ticker, days),
                )
                rows = cur.fetchall()
        if not rows:
            return _empty
        today_buy = float(rows[0][0] or 0)
        today_sell = float(rows[0][1] or 0)
        today_net = float(rows[0][2] or 0)
        total_today = today_buy + today_sell
        net_ratio_today = today_net / total_today if total_today > 0 else 0.0
        nets = [float(r[2] or 0) for r in rows]
        buy_days = sum(1 for n in nets if n > 0)
        accumulating = buy_days >= len(nets) * 0.6
        strong = today_net > 0 and accumulating and net_ratio_today > 0.02
        return {
            "accumulating_in_base": accumulating,
            "strong_signal": strong,
            "net_ratio_today": net_ratio_today,
            "net_total": sum(nets),
        }
    except Exception as e:
        sys.stderr.write(f"[breakout] _get_foreign_dominance({ticker}) failed: {e}\n")
        return _empty


def _market_uptrend(market_closes: list[float]) -> bool:
    ma20 = _simple_ma(market_closes, 20)
    ma60 = _simple_ma(market_closes, 60)
    return ma20 is not None and ma60 is not None and ma20 > ma60 * 0.97


def check_breakout_short(df: pd.DataFrame, market_df: pd.DataFrame, foreign: dict) -> Optional[BreakoutSignal]:
    closes = df["close"].tolist()
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    volumes = df["volume"].tolist()
    opens = df["open"].tolist()
    if len(closes) < 25:
        return None

    BASE = 20
    PIVOT_P = 10
    pivot = max(closes[-(PIVOT_P + 1):-1])
    base_low = min(lows[-(BASE + 1):-1])
    base_high = max(highs[-(BASE + 1):-1])
    if base_low <= 0:
        return None

    tight_range = (base_high - base_low) / base_low < 0.15
    dist_days = _count_distribution_days(closes[-(BASE + 2):], volumes[-(BASE + 2):], BASE)

    today_close = closes[-1]
    today_open = opens[-1]
    today_vol = volumes[-1]
    above_pivot = today_close > pivot
    strong_close = today_close > today_open

    avg_vol_20 = _simple_ma(volumes[-21:-1], 20)
    if not avg_vol_20:
        return None

    macd_bullish = _detect_macd_bullish(closes[:-1])
    if macd_bullish:
        vol_threshold = 1.0 if foreign.get("strong_signal") else 1.2
    else:
        vol_threshold = 1.4 if foreign.get("accumulating_in_base") else 1.5

    low_52w = min(lows[-252:]) if len(lows) >= 252 else min(lows)
    if low_52w > 0 and today_close > low_52w * 2.0:
        vol_threshold += 0.3

    vol_break = today_vol >= avg_vol_20 * vol_threshold
    ma20 = _simple_ma(closes[-21:-1], 20)
    ma20_entry = ma20 is not None and closes[-2] < ma20 * 0.995 and today_close > ma20
    breakout = (above_pivot and strong_close and vol_break) or ma20_entry

    ma60 = _simple_ma(closes[-61:-1], 60)
    weekly_uptrend = ma20 is not None and ma60 is not None and ma20 > ma60 * 0.98

    mkt_closes = market_df["close"].tolist() if len(market_df) >= 60 else []
    mkt_up = _market_uptrend(mkt_closes) if len(mkt_closes) >= 60 else True

    rs = _relative_strength(closes, mkt_closes) if len(mkt_closes) >= 64 else 1.0
    rs_ok = rs >= 0.85

    if not (breakout and tight_range and dist_days <= 3 and weekly_uptrend and mkt_up and rs_ok):
        return None

    base_height = base_high - base_low
    return BreakoutSignal(
        ticker="", signal_type="SHORT", date="",
        price=today_close, volume=int(today_vol), pivot=round(pivot, 0),
        target1=round(pivot + base_height * 0.618, 0), target2=round(pivot + base_height, 0),
        stop_loss=round(max(base_low, today_close * 0.92), 0),
        rs=round(rs, 2), distribution_days=dist_days, macd_bullish=macd_bullish,
        foreign_net_ratio=round(foreign.get("net_ratio_today", 0), 4),
        tight_range_pct=round((base_high - base_low) / base_low * 100, 1),
        vol_ratio=round(today_vol / avg_vol_20, 1),
    )


def check_breakout_mid(df: pd.DataFrame, market_df: pd.DataFrame, foreign: dict) -> Optional[BreakoutSignal]:
    closes = df["close"].tolist()
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    volumes = df["volume"].tolist()
    opens = df["open"].tolist()
    if len(closes) < 45:
        return None

    BASE = 40
    pivot = max(closes[-(BASE + 1):-1])
    base_low = min(lows[-(BASE + 1):-1])
    base_high = max(highs[-(BASE + 1):-1])
    if base_low <= 0:
        return None

    tight_range = (base_high - base_low) / base_low < 0.20
    dist_days = _count_distribution_days(closes[-(BASE + 2):], volumes[-(BASE + 2):], BASE)

    today_close = closes[-1]
    today_open = opens[-1]
    today_vol = volumes[-1]
    above_pivot = today_close > pivot
    strong_close = today_close > today_open

    avg_vol = _simple_ma(volumes[-41:-1], 40) or _simple_ma(volumes[-21:-1], 20)
    if not avg_vol:
        return None

    macd_bullish = _detect_macd_bullish(closes[:-1])
    if macd_bullish:
        vol_threshold = 1.2 if foreign.get("strong_signal") else 1.5
    else:
        vol_threshold = 1.6 if foreign.get("accumulating_in_base") else 1.8

    low_52w = min(lows[-252:]) if len(lows) >= 252 else min(lows)
    if low_52w > 0 and today_close > low_52w * 2.0:
        vol_threshold += 0.3

    vol_break = today_vol >= avg_vol * vol_threshold
    ma20 = _simple_ma(closes[-21:-1], 20)
    ma20_entry = ma20 is not None and closes[-2] < ma20 * 0.995 and today_close > ma20
    breakout = (above_pivot and strong_close and vol_break) or ma20_entry

    ma60 = _simple_ma(closes[-61:-1], 60)
    weekly_uptrend = ma20 is not None and ma60 is not None and ma20 > ma60 * 0.98

    mkt_closes = market_df["close"].tolist() if len(market_df) >= 60 else []
    mkt_up = _market_uptrend(mkt_closes) if len(mkt_closes) >= 60 else True

    rs = _relative_strength(closes, mkt_closes) if len(mkt_closes) >= 64 else 1.0
    rs_ok = rs >= 0.85

    if not (breakout and tight_range and dist_days <= 6 and weekly_uptrend and mkt_up and rs_ok):
        return None

    base_height = base_high - base_low
    return BreakoutSignal(
        ticker="", signal_type="MID", date="",
        price=today_close, volume=int(today_vol), pivot=round(pivot, 0),
        target1=round(pivot + base_height * 0.618, 0), target2=round(pivot + base_height, 0),
        stop_loss=round(max(base_low, today_close * 0.92), 0),
        rs=round(rs, 2), distribution_days=dist_days, macd_bullish=macd_bullish,
        foreign_net_ratio=round(foreign.get("net_ratio_today", 0), 4),
        tight_range_pct=round((base_high - base_low) / base_low * 100, 1),
        vol_ratio=round(today_vol / avg_vol, 1),
    )


def check_breakout_long(df: pd.DataFrame, market_df: pd.DataFrame, foreign: dict) -> Optional[BreakoutSignal]:
    closes = df["close"].tolist()
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    volumes = df["volume"].tolist()
    opens = df["open"].tolist()
    if len(closes) < 105:
        return None

    BASE = 100
    pivot = max(closes[-(BASE + 1):-1])
    base_low = min(lows[-(BASE + 1):-1])
    base_high = max(highs[-(BASE + 1):-1])
    if base_low <= 0:
        return None

    tight_range = (base_high - base_low) / base_low < 0.35
    dist_days = _count_distribution_days(closes[-(BASE + 2):], volumes[-(BASE + 2):], BASE)

    today_close = closes[-1]
    today_open = opens[-1]
    today_vol = volumes[-1]
    above_pivot = today_close > pivot
    strong_close = today_close > today_open

    avg_vol_20 = _simple_ma(volumes[-21:-1], 20)
    avg_vol_80 = _simple_ma(volumes[-81:-1], 80)
    if not avg_vol_20:
        return None

    # Volume contraction: recent 20d avg < prior 80d avg * 0.8
    vol_contracting = avg_vol_80 is not None and avg_vol_20 < avg_vol_80 * 0.8

    macd_bullish = _detect_macd_bullish(closes[:-1])
    vol_threshold = 2.0
    if macd_bullish:
        vol_threshold = 1.6
    if foreign.get("strong_signal"):
        vol_threshold -= 0.2

    low_52w = min(lows[-252:]) if len(lows) >= 252 else min(lows)
    if low_52w > 0 and today_close > low_52w * 2.0:
        vol_threshold += 0.3

    vol_break = today_vol >= avg_vol_20 * vol_threshold
    ma20 = _simple_ma(closes[-21:-1], 20)
    ma20_entry = ma20 is not None and closes[-2] < ma20 * 0.995 and today_close > ma20
    breakout = tight_range and (vol_contracting or ma20_entry) and (above_pivot and strong_close and vol_break)

    ma60 = _simple_ma(closes[-61:-1], 60)
    weekly_strong = ma20 is not None and ma60 is not None and ma20 > ma60 * 1.0

    mkt_closes = market_df["close"].tolist() if len(market_df) >= 60 else []
    mkt_up = _market_uptrend(mkt_closes) if len(mkt_closes) >= 60 else True

    rs = _relative_strength(closes, mkt_closes) if len(mkt_closes) >= 64 else 1.0
    rs_ok = rs >= 0.85

    if not (breakout and dist_days <= 10 and weekly_strong and mkt_up and rs_ok):
        return None

    base_height = base_high - base_low
    return BreakoutSignal(
        ticker="", signal_type="LONG", date="",
        price=today_close, volume=int(today_vol), pivot=round(pivot, 0),
        target1=round(pivot + base_height * 0.618, 0), target2=round(pivot + base_height, 0),
        stop_loss=round(max(base_low, today_close * 0.92), 0),
        rs=round(rs, 2), distribution_days=dist_days, macd_bullish=macd_bullish,
        foreign_net_ratio=round(foreign.get("net_ratio_today", 0), 4),
        tight_range_pct=round((base_high - base_low) / base_low * 100, 1),
        vol_ratio=round(today_vol / avg_vol_20, 1),
    )


def _check_pre_breakout(
    df: pd.DataFrame, market_df: pd.DataFrame, foreign: dict,
    period: int, tight_pct: float, max_dist: int, sig_type: str,
) -> Optional[BreakoutSignal]:
    closes = df["close"].tolist()
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    volumes = df["volume"].tolist()
    if len(closes) < period + 5:
        return None

    pivot = max(closes[-(period + 1):-1])
    base_low = min(lows[-(period + 1):-1])
    base_high = max(highs[-(period + 1):-1])
    if base_low <= 0:
        return None

    tight_base = (base_high - base_low) / base_low < tight_pct
    today_close = closes[-1]
    approach_floor = 0.94 if foreign.get("accumulating_in_base") else 0.95
    approaching = pivot * approach_floor <= today_close < pivot

    macd_bullish = _detect_macd_bullish(closes[:-1])
    dist_days = _count_distribution_days(closes[-(period + 2):], volumes[-(period + 2):], period)

    ma20 = _simple_ma(closes[-21:-1], 20)
    ma60 = _simple_ma(closes[-61:-1], 60)
    weekly_uptrend = ma20 is not None and ma60 is not None and ma20 > ma60 * 0.98

    mkt_closes = market_df["close"].tolist() if len(market_df) >= 60 else []
    mkt_up = _market_uptrend(mkt_closes) if len(mkt_closes) >= 60 else True

    rs = _relative_strength(closes, mkt_closes) if len(mkt_closes) >= 64 else 1.0
    rs_ok = rs >= 0.85

    if not (tight_base and approaching and macd_bullish and dist_days <= max_dist
            and weekly_uptrend and mkt_up and rs_ok):
        return None

    base_height = base_high - base_low
    avg_vol = _simple_ma(volumes[-21:-1], 20) or 1
    return BreakoutSignal(
        ticker="", signal_type=sig_type, date="",
        price=today_close, volume=int(volumes[-1]), pivot=round(pivot, 0),
        target1=round(pivot + base_height * 0.618, 0), target2=round(pivot + base_height, 0),
        stop_loss=round(max(base_low, today_close * 0.92), 0),
        rs=round(rs, 2), distribution_days=dist_days, macd_bullish=True,
        foreign_net_ratio=round(foreign.get("net_ratio_today", 0), 4),
        tight_range_pct=round((base_high - base_low) / base_low * 100, 1),
        vol_ratio=round(volumes[-1] / avg_vol, 1),
        pct_to_pivot=round((pivot - today_close) / today_close * 100, 1),
    )


def scan_ticker(ticker: str, market_df: pd.DataFrame) -> list[BreakoutSignal]:
    """Scan one ticker for all 5 signal types. Returns list of BreakoutSignal."""
    try:
        from tools.ohlcv_db import query_ohlcv
        df = query_ohlcv(ticker, days=130)
        if df is None or len(df) < 30:
            return []
        today_date = str(df["time"].iloc[-1])
        foreign = _get_foreign_dominance(ticker, days=20)
        signals: list[BreakoutSignal] = []
        for check_fn in (check_breakout_short, check_breakout_mid, check_breakout_long):
            sig = check_fn(df, market_df, foreign)
            if sig:
                sig.ticker = ticker
                sig.date = today_date
                signals.append(sig)
        for period, tight, max_d, stype in [
            (40, 0.20, 6, "MID_PRE"),
            (100, 0.35, 10, "LONG_PRE"),
        ]:
            sig = _check_pre_breakout(df, market_df, foreign, period, tight, max_d, stype)
            if sig:
                sig.ticker = ticker
                sig.date = today_date
                signals.append(sig)
        return signals
    except Exception as e:
        sys.stderr.write(f"[breakout] scan_ticker({ticker}) failed: {e}\n")
        return []


def get_active_tickers(exchange: Optional[str] = None) -> list[str]:
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                if exchange:
                    cur.execute(
                        "SELECT ticker FROM securities WHERE is_active = true AND exchange = %s ORDER BY ticker",
                        (exchange,),
                    )
                else:
                    cur.execute("SELECT ticker FROM securities WHERE is_active = true ORDER BY ticker")
                return [r[0] for r in cur.fetchall()]
    except Exception as e:
        sys.stderr.write(f"[breakout] get_active_tickers failed: {e}\n")
        return []


def scan_all(market_df: pd.DataFrame, tickers: list[str], max_workers: int = 8) -> list[BreakoutSignal]:
    """Scan all tickers in parallel. Returns all signals sorted by signal_type."""
    all_signals: list[BreakoutSignal] = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(scan_ticker, t, market_df): t for t in tickers}
        for fut in futures:
            try:
                all_signals.extend(fut.result())
            except Exception:
                pass
    # Sort: confirmed (SHORT/MID/LONG) before pre-breakout
    order = {"SHORT": 0, "MID": 1, "LONG": 2, "MID_PRE": 3, "LONG_PRE": 4}
    all_signals.sort(key=lambda s: (order.get(s.signal_type, 9), -s.rs))
    return all_signals
