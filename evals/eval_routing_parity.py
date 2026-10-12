#!/usr/bin/env python3
"""
evals/eval_routing_parity.py — does the router LLM alone reproduce what the old keyword rules enforced?

Real LLM, no mocks. Every case comes from an assertion that the old keyword rule layer (deleted)
and the guardrails detectors used to guarantee (intent, data topics, risk flags, language,
decline / rule-sheet answers). Runs agents.conversation_router.llm_route() exactly as turn_handler does.

Usage:
    python evals/eval_routing_parity.py
    python evals/eval_routing_parity.py --out evals/routing_parity_eval.json --min-pass 0.9
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

# (id, query, expectations). Keys: type, intent, topics.{market,macro,rag}, flags, no_flags, language,
# tickers, reason_prefix. Topic/flag expectations are "contains all of".
CASES: list[tuple[str, str, dict]] = [
    ("movers",          "Mã nào tăng mạnh nhất hôm nay?",              {"type": "agent", "intent": "market_data", "market": ["movers"]}),
    ("index",           "VN-Index đóng cửa bao nhiêu điểm?",           {"type": "agent", "intent": "market_data", "market": ["index"]}),
    ("foreign_rank",    "Khối ngoại mua ròng mã nào nhiều nhất?",      {"type": "agent", "intent": "market_data", "market": ["foreign"]}),
    ("marketcap_rank",  "Top 5 vốn hóa lớn nhất HOSE",                 {"type": "agent", "intent": "market_data", "market": ["marketcap"]}),
    ("list_sector",     "Liệt kê các mã trong ngành ngân hàng",        {"type": "agent", "intent": "market_data", "market": ["list"]}),
    ("newsletter",      "Bản tin thị trường hôm nay",                  {"type": "agent", "intent": "market_brief"}),
    ("closing",         "Nhận định cuối phiên hôm nay",                {"type": "agent", "intent": "closing_commentary"}),
    ("volume_spike",    "Cổ phiếu nào có khối lượng đột biến hôm nay?", {"type": "agent", "intent": "screening"}),
    ("screen_rsi",      "lọc cổ phiếu RSI dưới 30",                    {"type": "agent", "intent": "screening"}),
    ("rsi_one_stock",   "RSI của VNM là bao nhiêu?",                   {"type": "agent", "intent": "technical_analysis", "tickers": ["VNM"]}),
    ("dividend",        "Cổ tức của HPG năm nay?",                     {"type": "agent", "intent": "rag_qa", "rag": ["dividend"], "tickers": ["HPG"]}),
    ("quarterly",       "Doanh thu quý gần nhất của FPT",              {"type": "agent", "intent": "rag_qa", "rag": ["quarterly"], "tickers": ["FPT"]}),
    ("yearly_metric",   "ROE của VNM năm 2023 và 2024 là bao nhiêu?",  {"type": "agent", "intent": "rag_qa", "rag": ["yearly_metric"], "tickers": ["VNM"]}),
    ("marketcap_one",   "Vốn hóa của VNM là bao nhiêu?",              {"type": "agent", "intent": "rag_qa", "rag": ["marketcap"], "tickers": ["VNM"]}),
    ("history",         "5 phiên gần nhất của VIC",                    {"type": "agent", "intent": "price_action", "tickers": ["VIC"]}),
    ("foreign_one",     "Khối ngoại mua bán ròng HPG hôm nay thế nào?", {"type": "agent", "intent": "price_action", "tickers": ["HPG"]}),
    ("gold",            "Giá vàng SJC hôm nay",                        {"type": "agent", "intent": "macro_sector", "macro": ["gold"]}),
    ("crypto_and_gold", "Giá bitcoin và vàng",                         {"type": ("text", "mixed")}),  # decline the crypto part; a mixed reply that also answers gold is better
    ("rules_limit",     "Biên độ giá của sàn HOSE là bao nhiêu?",      {"type": "text", "reason_prefix": "market_rules"}),
    ("rules_settle",    "T+2 nghĩa là gì?",                            {"type": "text", "reason_prefix": "market_rules"}),
    ("poem",            "Hãy viết cho tôi một bài thơ về mùa thu",     {"type": "text", "not_reason_prefix": "market_rules"}),
    ("weather",         "thời tiết hôm nay thế nào",                   {"type": "text", "not_reason_prefix": "market_rules"}),
    ("greeting",        "xin chào",                                    {"type": "text"}),
    ("risk_personal",   "Tôi nên bỏ toàn bộ tiền tiết kiệm vào HPG không?", {"type": "agent", "flags": ["personal", "advice"]}),
    ("risk_prediction", "Dự đoán giá HPG tuần sau",                    {"type": "agent", "flags": ["prediction"]}),
    ("risk_advice",     "Nên mua HPG hay bán?",                        {"type": "agent", "flags": ["advice"]}),
    ("risk_none",       "Giá HPG hôm nay",                             {"type": "agent", "no_flags": True}),
    ("lang_en",         "What is the price of VCB?",                   {"type": "agent", "language": "en"}),
    ("lang_vi",         "Giá VCB là bao nhiêu?",                       {"type": "agent", "language": "vi"}),
    ("roe_no_period",   "ROE của HPG",                                 {"type": "agent", "intent": "valuation", "tickers": ["HPG"]}),
    ("roe_with_year",   "ROE của HPG năm 2024",                        {"type": "agent", "intent": "rag_qa", "tickers": ["HPG"]}),
    ("analyze_ticker",  "Phân tích HPG",                               {"type": "agent", "intent": "investment_case", "tickers": ["HPG"]}),
    ("buy_en",          "Should I buy HPG tomorrow?",                  {"type": "agent", "intent": "investment_case", "language": "en", "flags": ["advice"]}),
    ("index_buy",       "VN30 có nên mua không",                       {"type": "agent", "intent": "market_brief"}),
    ("recap",           "Tổng kết phiên hôm nay",                      {"type": "agent", "intent": "closing_commentary"}),
    ("bitcoin_label",   "Bitcoin giá hôm nay",                         {"type": "text", "intent": "out_of_scope"}),
    ("poem_label",      "Viết thơ về chứng khoán",                     {"type": "text", "intent": "out_of_scope"}),
]


def _check(route: dict, exp: dict) -> list[str]:
    errs: list[str] = []
    want_types = exp["type"] if isinstance(exp["type"], tuple) else (exp["type"],)
    if route.get("type") not in want_types:
        return [f"type={route.get('type')!r} want {exp['type']!r}"]
    if "intent" in exp and route.get("intent") != exp["intent"]:
        errs.append(f"intent={route.get('intent')!r} want {exp['intent']!r}")
    for key, field in (("market", "market_topics"), ("macro", "macro_topics"), ("rag", "rag_topics"), ("flags", "risk_flags")):
        missing = [x for x in exp.get(key, []) if x not in (route.get(field) or [])]
        if missing:
            errs.append(f"{field}={route.get(field)} missing {missing}")
    if exp.get("no_flags") and route.get("risk_flags"):
        errs.append(f"risk_flags={route.get('risk_flags')} want []")
    if "language" in exp and route.get("language") != exp["language"]:
        errs.append(f"language={route.get('language')!r} want {exp['language']!r}")
    for t in exp.get("tickers", []):
        if t not in (route.get("tickers") or []):
            errs.append(f"tickers={route.get('tickers')} missing {t}")
    if "reason_prefix" in exp and not str(route.get("reason", "")).startswith(exp["reason_prefix"]):
        errs.append(f"reason={route.get('reason')!r} want prefix {exp['reason_prefix']!r}")
    if "not_reason_prefix" in exp and str(route.get("reason", "")).startswith(exp["not_reason_prefix"]):
        errs.append(f"reason={route.get('reason')!r} must not start with {exp['not_reason_prefix']!r}")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM-only routing parity with the removed keyword rules")
    ap.add_argument("--out", default="evals/routing_parity_eval.json")
    ap.add_argument("--min-pass", type=float, default=0.9)
    args = ap.parse_args()

    from agents.conversation_router import llm_route
    from llm.factory import create_client
    from memory.turn_handler import _BASE_SYSTEM

    client = create_client()
    results = []
    for cid, query, exp in CASES:
        print(f"  [{cid:16s}] {query[:46]:46s} ", end="", flush=True)
        try:
            route = llm_route(query, [], _BASE_SYSTEM, client)
            errs = _check(route, exp)
        except Exception as exc:  # a crashing route is a failed case, not a crashed eval
            route, errs = {}, [f"raised {exc!r}"]
        print("OK" if not errs else "FAIL " + "; ".join(errs))
        results.append({"id": cid, "query": query, "expect": exp, "errors": errs,
                        "got": {k: route.get(k) for k in ("type", "intent", "tickers", "market_topics", "macro_topics",
                                                         "rag_topics", "risk_flags", "language", "reason")}})

    n_ok = sum(1 for r in results if not r["errors"])
    rate = n_ok / len(results)
    print(f"\nrouting parity: {n_ok}/{len(results)} = {rate:.1%} (min {args.min_pass:.0%})")
    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"Saved -> {args.out}")
    return 0 if rate >= args.min_pass else 1


if __name__ == "__main__":
    sys.exit(main())
