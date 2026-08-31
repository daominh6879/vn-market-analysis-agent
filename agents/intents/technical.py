"""
agents/intents/technical.py — Nhóm 2: Phân tích Kỹ thuật.

Market-brief pattern:
  - Python computes ALL data (indicators, S/R, OBV, Fibonacci, candle).
  - LLM writes ONLY 4 prose/table slots (TREND, MOMENTUM, FLOW, TRADE).
  - Python assembles final Markdown from fixed structure + LLM slots.
"""

from __future__ import annotations

import re

import pandas as pd

from langfuse import observe

from llm.factory import create_client
from llm.types import Message
from tools.price import get_historical_ohlcv, calculate_indicators


_SYSTEM = (
    "Bạn là chuyên gia phân tích kỹ thuật chứng khoán Việt Nam. "
    "KHÔNG tự bịa số liệu — dùng đúng các số đã cung cấp. "
    "TUYỆT ĐỐI không viết quá trình suy nghĩ, không ghi chú nội bộ, "
    "không giải thích bước phân tích. "
    "Viết HOÀN TOÀN bằng tiếng Việt — không dùng tiếng Anh trừ tên chỉ báo kỹ thuật (RSI, MACD, EMA, SMA, ADX, OBV). "
    "Kế hoạch Giao dịch BẮT BUỘC có Entry / SL / TP1 / TP2 / R:R — "
    "R:R phải ≥ 1:2, nếu không đạt thì ghi 'Setup chưa đủ hấp dẫn'. "
    "BẮT BUỘC bọc toàn bộ output trong thẻ <report>...</report>. "
    "Output chỉ gồm: <report>[4 phần đánh dấu]</report>, không có text nào khác."
)


def _get_ohlcv(ticker: str) -> tuple[str, "pd.DataFrame | None"]:
    """DB-first OHLCV fetch; falls back to live API."""
    try:
        from tools.ohlcv_db import query_ohlcv
        df = query_ohlcv(ticker, days=365)
        if df is not None and len(df) >= 20:
            return "ok", df
    except Exception:
        pass
    r = get_historical_ohlcv(ticker, days=365)
    return r.status, r.data if r.status == "ok" else None


def _compute_obv(df: pd.DataFrame) -> str:
    if df.empty or "close" not in df.columns or "volume" not in df.columns or len(df) < 10:
        return "N/A"
    direction = df["close"].diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    obv = (direction * df["volume"]).cumsum()
    if len(obv) >= 40:
        recent_avg = obv.tail(20).mean()
        prev_avg   = obv.iloc[-40:-20].mean()
        if recent_avg > prev_avg * 1.05:
            trend = "TĂNG → dòng tiền tích lũy"
        elif recent_avg < prev_avg * 0.95:
            trend = "GIẢM → dòng tiền phân phối"
        else:
            trend = "NGANG → trung tính"
        return f"OBV {trend} (hiện tại: {obv.iloc[-1]:,.0f})"
    return f"OBV: {obv.iloc[-1]:,.0f} (chưa đủ 40 phiên để xác định xu hướng)"


def _fibonacci_levels(df: pd.DataFrame) -> str:
    if df.empty or len(df) < 20:
        return "N/A"
    recent = df.tail(60)
    high = recent["high"].quantile(0.97) if "high" in df.columns else recent["close"].quantile(0.97)
    low  = recent["low"].quantile(0.03)  if "low"  in df.columns else recent["close"].quantile(0.03)
    diff = high - low
    if diff == 0:
        return "N/A"
    lvls = {
        "0.0% (đỉnh)": high,
        "23.6%":        high - 0.236 * diff,
        "38.2%":        high - 0.382 * diff,
        "50.0%":        high - 0.500 * diff,
        "61.8% (vàng)": high - 0.618 * diff,
        "100% (đáy)":   low,
    }
    return "Fibonacci 60 phiên:\n" + "\n".join(f"  {k}: {v:,.0f}" for k, v in lvls.items())


def _extract_slot(text: str, label: str, next_label: str | None) -> str:
    """Extract text between last 'LABEL:' and the next 'NEXT_LABEL:' (or end).

    Uses rfind so reasoning preamble (which also mentions labels) is skipped.
    """
    marker = f"{label}:"
    start = text.rfind(marker)
    if start == -1:
        return ""
    start += len(marker)
    if next_label:
        end = text.find(f"{next_label}:", start)
        end = end if end != -1 else len(text)
    else:
        end = len(text)
    return text[start:end].strip()


def _strip_prose(text: str) -> str:
    """Strip leading reasoning lines from a prose slot."""
    from llm.utils import strip_thinking
    return strip_thinking(text)


