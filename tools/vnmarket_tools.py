"""
tools/vnmarket_tools.py — Live VN market tools backed by vnmarket (no DB, no API key).

  get_live_quotes(tickers)           — price board incl. ref/ceiling/floor + foreign flow
  get_intraday_ticks(ticker, limit)  — latest matched ticks
  get_vn_index_board()               — HOSE index board (VNIndex, VN30, sector indices)
  get_live_sector_performance()      — today's % change per ICB sector
  get_proprietary_trading(ticker, days) — daily tự doanh buy/sell
  get_company_profile(ticker)        — overview + profile (leaders, ownership, subsidiaries)

All return ToolResult. Never raise.
"""

from __future__ import annotations

import re

from tools.result import ToolResult
from tracing import instrument_tool


def _client():
    from core.vnmarket_client import get_client
    return get_client()


def _failure(e: Exception, what: str) -> ToolResult:
    from vnmarket import NoData, RateLimited
    if isinstance(e, NoData):
        return ToolResult(status="no_data", data=None, message=f"Không có dữ liệu {what}.")
    if isinstance(e, RateLimited):
        return ToolResult(status="rate_limited", data=None,
                          message=f"Nguồn dữ liệu đang giới hạn tốc độ khi lấy {what}; thử lại sau ít phút.")
    return ToolResult(status="upstream_error", data=None, message=f"Lỗi lấy {what}: {e}")


def _bn(v) -> str:
    """VND → tỷ đồng string."""
    return f"{float(v) / 1e9:,.1f}"


@instrument_tool("get_live_quotes")
def get_live_quotes(tickers: list[str]) -> ToolResult:
    """Bảng giá trực tiếp: giá, tham chiếu/trần/sàn, khối lượng, khối ngoại mua/bán (VND)."""
    syms = [t.strip().upper() for t in tickers if t and t.strip()]
    if not syms:
        return ToolResult(status="invalid_input", data=None, message="Cần ít nhất một mã cổ phiếu.")
    try:
        df = _client().market.quotes(syms)
    except Exception as e:
        return _failure(e, "bảng giá")
    missing = sorted(set(syms) - set(df["symbol"]))
    lines = []
    for r in df.itertuples():
        chg = (r.price / r.ref - 1) * 100 if r.ref else 0.0
        lines.append(
            f"{r.symbol}: {r.price:,.0f} VND ({chg:+.2f}% so với tham chiếu {r.ref:,.0f}), "
            f"KL {r.volume:,.0f}, GT {_bn(r.value)} tỷ; "
            f"NN mua {_bn(r.foreign_buy_value)} / bán {_bn(r.foreign_sell_value)} tỷ"
        )
    msg = "\n".join(lines) + (f"\nKhông có dữ liệu: {', '.join(missing)}" if missing else "")
    return ToolResult(status="ok", data=df.to_dict("records"), message=msg)


@instrument_tool("get_intraday_ticks")
def get_intraday_ticks(ticker: str, limit: int = 20) -> ToolResult:
    """Các lệnh khớp gần nhất trong phiên của một mã."""
    sym = ticker.strip().upper()
    if not sym:
        return ToolResult(status="invalid_input", data=None, message="Thiếu mã cổ phiếu.")
    try:
        df = _client().prices.intraday(sym, limit=max(1, min(int(limit), 200)))
    except Exception as e:
        return _failure(e, f"lệnh khớp {sym}")
    head = df.head(10)
    lines = [f"{r.time}: {r.price:,.0f} x {r.volume:,.0f} ({r.side})" for r in head.itertuples()]
    return ToolResult(status="ok", data=df.to_dict("records"),
                      message=f"{sym}: {len(df)} lệnh khớp gần nhất. Mới nhất:\n" + "\n".join(lines))


