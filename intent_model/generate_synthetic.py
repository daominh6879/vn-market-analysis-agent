"""Generate synthetic ANNOTATED queries per intent with the project LLM -> data/synthetic.jsonl.

Each row carries the full routing target the live router produces, not just the intent:
  intent, route_type, tickers (+ surface text), time (relative/absolute + surface text),
  screening filters (indicator/op/threshold + surface text), market/macro/rag topics,
  risk_flags, language.

Time is labelled anchor-independent (kind/n/unit or year/quarter); `resolve_time` in common.py
turns it into start/end dates for a given "today", following the router's own convention.

Uses the llm factory (never a direct client). Run from the repo root:
    python intent_model/generate_synthetic.py --per-intent 120
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from agents.classifier import INTENTS, RAG_TOPICS, RISK_FLAGS  # noqa: E402
from common import (  # noqa: E402
    DATA, FILTER_INDICATORS, FILTER_OPS, MACRO_TOPICS, MARKET_TOPICS, TIME_UNITS, read_jsonl, write_jsonl,
)
from llm import create_client  # noqa: E402
from llm.types import Message  # noqa: E402

DESCRIPTIONS = {
    "price_action": "today's price, % change, volume, money flow, foreign buy/sell of ONE stock; also its price over the last N sessions",
    "technical_analysis": "technical indicators (RSI, MACD, MA, support/resistance, trend) of ONE stock, or a bare ticker",
    "rag_qa": "facts from a company's financial reports (revenue, profit, debt, dividends, a given quarter, ROE/EPS for a SPECIFIC year, one stock's market cap)",
    "valuation": "P/E, P/B, ROE, EPS with no period, valuation vs sector peers, comparing 2+ stocks (so sánh A và B)",
    "macro_sector": "macro (USD/VND, SJC gold, oil, commodities, interest rates) or a sector/industry outlook",
    "news_sentiment": "recent news and sentiment about a stock or the market",
    "investment_case": "buy/sell/hold, thesis, 'phân tích <ticker>' with no specific indicator",
    "screening": "filter/screen stocks by numeric conditions (RSI < 30, P/E < 10, ROE > 20%, sector, volume surge = volume > 1.5)",
    "market_brief": "the full market newsletter / daily overview / outlook of an index as a whole",
    "breakout_scan": "scan for stocks breaking out of a base or making new highs",
    "market_data": "ONE market-wide fact: index level, top gainers/losers, foreign net buy/sell ranking, top liquidity, top market cap, list tickers of a sector",
    "closing_commentary": "end-of-session market commentary ('nhận định cuối phiên', 'tổng kết phiên')",
    "conversation": "ONLY greeting / thanks / acknowledgement / 'what can you do' / trading-rule questions (price limits, T+2, sessions) / unintelligible input",
    "out_of_scope": "crypto, US/foreign stocks, non-VND forex (EUR/USD), poems, code, weather, translation",
}

SCHEMA_HELP = f"""\
Each item is a JSON object:
{{"query": str,
  "tickers": ["HPG", "HSG"] (EVERY stock the query names, uppercase, in order; resolve company names like 'hòa phát'->HPG; [] if none),
  "ticker_text": exact substring of query naming the first stock, or "",
  "time": {{"kind": "none"|"relative"|"absolute", "n": int, "unit": one of {list(TIME_UNITS)},
           "year": int|null, "quarter": 1-4|null, "text": exact substring of query or ""}},
  "filters": [{{"indicator": one of {list(FILTER_INDICATORS)}, "op": one of {list(FILTER_OPS)},
               "threshold": number, "text": exact substring of query}}],
  "market_topics": subset of {list(MARKET_TOPICS)},
  "macro_topics": subset of {list(MACRO_TOPICS)},
  "rag_topics": subset of {list(RAG_TOPICS)},
  "risk_flags": subset of {list(RISK_FLAGS)},
  "language": "vi"|"en"}}