def _assemble_report(
    ticker: str,
    trend: str,
    momentum: str,
    flow: str,
    trade: str,
    sr_text: str,
    fib_text: str,
    candle_text: str,
) -> str:
    return (
        f"# Phân tích Kỹ thuật {ticker}\n\n"
        f"## Cấu trúc Xu hướng (Ngắn / Trung / Dài hạn)\n{trend}\n\n"
        f"## Động lượng (RSI & MACD)\n{momentum}\n\n"
        f"## Dòng tiền & Khối lượng\n{flow}\n\n"
        f"## Vùng giá quan trọng (Hỗ trợ / Kháng cự / Fibonacci)\n"
        f"{sr_text}\n\n{fib_text}\n\n"
        f"## Tín hiệu nến\n{candle_text}\n\n"
        f"## Kế hoạch Giao dịch\n{trade}\n\n"
        f"[Nguồn: VCI REST API]"
    )


@observe(name="intent.technical_analysis")
def run(ticker: str, query: str) -> str:
    status, df = _get_ohlcv(ticker)

    if status != "ok" or df is None:
        return (
            f"Không tìm thấy dữ liệu giá cho mã **{ticker}**. "
            "Vui lòng kiểm tra lại mã cổ phiếu (ví dụ: MBB thay vì MB, VCB thay vì Vietcombank)."
        )

    # ── Python computes all data ──────────────────────────────────────────────
    ind_r = calculate_indicators(df)
    tech_signals = ind_r.message

    sr_text = "Không xác định."
    try:
        from tools.levels import find_support_resistance
        sr_r = find_support_resistance(df)
        sr_text = sr_r.message
    except Exception as exc:
        sr_text = f"Không tính được S/R: {exc}"

    obv_text = _compute_obv(df)
    fib_text = _fibonacci_levels(df)

    candle_text = "Không xác định."
    try:
        from tools.ohlcv_db import detect_candle_pattern
        cp_r = detect_candle_pattern(df)
        candle_text = cp_r.message
    except Exception:
        try:
            from tools.price import detect_candle_pattern as _cp
            cp_r = _cp(df)
            candle_text = cp_r.message
        except Exception as exc:
            candle_text = f"Không nhận diện được nến: {exc}"

    # ── LLM writes only 4 prose/table slots ──────────────────────────────────
    user_prompt = f"""Dữ liệu kỹ thuật {ticker} (365 phiên):

Chỉ báo (RSI, MACD, EMA20/SMA50/SMA200):
{tech_signals}

Hỗ trợ / Kháng cự:
{sr_text}

Dòng tiền: {obv_text}
{fib_text}

Mô hình nến: {candle_text}

Câu hỏi: {query}

Viết đúng 4 phần sau. Bọc TOÀN BỘ output trong <report>...</report>.

<report>
TREND: [2-3 câu: ngắn hạn từ EMA20, trung hạn từ SMA50, dài hạn từ SMA200 — nếu thiếu SMA50/SMA200 thì ghi "chưa đủ dữ liệu trung/dài hạn"]
MOMENTUM: [2-3 câu về RSI và MACD — nếu MACD không đủ dữ liệu thì ghi rõ; dùng ADX để đánh giá độ mạnh xu hướng]
FLOW: [1-2 câu về OBV và khối lượng so với MA20 — nếu OBV chưa đủ phiên thì ghi rõ]
TRADE:
Quy tắc bắt buộc:
1. SL đặt dưới hỗ trợ gần nhất hoặc dưới entry 3-5% (chọn mức nào GẦN HƠN).
2. Nếu khoảng cách Entry→SL > 7% hoặc không có kháng cự rõ ràng phía trên: ghi "Setup chưa đủ hấp dẫn" vào R:R, để TP1/TP2 là N/A.
3. Chỉ ghi R:R số khi (TP1-Entry)/(Entry-SL) ≥ 1:2 thực tế.

| | Giá |
|---|---|
| **Entry** | [giá vào lệnh hợp lý] |
| **Stop Loss** | [hỗ trợ gần nhất HOẶC entry - 3~5%, chọn mức gần hơn] |
| **Take Profit 1** | [kháng cự gần nhất phía trên, hoặc N/A] |
| **Take Profit 2** | [kháng cự tiếp theo, hoặc N/A] |
| **R:R** | [(TP1-Entry)/(Entry-SL) nếu ≥ 1:2, ngược lại: "Setup chưa đủ hấp dẫn"] |
</report>"""

    client = create_client()
    resp = client.generate(
        [Message(role="user", content=user_prompt)],
        max_tokens=1200,
        temperature=0,
        system=_SYSTEM,
    )

    # strip_thinking protects <report> blocks (step 0), so resp.text always has
    # clean <report>...</report> if the model followed instructions.
    from agents.intents import extract_report
    raw = extract_report(resp.text.strip())

    # ── Extract slots ─────────────────────────────────────────────────────────
    trend    = _strip_prose(_extract_slot(raw, "TREND",    "MOMENTUM"))
    momentum = _strip_prose(_extract_slot(raw, "MOMENTUM", "FLOW"))
    flow     = _strip_prose(_extract_slot(raw, "FLOW",     "TRADE"))
    trade    = _extract_slot(raw, "TRADE", None)  # table — no prose stripping

    # LLM did not follow label format — never surface raw reasoning to user
    if not trend and not momentum:
        return f"Không thể phân tích kỹ thuật **{ticker}** — vui lòng thử lại."

    return _assemble_report(ticker, trend, momentum, flow, trade, sr_text, fib_text, candle_text)
