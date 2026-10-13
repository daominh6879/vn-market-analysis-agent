"""Compare every routing field (not just intent) of the fine-tuned model and llm_route on the gold seed.

    python intent_model/fields_parity.py [--model multilingual-e5-base] [--refresh]

Fields: agent-vs-text route, language, tickers, time_context, market/macro/rag topics, risk flags.
Model side = the fine-tuned heads in artifacts/ft/<model>; tickers come from agents.focus, the same
deterministic extractor llm_route itself uses (so that column only checks the label, not the LLM).
Follow-up rows (prior_intent set) are excluded. The full llm_route output is cached.
"""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
from common import ARTIFACTS, DATA, build_time, read_jsonl, resolve_time  # noqa: E402
from finetune import KINDS, LANGS, MULTI, ROUTES, TIME_UNITS, build_model, multi_preds, predict  # noqa: E402

CACHE = ARTIFACTS / "llm_route_full.json"
KEYS = ("type", "intent", "tickers", "market_topics", "macro_topics", "rag_topics", "risk_flags", "language", "time_context", "reason")


def llm_full(seed, refresh):
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() and not refresh else {}
    todo = [r["query"] for r in seed if r["query"] not in cache]
    if todo:
        from agents.conversation_router import llm_route
        from llm.factory import create_client
        from memory.turn_handler import _BASE_SYSTEM
        client = create_client()

        def one(q):
            try:
                r = llm_route(q, [], _BASE_SYSTEM, client)
                return q, {k: r.get(k) for k in KEYS}
            except Exception as exc:  # noqa: BLE001
                return q, {"type": None, "reason": f"raised {exc!r}"[:80]}

        with ThreadPoolExecutor(4) as ex:
            for q, a in ex.map(one, todo):
                cache[q] = a
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return cache


def gold_route(label):
    return "agent" if label not in ("conversation", "out_of_scope") else "non-agent"


def main():
    import torch
    from transformers import AutoTokenizer
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="multilingual-e5-base")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    seed = [r for r in read_jsonl(DATA / "seed.jsonl") if not r.get("prior_intent")]
    llm = llm_full(seed, args.refresh)
    d = ARTIFACTS / "ft" / args.model
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    tok = AutoTokenizer.from_pretrained(d)
    net = build_model(meta["model"])
    net.load_state_dict(torch.load(d / "model.pt"))
    P = predict(net, tok, [r["query"] for r in seed], meta["prefix"])
    mp = multi_preds(P["multi"])
    from agents.focus import extract_focus_entities
    today = date.today()
    gold_rows = [i for i, r in enumerate(seed) if r["quality"] == "gold"]
    print(f"{len(seed)} single-turn rows, {len(gold_rows)} gold-quality (used for tickers/topics/flags)")

    def L(i):
        return llm[seed[i]["query"]]

    def table(name, idxs, gold_of, model_of, llm_of):
        g = [gold_of(i) for i in idxs]
        m = [model_of(i) for i in idxs]
        l = [llm_of(i) for i in idxs]
        n = len(idxs)
        print(f"{name:14s} n={n:3d} | model vs gold {sum(a == b for a, b in zip(m, g)) / n:.3f} | llm vs gold {sum(a == b for a, b in zip(l, g)) / n:.3f} "
              f"| model vs llm {sum(a == b for a, b in zip(m, l)) / n:.3f}")
        return g, m, l

    allrows = list(range(len(seed)))
    table("agent-vs-text", allrows, lambda i: gold_route(seed[i]["label"]),
          lambda i: "agent" if ROUTES[P["route"][i].argmax()] == "agent" else "non-agent",
          lambda i: "agent" if L(i).get("type") in ("agent", "mixed") else "non-agent")
    table("language", gold_rows, lambda i: seed[i]["language"], lambda i: LANGS[P["lang"][i].argmax()],
          lambda i: L(i).get("language") or "vi")
    table("tickers", gold_rows, lambda i: frozenset(seed[i]["tickers"]),
          lambda i: frozenset(extract_focus_entities(seed[i]["query"])), lambda i: frozenset(L(i).get("tickers") or []))

    t_idx = [i for i, r in enumerate(seed) if r["time"] is not None]

    def as_tuple(r):
        return (r["start_date"], r["end_date"], r["explicit"])

    def llm_time(i):
        tc = L(i).get("time_context") or {}
        return (tc.get("start_date"), tc.get("end_date") or today.isoformat(), bool(tc.get("explicit")))

    def model_time(i):
        t = build_time(seed[i]["query"], KINDS[P["kind"][i].argmax()], TIME_UNITS[P["unit"][i].argmax()])
        return as_tuple(resolve_time(t, today))

    g, m, l = table("time_context", t_idx, lambda i: as_tuple(resolve_time(seed[i]["time"], today)), model_time, llm_time)
    print(f"{'':14s} explicit flag only: model {np.mean([a[2] == b[2] for a, b in zip(m, g)]):.3f} | llm {np.mean([a[2] == b[2] for a, b in zip(l, g)]):.3f}")
    for i, a, b, c in zip(t_idx, g, m, l):
        if a != b or a != c:
            print(f"   time {seed[i]['query'][:50]!r}: gold={a[:2]} model={b[:2]} llm={c[:2]}")

    for name, vocab in MULTI.items():
        table(name, gold_rows, lambda i, name=name: frozenset(seed[i][name]),
              lambda i, name=name, vocab=vocab: frozenset(v for v, f in zip(vocab, mp[name][i]) if f),
              lambda i, name=name: frozenset(L(i).get(name) or []))

    print("\n== topic/flag disagreements (gold-quality rows) ==")
    shown = 0
    for i in gold_rows:
        for name, vocab in MULTI.items():
            gs = frozenset(seed[i][name])
            ms = frozenset(v for v, f in zip(vocab, mp[name][i]) if f)
            ls = frozenset(L(i).get(name) or [])
            if ms != gs or ls != gs:
                print(f"  {name:13s} {seed[i]['query'][:55]!r}: gold={sorted(gs)} model={sorted(ms)} llm={sorted(ls)}")
                shown += 1
    print(f"({shown} field disagreements)")


if __name__ == "__main__":
    main()
