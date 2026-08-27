"""
agents/market_brief_graph.py — LangGraph for daily market brief (Phase 6).

Architecture:
  start → collect_all (fan-out: world / vn / news / technical in parallel)
        → compose_outlook (1 LLM call, only 🎯 section)
        → render_report   (Python template, no LLM writes numbers)
        → END

Design rules:
  - State holds text strings only, no DataFrames.
  - Missing data → "(không có dữ liệu)", never let LLM fill gaps.
  - LLM only writes the 🎯 NHẬN ĐỊNH narrative.
  - Log missing_fields for every run.
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from langgraph.graph import END, StateGraph

from agents.market_brief_state import MarketBriefState

# Module-level imports so tests can patch at agents.market_brief_graph.<name>
from tools.global_market import (
    get_commodities,
    get_crypto_prices,
    get_fx_rates,
    get_global_indices,
    get_vn_gold,
)
from tools.index_db import query_index_latest
from tools.levels import find_support_resistance
from tools.price import (
    calculate_indicators,
    detect_candle_pattern,
    get_foreign_flows,
    get_historical_ohlcv,
    get_market_breadth,
    get_market_performance,
    get_sector_performance,
    get_top_movers,
    search_financial_news,
)
from tools.events_views import get_broker_views, get_corporate_events

# LLM — lazy-imported inside compose_outlook to avoid circular issues at test time
try:
    from llm.factory import create_client
    from llm.types import Message as LLMMessage
except ImportError:
    create_client = None  # type: ignore
    LLMMessage = None     # type: ignore

_MISSING = "(không có dữ liệu)"
_TEMPLATE_PATH = Path(__file__).parent / "templates" / "market_brief.txt"


# ── Helper ────────────────────────────────────────────────────────────────────

def _safe(fn, *args, **kwargs):
    """Call fn(*args, **kwargs); return None on exception (non-fatal)."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        sys.stderr.write(f"[market_brief] {fn.__name__} failed: {e}\n")
        return None


def _ok(result) -> bool:
    return result is not None and getattr(result, "status", None) == "ok"


# ── Sub-collectors ────────────────────────────────────────────────────────────

def _collect_world() -> dict:
    """Fetch world indices, gold (world + SJC), oil, crypto, FX. All best-effort."""
    missing: list[str] = []

    # World indices
    idx_r = _safe(get_global_indices)
    if _ok(idx_r):
        world_block = idx_r.message
    else:
        world_block = _MISSING
        missing.append("world_indices")

    # Commodities: split gold vs oil
    com_r = _safe(get_commodities)
    gold_line = _MISSING
    oil_parts: list[str] = []
    if _ok(com_r) and com_r.data:
        for item in com_r.data:
            name = item.get("name", "").lower()
            ticker = item.get("ticker", "").lower()
            price = item.get("price", 0)
            pct = item.get("change_pct", 0)
            unit = item.get("unit", "USD")
            sign = "+" if pct >= 0 else ""
            if "gold" in name or "gc=" in ticker or "xau" in name:
                gold_line = f"Vàng thế giới (XAU): {price:,.2f} {unit} ({sign}{pct:.2f}%)"
            elif "wti" in name or "cl=" in ticker:
                oil_parts.append(f"WTI: {price:.2f} USD/thùng ({sign}{pct:.2f}%)")
            elif "brent" in name or "bz=" in ticker:
                oil_parts.append(f"Brent: {price:.2f} USD/thùng ({sign}{pct:.2f}%)")

    oil_line = " | ".join(oil_parts) if oil_parts else _MISSING

    # VN gold (SJC)
    sjc_r = _safe(get_vn_gold)
    sjc_text = sjc_r.message if _ok(sjc_r) else _MISSING
    if not _ok(sjc_r):
        missing.append("sjc_gold")

    gold_oil_block = "\n".join(filter(None, [gold_line, sjc_text, oil_line]))

    # Crypto
    crypto_r = _safe(get_crypto_prices)
    if _ok(crypto_r):
        crypto_block = crypto_r.message
    else:
        crypto_block = _MISSING
        missing.append("crypto")

    # FX
    fx_r = _safe(get_fx_rates)
    if _ok(fx_r):
        fx_block = fx_r.message
    else:
        fx_block = _MISSING
        missing.append("fx_rates")

    return {
        "world_block": world_block,
        "gold_oil_block": gold_oil_block,
        "crypto_block": crypto_block,
        "fx_block": fx_block,
        "missing": missing,
    }


