"""
agents/intents/price_action.py — Nhóm 1: Hành động giá & Dòng tiền.

Market-brief pattern:
  - Python computes all data (price change, volume vs MA20, foreign flow).
  - LLM writes only 3 prose slots (GIA_BIEN_DONG, DONG_TIEN, KET_LUAN).
  - Python assembles final Markdown from fixed structure + LLM slots.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from langfuse import observe

from llm.factory import create_client
from llm.types import Message
from tools.price import get_realtime_price, get_historical_ohlcv
from agents.intents import strip_preamble, strip_thinking, extract_slot


_SYSTEM = (
    "Bạn là chuyên gia phân tích hành động giá chứng khoán Việt Nam. "
    "KHÔNG tự bịa số liệu — dùng đúng các số đã cung cấp. "
    "TUYỆT ĐỐI không viết quá trình suy nghĩ, không ghi chú nội bộ, "
    "không giải thích bước phân tích. "
    "Viết HOÀN TOÀN bằng tiếng Việt. "
    "Output chỉ gồm 3 phần được đánh dấu, không có text nào khác. "
    "BẮT BUỘC bọc toàn bộ output trong thẻ <report>...</report>. Output chỉ gồm: <report>[nội dung]</report>, không có text nào khác."
)


def _get_foreign_flow_summary(ticker: str) -> str:
    """Try DB foreign flow for ticker; fallback to market-level."""
    try:
        from datetime import date
        from tools.foreign_flow_db import query_ticker_foreign_net
        today = date.today()
        row = query_ticker_foreign_net(ticker, today)
        if row:
            return (
                f"Khối ngoại: mua {row['buy_value']:,.0f} tỷ, "
                f"bán {row['sell_value']:,.0f} tỷ, "
                f"ròng {row['net_value']:+,.0f} tỷ"
            )
    except Exception:
        pass
    return "Không có dữ liệu dòng tiền khối ngoại."


def _volume_vs_ma(df: pd.DataFrame, ma_window: int = 20) -> str:
    if df.empty or "volume" not in df.columns or len(df) < ma_window:
        return "Không đủ dữ liệu khối lượng."
    recent_vol = df["volume"].iloc[-1]
    ma_vol = df["volume"].tail(ma_window).mean()
    ratio = recent_vol / ma_vol if ma_vol > 0 else 0
    flag = "⚡ ĐỘT BIẾN" if ratio > 1.5 else ("Bình thường" if ratio >= 0.7 else "Thấp")
    return (
        f"Khối lượng phiên gần nhất: {recent_vol:,.0f} cp "
        f"({ratio:.1%} so với MA{ma_window}) — {flag}"
    )


def _price_change_summary(df: pd.DataFrame) -> str:
    if df.empty or "close" not in df.columns or len(df) < 2:
        return "Không đủ dữ liệu giá."
    last = df["close"].iloc[-1]
    prev = df["close"].iloc[-2]
    change_pct = (last - prev) / prev * 100 if prev > 0 else 0
    direction = "tăng" if change_pct > 0 else ("giảm" if change_pct < 0 else "không đổi")
    alert = " ⚠️ Biến động mạnh" if abs(change_pct) > 3 else ""
    return f"Giá đóng cửa: {last:,.0f} VND ({direction} {abs(change_pct):.2f}%){alert}"


def _load_ohlcv(ticker: str) -> pd.DataFrame | None:
    """Try live API first; fall back to agent cache CSV."""
    r = get_historical_ohlcv(ticker, days=25)
    if r.status == "ok" and r.data is not None:
        return r.data
    cache = Path("outputs/agent_cache") / f"{ticker}_ohlcv.csv"
    if cache.exists():
        try:
            return pd.read_csv(cache)
        except Exception:
            pass
    return None


def _assemble_report(ticker: str, gia_bien_dong: str, dong_tien: str, ket_luan: str) -> str:
    return (
        f"# Hành động giá {ticker}\n\n"
        f"## Giá & Biến động\n{gia_bien_dong}\n\n"
        f"## Dòng tiền & Khối lượng\n{dong_tien}\n\n"
        f"## Kết luận ngắn\n{ket_luan}\n\n"
        f"[Nguồn: VCI REST API / DB]"
    )


@observe(name="intent.price_action")
def run(ticker: str, query: str) -> str:
    price_r = get_realtime_price(ticker)
    df = _load_ohlcv(ticker)

    price_line  = price_r.message
    vol_line    = "Không có dữ liệu OHLCV."
    change_line = "Không có dữ liệu OHLCV."
    if df is not None:
        vol_line    = _volume_vs_ma(df)
        change_line = _price_change_summary(df)
    flow_line = _get_foreign_flow_summary(ticker)

    user_prompt = f"""Dữ liệu thị trường {ticker}:
- {price_line}
- {change_line}
- {vol_line}
- {flow_line}

Logic:
- Giá tăng/giảm >3% VÀ khối lượng >150% MA20 → "Phiên có dòng tiền lớn (Breakout/Selloff)"
- Khối ngoại ròng dương → tín hiệu tích lũy; âm → phân phối

Câu hỏi: {query}

Bọc TOÀN BỘ output trong <report>...</report>.

<report>
GIA_BIEN_DONG: [2-3 câu về giá hiện tại, mức biến động, ý nghĩa]
DONG_TIEN: [2-3 câu về khối lượng vs MA20 và dòng tiền khối ngoại]
KET_LUAN: [1-2 câu kết luận: Breakout / Selloff / Tích lũy / Bình thường]
</report>"""

    client = create_client()
    resp = client.generate(
        [Message(role="user", content=user_prompt)],
        max_tokens=800,
        temperature=0,
        system=_SYSTEM,
    )

    from agents.intents import extract_report
    raw = extract_report(resp.text.strip())
    gia_bien_dong = strip_thinking(extract_slot(raw, "GIA_BIEN_DONG", "DONG_TIEN"))
    dong_tien     = strip_thinking(extract_slot(raw, "DONG_TIEN",     "KET_LUAN"))
    ket_luan      = strip_thinking(extract_slot(raw, "KET_LUAN",      None))

    if not gia_bien_dong and not dong_tien:
        return f"Không thể phân tích giá & dòng tiền **{ticker}** — vui lòng thử lại."

    return _assemble_report(ticker, gia_bien_dong, dong_tien, ket_luan)
