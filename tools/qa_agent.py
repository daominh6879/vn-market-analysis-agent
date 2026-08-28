"""
tools/qa_agent.py — Orchestrator: interpret → dispatch → answer.

Flow:
  user question
    → query_interpreter.interpret()  → QueryIntent
    → dispatch by intent.tool:
        ask_report      → rag_query.ask_report()
        sql_query       → sql_agent.execute_safe()
        both            → ask_report + sql, merge
        news            → _search_sector_news() or search_financial_news per ticker
        report_and_news → ask_report + news, merge
        out_of_scope    → refusal
    → ToolResult

Usage:
  from tools.qa_agent import answer
  result = answer("So sánh HPG và VCB năm 2024")
  result = answer("Phân tích thị trường thép trong thời gian tới")
  print(result.data)
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from tools.result import ToolResult

_NEWS_DAYS = 30  # default lookback for news queries


def _fetch_price_and_indicators(ticker: str) -> tuple[str | None, str | None]:
    """Return (price_text, indicators_text) for a single ticker. Non-fatal."""
    from tools.price import get_realtime_price, get_historical_ohlcv, calculate_indicators
    from tools.providers import _detect_provider, YFinanceProvider

    price_text: str | None = None
    ind_text: str | None = None

    try:
        provider = _detect_provider(ticker)
        price_result = get_realtime_price(ticker, provider=provider)
        if price_result.status == "ok" and price_result.data is not None:
            price_text = f"{ticker}: {price_result.data:,.0f} VND"
    except Exception:
        pass

    try:
        provider = _detect_provider(ticker)
        currency = "USD" if isinstance(provider, YFinanceProvider) else "VND"
        ohlcv = get_historical_ohlcv(ticker, days=60, provider=provider)
        if ohlcv.status == "ok" and ohlcv.data is not None:
            ind_result = calculate_indicators(ohlcv.data, currency=currency)
            if ind_result.status == "ok" and ind_result.data:
                ind_text = str(ind_result.data)
    except Exception:
        pass

    return price_text, ind_text


def _lookup_sector(ticker: str) -> str | None:
    """Lookup sector from securities table. Returns lowercase sector string or None."""
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT sector, industry FROM securities WHERE ticker = %s", (ticker,))
                row = cur.fetchone()
                if row:
                    sector, industry = row
                    return f"{sector} {industry}".lower()
    except Exception:
        pass
    return None


def _fetch_macro_drivers(sector: str | None) -> ToolResult:
    """Fetch sector-relevant macro indicators.

    Sector mapping:
      vật liệu / năng lượng / dầu khí → commodities (WTI, Brent, Gold)
      ngân hàng / bất động sản / tài chính → FX (USD/VND)
      default → S&P 500 risk sentiment
    """
    from tools.global_market import get_commodities, get_fx_rates, get_global_indices

    sector_l = (sector or "").lower()
    parts: list[str] = []

    commodity_sectors = ("năng lượng", "dầu", "vật liệu", "thép", "kim loại")
    fx_sectors = ("ngân hàng", "tài chính", "bất động sản", "tiêu dùng", "bán lẻ")

    if any(kw in sector_l for kw in commodity_sectors):
        try:
            com_r = get_commodities()
            if com_r.status == "ok" and com_r.data:
                for item in com_r.data:
                    sign = "+" if item.get("change_pct", 0) >= 0 else ""
                    parts.append(
                        f"{item['name']}: {item['price']:.2f} {item.get('unit','')} "
                        f"({sign}{item['change_pct']:.2f}%)"
                    )
        except Exception:
            pass

    if any(kw in sector_l for kw in fx_sectors):
        try:
            fx_r = get_fx_rates()
            if fx_r.status == "ok":
                parts.append(f"Tỷ giá: {fx_r.message}")
        except Exception:
            pass

    # Always include S&P 500 risk-on/off signal
    try:
        idx_r = get_global_indices()
        if idx_r.status == "ok" and idx_r.data:
            sp = next((d for d in idx_r.data if "S&P" in d.get("name", "")), None)
            if sp:
                sign = "+" if sp["change_pct"] >= 0 else ""
                parts.append(f"S&P 500: {sp['close']:,.2f} ({sign}{sp['change_pct']:.2f}%)")
    except Exception:
        pass

    if not parts:
        return ToolResult(status="no_data", data=None, message="Không có dữ liệu vĩ mô.")
    return ToolResult(status="ok", data="\n".join(parts), message=f"{len(parts)} macro indicators.")


def _fetch_news(tickers: list[str], sector: str | None, days: int = _NEWS_DAYS) -> ToolResult:
    """Fetch recent news for tickers or sector.

    If tickers given → search per ticker, merge headlines.
    If only sector given → search news_chunks by sector keyword (no ticker filter).
    """
    from rag.news_index import search_news_by_text

    headlines: list[str] = []
    seen: set[str] = set()

    if tickers:
        # Primary: FireAnt API (authenticated, structured news with titles)
        for ticker in tickers[:3]:
            try:
                from data.fireant import fetch_ticker_news as fa_news
                items = fa_news(ticker, max_articles=10)
                for item in items:
                    title = item.get("title", "")
                    source = item.get("source", "")
                    key = title[:60]
                    if key not in seen:
                        seen.add(key)
                        suffix = f" [{source}]" if source else ""
                        headlines.append(f"- {title}{suffix}")
            except Exception:
                pass
        # Secondary: CafeF live search if FireAnt returned nothing
        if not headlines:
            for ticker in tickers[:3]:
                try:
                    from data.cafef_rss import fetch_ticker_news as cafef_news
                    items = cafef_news(ticker, max_articles=8)
                    for item in items:
                        title = item.get("title", "")
                        url = item.get("url", "")
                        key = title[:60]
                        if key not in seen:
                            seen.add(key)
                            headlines.append(f"- {title}" + (f" ({url})" if url else ""))
                except Exception:
                    pass
        # Fallback: RAG news index if both live sources returned nothing
        if not headlines:
            for ticker in tickers[:3]:
                try:
                    items = search_news_by_text(ticker, days=days, limit=5, ticker=ticker)
                    for item in items:
                        title = item.get("title") or item.get("text", "")[:120]
                        url = item.get("url", "")
                        key = title[:60]
                        if key not in seen:
                            seen.add(key)
                            headlines.append(f"- {title}" + (f" ({url})" if url else ""))
                except Exception:
                    continue
    elif sector:
        # No ticker filter — semantic search on sector keyword
        query = f"thị trường {sector} Việt Nam triển vọng"
        try:
            items = search_news_by_text(query, days=days, limit=8, ticker=None)
            for item in items:
                title = item.get("title") or item.get("text", "")[:120]
                url = item.get("url", "")
                key = title[:60]
                if key not in seen:
                    seen.add(key)
                    headlines.append(f"- {title}" + (f" ({url})" if url else ""))
        except Exception as e:
            return ToolResult(status="upstream_error", data=None, message=f"News search lỗi: {e}")

    if not headlines:
        scope = ", ".join(tickers) if tickers else (sector or "thị trường")
        return ToolResult(
            status="no_data",
            data=None,
            message=f"Không tìm thấy tin tức {days} ngày gần nhất cho {scope}.",
        )

    return ToolResult(
        status="ok",
        data="\n".join(headlines),
        message=f"{len(headlines)} tin tức ({days} ngày gần nhất).",
    )


def answer(question: str, client=None) -> ToolResult:
    """Main entry point. Interpret question and dispatch to correct tool(s)."""
    try:
        return _answer_inner(question, client=client)
    except Exception as exc:
        return ToolResult(
            status="upstream_error",
            data=None,
            message=f"Lỗi xử lý câu hỏi: {exc}",
        )


def _answer_inner(question: str, client=None) -> ToolResult:
    from tools.query_interpreter import interpret
    from tools.rag_query import ask_report
    from rag.sql_agent import execute_safe

    intent = interpret(question, client=client)

    if intent.tool == "out_of_scope":
        # Fallback: extract tickers from question text (LLM sometimes drops them on misroute).
        # If ticker found → route to ask_report so user gets useful answer.
        if not intent.tickers:
            import re as _re
            found = _re.findall(r'\b([A-Z]{2,5})\b', question.upper())
            # Check against known VN tickers to avoid false positives
            try:
                from tools.providers import _vn_ticker_set
                known = _vn_ticker_set()
                intent.tickers = [t for t in found if t in known]
            except Exception:
                intent.tickers = found[:1]  # best-effort

        if intent.tickers:
            intent.tool = "ask_report"
        else:
            return ToolResult(
                status="no_data",
                data=None,
                message=(
                    f"Câu hỏi ngoài phạm vi dữ liệu. {intent.reason} "
                    "Hệ thống chỉ có báo cáo tài chính và giá lịch sử."
                ),
            )

    if intent.tool == "ask_report":
        return ask_report(
            question=question,
            tickers=intent.tickers or None,
            sector=intent.sector,
            year=intent.year,
        )

    if intent.tool == "sql_query":
        # Retry once with error feedback if execution fails
        for attempt in range(2):
            try:
                sql_result = execute_safe(question, client=client)
                return ToolResult(
                    status="ok",
                    data={"rows": sql_result.rows, "sql": sql_result.sql},
                    message=f"SQL query thành công: {len(sql_result.rows)} rows.",
                )
            except Exception as exc:
                if attempt == 0:
                    question = f"{question}\n\n[Lần trước bị lỗi: {exc}. Hãy sửa SQL.]"
                else:
                    return ToolResult(
                        status="upstream_error",
                        data=None,
                        message=f"SQL lỗi sau 2 lần thử: {exc}",
                    )

    if intent.tool == "both":
        rag_result = ask_report(
            question=question,
            tickers=intent.tickers or None,
            sector=intent.sector,
            year=intent.year,
        )
        sql_rows: list[dict] = []
        sql_status = "skipped"
        try:
            sql_result = execute_safe(question, client=client)
            sql_rows = sql_result.rows
            sql_status = "ok"
        except Exception as exc:
            sql_status = f"error: {exc}"

        parts: list[str] = []
        if rag_result.status == "ok" and rag_result.data:
            parts.append(f"**Từ báo cáo tài chính:**\n{rag_result.data}")
        if sql_rows:
            parts.append(f"**Số liệu từ DB:**\n{_format_rows(sql_rows)}")

        if not parts:
            return ToolResult(status="no_data", data=None,
                              message="Không tìm thấy dữ liệu từ cả RAG lẫn SQL.")

        return ToolResult(
            status="ok",
            data="\n\n".join(parts),
            message=f"RAG: {rag_result.status} | SQL: {sql_status}",
        )

    if intent.tool == "news":
        return _fetch_news(intent.tickers, intent.sector)

    if intent.tool == "report_and_news":
        single_ticker = intent.tickers[0] if len(intent.tickers) == 1 else None

        # Reframe for BCTC: strip time-specific words so RAG retrieves fundamentals
        _time_words = ("hôm nay", "tuần này", "tháng này", "gần đây", "hiện tại", "mới nhất")
        bctc_question = question
        for w in _time_words:
            bctc_question = bctc_question.replace(w, "").strip()
        if not bctc_question:
            bctc_question = question

        # Derive macro sector from DB if intent.sector is None (ticker-based query)
        macro_sector = intent.sector
        if macro_sector is None and single_ticker:
            macro_sector = _lookup_sector(single_ticker)

        # Run all sources in parallel
        futures: dict[str, any] = {}
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures["rag"] = pool.submit(
                ask_report,
                question=bctc_question,
                tickers=intent.tickers or None,
                sector=intent.sector,
                year=intent.year,
            )
            futures["news"] = pool.submit(_fetch_news, intent.tickers, intent.sector)
            futures["macro"] = pool.submit(_fetch_macro_drivers, macro_sector)
            if single_ticker:
                futures["price_ind"] = pool.submit(_fetch_price_and_indicators, single_ticker)

        rag_result = futures["rag"].result()
        news_result = futures["news"].result()
        macro_result = futures["macro"].result()
        price_text, ind_text = (
            futures["price_ind"].result() if "price_ind" in futures else (None, None)
        )

        parts: list[str] = []
        if price_text:
            parts.append(f"**Giá hiện tại:**\n{price_text}")
        if ind_text:
            parts.append(f"**Chỉ báo kỹ thuật:**\n{ind_text}")
        if macro_result.status == "ok" and macro_result.data:
            parts.append(f"**Vĩ mô & Hàng hóa:**\n{macro_result.data}")
        if rag_result.status == "ok" and rag_result.data:
            parts.append(f"**Từ báo cáo tài chính:**\n{rag_result.data}")
        if news_result.status == "ok" and news_result.data:
            parts.append(f"**Tin tức gần đây:**\n{news_result.data}")

        if not parts:
            return ToolResult(status="no_data", data=None,
                              message="Không tìm thấy dữ liệu từ cả BCTC lẫn tin tức.")

        status_msg = (
            f"RAG: {rag_result.status} | News: {news_result.status} | Macro: {macro_result.status}"
            + (f" | Price: {'ok' if price_text else 'miss'}" if single_ticker else "")
        )
        return ToolResult(status="ok", data="\n\n".join(parts), message=status_msg)

    # Unknown tool — fallback to ask_report
    return ask_report(question=question)


def _format_rows(rows: list[dict]) -> str:
    if not rows:
        return "(không có dữ liệu)"
    headers = list(rows[0].keys())
    lines = [" | ".join(headers)]
    lines.append("-" * len(lines[0]))
    for row in rows[:20]:
        lines.append(" | ".join(str(row.get(h, "")) for h in headers))
    if len(rows) > 20:
        lines.append(f"... ({len(rows) - 20} rows nữa)")
    return "\n".join(lines)
