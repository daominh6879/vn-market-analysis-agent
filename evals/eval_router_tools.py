#!/usr/bin/env python3
"""
evals/eval_router_tools.py — Router + tool-wiring accuracy across ALL intents.

Two things checked per case, both against the real pipeline (no mocks):
  1. classify_hybrid() resolves the expected intent + ticker.
  2. The tool wired to that intent in agents.graph._get_gather_map() actually
     runs and returns usable data (not an error/empty marker).

Cases are the CLASSIFY_CASES table from tests/test_router_intents_cache.py —
one sweep across every intent the router supports (price_action,
technical_analysis, valuation, rag_qa, macro_sector, news_sentiment,
investment_case, screening, breakout_scan, market_brief, conversation).

RAGAS content-quality metrics (faithfulness/relevancy/precision/recall) are a
separate concern, scoped to rag_qa only — see evals/eval_rag_qa_ragas.py and
docs/notes/eval-rag-qa-ragas.md. This script answers a different question:
"did the router pick the right intent+ticker, and did the right tool run" —
routing/tool-wiring correctness, not answer quality.

Usage:
    python evals/eval_router_tools.py
    python evals/eval_router_tools.py --out evals/router_tools_eval.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

_SKIP = object()  # "don't assert ticker" (router may or may not extract one)

# (intent, question, ticker) — mirrors tests/test_router_intents_cache.py's
# CLASSIFY_CASES, one sweep per intent the router supports. Kept as a literal
# copy (not imported from tests/) so this eval doesn't depend on the test
# module's import side effects (stdout reconfigure, load_dotenv override).
CLASSIFY_CASES: list[tuple[str, str, object]] = [
    ("price_action",       "Khối ngoại mua bán ròng HPG hôm nay thế nào?", "HPG"),
    ("price_action",       "Khối lượng giao dịch HPG có đột biến không?", _SKIP),
    ("technical_analysis", "RSI và MACD của FPT đang như thế nào?",       "FPT"),
    ("technical_analysis", "Vùng hỗ trợ kháng cự của VNM ở đâu?",         "VNM"),
    ("technical_analysis", "Phân tích kỹ thuật HPG hôm nay",              "HPG"),
    ("valuation",          "P/E của HPG hiện tại so với trung bình ngành thế nào?", "HPG"),
    ("valuation",          "ROE của VCB năm ngoái là bao nhiêu?",         _SKIP),
    ("rag_qa",             "Doanh thu HPG năm 2024 là bao nhiêu?",        "HPG"),
    ("rag_qa",             "Doanh thu và lợi nhuận HPG năm 2024 là bao nhiêu?", "HPG"),
    ("macro_sector",       "Tỷ giá USD/VND hôm nay ảnh hưởng gì đến FPT?", _SKIP),
    ("macro_sector",       "Giá thép HRC thế giới tăng ảnh hưởng HPG thế nào?", _SKIP),
    ("news_sentiment",     "Tin tức về HPG trong 3 ngày gần nhất",        "HPG"),
    ("news_sentiment",     "Sentiment của cộng đồng về VNM như thế nào?", _SKIP),
    ("investment_case",    "HPG có nên mua không?",                       "HPG"),
    ("investment_case",    "Khuyến nghị VCB lúc này: mua bán hay nắm giữ?", "VCB"),
    ("screening",          "Lọc cổ phiếu có ROE > 20%",                   _SKIP),
    ("breakout_scan",      "Quét cổ phiếu đang breakout tạo đỉnh mới",    None),
    ("market_brief",       "Thị trường chứng khoán hôm nay thế nào?",     None),
    ("conversation",       "Xin chào bạn tên gì",                         None),
]


def _check_tool(intent: str, ticker: str | None, question: str, gather_map: dict) -> tuple[bool, str]:
    """Run the tool wired to `intent` in agents.graph._get_gather_map(). Returns
    (ran_ok, preview). market_brief/conversation have no real tool — trivially ok.

    NOT using agents.graph._is_empty_result() here: that function does substring
    containment ("không có dữ liệu" anywhere in the string → empty), which is right
    for its own job (deciding whether a sub-task result is worth keeping before
    replan) but wrong for "did the tool run and produce useful data" — a gather
    result can legitimately contain one "no data for X" clause (e.g. "Ngành: Không
    có dữ liệu ngành.") while the rest of the string carries real data (tỷ giá,
    hàng hóa, giá cổ phiếu...). _is_empty_result would flag the whole thing as
    empty — a false positive for this check (confirmed by manual repro: see
    docs/notes/eval-router-tools.md). This checks instead whether the output is
    substantial AND doesn't consist ENTIRELY of an error/no-data template.
    """
    if intent in ("market_brief", "conversation"):
        return True, "(no tool — direct/static path)"

    fn = gather_map.get(intent)
    if fn is None:
        return False, "no gather fn wired for this intent"

    try:
        if intent == "screening":
            out = fn(ticker or "", question, None, sf=[{"indicator": "roe", "op": ">", "threshold": 20}])
        elif intent in ("macro_sector", "breakout_scan"):
            out = fn(ticker or "", question)
        else:
            if not ticker:
                return False, "SKIPPED — router didn't resolve a ticker, can't call ticker-scoped tool"
            out = fn(ticker, question)
    except Exception as exc:
        return False, f"tool raised: {exc}"

    stripped = (out or "").strip()
    whole_string_is_error = stripped.lower().startswith((
        "lỗi:", "không có dữ liệu", "no_data", "không thể lấy", "ticker không được rỗng",
    ))
    if not stripped or whole_string_is_error:
        return False, f"tool ran but returned empty/error: {stripped[:200]!r}"
    return True, stripped[:150]


def main() -> None:
    parser = argparse.ArgumentParser(description="Router + tool-wiring accuracy, all intents")
    parser.add_argument("--out", default="evals/router_tools_eval.json")
    args = parser.parse_args()

    from agents.classifier import classify_hybrid
    from agents.graph import _get_gather_map

    gather_map = _get_gather_map()

    results = []
    n_intent_ok = n_ticker_ok = n_tool_ok = 0

    for expected_intent, question, expected_ticker in CLASSIFY_CASES:
        print(f"  [{expected_intent:20s}] {question[:45]:45s} ", end="", flush=True)
        route = classify_hybrid(question)

        intent_ok = route.intent == expected_intent
        ticker_ok = expected_ticker is _SKIP or route.ticker == expected_ticker
        n_intent_ok += int(intent_ok)
        n_ticker_ok += int(ticker_ok)

        tool_ok, tool_note = False, "SKIPPED — intent mismatch, not calling tool"
        if intent_ok:
            tool_ok, tool_note = _check_tool(expected_intent, route.ticker, question, gather_map)
            n_tool_ok += int(tool_ok)

        mark = lambda b: "OK" if b else "FAIL"
        print(f"intent={mark(intent_ok)} ticker={mark(ticker_ok)} tool={mark(tool_ok)}")

        results.append({
            "expected_intent": expected_intent, "question": question, "expected_ticker": str(expected_ticker),
            "got_intent": route.intent, "got_ticker": route.ticker,
            "intent_ok": intent_ok, "ticker_ok": ticker_ok, "tool_ok": tool_ok, "tool_note": tool_note,
        })

    n = len(CLASSIFY_CASES)
    print(f"\nintent accuracy: {n_intent_ok}/{n} = {n_intent_ok/n:.1%}")
    print(f"ticker accuracy: {n_ticker_ok}/{n} = {n_ticker_ok/n:.1%}")
    print(f"tool     ok:     {n_tool_ok}/{n} = {n_tool_ok/n:.1%}")

    fails = [r for r in results if not (r["intent_ok"] and r["ticker_ok"] and r["tool_ok"])]
    if fails:
        print(f"\n{len(fails)} case(s) with at least one mismatch:")
        for r in fails:
            print(f"  [{r['expected_intent']}] {r['question'][:50]!r} — "
                  f"intent={r['got_intent']} ticker={r['got_ticker']} tool_note={r['tool_note']}")

    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved -> {args.out}")


if __name__ == "__main__":
    main()