def _collect_vn(target_date: Optional[str]) -> dict:
    """Fetch VN market: index, breadth, top movers, foreign flows, sector."""
    missing: list[str] = []

    # VN-Index (DB-first, fallback to market_performance)
    # as_of_date: the brief for date D shows the session that CLOSED before D's open.
    # If target_date is given, use it as the upper bound so yesterday's close is returned.
    vn_index_text = _MISSING
    row = _safe(query_index_latest, "VNINDEX", target_date)
    if row:
        close = row.get("close", 0)
        chg_pts = row.get("change_pts", 0)
        chg_pct = row.get("change_pct", 0)
        mv = row.get("matched_value", 0)
        sign = "+" if chg_pts >= 0 else ""
        mv_bn = mv / 1e9 if mv else 0

        # Count consecutive up/down sessions from DB history
        streak_note = ""
        try:
            from tools.index_db import query_index
            hist_df = query_index("VNINDEX", 10)
            if hist_df is not None and not hist_df.empty and "change_pts" in hist_df.columns:
                changes = hist_df["change_pts"].tolist()
                if changes:
                    last = changes[-1]
                    direction = last > 0
                    streak = sum(1 for c in reversed(changes) if (c > 0) == direction and c != 0)
                    if streak >= 2:
                        word = "tăng" if direction else "giảm"
                        streak_note = f" — phiên {word} thứ {streak} liên tiếp"
        except Exception:
            pass

        # "Hụt mốc" / "Vượt mốc" check — use high/low from the row if available
        level_note = ""
        try:
            row_high = row.get("high", 0) or 0
            row_low = row.get("low", 0) or 0
            # Check round psychological levels: 1800, 1850, 1900, 1950, 2000
            for level in [1800, 1850, 1900, 1950, 2000, 1750, 1700]:
                if row_high >= level > close:
                    level_note = f", hụt mốc {level:,.0f} sau khi đã vượt trong phiên"
                    break
                if row_low <= level < close:
                    level_note = f", vượt mốc {level:,.0f} thành công"
                    break
        except Exception:
            pass

        vn_index_text = (
            f"VN-Index đóng cửa {close:,.2f} điểm ({sign}{chg_pts:.2f}đ, "
            f"{sign}{chg_pct:.2f}%){streak_note}{level_note}. "
            f"Khớp lệnh HoSE ~{mv_bn:,.0f} tỷ đồng."
        )
    else:
        mp_r = _safe(get_market_performance, "today", "VNINDEX")
        if _ok(mp_r):
            d = mp_r.data
            close = d.get("last_close", 0)
            pct = d.get("pct_change", 0)
            sign = "+" if pct >= 0 else ""
            vn_index_text = f"VN-Index đóng cửa {close:,.2f} điểm ({sign}{pct:.2f}%)."
        else:
            missing.append("vn_index")

    # Market breadth
    breadth_r = _safe(get_market_breadth, "HOSE")
    if _ok(breadth_r):
        d = breadth_r.data
        total = d.get('advances', 0) + d.get('declines', 0) + d.get('unchanged', 0)
        total_tag = f" ({total} mã)" if total > 0 else ""
        breadth_text = f"{d.get('advances', 0)} mã tăng / {d.get('declines', 0)} mã giảm{total_tag}"
    else:
        breadth_text = _MISSING
        missing.append("breadth")

    # Top movers by value
    movers_r = _safe(get_top_movers, "value", 3)
    if _ok(movers_r) and movers_r.data:
        items = movers_r.data[:3]
        parts = [
            f"{r['ticker']} {r.get('traded_value', 0)/1e9:.0f}tỷ"
            for r in items
        ]
        movers_text = "Dẫn dắt thanh khoản: " + ", ".join(parts) + "."
    else:
        movers_text = ""
        missing.append("top_movers")

    # Foreign flows — pass target_date so we don't pull future-date data if ingest ran ahead
    ff_r = _safe(get_foreign_flows, 1, as_of_date=target_date or None)
    if _ok(ff_r):
        foreign_text = ff_r.message
    else:
        foreign_text = _MISSING
        missing.append("foreign_flows")

    # Sector performance
    sec_r = _safe(get_sector_performance, "day")
    if _ok(sec_r) and sec_r.data:
        sectors = sec_r.data[:4]
        parts = [f"{s['sector']} {s['pct_change']:+.1f}%" for s in sectors]
        sector_text = "Nhóm ngành: " + ", ".join(parts) + "."
    else:
        sector_text = ""

    return {
        "vn_index_text": vn_index_text,
        "breadth_text": breadth_text,
        "movers_text": movers_text,
        "foreign_text": foreign_text,
        "sector_text": sector_text,
        "missing": missing,
    }


