#!/usr/bin/env python3
"""
evals/eval_multiturn_routing.py — follow-up turns with the keyword carry-forward rules removed.

Real LLM. Turn 1 is routed normally; its result becomes the Focus (same fields turn_handler
stores) and turn 2 is routed with that focus + history. The router LLM alone must decide
whether turn 2 inherits the prior intent/subject or classifies fresh.

Usage:  python evals/eval_multiturn_routing.py [--out evals/multiturn_routing_eval.json] [--min-pass 0.9]
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

# (id, turn1, turn2, expectations: intent, tickers_all (must be present), tickers_none (must be absent))
CASES = [
    ("deepen_comparison", "so sánh P/E của BID và CTG", "phân tích sâu hơn",
     {"intent": "valuation", "tickers_all": ["BID", "CTG"]}),
    ("deepen_single", "phân tích kỹ thuật HPG", "chi tiết hơn",
     {"intent": "technical_analysis", "tickers_all": ["HPG"]}),
    ("deepen_sector", "phân tích ngành ngân hàng", "phân tích sâu hơn",
     {"intent": "macro_sector"}),
    ("new_action_same_subject", "P/E của VCB", "giá cổ phiếu hôm nay?",
     {"intent": "price_action", "tickers_all": ["VCB"]}),
    ("new_ticker_not_inherited", "phân tích kỹ thuật HPG", "còn VNM thì sao?",
     {"tickers_all": ["VNM"], "tickers_none": ["HPG"]}),
    ("continue_with_new_ticker", "phân tích kỹ thuật HPG", "phân tích thêm FPT",
     {"tickers_all": ["FPT"], "tickers_none": ["HPG"]}),
]


def _focus_from(route: dict, turn1: str):
    from agents.focus import Focus
    return Focus(tickers=list(route.get("tickers") or []), sector=route.get("sector") or "",
                 intent=route.get("intent") or "", query=route.get("query") or turn1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="evals/multiturn_routing_eval.json")
    ap.add_argument("--min-pass", type=float, default=0.9)
    args = ap.parse_args()

    from agents.conversation_router import llm_route
    from llm.factory import create_client
    from memory.turn_handler import _BASE_SYSTEM

    client = create_client()
    results = []
    for cid, t1, t2, exp in CASES:
        print(f"  [{cid:26s}] {t1!r} -> {t2!r} ", end="", flush=True)
        errs: list[str] = []
        try:
            r1 = llm_route(t1, [], _BASE_SYSTEM, client)
            history = [{"role": "user", "content": t1}, {"role": "assistant", "content": "(đã trả lời)"}]
            r2 = llm_route(t2, history, _BASE_SYSTEM, client, focus=_focus_from(r1, t1))
            if r2.get("type") != "agent":
                errs.append(f"turn2 type={r2.get('type')!r}")
            else:
                if "intent" in exp and r2.get("intent") != exp["intent"]:
                    errs.append(f"intent={r2.get('intent')!r} want {exp['intent']!r}")
                got = set(r2.get("tickers") or [])
                errs += [f"missing ticker {t}" for t in exp.get("tickers_all", []) if t not in got]
                errs += [f"leaked ticker {t}" for t in exp.get("tickers_none", []) if t in got]
        except Exception as exc:
            r1, r2, errs = {}, {}, [f"raised {exc!r}"]
        print("OK" if not errs else "FAIL " + "; ".join(errs))
        results.append({"id": cid, "turn1": t1, "turn2": t2, "errors": errs,
                        "turn1_route": {k: r1.get(k) for k in ("intent", "tickers", "sector")},
                        "turn2_route": {k: r2.get(k) for k in ("type", "intent", "tickers", "sector", "query")}})

    n_ok = sum(1 for r in results if not r["errors"])
    rate = n_ok / len(results)
    print(f"\nmulti-turn routing: {n_ok}/{len(results)} = {rate:.1%} (min {args.min_pass:.0%})")
    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return 0 if rate >= args.min_pass else 1


if __name__ == "__main__":
    sys.exit(main())
