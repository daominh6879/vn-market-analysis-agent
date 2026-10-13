"""Compare the ensemble (and llm_route) against the gold seed, row by row.

    python intent_model/parity.py                    # uses cached LLM answers if present
    python intent_model/parity.py --refresh          # call llm_route again

Follow-up rows (prior_intent set) are excluded: they need conversation context, which a
single-turn model and a history-less llm_route call do not have.
"""
import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
from agents.classifier import INTENTS  # noqa: E402
from common import ARTIFACTS, DATA, read_jsonl  # noqa: E402

MODELS = tuple(["probs_lr_bge-m3"] + sorted(p.stem for p in ARTIFACTS.glob("probs_ft_*.npz")))
CACHE = ARTIFACTS / "llm_route_seed.json"


def llm_answers(seed, refresh):
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() and not refresh else {}
    todo = [r["query"] for r in seed if r["query"] not in cache]
    if todo:
        from agents.conversation_router import llm_route
        from llm.factory import create_client
        from memory.turn_handler import _BASE_SYSTEM
        client = create_client()

        def one(q):
            t0 = time.time()
            try:
                r = llm_route(q, [], _BASE_SYSTEM, client)
                intent = r.get("intent") or ("out_of_scope" if r.get("type") == "text" and "out_of_scope" in str(r.get("reason", "")) else "conversation")
                return q, {"intent": intent, "type": r.get("type"), "reason": str(r.get("reason", ""))[:80], "sec": time.time() - t0}
            except Exception as exc:  # noqa: BLE001
                return q, {"intent": None, "type": None, "reason": f"raised {exc!r}"[:80], "sec": time.time() - t0}

        with ThreadPoolExecutor(4) as ex:
            for q, a in ex.map(one, todo):
                cache[q] = a
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return cache


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--threshold", type=float, default=0.8)
    args = ap.parse_args()

    seed = read_jsonl(DATA / "seed.jsonl")
    P = np.mean([np.load(ARTIFACTS / f"{m}.npz")["gold"] for m in MODELS], 0)
    llm = llm_answers(seed, args.refresh)
    keep = [i for i, r in enumerate(seed) if not r.get("prior_intent")]
    print(f"gold seed {len(seed)} rows, {len(seed) - len(keep)} follow-ups excluded -> {len(keep)} single-turn rows")

    pred = [INTENTS[P[i].argmax()] for i in keep]
    conf = np.array([P[i].max() for i in keep])
    gold = [seed[i]["label"] for i in keep]
    lp = [llm[seed[i]["query"]]["intent"] for i in keep]
    m_ok = np.array([p == g for p, g in zip(pred, gold)])
    l_ok = np.array([p == g for p, g in zip(lp, gold)])
    cov = conf >= args.threshold
    secs = [llm[seed[i]["query"]]["sec"] for i in keep]
    print(f"ensemble acc={m_ok.mean():.3f} | llm_route acc={l_ok.mean():.3f} | llm latency mean={np.mean(secs):.1f}s p95={np.percentile(secs, 95):.1f}s")
    print(f"@{args.threshold}: covered {cov.mean():.0%} ({cov.sum()} rows) | ensemble acc on covered={m_ok[cov].mean():.3f} | "
          f"llm_route acc on the same rows={l_ok[cov].mean():.3f} | agree on covered={np.mean([pred[j] == lp[j] for j in np.where(cov)[0]]):.3f}")
    print(f"not covered ({(~cov).sum()} rows) -> go to LLM: llm acc there={l_ok[~cov].mean():.3f}; cascade acc overall="
          f"{np.where(cov, m_ok, l_ok).mean():.3f}")

    print("\n== ensemble wrong (conf, query, pred vs gold, llm) ==")
    for j in np.argsort(-conf):
        if not m_ok[j]:
            i = keep[j]
            top2 = np.argsort(-P[i])[:2]
            print(f"  {conf[j]:.2f} {'COVERED ' if cov[j] else '        '}{seed[i]['query'][:60]!r}: pred={pred[j]} gold={gold[j]} "
                  f"(2nd={INTENTS[top2[1]]}) | llm={lp[j]} [{seed[i]['quality']}/{seed[i]['source']}]")
    print("\n== llm_route wrong ==")
    for j in range(len(keep)):
        if not l_ok[j]:
            i = keep[j]
            print(f"  {seed[i]['query'][:60]!r}: llm={lp[j]} gold={gold[j]} | ensemble={pred[j]} ({conf[j]:.2f}) [{seed[i]['quality']}/{seed[i]['source']}]")


if __name__ == "__main__":
    main()
