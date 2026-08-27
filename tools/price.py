"""
tools/price.py — Tool giá chứng khoán (bài 19 + 19B + 20).

Providers live in tools/providers.py.
Mọi public function trả ToolResult — không raise ra ngoài,
không trả empty list trần. Agent đọc .message để quyết định bước tiếp.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from tools.providers import (
    PriceProvider,
    VciDirectProvider,
    YFinanceProvider,
    _detect_provider,
    _history_cache,
    _price_cache,
    resolve_ticker,
)
from tools.result import ToolResult


_default_provider: PriceProvider = VciDirectProvider()


def set_provider(provider: PriceProvider) -> None:
    """Swap provider (dùng trong test để inject mock)."""
    global _default_provider
    _default_provider = provider


# ── Error mapping helper ──────────────────────────────────────────────────────

def _map_upstream_error(ticker: str, exc: Exception) -> ToolResult:
    msg = str(exc).lower()
    if "429" in msg or "rate" in msg or "too many" in msg:
        return ToolResult(
            status="rate_limited",
            data=None,
            message=(
                f"Đã vượt giới hạn request khi lấy dữ liệu '{ticker}'. "
                "Chờ 60 giây rồi thử lại. Đừng gọi lại ngay — sẽ bị chặn tiếp."
            ),
        )
    if "timeout" in msg or "timed out" in msg:
        return ToolResult(
            status="upstream_error",
            data=None,
            message=(
                f"Timeout khi kết nối nguồn dữ liệu cho '{ticker}'. "
                "Thử lại sau 1–2 phút. Không cần đổi tham số."
            ),
        )
    if "500" in msg or "server error" in msg or "internal server" in msg:
        return ToolResult(
            status="upstream_error",
            data=None,
            message=(
                f"Server nguồn dữ liệu trả lỗi 500 khi lấy '{ticker}'. "
                "Đây là lỗi tạm thời phía server. Thử lại sau 1–2 phút, không đổi tham số."
            ),
        )
    return ToolResult(
        status="upstream_error",
        data=None,
        message=(
            f"Lỗi kết nối khi lấy dữ liệu '{ticker}': {exc}. "
            "Thử lại sau vài phút."
        ),
    )


# ── Tool 1: Giá hiện tại ─────────────────────────────────────────────────────

def get_realtime_price(ticker: str, provider: PriceProvider | None = None) -> ToolResult:
    """Trả giá đóng cửa gần nhất (VND). Luôn trả ToolResult, không raise."""
    if not ticker or not ticker.strip():
        return ToolResult(
            status="invalid_input",
            data=None,
            message="ticker không được rỗng. Thử với mã hợp lệ như 'HPG' hoặc 'FPT'.",
        )
    t = ticker.strip().upper()
    resolved = resolve_ticker(t)  # VNINDEX → ^VNINDEX, stock → unchanged
    p = provider or _detect_provider(t)
    try:
        price = p.get_price(resolved)
        return ToolResult(
            status="ok",
            data=price,
            message=f"Giá {t}: {price:,.0f} VND (phiên gần nhất).",
        )
    except ValueError as e:
        return ToolResult(
            status="no_data",
            data=None,
            message=(
                f"Không có dữ liệu giá cho '{t}': {e}. "
                "Kiểm tra mã CK đúng chính tả. Thử mã khác hoặc dùng get_historical_ohlcv."
            ),
        )
    except Exception as e:
        return _map_upstream_error(t, e)


def get_realtime_price_intl(ticker: str, provider: PriceProvider | None = None) -> ToolResult:
    """Trả giá đóng cửa gần nhất (USD) cho mã quốc tế. Luôn trả ToolResult, không raise."""
    if not ticker or not ticker.strip():
        return ToolResult(
            status="invalid_input",
            data=None,
            message="ticker không được rỗng. Thử với mã quốc tế như 'AAPL' hoặc 'TSLA'.",
        )
    t = ticker.strip().upper()
    p = provider or YFinanceProvider()
    try:
        price = p.get_price(t)
        return ToolResult(
            status="ok",
            data=price,
            message=f"Giá {t}: {price:.2f} USD (phiên gần nhất).",
        )
    except ValueError as e:
        return ToolResult(
            status="no_data",
            data=None,
            message=(
                f"Không có dữ liệu giá cho '{t}': {e}. "
                "Kiểm tra mã NYSE/NASDAQ đúng chính tả. Thử mã khác."
            ),
        )
    except Exception as e:
        return _map_upstream_error(t, e)


# ── Tool 2: Lịch sử OHLCV ────────────────────────────────────────────────────

def get_historical_ohlcv(
    ticker: str,
    days: int = 60,
    provider: PriceProvider | None = None,
) -> ToolResult:
    """Trả DataFrame OHLCV `days` phiên gần nhất. Luôn trả ToolResult, không raise."""
    if not ticker or not ticker.strip():
        return ToolResult(
            status="invalid_input",
            data=None,
            message="ticker không được rỗng. Thử với mã hợp lệ như 'HPG'.",
        )
    if days < 1:
        return ToolResult(
            status="invalid_input",
            data=None,
            message=f"days={days} không hợp lệ. Phải >= 1. Thử days=30 hoặc days=60.",
        )
    t = ticker.strip().upper()
    resolved = resolve_ticker(t)  # VNINDEX → ^VNINDEX, stock → unchanged
    p = provider or _detect_provider(t)
    try:
        df = p.get_history(resolved, days)
        return ToolResult(
            status="ok",
            data=df,
            message=f"Lấy được {len(df)} phiên lịch sử OHLCV của {t} (VND).",
        )
    except ValueError as e:
        return ToolResult(
            status="no_data",
            data=None,
            message=(
                f"Không có dữ liệu lịch sử cho '{t}': {e}. "
                "Kiểm tra mã CK hoặc giảm số ngày (days)."
            ),
        )
    except Exception as e:
        return _map_upstream_error(t, e)


def get_historical_ohlcv_intl(
    ticker: str,
    days: int = 60,
    provider: PriceProvider | None = None,
) -> ToolResult:
    """Trả DataFrame OHLCV `days` phiên gần nhất cho mã quốc tế (USD). Luôn trả ToolResult."""
    if not ticker or not ticker.strip():
        return ToolResult(
            status="invalid_input",
            data=None,
            message="ticker không được rỗng. Thử với mã quốc tế như 'AAPL'.",
        )
    if days < 1:
        return ToolResult(
            status="invalid_input",
            data=None,
            message=f"days={days} không hợp lệ. Phải >= 1. Thử days=30.",
        )
    t = ticker.strip().upper()
    p = provider or YFinanceProvider()
    try:
        df = p.get_history(t, days)
        return ToolResult(
            status="ok",
            data=df,
            message=f"Lấy được {len(df)} phiên lịch sử OHLCV của {t} (USD).",
        )
    except ValueError as e:
        return ToolResult(
            status="no_data",
            data=None,
            message=(
                f"Không có dữ liệu lịch sử cho '{t}': {e}. "
                "Kiểm tra mã NYSE/NASDAQ hoặc giảm số ngày."
            ),
        )
    except Exception as e:
        return _map_upstream_error(t, e)


# ── Tool 3: Chỉ báo kỹ thuật ─────────────────────────────────────────────────

def calculate_indicators(df: pd.DataFrame, currency: str = "VND") -> ToolResult:
    """
    Tính RSI(14), MACD(12,26,9), MA(20/50/200), EMA(200), ADX(14),
    Ichimoku Kumo position, volume vs TB 20 tuần (100 phiên).
    Luôn trả ToolResult, không raise.

    Args:
        df: DataFrame từ get_historical_ohlcv (phải có cột 'close', 'high', 'low', 'volume').
        currency: 'VND' hoặc 'USD'. Tag vào output để model không so sánh sai đơn vị.
    """
    try:
        import pandas_ta as ta  # noqa: F401
    except ImportError:
        return ToolResult(
            status="upstream_error",
            data=None,
            message="pandas-ta chưa cài. Chạy: pip install pandas-ta rồi thử lại.",
        )

    if df is None or df.empty:
        return ToolResult(
            status="invalid_input",
            data=None,
            message="DataFrame rỗng. Truyền vào DataFrame có dữ liệu từ get_historical_ohlcv.",
        )
    if "close" not in df.columns:
        return ToolResult(
            status="invalid_input",
            data=None,
            message="DataFrame thiếu cột 'close'. Truyền vào DataFrame từ get_historical_ohlcv.",
        )

    def _safe_float(series) -> float:
        try:
            v = float(series.iloc[-1]) if series is not None else float("nan")
            return float("nan") if pd.isna(v) else v
        except (TypeError, ValueError, IndexError):
            return float("nan")

    try:
        lines: list[str] = [f"[Đơn vị: {currency}]"]
        close_last = float(df["close"].iloc[-1])

        # RSI
        rsi = _safe_float(df.ta.rsi(length=14))
        if pd.isna(rsi):
            lines.append("RSI(14): không đủ dữ liệu (cần ít nhất 14 phiên)")
        else:
            zone = "quá mua" if rsi > 70 else "quá bán" if rsi < 30 else "trung tính"
            lines.append(f"RSI(14) = {rsi:.1f} → vùng {zone}")

        # MACD
        macd_df = df.ta.macd(fast=12, slow=26, signal=9)
        if macd_df is None or "MACD_12_26_9" not in macd_df.columns:
            lines.append("MACD(12,26,9): không đủ dữ liệu (cần ít nhất 26 phiên)")
        else:
            macd_val = _safe_float(macd_df["MACD_12_26_9"])
            signal_val = _safe_float(macd_df["MACDs_12_26_9"])
            hist_val = _safe_float(macd_df["MACDh_12_26_9"])
            if pd.isna(macd_val):
                lines.append("MACD(12,26,9): không đủ dữ liệu")
            else:
                trend = "tăng" if hist_val > 0 else "giảm"
                lines.append(
                    f"MACD(12,26,9) = {macd_val:.2f}, Signal = {signal_val:.2f}, "
                    f"Histogram = {hist_val:.2f} → xu hướng {trend}"
                )

        # SMA 20/50/200
        for length, label in [(20, "MA(20)"), (50, "MA(50)"), (200, "MA(200)")]:
            ma_val = _safe_float(df.ta.sma(length=length))
            if pd.isna(ma_val):
                lines.append(f"{label}: không đủ dữ liệu (cần ít nhất {length} phiên)")
            else:
                pos = "trên" if close_last > ma_val else "dưới"
                lines.append(f"{label} = {ma_val:,.0f} → giá đang {pos} {label}")

        # EMA 200
        ema200_val = _safe_float(df.ta.ema(length=200))
        if pd.isna(ema200_val):
            lines.append("EMA(200): không đủ dữ liệu (cần ít nhất 200 phiên)")
        else:
            pos = "trên" if close_last > ema200_val else "dưới"
            lines.append(f"EMA(200) = {ema200_val:,.0f} → giá đang {pos} EMA(200)")

        # ADX(14) — cần high/low/close
        has_hlc = all(c in df.columns for c in ("high", "low", "close"))
        if has_hlc:
            adx_df = df.ta.adx(length=14)
            adx_col = "ADX_14"
            if adx_df is not None and adx_col in adx_df.columns:
                adx_val = _safe_float(adx_df[adx_col])
                if pd.isna(adx_val):
                    lines.append("ADX(14): không đủ dữ liệu")
                else:
                    strength = "xu hướng mạnh" if adx_val > 25 else "xu hướng yếu/sideway"
                    lines.append(f"ADX(14) = {adx_val:.1f} → {strength}")
            else:
                lines.append("ADX(14): không đủ dữ liệu")
        else:
            lines.append("ADX(14): thiếu cột high/low")

        # Ichimoku Kumo — cần high/low (dùng pandas-ta ichimoku)
        if has_hlc and len(df) >= 52:
            ichi = df.ta.ichimoku()
            # pandas-ta trả tuple (span_a_df, span_b_df) hoặc DataFrame
            if isinstance(ichi, tuple):
                ichi_df = ichi[0]
            else:
                ichi_df = ichi
            if ichi_df is not None and not ichi_df.empty:
                # cột IKS_9 (Senkou Span A) và IKS_26 (Senkou Span B) — tên thực tế:
                span_a_col = next((c for c in ichi_df.columns if c.startswith("ISA")), None)
                span_b_col = next((c for c in ichi_df.columns if c.startswith("ISB")), None)
                if span_a_col and span_b_col:
                    sa = _safe_float(ichi_df[span_a_col])
                    sb = _safe_float(ichi_df[span_b_col])
                    if not (pd.isna(sa) or pd.isna(sb)):
                        kumo_top = max(sa, sb)
                        kumo_bot = min(sa, sb)
                        if close_last > kumo_top:
                            kumo_pos = "trên Kumo (bullish)"
                        elif close_last < kumo_bot:
                            kumo_pos = "dưới Kumo (bearish)"
                        else:
                            kumo_pos = "trong Kumo (neutral)"
                        lines.append(
                            f"Ichimoku Kumo: SpanA={sa:,.0f}, SpanB={sb:,.0f} → giá {kumo_pos}"
                        )
                    else:
                        lines.append("Ichimoku: không đủ dữ liệu")
                else:
                    lines.append("Ichimoku: không nhận được cột Span A/B")
            else:
                lines.append("Ichimoku: không đủ dữ liệu")
        else:
            needed = "thiếu cột high/low" if not has_hlc else "không đủ dữ liệu (cần ít nhất 52 phiên)"
            lines.append(f"Ichimoku: {needed}")

        # Volume vs TB 20 tuần (100 phiên giao dịch)
        if "volume" in df.columns and len(df) >= 5:
            vol_last = float(df["volume"].iloc[-1])
            n_avg = min(100, len(df) - 1)
            vol_avg = float(df["volume"].iloc[-1 - n_avg: -1].mean()) if n_avg > 0 else float("nan")
            if not pd.isna(vol_avg) and vol_avg > 0:
                vol_ratio = vol_last / vol_avg
                if vol_ratio >= 1:
                    vol_note = f"cao hơn TB {n_avg} phiên ~{(vol_ratio - 1) * 100:.0f}%"
                else:
                    vol_note = f"thấp hơn TB {n_avg} phiên ~{(1 - vol_ratio) * 100:.0f}%"
                lines.append(f"Volume = {vol_last:,.0f} → {vol_note}")
            else:
                lines.append("Volume TB: không đủ dữ liệu")
        else:
            lines.append("Volume TB: thiếu cột volume hoặc không đủ dữ liệu")

        result_str = "\n".join(lines)
        return ToolResult(status="ok", data=result_str, message=result_str)

    except Exception as e:
        return ToolResult(
            status="upstream_error",
            data=None,
            message=f"Lỗi khi tính chỉ báo kỹ thuật: {e}. Kiểm tra DataFrame đầu vào.",
        )


def detect_candle_pattern(df: pd.DataFrame) -> ToolResult:
    """
    Nhận diện mẫu nến Doji / Marubozu / Hammer trên nến cuối cùng.
    Dùng rule thuần, không cần TA-Lib.

    Args:
        df: DataFrame OHLCV với cột open, high, low, close.
    Returns:
        ToolResult(data=str) — tên mẫu nến hoặc "Không xác định".
    """
    required = {"open", "high", "low", "close"}
    if df is None or df.empty:
        return ToolResult(status="invalid_input", data=None,
                          message="DataFrame rỗng.")
    if not required.issubset(df.columns):
        missing = required - set(df.columns)
        return ToolResult(status="invalid_input", data=None,
                          message=f"Thiếu cột: {missing}. Cần open/high/low/close.")

    try:
        row = df.iloc[-1]
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        body = abs(c - o)
        candle_range = h - l
        if candle_range == 0:
            pattern = "Không xác định (range = 0)"
        else:
            body_ratio = body / candle_range
            upper_shadow = h - max(o, c)
            lower_shadow = min(o, c) - l

            # Hammer/Hanging Man checked first: long lower shadow can have tiny body
            # that would otherwise be misclassified as Doji
            if (
                lower_shadow >= max(body * 2, candle_range * 0.5)
                and upper_shadow < lower_shadow * 0.3
                and c >= o
            ):
                pattern = "Hammer — râu dưới dài, tiềm năng đảo chiều tăng"
            elif (
                lower_shadow >= max(body * 2, candle_range * 0.5)
                and upper_shadow < lower_shadow * 0.3
                and c < o
            ):
                pattern = "Hanging Man — râu dưới dài trên đỉnh, cảnh báo đảo chiều giảm"
            elif body_ratio > 0.85 and upper_shadow < body * 0.1 and lower_shadow < body * 0.1:
                color = "xanh" if c >= o else "đỏ"
                pattern = f"Marubozu {color} — thân lớn, không râu, xu hướng mạnh"
            elif body_ratio < 0.1:
                pattern = "Doji — thân nến rất nhỏ, do dự"
            else:
                pattern = "Không xác định"

        msg = f"Mẫu nến (phiên cuối): {pattern}"
        return ToolResult(status="ok", data=pattern, message=msg)

    except Exception as e:
        return ToolResult(status="upstream_error", data=None,
                          message=f"Lỗi nhận diện mẫu nến: {e}.")


# ── Tool 4: Tin tức tài chính ─────────────────────────────────────────────────

# Market indices — skip VCI price validation, use general search (no ticker filter)
_MARKET_INDICES = frozenset({
    "VNINDEX", "VN-INDEX", "VN30", "VN100",
    "HOSE", "HNX", "UPCOM", "HNX30",
})


def _is_market_index(ticker: str) -> bool:
    return ticker.strip().upper() in _MARKET_INDICES


def _auto_fetch_ticker_news(ticker: str, days: int) -> None:
    """Background fetch from cafef + tavily when ticker has no news. Non-fatal."""
    try:
        from data.cafef_ticker_scraper import fetch_and_save as cafef_fetch
        from data.tavily_news import fetch_and_save as tavily_fetch
        from rag.news_index import index_unindexed_batch
        cafef_fetch(ticker)
        tavily_fetch(ticker, days=max(days, 30))
        index_unindexed_batch()
    except Exception as e:
        import sys
        sys.stderr.write(f"[auto-fetch] failed for {ticker}: {e}\n")


def _dedup_news(raw: list[dict], limit: int = 5) -> list[dict]:
    seen_urls: set[str] = set()
    unique: list[dict] = []
    for item in raw:
        url = item.get("url", "")
        if url not in seen_urls:
            seen_urls.add(url)
            unique.append(item)
        if len(unique) == limit:
            break
    return unique


def search_financial_news(
    ticker: str,
    days: int = 7,
    provider: PriceProvider | None = None,  # unused — kept for interface consistency
) -> ToolResult:
    """Tìm tin tức tài chính về ticker trong N ngày gần nhất từ Qdrant news_chunks.

    ticker có thể là mã cổ phiếu (HPG, VNM) hoặc chỉ số thị trường (VNINDEX, HOSE, VN30).
    Với chỉ số: tìm tin tức chung về thị trường, không lọc theo ticker.
    Với mã CK chưa có news: tự động fetch từ cafef + Tavily rồi retry.
    """
    if not ticker or not ticker.strip():
        return ToolResult(
            status="invalid_input",
            data=None,
            message="ticker không được rỗng. Thử với mã như 'HPG', 'VNM' hoặc chỉ số 'VNINDEX', 'HOSE'.",
        )
    if days < 1 or days > 365:
        return ToolResult(
            status="invalid_input",
            data=None,
            message=f"days={days} không hợp lệ. Phải từ 1 đến 365. Thử days=7.",
        )

    t = ticker.strip().upper()
    market_query = _is_market_index(t)

    # For stock tickers: pre-validate via VCI/yfinance
    if not market_query:
        p = provider or _detect_provider(t)
        try:
            p.get_price(t)
        except Exception:
            return ToolResult(
                status="no_data",
                data=None,
                message=(
                    f"Mã '{t}' không tồn tại hoặc không có dữ liệu giá. "
                    "Kiểm tra lại mã CK. Không tìm kiếm tin tức cho mã không hợp lệ."
                ),
            )

    try:
        from rag.news_index import search_news_by_text
    except ImportError:
        return ToolResult(
            status="upstream_error",
            data=None,
            message="Không thể import rag.news_index. Kiểm tra Qdrant đang chạy và news_chunks đã được index.",
        )

    # Market index → general search (no ticker filter)
    # Stock ticker → filter by ticker
    search_ticker = None if market_query else t

    try:
        raw = search_news_by_text(t, days=days, limit=10, ticker=search_ticker)
    except Exception as e:
        return _map_upstream_error(t, e)

    unique = _dedup_news(raw, limit=5)

    # Auto-fetch on miss for stock tickers (not indices)
    if not unique and not market_query:
        _auto_fetch_ticker_news(t, days)
        try:
            raw2 = search_news_by_text(t, days=days, limit=10, ticker=t)
            unique = _dedup_news(raw2, limit=5)
        except Exception:
            pass

    if not unique:
        if market_query:
            return ToolResult(
                status="no_data",
                data=None,
                message=(
                    f"Không có tin tức thị trường trong {days} ngày gần nhất. "
                    "Tăng khoảng thời gian (days=30) để tìm tin tức cũ hơn."
                ),
            )
        return ToolResult(
            status="no_data",
            data=None,
            message=(
                f"Không có tin tức về {t} trong {days} ngày gần nhất. "
                "Tăng khoảng thời gian (days) hoặc thử mã CK khác."
            ),
        )

    lines: list[str] = []
    for item in unique:
        source = item.get("source", "unknown")
        pub = item.get("published_at", "")
        date_str = pub[:10] if pub else "N/A"
        title = item.get("title", "").strip()
        body = item.get("text", "").strip()
        summary_raw = body.split("\n")[0][:120] if body else ""
        summary = summary_raw if not summary_raw.startswith(title) else summary_raw[len(title):].strip(" —-")
        line = f"[{source} | {date_str}] {title}"
        if summary:
            line += f" — {summary}"
        lines.append(line)

    result_str = "\n".join(lines)
    return ToolResult(status="ok", data=result_str, message=result_str)


# ── Tool 5: Hiệu suất thị trường theo kỳ ────────────────────────────────────

_PERIOD_DAYS: dict[str, int] = {
    "today": 1,
    "week": 5,
    "month": 22,
    "quarter": 65,
    "year": 250,
}

_PERIOD_ALIASES: dict[str, str] = {
    "hôm nay": "today", "hom nay": "today",
    "tuần này": "week", "tuan nay": "week", "tuần": "week",
    "tháng này": "month", "thang nay": "month", "tháng": "month",
    "quý này": "quarter", "quy nay": "quarter", "quý": "quarter",
    "năm nay": "year", "nam nay": "year", "năm": "year",
}

_PERIOD_LABEL_VI: dict[str, str] = {
    "today": "hôm nay",
    "week": "tuần này",
    "month": "tháng này",
    "quarter": "quý này",
    "year": "năm nay",
}


def _compute_performance_from_df(
    df: pd.DataFrame, period_key: str, t: str
) -> ToolResult:
    """Shared computation for get_market_performance — works on any OHLCV DataFrame."""
    days = _PERIOD_DAYS[period_key]

    if df.empty or len(df) < 2:
        return ToolResult(
            status="no_data",
            data=None,
            message=f"Không đủ dữ liệu lịch sử để tính hiệu suất '{t}' kỳ '{period_key}'.",
        )

    if period_key == "today":
        period_df = df.iloc[-1:]
        prev_close = float(df["close"].iloc[-2]) if len(df) >= 2 else None
    else:
        period_df = df.tail(days) if len(df) >= days else df
        prev_idx = len(df) - len(period_df) - 1
        prev_close = float(df["close"].iloc[prev_idx]) if prev_idx >= 0 else None

    last_close = float(period_df["close"].iloc[-1])
    period_high = float(period_df["high"].max())
    period_low = float(period_df["low"].min())
    avg_vol = float(period_df["volume"].mean())

    base = prev_close if prev_close is not None else float(period_df["close"].iloc[0])
    pct_change = (last_close - base) / base * 100 if base else 0.0

    if pct_change > 3:
        trend = "tăng mạnh"
    elif pct_change > 0.5:
        trend = "tăng nhẹ"
    elif pct_change > -0.5:
        trend = "đi ngang"
    elif pct_change > -3:
        trend = "giảm nhẹ"
    else:
        trend = "giảm mạnh"

    period_label = _PERIOD_LABEL_VI[period_key]
    range_pct = (period_high - period_low) / period_low * 100 if period_low else 0.0

    summary_lines = [
        f"{t} {period_label}: {trend} ({pct_change:+.2f}%)",
        f"Đóng cửa: {last_close:,.0f} | Cao: {period_high:,.0f} | Thấp: {period_low:,.0f}",
        f"Biên độ kỳ: {range_pct:.1f}% | Khối lượng TB: {avg_vol:,.0f}",
    ]
    summary = "\n".join(summary_lines)
    return ToolResult(
        status="ok",
        data={
            "period": period_key,
            "ticker": t,
            "pct_change": round(pct_change, 2),
            "last_close": last_close,
            "high": period_high,
            "low": period_low,
            "avg_volume": round(avg_vol),
            "trend": trend,
            "trading_days": len(period_df),
            "summary": summary,
        },
        message=summary,
    )


def get_market_performance(period: str = "week", ticker: str = "VNINDEX") -> ToolResult:
    """Tóm tắt hiệu suất thị trường trong kỳ: % thay đổi, high/low, xu hướng.

    DB-first: query ohlcv_daily (Postgres). Falls back to live VCI API if DB empty.
    period: "today"|"week"|"month"|"quarter"|"year" hoặc tiếng Việt
            ("hôm nay", "tuần này", "quý này", "năm nay")
    ticker: chỉ số hoặc mã CK — mặc định VNINDEX (proxy VN30 trên VCI)
    """
    period_key = _PERIOD_ALIASES.get(period.strip().lower(), period.strip().lower())
    if period_key not in _PERIOD_DAYS:
        valid = "today, week, month, quarter, year"
        return ToolResult(
            status="invalid_input",
            data=None,
            message=(
                f"period='{period}' không hợp lệ. Dùng: {valid} "
                "hoặc tiếng Việt: 'hôm nay', 'tuần này', 'tháng này', 'quý này', 'năm nay'."
            ),
        )

    days = _PERIOD_DAYS[period_key]
    t = (ticker.strip().upper() if ticker else "VNINDEX")
    resolved = resolve_ticker(t)

    # DB-first
    from tools.ohlcv_db import query_ohlcv
    df = query_ohlcv(resolved, days + 15)
    if df is not None and len(df) >= 2:
        return _compute_performance_from_df(df, period_key, t)

    # Fallback: live VCI API
    p = _detect_provider(t)
    try:
        df = p.get_history(resolved, days + 15)
    except ValueError as e:
        return ToolResult(
            status="no_data",
            data=None,
            message=f"Không có dữ liệu lịch sử cho '{t}': {e}.",
        )
    except Exception as e:
        return _map_upstream_error(t, e)

    return _compute_performance_from_df(df, period_key, t)


# ── Tool 6: Market breadth VN30 ───────────────────────────────────────────────

_VN30_CONSTITUENTS: list[str] = [
    "ACB", "BCM", "BID", "BVH", "CTG", "FPT", "GAS", "GVR", "HDB", "HPG",
    "MBB", "MSN", "MWG", "PDR", "PLX", "POW", "SAB", "SHB", "SSB", "SSI",
    "STB", "TCB", "TPB", "VCB", "VHM", "VIB", "VIC", "VJC", "VNM", "VPB",
]


def _build_breadth_result(changes: list[dict], label: str = "VN30") -> ToolResult:
    """Shared result builder for get_market_breadth."""
    advances = [c for c in changes if c["pct_change"] > 0]
    declines = [c for c in changes if c["pct_change"] < 0]
    unchanged = [c for c in changes if c["pct_change"] == 0]

    top_gainers = sorted(advances, key=lambda x: x["pct_change"], reverse=True)[:5]
    top_losers = sorted(declines, key=lambda x: x["pct_change"])[:5]

    gainers_str = " | ".join(f"{g['ticker']} {g['pct_change']:+.1f}%" for g in top_gainers)
    losers_str = " | ".join(f"{l['ticker']} {l['pct_change']:+.1f}%" for l in top_losers)

    summary_lines = [
        f"{label} breadth: {len(advances)} tăng / {len(unchanged)} đứng / {len(declines)} giảm",
        f"Top tăng: {gainers_str}" if gainers_str else "Top tăng: (không có)",
        f"Top giảm: {losers_str}" if losers_str else "Top giảm: (không có)",
    ]
    summary = "\n".join(summary_lines)
    return ToolResult(
        status="ok",
        data={
            "advances": len(advances),
            "declines": len(declines),
            "unchanged": len(unchanged),
            "top_gainers": top_gainers,
            "top_losers": top_losers,
            "all_changes": changes,
            "summary": summary,
        },
        message=summary,
    )


def get_market_breadth(universe: str = "HOSE") -> ToolResult:
    """Độ rộng thị trường: advance/decline/unchanged + top gainers/losers.

    universe: "HOSE" (default, ~150-400 mã) | "VN30" (30 mã, faster)

    DB-first: query ohlcv_daily. Falls back to live batch VCI API if DB empty.
    """
    # Resolve ticker list
    if universe.upper() == "VN30":
        tickers = _VN30_CONSTITUENTS
        label = "VN30"
    else:
        from data.hose_universe import load_hose_tickers
        tickers = load_hose_tickers()
        label = "HOSE"

    # DB-first
    from tools.ohlcv_db import query_universe_latest
    db_df = query_universe_latest(tickers)
    if db_df is not None and not db_df.empty:
        changes = [
            {
                "ticker": row["ticker"],
                "pct_change": float(row["pct_change"]),
                "close": float(row["close"]),
                "volume": 0,
            }
            for _, row in db_df.iterrows()
        ]
        if changes:
            return _build_breadth_result(changes, label=label)

    # Fallback: live batch VCI API (chunk into groups of 30 to respect API limits)
    from tools.providers import VciDirectProvider
    provider = VciDirectProvider()
    changes = []
    chunk_size = 30
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i:i + chunk_size]
        try:
            batch = provider.fetch_batch_latest(chunk, count_back=3)
        except Exception as e:
            continue  # skip failed chunks, don't abort entire breadth
        for sym, df in batch.items():
            if df.empty or len(df) < 2:
                continue
            prev = float(df["close"].iloc[-2])
            curr = float(df["close"].iloc[-1])
            vol = float(df["volume"].iloc[-1])
            pct = (curr - prev) / prev * 100 if prev else 0.0
            changes.append({"ticker": sym, "pct_change": round(pct, 2), "close": curr, "volume": vol})

    if not changes:
        return ToolResult(
            status="no_data",
            data=None,
            message=f"Không đủ dữ liệu để tính advance/decline {label} (cần ít nhất 2 phiên).",
        )
    return _build_breadth_result(changes, label=label)


def get_top_movers(by: str = "value", limit: int = 5) -> ToolResult:
    """Top mã theo thanh khoản hoặc % thay đổi trong phiên gần nhất.

    by: "value" (top theo giá trị giao dịch ≈ close×volume) |
        "pct_gain" (top tăng giá) | "pct_loss" (top giảm giá)
    limit: số mã trả về (mặc định 5)
    """
    from data.hose_universe import load_hose_tickers
    tickers = load_hose_tickers()

    by = by.lower().strip()
    if by not in ("value", "pct_gain", "pct_loss"):
        return ToolResult(
            status="invalid_input",
            data=None,
            message="by phải là 'value', 'pct_gain', hoặc 'pct_loss'.",
        )

    try:
        if by == "value":
            from tools.ohlcv_db import query_top_by_value
            df = query_top_by_value(tickers, limit=limit)
            if df is None or df.empty:
                return ToolResult(status="no_data", data=None,
                                  message="Không có dữ liệu top thanh khoản từ DB.")
            items = [
                {
                    "ticker": row["ticker"],
                    "close": float(row["close"]),
                    "volume": int(row["volume"]),
                    "traded_value": float(row["traded_value"]),
                    "pct_change": float(row["pct_change"]),
                }
                for _, row in df.iterrows()
            ]
            lines = [f"• {r['ticker']}: {r['close']:,.0f} ({r['pct_change']:+.1f}%)"
                     f" — value ~{r['traded_value']/1e9:.0f} tỷ"
                     for r in items]
            message = "Top thanh khoản:\n" + "\n".join(lines)
            return ToolResult(status="ok", data=items, message=message)

        else:
            from tools.ohlcv_db import query_universe_latest
            db_df = query_universe_latest(tickers)
            if db_df is None or db_df.empty:
                return ToolResult(status="no_data", data=None,
                                  message="Không có dữ liệu từ DB.")

            if by == "pct_gain":
                top = db_df.nlargest(limit, "pct_change")
                label = "Top tăng"
            else:
                top = db_df.nsmallest(limit, "pct_change")
                label = "Top giảm"

            items = [
                {
                    "ticker": row["ticker"],
                    "close": float(row["close"]),
                    "pct_change": float(row["pct_change"]),
                }
                for _, row in top.iterrows()
            ]
            lines = [f"• {r['ticker']}: {r['close']:,.0f} ({r['pct_change']:+.1f}%)"
                     for r in items]
            message = f"{label}:\n" + "\n".join(lines)
            return ToolResult(status="ok", data=items, message=message)

    except Exception as e:
        return ToolResult(status="upstream_error", data=None,
                          message=f"Lỗi get_top_movers: {e}")


# ── Tool 7: Khối ngoại mua/bán ròng ─────────────────────────────────────────

def get_foreign_flows(days: int = 1) -> ToolResult:
    """Khối ngoại mua/bán ròng toàn thị trường + top mua/top bán.

    DB-first: query foreign_flows (Postgres).
    Fallback: live VCI price board nếu DB rỗng.
    days: số phiên giao dịch gần nhất (mặc định 1 = phiên hôm qua/gần nhất).
    """
    if days < 1 or days > 30:
        return ToolResult(
            status="invalid_input",
            data=None,
            message="days phải từ 1–30. Thử days=1 (phiên gần nhất).",
        )

    from tools.foreign_flow_db import (
        query_latest_foreign_date,
        query_market_foreign_net,
        query_top_foreign,
    )

    target_date = query_latest_foreign_date()
    market = query_market_foreign_net(target_date) if target_date else None

    # Fallback: live VCI if DB empty
    if market is None:
        return _get_foreign_flows_live()

    top_buyers = query_top_foreign(target_date, n=5, direction="buy") or []
    top_sellers = query_top_foreign(target_date, n=5, direction="sell") or []
    return _build_foreign_result(str(target_date), market["net_value"],
                                 market["total_buy"], market["total_sell"],
                                 top_buyers, top_sellers)


def _get_foreign_flows_live() -> ToolResult:
    """Live fallback: fetch current-session foreign data from VCI price board."""
    import sys
    from datetime import date as date_type
    from data.hose_universe import load_hose_tickers
    from tools.providers import VciDirectProvider

    try:
        tickers = load_hose_tickers()
        provider = VciDirectProvider()
        all_rows: list[dict] = []
        for i in range(0, len(tickers), 50):
            try:
                all_rows.extend(provider.fetch_foreign_batch(tickers[i:i + 50]))
            except Exception as e:
                sys.stderr.write(f"[get_foreign_flows live] chunk {i} failed: {e}\n")
    except Exception as e:
        return ToolResult(
            status="upstream_error",
            data=None,
            message=f"Không lấy được dữ liệu khối ngoại (DB rỗng, VCI fallback lỗi): {e}",
        )

    if not all_rows:
        return ToolResult(
            status="no_data",
            data=None,
            message=(
                "Không có dữ liệu khối ngoại. DB rỗng và VCI không trả về dữ liệu. "
                "Chạy: python ingest/fetch_foreign_flows.py"
            ),
        )

    total_buy = sum(r["buy_value"] for r in all_rows)
    total_sell = sum(r["sell_value"] for r in all_rows)
    net = total_buy - total_sell
    top_buyers = sorted(all_rows, key=lambda x: x["buy_value"], reverse=True)[:5]
    top_sellers = sorted(all_rows, key=lambda x: x["sell_value"], reverse=True)[:5]
    return _build_foreign_result(str(date_type.today()), net, total_buy, total_sell,
                                 top_buyers, top_sellers, source="live")


def _build_foreign_result(
    date_str: str,
    net_value: float,
    total_buy: float,
    total_sell: float,
    top_buyers: list[dict],
    top_sellers: list[dict],
    source: str = "db",
) -> ToolResult:
    net_bn = net_value / 1e9
    direction = "Mua ròng" if net_bn >= 0 else "Bán ròng"
    top_buy_str = " / ".join(f"{r['ticker']} {r['buy_value']/1e9:.0f}tỷ" for r in top_buyers[:3])
    top_sell_str = " / ".join(f"{r['ticker']} {r['sell_value']/1e9:.0f}tỷ" for r in top_sellers[:3])
    source_tag = " (live)" if source == "live" else ""
    summary = "\n".join([
        f"Khối ngoại {date_str}{source_tag}: {direction} {abs(net_bn):.0f} tỷ đồng",
        f"Mua nhiều nhất: {top_buy_str}" if top_buy_str else "Mua nhiều nhất: (không có dữ liệu)",
        f"Bán nhiều nhất: {top_sell_str}" if top_sell_str else "Bán nhiều nhất: (không có dữ liệu)",
    ])
    return ToolResult(
        status="ok",
        data={
            "date": date_str,
            "market_net_value": net_value,
            "market_net_value_bn": round(net_bn, 1),
            "total_buy": total_buy,
            "total_sell": total_sell,
            "top_buyers": top_buyers,
            "top_sellers": top_sellers,
            "source": source,
            "summary": summary,
        },
        message=summary,
    )


# ── Tool 8: Hiệu suất nhóm ngành ─────────────────────────────────────────────

def get_sector_performance(period: str = "day") -> ToolResult:
    """Hiệu suất theo nhóm ngành: % thay đổi weighted theo giá trị giao dịch.

    Dữ liệu: JOIN ohlcv_daily × securities.sector.
    Fallback nếu bảng securities rỗng: dùng hose_universe seed (~140 mã).
    period: "day" (mặc định) — hiện chỉ hỗ trợ phiên gần nhất.
    """
    if period not in ("day",):
        return ToolResult(
            status="invalid_input",
            data=None,
            message="period phải là 'day'. Hỗ trợ thêm 'week'/'month' trong phiên bản sau.",
        )

    sectors = _query_sector_performance_db()
    if sectors is None:
        sectors = _query_sector_performance_fallback()

    if not sectors:
        return ToolResult(
            status="no_data",
            data=None,
            message=(
                "Chưa đủ dữ liệu để tính hiệu suất nhóm ngành. "
                "Cần ohlcv_daily có ít nhất 2 phiên và securities/hose_universe có sector."
            ),
        )

    lines = [
        f"• {s['sector']}: {s['pct_change']:+.2f}% ({s['ticker_count']} mã, "
        f"~{s['total_value_bn']:.0f} tỷ)"
        for s in sectors
    ]
    summary = "Hiệu suất nhóm ngành (phiên gần nhất):\n" + "\n".join(lines)
    return ToolResult(status="ok", data=sectors, message=summary)


def _query_sector_performance_db() -> list[dict] | None:
    """SQL path: JOIN ohlcv_daily × securities. Returns None on error or empty securities."""
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                # Check securities is populated
                cur.execute("SELECT COUNT(*) FROM securities WHERE sector IS NOT NULL")
                if cur.fetchone()[0] == 0:
                    return None  # trigger fallback

                cur.execute(
                    """
                    WITH dates AS (
                        SELECT DISTINCT date FROM ohlcv_daily ORDER BY date DESC LIMIT 2
                    ),
                    latest_date AS (SELECT MAX(date) AS d FROM dates),
                    prev_date   AS (SELECT MIN(date) AS d FROM dates),
                    changes AS (
                        SELECT
                            curr.ticker,
                            s.sector,
                            CASE WHEN prev.close > 0
                                 THEN (curr.close - prev.close) / prev.close * 100
                                 ELSE 0 END                             AS pct_change,
                            curr.close * curr.volume                   AS trade_value
                        FROM ohlcv_daily curr
                        JOIN ohlcv_daily prev
                            ON curr.ticker = prev.ticker
                           AND prev.date = (SELECT d FROM prev_date)
                        JOIN securities s ON curr.ticker = s.ticker
                        WHERE curr.date = (SELECT d FROM latest_date)
                          AND s.sector IS NOT NULL
                    )
                    SELECT
                        sector,
                        SUM(trade_value * pct_change) / NULLIF(SUM(trade_value), 0) AS weighted_pct,
                        COUNT(*)                                                      AS ticker_count,
                        SUM(trade_value)                                              AS total_value
                    FROM changes
                    GROUP BY sector
                    ORDER BY weighted_pct DESC
                    """
                )
                rows = cur.fetchall()

        if not rows:
            return None

        return [
            {
                "sector": row[0],
                "pct_change": round(float(row[1]), 2),
                "ticker_count": int(row[2]),
                "total_value_bn": round(float(row[3]) / 1e9, 1),
            }
            for row in rows
        ]
    except Exception as e:
        import sys
        sys.stderr.write(f"[get_sector_performance] DB path failed: {e}\n")
        return None


def _query_sector_performance_fallback() -> list[dict] | None:
    """In-memory fallback: JOIN ohlcv_db result with hose_universe seed."""
    try:
        from data.hose_universe import load_hose_universe
        from tools.ohlcv_db import query_top_by_value, query_universe_latest

        universe = load_hose_universe()  # [{ticker, sector, index_member}]
        if not universe:
            return None

        ticker_to_sector = {u["ticker"]: u["sector"] for u in universe}
        tickers = list(ticker_to_sector.keys())

        db_df = query_universe_latest(tickers)
        if db_df is None or db_df.empty:
            return None

        # Get volume/value via top_by_value (returns close*volume)
        val_df = query_top_by_value(tickers, limit=len(tickers))

        import pandas as pd
        db_df["sector"] = db_df["ticker"].map(ticker_to_sector)
        db_df = db_df.dropna(subset=["sector"])

        if val_df is not None and not val_df.empty:
            val_map = val_df.set_index("ticker")["traded_value"].to_dict()
            db_df["trade_value"] = db_df["ticker"].map(val_map).fillna(0.0)
        else:
            db_df["trade_value"] = db_df["close"] * 1000  # crude proxy if no volume

        results = []
        for sector, grp in db_df.groupby("sector"):
            total_val = float(grp["trade_value"].sum())
            if total_val > 0:
                weighted_pct = float(
                    (grp["pct_change"] * grp["trade_value"]).sum() / total_val
                )
            else:
                weighted_pct = float(grp["pct_change"].mean())
            results.append({
                "sector": sector,
                "pct_change": round(weighted_pct, 2),
                "ticker_count": len(grp),
                "total_value_bn": round(total_val / 1e9, 1),
            })

        results.sort(key=lambda x: x["pct_change"], reverse=True)
        return results if results else None
    except Exception as e:
        import sys
        sys.stderr.write(f"[get_sector_performance] fallback failed: {e}\n")
        return None


# ── Tool 9: Phân tích sentiment thị trường ───────────────────────────────────

def analyze_market_sentiment(ticker: str, days: int = 7) -> ToolResult:
    """Phân tích cảm xúc thị trường về ticker từ tin tức gần nhất (few-shot LLM).

    ticker có thể là mã cổ phiếu (HPG) hoặc chỉ số thị trường (VNINDEX, HOSE, VN30).
    """
    if not ticker or not ticker.strip():
        return ToolResult(
            status="invalid_input",
            data=None,
            message="ticker không được rỗng. Thử với mã như 'HPG', 'VNM' hoặc chỉ số 'VNINDEX'.",
        )
    if days < 1 or days > 365:
        return ToolResult(
            status="invalid_input",
            data=None,
            message=f"days={days} không hợp lệ. Phải từ 1 đến 365. Thử days=7.",
        )

    t = ticker.strip().upper()
    market_query = _is_market_index(t)

    if not market_query:
        p = _detect_provider(t)
        try:
            p.get_price(t)
        except Exception:
            return ToolResult(
                status="no_data",
                data=None,
                message=(
                    f"Mã '{t}' không tồn tại hoặc không có dữ liệu giá. "
                    "Kiểm tra lại mã CK. Không phân tích sentiment cho mã không hợp lệ."
                ),
            )

    try:
        from rag.news_index import search_news_by_text
    except ImportError:
        return ToolResult(
            status="upstream_error",
            data=None,
            message="Không thể import rag.news_index. Kiểm tra Qdrant đang chạy và news_chunks đã được index.",
        )

    search_ticker = None if market_query else t
    try:
        raw = search_news_by_text(t, days=days, limit=5, ticker=search_ticker)
    except Exception as e:
        return _map_upstream_error(t, e)

    unique = _dedup_news(raw, limit=5)

    # Auto-fetch on miss for stock tickers
    if not unique and not market_query:
        _auto_fetch_ticker_news(t, days)
        try:
            raw2 = search_news_by_text(t, days=days, limit=5, ticker=t)
            unique = _dedup_news(raw2, limit=5)
        except Exception:
            pass

    if not unique:
        label = "thị trường" if market_query else t
        return ToolResult(
            status="no_data",
            data=None,
            message=(
                f"Không đủ tin tức để phân tích sentiment cho {label}. "
                "Tăng khoảng thời gian (days) hoặc dùng search_financial_news để kiểm tra."
            ),
        )

    headlines = [item.get("title", "").strip() for item in unique if item.get("title")]
    if not headlines:
        return ToolResult(
            status="no_data",
            data=None,
            message=(
                f"Tin tức về {t} không có tiêu đề. "
                "Không thể phân tích sentiment. Thử lại với khoảng thời gian khác."
            ),
        )

    shots_path = Path(__file__).parent.parent / "data" / "sentiment_shots_vi.json"
    shots: list[dict] = []
    if shots_path.exists():
        with shots_path.open(encoding="utf-8") as f:
            shots = json.load(f)

    label_vi = {"positive": "tích cực", "negative": "tiêu cực", "neutral": "trung tính"}
    by_label: dict[str, list[str]] = {"positive": [], "negative": [], "neutral": []}
    for s in shots:
        lbl = s.get("label", "neutral")
        if lbl in by_label:
            by_label[lbl].append(s.get("text", ""))

    few_shot_lines = [
        f'"{text}" → {label_vi[lbl]}'
        for lbl, count in [("positive", 2), ("negative", 2), ("neutral", 1)]
        for text in by_label[lbl][:count]
    ]

    news_block = "\n".join(f"{i + 1}. {h}" for i, h in enumerate(headlines))
    few_shot_block = "\n".join(few_shot_lines)
    n = len(headlines)

    prompt = (
        f"Tin tức về {t} trong {days} ngày gần nhất:\n{news_block}\n\n"
        f"Ví dụ phân loại:\n{few_shot_block}\n\n"
        f"Phân tích xu hướng tổng thể ({n} tin trên) là tích cực, tiêu cực hay trung tính. "
        f"Trả lời đúng format: 'Xu hướng [NHÃN] — [lý do 1–2 câu ngắn gọn]'"
    )

    try:
        from llm.factory import create_client
        from llm.types import Message

        client = create_client()
        resp = client.generate(
            [Message(role="user", content=prompt)],
            max_tokens=150,
            system="Bạn là chuyên gia phân tích tài chính. Phân tích sentiment tin tức chứng khoán Việt Nam.",
        )
        result_str = resp.text.strip()
        return ToolResult(status="ok", data=result_str, message=result_str)
    except Exception as e:
        return ToolResult(
            status="upstream_error",
            data=None,
            message=(
                f"Lỗi khi gọi LLM để phân tích sentiment cho '{t}': {e}. "
                "Thử lại sau. Nếu lỗi tiếp, kiểm tra kết nối LLM provider."
            ),
        )