@instrument_tool("get_vn_index_board")
def get_vn_index_board() -> ToolResult:
    """Bảng chỉ số HOSE: VNIndex, VN30, VNMid, chỉ số ngành (điểm, % thay đổi)."""
    try:
        df = _client().market.indices()
    except Exception as e:
        return _failure(e, "bảng chỉ số")
    cols = [c for c in ("index", "value", "change_pct") if c in df.columns]
    lines = [f"{r['index']}: {r['value']:,.2f} ({r['change_pct']:+.2f}%)" for r in df[cols].to_dict("records")
             if "change_pct" in r and r["change_pct"] == r["change_pct"]]
    return ToolResult(status="ok", data=df.to_dict("records"),
                      message=f"Bảng chỉ số (nguồn: {df.attrs.get('source', 'hose')}):\n" + "\n".join(lines))


@instrument_tool("get_live_sector_performance")
def get_live_sector_performance() -> ToolResult:
    """% thay đổi hôm nay theo ngành (ICB), trực tiếp từ sàn."""
    try:
        df = _client().market.sectors_performance()
    except Exception as e:
        return _failure(e, "hiệu suất ngành")
    name_col = "name" if "name" in df.columns else df.columns[0]
    df = df.sort_values("change_pct", ascending=False)
    lines = [f"{r[name_col]}: {r['change_pct']:+.2f}%" for r in df.to_dict("records")]
    return ToolResult(status="ok", data=df.to_dict("records"),
                      message=f"Hiệu suất ngành hôm nay (nguồn: {df.attrs.get('source', '?')}):\n" + "\n".join(lines))


@instrument_tool("get_proprietary_trading")
def get_proprietary_trading(ticker: str, days: int = 10) -> ToolResult:
    """Giao dịch tự doanh theo ngày của một mã (giá trị: tỷ đồng)."""
    sym = ticker.strip().upper()
    if not sym:
        return ToolResult(status="invalid_input", data=None, message="Thiếu mã cổ phiếu.")
    try:
        df = _client().market.proprietary(sym, limit=max(1, min(int(days), 90)))
    except Exception as e:
        return _failure(e, f"tự doanh {sym}")
    lines = [
        f"{r.date:%Y-%m-%d}: mua {_bn(r.buy_value)} / bán {_bn(r.sell_value)} tỷ"
        for r in df.itertuples()
    ]
    return ToolResult(status="ok", data=df.to_dict("records"),
                      message=f"Tự doanh {sym} {len(df)} phiên gần nhất:\n" + "\n".join(lines))


@instrument_tool("get_company_profile")
def get_company_profile(ticker: str) -> ToolResult:
    """Hồ sơ doanh nghiệp: vốn hóa, biên độ 52 tuần, sở hữu nước ngoài, lãnh đạo, cổ đông, công ty con."""
    sym = ticker.strip().upper()
    if not sym:
        return ToolResult(status="invalid_input", data=None, message="Thiếu mã cổ phiếu.")
    vn = _client()
    try:
        overview = vn.company.overview(sym)
    except Exception as e:
        return _failure(e, f"hồ sơ {sym}")
    try:
        profile = vn.company.profile(sym)
    except Exception:
        profile = {}
    if not overview.get("market_cap") and not profile.get("business_summary"):
        return ToolResult(status="no_data", data=None, message=f"Không tìm thấy thông tin doanh nghiệp {sym}.")
    parts = [f"{sym}: vốn hóa {_bn(overview['market_cap'])} tỷ" if overview.get("market_cap") else sym]
    if overview.get("highest_price1_year") and overview.get("lowest_price1_year"):
        parts.append(f"52 tuần {overview['lowest_price1_year']:,.0f}–{overview['highest_price1_year']:,.0f} VND")
    if overview.get("foreigner_percentage") is not None:
        parts.append(f"NN sở hữu {overview['foreigner_percentage'] * 100:.1f}%")
    msg = ", ".join(parts)
    if profile.get("business_summary"):
        summary = re.sub(r"\s+", " ", re.sub(r"</?p>|<br\s*/?>", " ", str(profile["business_summary"]))).strip()
        msg += "\n" + summary[:600]
    return ToolResult(status="ok", data={"overview": overview, "profile": profile}, message=msg)