def _collect_news(target_date: Optional[str]) -> dict:
    """Fetch news, corporate events, broker views."""
    missing: list[str] = []

    # Market news — semantic search for financial relevance, then sort by recency.
    # Pure DB recency pulls in non-market articles (politics, crime); semantic filter fixes that.
    # Pure semantic search misses today's top stories if embedding score is low; recency sort fixes that.
    import re as _re
    try:
        from rag.news_index import search_news_by_text as _snbt
        # Broad financial market query — filters to market-relevant articles only
        _hits = _snbt(
            "tin tức tài chính chứng khoán thị trường Việt Nam VN-Index cổ phiếu đầu tư",
            days=3,
            limit=10,
        )
        # Sort by recency so today's top story wins over yesterday's
        _hits.sort(key=lambda x: str(x.get("published_at", "")), reverse=True)
        _raw_lines = [
            f"[{_h.get('source','')} | {str(_h.get('published_at',''))[:10]}] {_h.get('title','').strip()}"
            for _h in _hits[:5]
            if _h.get("title", "").strip()
        ]
        _has_news = bool(_raw_lines)
    except Exception:
        _news_r = _safe(search_financial_news, "VNINDEX", 2)
        _raw_lines = [l for l in _news_r.message.split("\n") if l.strip()] if _ok(_news_r) else []
        _has_news = bool(_raw_lines)

    if _has_news:
        clean_lines = []
        for l in _raw_lines[:3]:
            cleaned = _re.sub(r"^\[[^\]]+\]\s*", "", l).strip()
            if cleaned:
                clean_lines.append(cleaned)
        news_text = "\n".join(clean_lines) if clean_lines else _MISSING
    else:
        news_text = _MISSING
        missing.append("news")

    # Broker views for VNINDEX — fetched first so it can serve as fallback for events section
    bv_r = _safe(get_broker_views, "VNINDEX", 7)
    if _ok(bv_r):
        broker_text = bv_r.message
    elif bv_r is not None and bv_r.status == "no_data":
        broker_text = "Không có nhận định CTCK gần đây."
    else:
        broker_text = _MISSING
        missing.append("broker_views")

    # Corporate events — include yesterday (days_back=1) to catch same-day ex_date events
    ev_r = _safe(get_corporate_events, None, 14, 1)
    if _ok(ev_r) and ev_r.data:
        ev_lines = ev_r.message.split("\n")
        events_text = "; ".join(l.strip().lstrip("• ") for l in ev_lines[:3] if l.strip())
    elif ev_r is not None and ev_r.status == "no_data":
        # No rights events — fall back to broker views if available
        if _ok(bv_r) and bv_r.data:
            first_line = bv_r.message.split("\n")[0].strip().lstrip("• ")
            events_text = f"Nhận định CTCK: {first_line}"
        else:
            events_text = "Không có sự kiện quyền trong 3 ngày tới."
    else:
        events_text = _MISSING
        missing.append("corporate_events")

    return {
        "news_text": news_text,
        "events_text": events_text,
        "broker_text": broker_text,
        "missing": missing,
    }


