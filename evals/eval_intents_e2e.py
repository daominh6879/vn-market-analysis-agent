#!/usr/bin/env python3
"""
evals/eval_intents_e2e.py — every intent, production path, real LLM + real tools (no mocks).

Path per case (same as memory.turn_handler.stream_turn, minus DB history):
    llm_route() -> _build_agent_state() -> compiled graph .invoke() -> state["report"]

Checks per case: routed to the expected intent, no clarification, report long enough,
contains at least one expected marker, no internal-name / reasoning leaks.
Full reports are saved so quality can be read by a human (the real acceptance step).

Usage:
    python evals/eval_intents_e2e.py
    python evals/eval_intents_e2e.py --only closing_commentary,screening_rsi
    python evals/eval_intents_e2e.py --out evals/intents_e2e_eval.json --min-pass 0.9
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

# (id, query, expected intent, min report chars, any-of markers (lowercase))
CASES: list[tuple[str, str, str, int, list[str]]] = [
    ("price_action",       "giá và dòng tiền HPG hôm nay",                    "price_action",       80,  ["hpg"]),
    ("price_history",      "5 phiên gần nhất của VIC",                        "price_action",      80,  ["vic"]),
    ("technical",          "phân tích kỹ thuật HPG: RSI, MACD, xu hướng",     "technical_analysis", 150, ["rsi", "macd"]),
    ("rag_qa",             "doanh thu HPG năm 2024 bao nhiêu?",               "rag_qa",            40,  ["doanh thu", "tỷ"]),
    ("rag_dividend",       "Cổ tức của HPG năm nay?",                         "rag_qa",            40,  ["cổ tức"]),
    ("valuation",          "P/E của HPG so với trung bình ngành thép?",       "valuation",         80,  ["p/e", "pe"]),
    ("macro_fx",           "Tỷ giá trung tâm hôm nay",                        "macro_sector",      40,  ["tỷ giá", "usd"]),
    ("macro_gold",         "Giá vàng SJC hôm nay",                            "macro_sector",      40,  ["vàng", "sjc"]),
    ("news",               "tin tức về HPG trong 3 ngày gần nhất",            "news_sentiment",    60,  ["hpg"]),
    ("investment_case",    "HPG có đáng đầu tư không? Bull case và bear case", "investment_case",   200, ["bull", "bear", "rủi ro"]),
    ("screening_rsi",      "lọc cổ phiếu RSI dưới 30",                        "screening",         40,  ["rsi"]),
    ("screening_fund",     "lọc cổ phiếu ngành thép có ROE trên 10",          "screening",         40,  ["roe"]),
    ("volume_spike",       "Cổ phiếu nào có khối lượng đột biến hôm nay?",    "screening",         40,  ["khối lượng", "volume"]),
    ("market_brief",       "Bản tin thị trường hôm nay",                      "market_brief",      200, ["vn-index", "vnindex", "thị trường"]),
    ("market_index",       "VN-Index đóng cửa bao nhiêu điểm?",               "market_data",       30,  ["vn-index", "vnindex", "điểm"]),
    ("market_movers",      "Mã nào tăng mạnh nhất hôm nay?",                  "market_data",       40,  ["tăng"]),
    ("market_foreign",     "Khối ngoại mua ròng mã nào nhiều nhất?",          "market_data",       40,  ["khối ngoại", "ròng"]),
    ("breakout",           "quét cổ phiếu đang breakout tạo đỉnh mới",        "breakout_scan",     40,  ["breakout", "đỉnh"]),
    ("closing_commentary", "Nhận định cuối phiên hôm nay",                    "closing_commentary", 400, ["vn-index", "vnindex"]),
]

# Internal names / reasoning that must never reach the user.
_LEAKS = re.compile(
    r"ohlcv_daily|financial_facts|securities\.|<think>|</think>|DSML|\[SCREENING\]|\[MARKET|"
    r"tool_call|sector_tickers|get_tickers",
    re.IGNORECASE,
)


def _run_case(app, client, base_system, cid, query, want_intent, min_chars, markers):
    from agents.conversation_router import llm_route
    from memory.turn_handler import _build_agent_state

    t0 = time.time()
    route = llm_route(query, [], base_system, client)
    errs: list[str] = []
    rec = {"id": cid, "query": query, "route_type": route.get("type"), "intent": route.get("intent"),
           "tickers": route.get("tickers"), "market_topics": route.get("market_topics"),
           "rag_topics": route.get("rag_topics"), "screening": route.get("screening")}
    if route.get("type") != "agent":
        errs.append(f"route type={route.get('type')!r} (want agent); text={str(route.get('text'))[:120]!r}")
        rec.update(errors=errs, report="", seconds=round(time.time() - t0, 1))
        return rec
    if route.get("intent") != want_intent:
        errs.append(f"intent={route.get('intent')!r} want {want_intent!r}")

    state = _build_agent_state(route, query, f"eval-{cid}", "eval-user", "default", [])
    final = app.invoke(state)
    report = (final.get("report") or "").strip()
    if final.get("needs_clarification"):
        errs.append("needs_clarification")
    if len(report) < min_chars:
        errs.append(f"report too short: {len(report)} < {min_chars}")
    if report and not any(m in report.lower() for m in markers):
        errs.append(f"none of markers {markers} in report")
    leak = _LEAKS.search(report)
    if leak:
        errs.append(f"internal leak: {leak.group(0)!r}")
    if report.lower().startswith("không có dữ liệu"):
        errs.append("empty-data reply (no data for a core intent)")
    rec.update(errors=errs, report=report, final_intent=final.get("intent"), seconds=round(time.time() - t0, 1))
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description="End-to-end per-intent eval (real LLM + tools)")
    ap.add_argument("--only", default="", help="comma-separated case ids")
    ap.add_argument("--out", default="evals/intents_e2e_eval.json")
    ap.add_argument("--min-pass", type=float, default=0.9)
    args = ap.parse_args()

    from agents.graph import build_graph
    from llm.factory import create_client
    from memory.turn_handler import _BASE_SYSTEM

    only = {x.strip() for x in args.only.split(",") if x.strip()}
    cases = [c for c in CASES if not only or c[0] in only]
    client = create_client()
    app = build_graph()

    results = []
    for cid, query, want, min_chars, markers in cases:
        print(f"  [{cid:19s}] {query[:44]:44s} ", end="", flush=True)
        try:
            rec = _run_case(app, client, _BASE_SYSTEM, cid, query, want, min_chars, markers)
        except Exception as exc:  # a crash is a failed case, not a crashed eval
            rec = {"id": cid, "query": query, "errors": [f"raised {exc!r}"], "report": "", "seconds": 0}
        print(("OK" if not rec["errors"] else "FAIL " + "; ".join(rec["errors"])) + f"  ({rec['seconds']}s)")
        results.append(rec)

    n_ok = sum(1 for r in results if not r["errors"])
    rate = n_ok / max(len(results), 1)
    print(f"\nper-intent e2e: {n_ok}/{len(results)} = {rate:.1%} (min {args.min_pass:.0%})")
    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"Saved -> {args.out}")
    return 0 if rate >= args.min_pass else 1


if __name__ == "__main__":
    sys.exit(main())
