"""
portfolio/ablation_news.py — how much does news actually move the advisor's decisions?

Gathers the evidence once for a real advice request (the JSON body vn-portfolio sent, e.g. advice_runs.request), then
runs the judge on variants of it:
- full (twice: the run-to-run noise floor, backlog I-008),
- no_context: ticker news only (no sector / related news),
- no_news: no news at all (ticker, sector, related, market).
Per ticker it prints the action and confidence of each variant; a variant that flips no more decisions than the
repeated full run has no measurable weight. Nothing is stored and no order is affected (the advice is not returned to
vn-portfolio).

Usage:
    python -m portfolio.ablation_news --request /tmp/request.json [--repeats 1]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


def variants(ev: dict) -> dict[str, dict]:
    no_ctx = {**ev, "context_news": {t: [] for t in ev.get("context_news", {})}}
    return {"full": ev, "full_again": ev, "no_context": no_ctx,
            "no_news": {**no_ctx, "news": {t: [] for t in ev["news"]}, "market_news": []}}


def decisions(out: dict) -> dict[str, tuple[str, float, int]]:
    items = out["actions"] + out["new_ideas"] + out["skipped_ideas"]
    return {i["ticker"]: (i["action"], i["confidence"], len(i.get("cited_news") or [])) for i in items}


def main(request: str, repeats: int) -> None:
    from portfolio.advisor import _advise
    from portfolio.evidence import gather_evidence_for
    body = json.loads(Path(request).read_text())
    ev = gather_evidence_for(body["positions"], body["cash"], body.get("user_id", "ablation"), body.get("watchlist") or [])
    print("news scoring:", ev.get("news_scoring"))
    results: dict[str, list[dict]] = {}
    for _ in range(repeats):
        for name, v in variants(ev).items():
            results.setdefault(name, []).append(decisions(_advise(v)))
    tickers = sorted({t for runs in results.values() for r in runs for t in r})
    names = list(results)
    print("| ticker | " + " | ".join(names) + " |\n|---|" + "---|" * len(names))
    for t in tickers:
        cells = [" / ".join(f"{r[t][0]} {r[t][1]:.2f} ({r[t][2]} tin)" if t in r else "—" for r in results[n]) for n in names]
        print(f"| {t} | " + " | ".join(cells) + " |")
    base = results["full"]
    for n in names[1:]:
        flips = sum(b.get(t, ("",))[0] != o.get(t, ("",))[0] for b, o in zip(base, results[n]) for t in tickers)
        dconf = [abs(b[t][1] - o[t][1]) for b, o in zip(base, results[n]) for t in tickers if t in b and t in o]
        print(f"{n}: {flips}/{len(tickers) * repeats} actions differ from full, mean |Δconfidence| "
              f"{sum(dconf) / len(dconf) if dconf else 0:.3f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--request", required=True)
    ap.add_argument("--repeats", type=int, default=1)
    a = ap.parse_args()
    main(a.request, a.repeats)