def _collect_technical(target_date: Optional[str]) -> dict:
    """Fetch VNINDEX OHLCV + calculate indicators, candle pattern, support/resistance."""
    missing: list[str] = []

    ohlcv_r = _safe(get_historical_ohlcv, "VNINDEX", 250)
    if not _ok(ohlcv_r) or ohlcv_r.data is None:
        missing.extend(["tech_signals", "candle_pattern", "levels"])
        return {
            "tech_signals": _MISSING,
            "candle_pattern": _MISSING,
            "levels_text": _MISSING,
            "missing": missing,
        }

    df = ohlcv_r.data
    # Cap to target_date so future rows don't bleed into indicators/levels
    if target_date:
        try:
            import pandas as _pd
            df = df[_pd.to_datetime(df["date"]).dt.date <= _pd.Timestamp(target_date).date()].copy()
        except Exception:
            pass

    ind_r = _safe(calculate_indicators, df)
    tech_signals = ind_r.message if _ok(ind_r) else _MISSING
    if not _ok(ind_r):
        missing.append("tech_signals")

    candle_r = _safe(detect_candle_pattern, df)
    candle_pattern = candle_r.message if _ok(candle_r) else _MISSING
    if not _ok(candle_r):
        missing.append("candle_pattern")

    lvl_r = _safe(find_support_resistance, df)
    levels_text = lvl_r.message if _ok(lvl_r) else _MISSING
    if not _ok(lvl_r):
        missing.append("levels")

    return {
        "tech_signals": tech_signals,
        "candle_pattern": candle_pattern,
        "levels_text": levels_text,
        "missing": missing,
    }


# ── Node 1: collect_all ───────────────────────────────────────────────────────

def collect_all(state: MarketBriefState) -> dict:
    """Fan-out: run 4 sub-collectors in parallel threads, merge results."""
    date = state.get("date", "")

    tasks = {
        "world": lambda: _collect_world(),
        "vn":    lambda: _collect_vn(date),
        "news":  lambda: _collect_news(date),
        "tech":  lambda: _collect_technical(date),
    }

    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(fn): key for key, fn in tasks.items()}
        for fut in as_completed(futures):
            key = futures[fut]
            try:
                results[key] = fut.result()
            except Exception as e:
                sys.stderr.write(f"[collect_all] collector '{key}' raised: {e}\n")
                results[key] = {"missing": [key]}

    # Aggregate missing fields
    all_missing: list[str] = []
    for r in results.values():
        all_missing.extend(r.get("missing", []))

    if all_missing:
        sys.stderr.write(f"[market_brief] missing fields: {all_missing}\n")

    w = results.get("world", {})
    vn = results.get("vn", {})
    n = results.get("news", {})
    t = results.get("tech", {})

    return {
        "world_block":    w.get("world_block", _MISSING),
        "gold_oil_block": w.get("gold_oil_block", _MISSING),
        "crypto_block":   w.get("crypto_block", _MISSING),
        "fx_block":       w.get("fx_block", _MISSING),
        "vn_index_text":  vn.get("vn_index_text", _MISSING),
        "breadth_text":   vn.get("breadth_text", _MISSING),
        "movers_text":    vn.get("movers_text", ""),
        "foreign_text":   vn.get("foreign_text", _MISSING),
        "sector_text":    vn.get("sector_text", ""),
        "news_text":      n.get("news_text", _MISSING),
        "events_text":    n.get("events_text", _MISSING),
        "broker_text":    n.get("broker_text", _MISSING),
        "tech_signals":   t.get("tech_signals", _MISSING),
        "candle_pattern": t.get("candle_pattern", _MISSING),
        "levels_text":    t.get("levels_text", _MISSING),
        "missing_fields": all_missing,
        "step_count": state.get("step_count", 0) + 1,
        "history": state.get("history", []) + [{"step": "collect_all", "missing": all_missing}],
    }


# ── Reasoning stripper ───────────────────────────────────────────────────────

_ANSWER_MARKER = "###NHẬN ĐỊNH###"
_END_MARKER = "###END###"

# Vietnamese reasoning phrases that indicate the model is still in thinking mode
_REASONING_PREFIXES = (
    "Chuẩn bị", "Lưu ý:", "Cần xử lý", "Dữ liệu cung cấp",
    "Cách xử lý", "Tốt nhất", "Có thể nói", "Xung đột",
)


