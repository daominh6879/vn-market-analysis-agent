"""
agents/graph.py — LangGraph sequential graph for bài 22.

4 nodes: collect → analyze_technical → assess_risk → synthesize

Design rules:
- state stores only paths, never DataFrames
- risk node: pure if/else, no model call
- synthesize: LLM via create_client() factory
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
from langgraph.graph import END, StateGraph

from agents.state import AgentState
from tools.price import (
    analyze_market_sentiment,
    calculate_indicators,
    get_historical_ohlcv,
    search_financial_news,
)

_CACHE_DIR = Path("outputs/agent_cache")
_VOLATILITY_THRESHOLD = 0.04  # 4% daily return std → HIGH_VOLATILITY


# ── Node 1: collect ────────────────────────────────────────────────────────────

def collect(state: AgentState) -> dict:
    ticker = state["ticker"]
    is_market = state.get("is_market_query", False)
    ohlcv_days = 60 if is_market else 60
    news_days = 1 if is_market else 7

    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_ohlcv = ex.submit(get_historical_ohlcv, ticker, ohlcv_days)
        fut_news = ex.submit(search_financial_news, ticker, news_days)
        ohlcv_result = fut_ohlcv.result()
        news_result = fut_news.result()

    updates: dict = {"step_count": state.get("step_count", 0) + 1}

    if ohlcv_result.status == "ok":
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = str(_CACHE_DIR / f"{ticker}_ohlcv.csv")
        ohlcv_result.data.to_csv(path, index=False)
        updates["price_data_path"] = path
    else:
        updates["error"] = ohlcv_result.message

    updates["news_data"] = (
        news_result.message if news_result.status == "ok"
        else f"[Không có tin tức: {news_result.message}]"
    )
    updates["history"] = state.get("history", []) + [
        {"step": "collect", "ohlcv_status": ohlcv_result.status, "news_status": news_result.status}
    ]
    return updates


# ── Node 2: analyze_technical ─────────────────────────────────────────────────

def analyze_technical(state: AgentState) -> dict:
    path = state.get("price_data_path", "")
    updates: dict = {"step_count": state.get("step_count", 0) + 1}

    if not path or not Path(path).exists():
        updates["tech_signals"] = "Không có dữ liệu giá để tính chỉ báo kỹ thuật."
        return updates

    df = pd.read_csv(path)
    result = calculate_indicators(df)
    updates["tech_signals"] = result.message
    return updates


# ── Node 3: assess_risk ────────────────────────────────────────────────────────

def assess_risk(state: AgentState) -> dict:
    """Pure if/else — no model call."""
    path = state.get("price_data_path", "")
    updates: dict = {"step_count": state.get("step_count", 0) + 1}

    if not path or not Path(path).exists():
        updates["risk_verdict"] = "INSUFFICIENT_DATA"
        return updates

    df = pd.read_csv(path)
    if len(df) < 14:
        updates["risk_verdict"] = "INSUFFICIENT_DATA"
        return updates

    returns = df["close"].tail(14).pct_change().dropna()
    volatility = float(returns.std())

    if volatility > _VOLATILITY_THRESHOLD:
        updates["risk_verdict"] = f"HIGH_VOLATILITY (14-session std={volatility:.2%})"
    else:
        updates["risk_verdict"] = f"OK (14-session std={volatility:.2%})"
        sentiment_result = analyze_market_sentiment(state["ticker"], days=7)
        updates["sentiment"] = (
            sentiment_result.message if sentiment_result.status == "ok" else ""
        )

    return updates


# ── Node 4: synthesize ────────────────────────────────────────────────────────

def synthesize(state: AgentState) -> dict:
    from llm.factory import create_client
    from llm.types import Message

    ticker = state.get("ticker", "")
    is_market = state.get("is_market_query", False)
    subject = "thị trường chứng khoán" if is_market else f"cổ phiếu {ticker}"
    tech = state.get("tech_signals") or "Không có dữ liệu kỹ thuật."
    risk = state.get("risk_verdict") or "Chưa đánh giá."
    news = state.get("news_data") or "Không có tin tức."
    sentiment = state.get("sentiment") or ""
    data_source = "yfinance (^VN30 proxy)" if is_market else "VCI REST API"

    high_vol_warning = (
        "\n⚠️ **CẢNH BÁO:** Biến động cao trong 14 phiên gần nhất. Rủi ro tăng đáng kể.\n"
        if "HIGH_VOLATILITY" in risk else ""
    )

    sentiment_block = f"\n## Sentiment thị trường:\n{sentiment}" if sentiment else ""

    prompt = f"""Dữ liệu phân tích {subject}:

{high_vol_warning}
### Chỉ báo kỹ thuật
{tech}

### Rủi ro
{risk}

### Tin tức
{news}{sentiment_block}

Viết ngay báo cáo Markdown (không có văn bản nào trước báo cáo). Cấu trúc:
# Báo cáo phân tích {ticker}
## Kết luận: [Tích cực / Trung tính / Tiêu cực]
## Kỹ thuật
## Rủi ro
## Tin tức & Sentiment
## Khuyến nghị

Trích nguồn dạng [Nguồn: {data_source}] hoặc [Nguồn: CafeF/Tavily, <ngày>]."""

    t0 = time.perf_counter()
    client = create_client()
    resp = client.generate(
        [Message(role="user", content=prompt)],
        max_tokens=4000,
        system=(
            "Bạn là chuyên gia phân tích tài chính Việt Nam. "
            "Trả lời chỉ bằng báo cáo Markdown, không có văn bản nào trước hoặc sau."
        ),
    )
    elapsed = time.perf_counter() - t0

    return {
        "report": resp.text.strip(),
        "summary": resp.text.strip()[:120],
        "step_count": state.get("step_count", 0) + 1,
        "history": state.get("history", []) + [{
            "step": "synthesize",
            "input_tokens": resp.input_tokens,
            "output_tokens": resp.output_tokens,
            "elapsed_seconds": round(elapsed, 2),
        }],
    }


# ── Graph builder ─────────────────────────────────────────────────────────────

def build_graph() -> "CompiledGraph":
    g = StateGraph(AgentState)
    g.add_node("collect", collect)
    g.add_node("analyze_technical", analyze_technical)
    g.add_node("assess_risk", assess_risk)
    g.add_node("synthesize", synthesize)

    g.set_entry_point("collect")
    g.add_edge("collect", "analyze_technical")
    g.add_edge("analyze_technical", "assess_risk")
    g.add_edge("assess_risk", "synthesize")
    g.add_edge("synthesize", END)

    return g.compile()


def save_graph_image(app, path: str = "agents/graph.png") -> bool:
    """Export graph diagram to PNG. Returns True on success."""
    try:
        app.get_graph().draw_mermaid_png(output_file_path=path)
        return True
    except Exception as e:
        print(f"[graph image] Không xuất được PNG: {e}")
        # Fallback: save mermaid text
        try:
            mermaid_txt = path.replace(".png", ".md")
            Path(mermaid_txt).write_text(
                app.get_graph().draw_mermaid(), encoding="utf-8"
            )
            print(f"[graph image] Đã lưu Mermaid text → {mermaid_txt}")
        except Exception:
            pass
        return False