Time rules: no time words ('hiện tại','hôm nay','mới nhất') -> kind none. 'hôm qua'->relative n=1 day;
'tuần trước'->1 week; 'tháng trước'->1 month; 'quý trước'->1 quarter; 'năm ngoái'->1 year;
'5 phiên gần nhất'->n=5 session; 'N ngày/tuần/tháng/năm qua'->relative N unit;
'năm 2024'->absolute year=2024; 'quý 3/2024'->absolute year=2024 quarter=3.
Only fill topics/filters/ticker when they genuinely apply to the query's intent; otherwise empty lists / "".
"""


# Each batch uses a different style so the pool covers how people actually type.
STYLES = (
    "very short chat style, 1-5 words, abbreviations, no punctuation",
    "full polite sentences, formal Vietnamese",
    "no Vietnamese diacritics at all (as typed on a phone), lowercase tickers",
    "English or mixed Vietnamese-English (code-switching)",
    "long messages packing ticker + time range + a specific metric together",
    "vague or ambiguous phrasing with the key noun omitted, implicit subject",
    "typos, slang, teencode, repeated letters",
    "questions naming companies by full name or nickname rather than the ticker",
)

# Boundaries the model confuses; stated in every batch for the intent so labels stay consistent.
CONTRAST = {
    "market_data": "It is MARKET-WIDE: no single stock is the subject (top gainers/losers, index level, ranking, sector list). A question about ONE named stock's price/volume is price_action, NOT this.",
    "price_action": "It is about ONE named stock (or a few named stocks) price/volume/foreign flow. A market-wide ranking or index level is market_data, NOT this.",
    "valuation": "A metric with NO period (current P/E, ROE) or a peer/stock comparison. A metric for a specific year/quarter is rag_qa, NOT this.",
    "rag_qa": "Financial-report facts, often with a specific year/quarter, dividends, one stock's market cap. A current ratio with no period is valuation, NOT this.",
    "technical_analysis": "Indicators/trend/support-resistance of ONE stock, or a bare ticker. A filter across many stocks is screening, NOT this.",
    "investment_case": "Buy/sell/hold or a general 'analyse <stock>' with no specific indicator or metric. An index-wide outlook is market_brief, NOT this.",
    "market_brief": "The full newsletter, or an index as a whole (VN-Index/VN30 outlook, including 'should I buy VN-Index'). One stock is NOT this.",
    "closing_commentary": "End-of-session commentary specifically. The full newsletter is market_brief, a single fact is market_data.",
    "screening": "Filter/scan MANY stocks by numeric criteria, including 'volume surge' across the market. A single stock's indicator is technical_analysis, NOT this.",
    "conversation": "ONLY pure social turns or trading-rule questions (price limits, T+2, sessions) or unintelligible input. NEVER mention any stock, ticker, company, metric or time range - no 'I asked about VCB but actually just want T+2' mixes.",
}


def gen(client, intent: str, n: int, style: str = "") -> list[dict]:
    prompt = (
        f"Generate {n} diverse, realistic user messages to a Vietnamese stock-market assistant whose correct "
        f"routing intent is '{intent}': {DESCRIPTIONS[intent]}.\n"
        + (f"Boundary: {CONTRAST[intent]}\n" if intent in CONTRAST else "")
        + (f"Style for this batch: {style}.\n" if style else "")
        + "Mix: ~75% Vietnamese (with and without diacritics, casual/abbreviated), ~25% English unless the style "
        "says otherwise. Vary tickers (HPG, VCB, FPT, VNM, MWG, SSI, VPB, TCB, MSN, VIC, ... use many different "
        "ones), phrasing, length. About 35% should contain a time expression; about 25% should name a company "
        "by name instead of ticker. Do not repeat.\n\n"
        + SCHEMA_HELP + "\nOutput ONLY a JSON array of such objects."
    )
    resp = client.generate(messages=[Message(role="user", content=prompt)], max_tokens=8000, temperature=0.9)
    m = re.search(r"\[.*\]", resp.text, re.S)
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    return [i for i in items if isinstance(i, dict)]


def _enum_list(v, allowed):
    return [x for x in dict.fromkeys(v or []) if isinstance(x, str) and x in allowed]


def clean(item: dict, intent: str) -> dict | None:
    """Validate/normalise one generated item; None if unusable."""
    q = (item.get("query") or "").strip()
    if not q:
        return None
    t = item.get("time") or {}
    kind = t.get("kind", "none")
    if kind not in ("none", "relative", "absolute"):
        return None
    time = {"kind": kind, "text": (t.get("text") or "") if (t.get("text") or "") in q else ""}
    if kind == "relative":
        if t.get("unit") not in TIME_UNITS or not isinstance(t.get("n"), int) or t["n"] < 1:
            return None
        time.update(n=t["n"], unit=t["unit"])
    elif kind == "absolute":
        if not isinstance(t.get("year"), int):
            return None
        qt = t.get("quarter")
        time.update(year=t["year"], quarter=qt if qt in (1, 2, 3, 4) else None)
    filters = []
    for f in item.get("filters") or []:
        if (isinstance(f, dict) and f.get("indicator") in FILTER_INDICATORS and f.get("op") in FILTER_OPS
                and isinstance(f.get("threshold"), (int, float))):
            filters.append({"indicator": f["indicator"], "op": f["op"], "threshold": f["threshold"],
                            "text": f.get("text") if f.get("text") in q else ""})
    if intent != "screening":
        filters = []  # "P/E < 10" inside a valuation question is not a screening filter
    raw = item.get("tickers")
    if raw is None:  # tolerate the old single-field shape
        raw = [item.get("ticker")] if item.get("ticker") else []
    tickers = [t.strip().upper() for t in raw if isinstance(t, str) and t.strip()]
    if any(not re.fullmatch(r"[A-Z0-9]{2,5}", t) for t in tickers):
        return None
    tickers = list(dict.fromkeys(tickers))
    route_type = {"out_of_scope": "out_of_scope", "conversation": "text"}.get(intent, "agent")
    return {
        "query": q, "label": intent, "route_type": route_type, "prior_intent": None, "source": "synthetic",
        "tickers": tickers, "ticker_text": item.get("ticker_text") if item.get("ticker_text") in q else "",
        "time": time, "filters": filters,
        "market_topics": _enum_list(item.get("market_topics"), MARKET_TOPICS) if intent == "market_data" else [],
        "macro_topics": _enum_list(item.get("macro_topics"), MACRO_TOPICS) if intent == "macro_sector" else [],
        "rag_topics": _enum_list(item.get("rag_topics"), RAG_TOPICS) if intent == "rag_qa" else [],
        "risk_flags": _enum_list(item.get("risk_flags"), RISK_FLAGS),
        "language": item.get("language") if item.get("language") in ("vi", "en") else "vi",
    }


def main():
    from concurrent.futures import ThreadPoolExecutor

    ap = argparse.ArgumentParser()
    ap.add_argument("--per-intent", type=int, default=120, help="new rows to add per intent")
    ap.add_argument("--batch", type=int, default=25)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--intents", nargs="*", help="only these intents (default: all)")
    ap.add_argument("--append", action="store_true", help="keep existing rows and add new ones")
    args = ap.parse_args()
    client = create_client()
    out = DATA / "synthetic.jsonl"
    intents = args.intents or list(INTENTS)
    existing = read_jsonl(out) if out.exists() else []
    keep = existing if args.append else [r for r in existing if r["label"] not in intents]
    seen = {r["query"].lower() for r in keep}
    rows, dropped = list(keep), Counter()

    # (intent, style) batches; a couple of spare batches per intent absorb duplicates/invalid rows.
    n_batches = -(-args.per_intent // args.batch) + 2
    tasks = [(intent, STYLES[(b + len(intent)) % len(STYLES)]) for intent in intents for b in range(n_batches)]

    def run(task):
        intent, style = task
        try:
            return intent, gen(client, intent, args.batch, style)
        except Exception as e:  # noqa: BLE001 - one failed batch must not kill the run
            print(f"  batch failed ({intent}): {type(e).__name__}")
            return intent, []

    got = Counter()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for intent, items in ex.map(run, tasks):
            for item in items:
                row = clean(item, intent)
                if row is None:
                    dropped[intent] += 1
                elif row["query"].lower() not in seen and got[intent] < args.per_intent:
                    seen.add(row["query"].lower())
                    rows.append(row)
                    got[intent] += 1
    for intent in intents:
        print(f"{intent:20s} added={got[intent]} dropped={dropped[intent]}")
        if got[intent] == 0:
            raise RuntimeError(f"no valid queries generated for intent {intent}")
    write_jsonl(out, rows)
    print(f"{len(rows)} rows -> data/synthetic.jsonl")


if __name__ == "__main__":
    main()