def _strip_reasoning(text: str) -> str:
    """Extract only the answer after ###NHẬN ĐỊNH### marker.

    Fallback: if marker absent, discard leading paragraphs that look like
    internal reasoning (contain known Vietnamese thinking-mode phrases) or
    are too short to be real content (< 30 chars — e.g. a lone ".").
    """
    if _ANSWER_MARKER in text:
        content = text.split(_ANSWER_MARKER, 1)[1]
        # Strip trailing self-check notes after closing marker
        if _END_MARKER in content:
            content = content.split(_END_MARKER, 1)[0]
        # Also strip trailing paragraphs that are reasoning/English notes
        # (model sometimes appends "Let me check...", "P1:", "Also check" etc.)
        _TRAILING_REASONING = (
            "let me", "p1:", "p2:", "p3:", "check", "also check",
            "draft:", "note:", "paragraph", "sentence count",
        )
        paras = [p.strip() for p in content.split("\n\n") if p.strip()]
        # Walk from end, drop paragraphs that look like trailing meta-commentary
        while paras:
            last = paras[-1].lower()
            if (
                len(paras[-1]) < 60
                or any(last.startswith(t) for t in _TRAILING_REASONING)
                or last.startswith("let ")
                or (paras[-1][0].islower() and len(paras[-1]) < 120)
            ):
                paras.pop()
            else:
                break
        return "\n\n".join(paras).strip() if paras else content.strip()

    # Marker not found — strip reasoning paragraphs heuristically
    def _looks_like_reasoning(para: str) -> bool:
        if len(para) < 30:
            return True
        first_line = (para.splitlines()[0] if para.splitlines() else para).strip()
        # Vietnamese reasoning prefixes
        if any(first_line.startswith(prefix) for prefix in _REASONING_PREFIXES):
            return True
        # English reasoning: paragraph is mostly ASCII (no Vietnamese diacritics)
        non_ascii = sum(1 for c in para if ord(c) > 127)
        if non_ascii < max(3, len(para) * 0.04):  # < 4% non-ASCII = English-heavy
            return True
        return False

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    # Best signal: find paragraph that starts with "VN-Index" — content always opens there
    _CONTENT_STARTERS = ("VN-Index", "Thị trường", "Chỉ số")
    for i, para in enumerate(paragraphs):
        if any(para.startswith(s) for s in _CONTENT_STARTERS):
            # Take up to 3 consecutive content paragraphs from here
            content_paras = [p for p in paragraphs[i:i + 5] if not _looks_like_reasoning(p)]
            return "\n\n".join(content_paras[:3]).strip()

    # Fallback: first non-reasoning paragraph
    for i, para in enumerate(paragraphs):
        if not _looks_like_reasoning(para):
            return "\n\n".join(paragraphs[i:]).strip()

    # Last resort: last substantial paragraph
    substantial = [p for p in paragraphs if len(p) >= 30]
    return substantial[-1].strip() if substantial else text


# ── Node 2: compose_outlook ───────────────────────────────────────────────────

def compose_outlook(state: MarketBriefState) -> dict:
    """Single LLM call — writes ONLY the 🎯 NHẬN ĐỊNH narrative (no numbers)."""
    tech = state.get("tech_signals", _MISSING)
    candle = state.get("candle_pattern", _MISSING)
    levels = state.get("levels_text", _MISSING)
    broker = state.get("broker_text", _MISSING)
    news = state.get("news_text", _MISSING)
    vn_idx = state.get("vn_index_text", _MISSING)
    breadth = state.get("breadth_text", _MISSING)
    foreign = state.get("foreign_text", _MISSING)
    sector = state.get("sector_text", "")

    system_prompt = (
        "Bạn là chuyên gia phân tích kỹ thuật chứng khoán Việt Nam. "
        "Viết phần nhận định ngắn gọn, chuyên nghiệp, đúng thực tế. "
        "KHÔNG tự bịa số liệu. Dùng đúng các số đã cung cấp. "
        "TUYỆT ĐỐI không viết quá trình suy nghĩ, không ghi chú nội bộ, "
        "không giải thích bước phân tích, không nói 'Chuẩn bị', 'Lưu ý', 'Dữ liệu cung cấp', "
        "không nhắc đến việc 'xử lý' hay 'kiểm tra' dữ liệu. "
        "Output chỉ gồm nội dung nhận định thuần túy — viết như bài đăng mạng xã hội chuyên nghiệp."
    )

    user_prompt = f"""Dữ liệu phiên hôm nay:

VN-Index: {vn_idx}
Độ rộng: {breadth}
Khối ngoại: {foreign}
Nhóm ngành: {sector}

Chỉ báo kỹ thuật:
{tech}

Mẫu nến: {candle}
Hỗ trợ/kháng cự: {levels}

Nhận định CTCK:
{broker}

Tin tức: {news}

Yêu cầu: Viết 2-3 đoạn nhận định ngắn gọn (kỹ thuật → kịch bản → rủi ro). Mỗi đoạn tối đa 3-4 câu. Không dùng tiêu đề đoạn như "Đoạn 1:", "Kỹ thuật:", "Kịch bản:", "Nội dung:". Viết liền mạch như bài đăng mạng xã hội.
Bắt buộc: Bắt đầu phần trả lời bằng đúng dòng: ###NHẬN ĐỊNH###
Sau đó viết thẳng nội dung. Không thêm chú thích, kiểm tra, hay bình luận sau nội dung."""

    t0 = time.perf_counter()
    try:
        # Use module-level create_client (patchable in tests); fall back to lazy import
        _fn = create_client
        if _fn is None:
            from llm.factory import create_client as _fn  # type: ignore
        client = _fn()

        _Msg = LLMMessage
        if _Msg is None:
            from llm.types import Message as _Msg  # type: ignore

        resp = client.generate(
            [_Msg(role="user", content=user_prompt)],
            max_tokens=2000,
            temperature=0.0,
            system=system_prompt,
        )
        outlook_text = _strip_reasoning(resp.text.strip())
        elapsed = time.perf_counter() - t0
        history_entry = {
            "step": "compose_outlook",
            "input_tokens": resp.input_tokens,
            "output_tokens": resp.output_tokens,
            "elapsed_seconds": round(elapsed, 2),
        }
    except Exception as e:
        sys.stderr.write(f"[compose_outlook] LLM call failed: {e}\n")
        outlook_text = "(không thể tạo nhận định — lỗi LLM)"
        history_entry = {"step": "compose_outlook", "error": str(e)}

    return {
        "outlook_text": outlook_text,
        "step_count": state.get("step_count", 0) + 1,
        "history": state.get("history", []) + [history_entry],
    }


