"""Label every (filtered) synthetic query with the live router, as a teacher.

    python intent_model/teacher.py [--workers 6]

Writes artifacts/teacher_synth.json {query: {type, intent, market_topics, macro_topics, rag_topics,
risk_flags, language, reason}}. Resumable: queries already in the file are skipped.
common.load_synthetic() then keeps only rows where the generator label and the teacher intent agree and
takes topics/flags from the teacher, so the trained model imitates llm_route.
"""
import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
from common import ARTIFACTS, DATA, TEACHER_FILE, load_synthetic, read_jsonl  # noqa: E402

KEYS = ("type", "intent", "market_topics", "macro_topics", "rag_topics", "risk_flags", "language", "reason")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    from agents.conversation_router import llm_route
    from llm.factory import create_client
    from memory.turn_handler import _BASE_SYSTEM

    seed = read_jsonl(DATA / "seed.jsonl")
    rows = load_synthetic((r["query"] for r in seed), teacher=False)
    cache = json.loads(TEACHER_FILE.read_text(encoding="utf-8")) if TEACHER_FILE.exists() else {}
    todo = [r["query"] for r in rows if r["query"] not in cache]
    print(f"teacher: {len(rows)} rows, {len(todo)} to label", flush=True)
    client = create_client()
    lock = threading.Lock()
    done = [0]

    def one(q):
        try:
            r = llm_route(q, [], _BASE_SYSTEM, client)
            out = {k: r.get(k) for k in KEYS}
            if r.get("type") == "text" and not out["intent"]:
                out["intent"] = "out_of_scope" if "out_of_scope" in str(r.get("reason", "")) else "conversation"
        except Exception as exc:  # noqa: BLE001
            return q, None
        return q, out

    ARTIFACTS.mkdir(exist_ok=True)
    with ThreadPoolExecutor(args.workers) as ex:
        for q, out in ex.map(one, todo):
            if out is None:
                continue
            with lock:
                cache[q] = out
                done[0] += 1
                if done[0] % 200 == 0:
                    TEACHER_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
                    print(f"  {done[0]}/{len(todo)}", flush=True)
    TEACHER_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    print(f"saved {len(cache)} teacher labels -> {TEACHER_FILE}")


if __name__ == "__main__":
    main()
