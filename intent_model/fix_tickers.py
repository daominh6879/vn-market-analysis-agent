"""Re-label `tickers` in data/synthetic.jsonl with the app's own deterministic extractor.

The generating LLM's ticker labels are unreliable (blank for multi-ticker and many single-ticker
queries), so `tickers` := agents.focus.extract_focus_entities(query) — the same function the live
router uses to override the LLM. The generator's original guess is kept as `gen_tickers` and the
disagreements are printed so bad rows/prompts can be seen.

    python intent_model/fix_tickers.py            # rewrite synthetic.jsonl
    python intent_model/fix_tickers.py --dry-run  # report only
Run after generate_synthetic.py (which rewrites the whole file when it finishes).
"""
import argparse
import functools
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import core.tickers as ct  # noqa: E402
from agents.focus import extract_focus_entities  # noqa: E402
from common import DATA, read_jsonl, write_jsonl  # noqa: E402

# The extractor hits Postgres on every call; the alias index and ticker list are static for this run.
ct._load_name_index = functools.lru_cache(maxsize=None)(ct._load_name_index)
ct.get_tickers = functools.lru_cache(maxsize=None)(ct.get_tickers)

SINGLE_STOCK = {"price_action", "technical_analysis", "valuation", "investment_case", "rag_qa", "news_sentiment"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    path = DATA / "synthetic.jsonl"
    rows = read_jsonl(path)
    stats, samples = Counter(), {}
    for r in rows:
        gen = r.get("gen_tickers")
        if gen is None:  # first pass: whatever the generator produced (old: str, new: list)
            gen = r["tickers"] if "tickers" in r else ([r["ticker"]] if r.get("ticker") else [])
        ext = extract_focus_entities(r["query"])
        r.pop("ticker", None)
        r["gen_tickers"], r["tickers"] = gen, ext
        if set(gen) == set(ext):
            kind = "agree"
        elif not gen and ext:
            kind = "generator blank, extractor found"
        elif gen and not ext:
            kind = "generator had, extractor found none"
        else:
            kind = "both found, differ"
        stats[kind] += 1
        samples.setdefault(kind, []).append((r["label"], r["query"], gen, ext))
        if r["label"] in SINGLE_STOCK and not ext:
            stats["single-stock intent, still no ticker"] += 1
            samples.setdefault("single-stock intent, still no ticker", []).append((r["label"], r["query"], gen, ext))
    print(f"{len(rows)} rows")
    for k, v in stats.most_common():
        print(f"{v:5d}  {k}")
        if k != "agree":
            for lab, q, g, e in samples[k][:5]:
                print(f"         [{lab}] {q!r} gen={g} ext={e}")
    if not args.dry_run:
        write_jsonl(path, rows)
        print("rewrote data/synthetic.jsonl (tickers = extractor, gen_tickers = generator)")


if __name__ == "__main__":
    main()