# ── Node 3: render_report ─────────────────────────────────────────────────────

def render_report(state: MarketBriefState) -> dict:
    """Python template rendering — numbers come from data, never from LLM."""
    date_str = state.get("date", "")
    if date_str:
        try:
            from datetime import datetime
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            date_display = dt.strftime("%d/%m/%Y")
        except ValueError:
            date_display = date_str
    else:
        from datetime import date as date_cls
        date_display = date_cls.today().strftime("%d/%m/%Y")

    template = _TEMPLATE_PATH.read_text(encoding="utf-8")

    report = template.format(
        date_display=date_display,
        world_block=state.get("world_block", _MISSING),
        gold_oil_block=state.get("gold_oil_block", _MISSING),
        crypto_block=state.get("crypto_block", _MISSING),
        fx_block=state.get("fx_block", _MISSING),
        vn_index_text=state.get("vn_index_text", _MISSING),
        breadth_text=state.get("breadth_text", _MISSING),
        movers_text=state.get("movers_text", ""),
        foreign_text=state.get("foreign_text", _MISSING),
        news_text=state.get("news_text", _MISSING),
        events_text=state.get("events_text", _MISSING),
        outlook_text=state.get("outlook_text", _MISSING),
    )

    # Write to file if output_path given
    output_file = ""
    out_path = state.get("output_path", "")
    if out_path:
        try:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_text(report, encoding="utf-8")
            output_file = out_path
            sys.stdout.write(f"[market_brief] Written → {out_path}\n")
        except Exception as e:
            sys.stderr.write(f"[render_report] Failed to write {out_path}: {e}\n")

    return {
        "report_text": report,
        "output_file": output_file,
        "step_count": state.get("step_count", 0) + 1,
        "history": state.get("history", []) + [{"step": "render_report", "output_file": output_file}],
    }


# ── Graph builder ─────────────────────────────────────────────────────────────

def build_brief_graph():
    """Build and compile the market brief StateGraph."""
    g = StateGraph(MarketBriefState)
    g.add_node("collect_all", collect_all)
    g.add_node("compose_outlook", compose_outlook)
    g.add_node("render_report", render_report)

    g.set_entry_point("collect_all")
    g.add_edge("collect_all", "compose_outlook")
    g.add_edge("compose_outlook", "render_report")
    g.add_edge("render_report", END)

    return g.compile()


def make_initial_state(date: str = "", output_path: str = "") -> MarketBriefState:
    return MarketBriefState(
        date=date,
        output_path=output_path,
        step_count=0,
        history=[],
        missing_fields=[],
        error="",
    )
