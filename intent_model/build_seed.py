"""Build the gold seed set -> data/seed.jsonl.

Sources:
  - evals/routing_parity_eval.json: `expect` fields are gold; where a field is not in `expect`
    the recorded router output (`got`) is used only if the row passed (no errors) -> marked silver.
  - evals/intents_e2e_eval.json, multiturn_routing_eval.json: recorded routes of passing rows (silver).
  - data/seed_manual*.jsonl: hand-written gold with ticker/time/filters/topics/flags (the evals have no time labels).

Fields: label, tickers, time (None = unlabelled, only the manual rows have it), filters,
market/macro/rag topics, risk_flags, language, source, quality (gold|silver).
Review the output by hand before trusting it.
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DATA, read_jsonl, write_jsonl  # noqa: E402

EVALS = Path(__file__).resolve().parents[1] / "evals"


def _load(name):
    return json.loads((EVALS / name).read_text(encoding="utf-8"))


def _row(query, label, source, quality, prior_intent=None, tickers=None, filters=None, market=None,
         macro=None, rag=None, flags=None, language=None):
    return {"query": query, "label": label, "prior_intent": prior_intent, "source": source, "quality": quality,
            "tickers": tickers or [], "time": None, "filters": filters or [], "market_topics": market or [],
            "macro_topics": macro or [], "rag_topics": rag or [], "risk_flags": flags or [], "language": language}


def main():
    rows = []
    for r in _load("routing_parity_eval.json"):
        exp, got = r.get("expect") or {}, r.get("got") or {}
        passed = not r.get("errors")
        label = exp.get("intent") or (got.get("intent") if passed else None)
        if not label:
            label = "conversation" if got.get("type") == "text" else None
        if not label:
            continue
        gold = "intent" in exp
        pick = lambda k_exp, k_got: exp[k_exp] if k_exp in exp else (got.get(k_got) if passed else None)  # noqa: E731
        rows.append(_row(r["query"], label, "parity", "gold" if gold else "silver",
                         tickers=pick("tickers", "tickers"), market=pick("market", "market_topics"),
                         macro=pick("macro", "macro_topics"), rag=pick("rag", "rag_topics"),
                         flags=pick("flags", "risk_flags"), language=pick("language", "language")))
    for r in _load("intents_e2e_eval.json"):
        if r.get("errors") or not r.get("intent"):
            continue
        rows.append(_row(r["query"], r["intent"], "e2e", "silver", tickers=r.get("tickers"),
                         filters=r.get("screening") or [], market=r.get("market_topics"), rag=r.get("rag_topics")))
    for r in _load("multiturn_routing_eval.json"):
        t2 = r.get("turn2_route") or {}
        if r.get("errors") or not t2.get("intent"):
            continue
        rows.append(_row(r["turn2"], t2["intent"], "multiturn", "silver",
                         prior_intent=(r.get("turn1_route") or {}).get("intent"), tickers=t2.get("tickers")))
    for path in sorted(DATA.glob("seed_manual*.jsonl")):
        for r in read_jsonl(path):
            base = _row(r["query"], r["label"], "manual", "gold")
            base["time"] = {"kind": "none"}
            base["language"] = "vi"
            rows.append({**base, **r})

    # de-duplicate by query, preferring gold over silver
    best = {}
    for r in rows:
        k = (r["query"].lower(), r["prior_intent"])
        if k not in best or (best[k]["quality"] == "silver" and r["quality"] == "gold"):
            best[k] = r
    rows = list(best.values())
    write_jsonl(DATA / "seed.jsonl", rows)
    print(f"{len(rows)} rows -> data/seed.jsonl  ({Counter(r['quality'] for r in rows)})")
    print("with time label:", sum(r["time"] is not None for r in rows),
          "| with tickers:", sum(bool(r["tickers"]) for r in rows),
          "| with filters:", sum(bool(r["filters"]) for r in rows))
    for k, v in sorted(Counter(r["label"] for r in rows).items()):
        print(f"  {k:20s} {v}")


if __name__ == "__main__":
    main()
