"""
agents/intents/breakout.py — Intent: quét tín hiệu breakout.

Scans ohlcv_daily for breakout signals (SHORT/MID/LONG/PRE) using
the algorithm from tools/breakout.py. Enriches top signals with news.
"""
from __future__ import annotations

import pandas as pd
from langfuse import observe

from llm.factory import create_client
from llm.types import Message
from tools.breakout import (
    BreakoutSignal,
    get_active_tickers,
    scan_all,
    scan_ticker,
)


_SYSTEM = (
    "Bạn là chuyên gia phân tích kỹ thuật chứng khoán Việt Nam, chuyên về phương pháp CANSLIM và breakout. "
    "KHÔNG tự bịa số liệu — dùng đúng các số đã cung cấp. "
    "Viết HOÀN TOÀN bằng tiếng Việt. "
    "BẮT BUỘC bọc toàn bộ output trong <report>...</report>."
)


def _get_market_df() -> pd.DataFrame:
    try:
        from tools.ohlcv_db import query_ohlcv
        df = query_ohlcv("VNINDEX", days=150)
        if df is not None and len(df) >= 60:
            return df
    except Exception:
        pass
    try:
        from tools.price import get_historical_ohlcv
        r = get_historical_ohlcv("VNINDEX", days=150)
        if r.status == "ok" and r.data is not None:
            return r.data
    except Exception:
        pass
    return pd.DataFrame()


def _format_signal(s: BreakoutSignal) -> str:
    type_label = {
        "SHORT": "📈 SHORT (nền 20 phiên)",
        "MID": "📊 MID (nền 40 phiên)",
        "LONG": "🔭 LONG (nền 100 phiên)",
        "MID_PRE": "⏳ MID_PRE (sắp breakout)",
        "LONG_PRE": "⏳ LONG_PRE (sắp breakout)",
    }.get(s.signal_type, s.signal_type)

    pct_to_pivot = f" | Cách pivot: {s.pct_to_pivot:.1f}%" if s.pct_to_pivot > 0 else ""
    return (
        f"**{s.ticker}** — {type_label}\n"
        f"  Giá: {s.price:,.0f} | Pivot: {s.pivot:,.0f}{pct_to_pivot}\n"
        f"  SL: {s.stop_loss:,.0f} | TP1: {s.target1:,.0f} | TP2: {s.target2:,.0f}\n"
        f"  RS: {s.rs:.2f} | Vol×: {s.vol_ratio:.1f}x | Dist ngày: {s.distribution_days} "
        f"| MACD: {'✓' if s.macd_bullish else '✗'} | NNước: {s.foreign_net_ratio:+.2%}\n"
        f"  Biên nền: {s.tight_range_pct:.1f}%"
    )


def _get_news(ticker: str) -> str:
    try:
        from tools.price import search_financial_news
        r = search_financial_news(ticker, days=3)
        if r.status == "ok" and r.message:
            return r.message[:600]
    except Exception:
        pass
    return ""


@observe(name="intent.breakout_scan")
def run(ticker: str, query: str) -> str:
    market_df = _get_market_df()

    # Single ticker or full scan
    if ticker and ticker.upper() not in ("VNINDEX", "VN30", "VN100", "HOSE", "HNX"):
        t = ticker.upper()
        signals = scan_ticker(t, market_df)
        mode_label = f"mã **{t}**"
        tickers_scanned = 1
    else:
        active = get_active_tickers()
        if not active:
            return "Không lấy được danh sách mã từ bảng `securities`. Kiểm tra kết nối DB."
        signals = scan_all(market_df, active)
        mode_label = f"**{len(active)} mã** trên sàn"
        tickers_scanned = len(active)

    if not signals:
        return (
            f"# Quét Breakout — {mode_label}\n\n"
            "**Không phát hiện tín hiệu breakout** trong phiên gần nhất.\n\n"
            "_Có thể thị trường chưa đủ điều kiện (uptrend yếu, volume thấp) "
            "hoặc chưa có mã nào hội đủ điều kiện nền giá tích lũy._"
        )

    # Stats
    by_type: dict[str, list[BreakoutSignal]] = {}
    for s in signals:
        by_type.setdefault(s.signal_type, []).append(s)

    stats_lines = []
    for t in ("SHORT", "MID", "LONG", "MID_PRE", "LONG_PRE"):
        if t in by_type:
            stats_lines.append(f"- {t}: {len(by_type[t])} mã")

    # Top signals for display (cap at 15)
    top_signals = signals[:15]
    signals_block = "\n\n".join(_format_signal(s) for s in top_signals)

    # News for top 5 unique tickers with confirmed signals
    confirmed = [s for s in signals if s.signal_type in ("SHORT", "MID", "LONG")]
    news_tickers = list(dict.fromkeys(s.ticker for s in confirmed))[:5]
    news_parts = []
    for t in news_tickers:
        news = _get_news(t)
        if news:
            news_parts.append(f"**{t}:** {news}")
    news_block = "\n\n".join(news_parts) if news_parts else "Không có tin tức mới."

    # Build LLM prompt
    user_prompt = f"""Kết quả quét breakout ({mode_label}, {tickers_scanned} mã):

Thống kê tín hiệu:
{chr(10).join(stats_lines)}

Top tín hiệu:
{signals_block}

Tin tức gần đây ({", ".join(news_tickers) or "không có"}):
{news_block}

Câu hỏi: {query}

Viết báo cáo phân tích breakout. Bọc toàn bộ trong <report>...</report>.

<report>
## Tổng quan thị trường
[1-2 câu về sức mạnh chung: có bao nhiêu mã breakout, xu hướng thị trường hỗ trợ không]

## Các tín hiệu nổi bật
[Phân tích 3-5 mã có tín hiệu mạnh nhất — nhận xét về nền giá, khối lượng, dòng tiền nước ngoài, tin tức]

## Rủi ro cần lưu ý
[Điều kiện thị trường có thể khiến breakout thất bại, các mã có RS thấp hoặc nhiều ngày phân phối]

## Kế hoạch hành động
[Top 3 mã ưu tiên theo dõi với entry/SL/TP cụ thể từ dữ liệu trên]
</report>"""

    client = create_client()
    resp = client.generate(
        [Message(role="user", content=user_prompt)],
        max_tokens=1800,
        temperature=0,
        system=_SYSTEM,
    )

    from agents.intents import extract_report
    raw = extract_report(resp.text.strip())

    if not raw:
        # Fallback: return structured data without LLM narrative
        return (
            f"# Quét Breakout — {mode_label}\n\n"
            f"**Tổng:** {len(signals)} tín hiệu từ {tickers_scanned} mã\n"
            f"{chr(10).join(stats_lines)}\n\n"
            f"## Top tín hiệu\n\n{signals_block}"
        )

    # Prepend structured header to LLM report
    header = (
        f"# Quét Breakout — {mode_label}\n\n"
        f"**Tổng:** {len(signals)} tín hiệu | "
        f"{' | '.join(f'{t}: {len(v)}' for t, v in by_type.items())}\n\n"
        f"## Dữ liệu tín hiệu\n\n{signals_block}\n\n---\n\n"
    )
    return header + raw
