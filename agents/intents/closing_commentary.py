"""
agents/intents/closing_commentary.py — end-of-session market commentary ("nhận định cuối phiên").

gather_data() reads Postgres only (index, breadth/movers, liquidity, foreign flow, sector
performance, index reference levels) and returns labelled text. The shared synthesize_final
step writes the commentary, guided by CLOSING_RULE below.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

_INDEXES = ("VNINDEX", "VN30", "HNX")
_LOOKBACK = 20

# Appended to the synthesis system prompt when intent == closing_commentary.
CLOSING_RULE = (
    "Đây là NHẬN ĐỊNH CUỐI PHIÊN. Viết đúng 4 mục Markdown, theo thứ tự: "
    "(1) Diễn biến chỉ số & thanh khoản (VN-Index, VN30, HNX; GTGD so với trung bình 20 phiên); "
    "(2) Độ rộng & nhóm ngành dẫn dắt/kéo lùi, các mã ảnh hưởng nổi bật; "
    "(3) Dòng tiền khối ngoại (tách giao dịch thỏa thuận nếu có); "
    "(4) Kịch bản phiên tới: nêu 2 kịch bản có điều kiện theo mốc (ví dụ 'nếu giữ được đáy 20 phiên X thì…, nếu thủng thì…; vượt MA20 Y thì…'), chỉ dùng các mốc tham chiếu trong ngữ cảnh (đỉnh/đáy 20 phiên, MA20), không đưa khuyến nghị mua/bán. "
    "Không bịa nguyên nhân/tin tức không có trong ngữ cảnh. Ngắn gọn, mỗi mục 2-4 câu. "
)


def _index_levels(code: str) -> str:
    """Reference levels from the last 20 sessions: high/low/MA20 and liquidity vs average."""
    try:
        from tools.index_db import query_index
        df = query_index(code, _LOOKBACK)
    except Exception:
        df = None
    if df is None or len(df) < 5:
        return f"[MỐC THAM CHIẾU {code}]\nKhông đủ dữ liệu lịch sử chỉ số."
    last = df.iloc[-1]
    win = df.tail(_LOOKBACK)
    lines = [
        f"[MỐC THAM CHIẾU {code} — {len(win)} phiên gần nhất đến {str(last['time'])[:10]}]",
        f"Đóng cửa {last['close']:,.2f}; đỉnh {win['high'].max():,.2f}; đáy {win['low'].min():,.2f}; "
        f"MA{len(win)} {win['close'].mean():,.2f}",
    ]
    prior = win["matched_value"].iloc[:-1].dropna()
    if last["matched_value"] is not None and len(prior) >= 3 and prior.mean() > 0:
        ratio = float(last["matched_value"]) / float(prior.mean())
        lines.append(
            f"GTGD phiên cuối {float(last['matched_value']) / 1e9:,.0f} tỷ = {ratio:.0%} trung bình {len(prior)} phiên trước "
            f"({float(prior.mean()) / 1e9:,.0f} tỷ)"
        )
    return "\n".join(lines)


def gather_data(ticker: str | None, query: str, time_context: dict | None = None) -> str:
    """Closing-session snapshot - no LLM call. time_context accepted for a uniform signature
    (all blocks are latest-session snapshots)."""
    from agents.intents import market_data as md
    from agents.intents.macro_sector import _sector_performance_text

    jobs = {
        **{f"idx_{c}": (lambda c=c: md._index(c)) for c in _INDEXES},
        "levels": lambda: _index_levels("VNINDEX"),
        "movers": lambda: md._movers("top 5 tăng giảm"),
        "liquidity": lambda: md._liquidity("top 5 thanh khoản"),
        "foreign": lambda: md._foreign("top 5 khối ngoại mua bán"),
        "sector": lambda: f"[NGÀNH]\n{_sector_performance_text()}",
    }
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:  # independent DB reads
        futs = {k: pool.submit(fn) for k, fn in jobs.items()}
        res = {}
        for k, f in futs.items():
            try:
                res[k] = f.result()
            except Exception as exc:
                res[k] = f"[{k}] lỗi: {exc}"

    order = [f"idx_{c}" for c in _INDEXES] + ["levels", "movers", "liquidity", "foreign", "sector"]
    out = "\n\n".join(res[k] for k in order if res.get(k))
    if not any(res.get(f"idx_{c}") and "Không có dữ liệu" not in res[f"idx_{c}"] for c in _INDEXES):
        return "[NHẬN ĐỊNH CUỐI PHIÊN]\nKhông có dữ liệu chỉ số phiên gần nhất."  # empty → replan/alert, not a fake commentary
    return out
