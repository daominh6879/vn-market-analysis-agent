"""
agents/run_brief.py — CLI runner for daily market brief.

Usage:
    python agents/run_brief.py --date 2026-08-26
    python agents/run_brief.py --date 2026-08-26 --out info/26_08_2026.txt
    python agents/run_brief.py                    # uses today's date, auto output path

Writes report to info/{DD_MM_YYYY}.txt by default.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date as date_cls
from pathlib import Path


def _default_out(date_str: str) -> str:
    from datetime import datetime
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return f"info/{dt.strftime('%d_%m_%Y')}.txt"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate daily market brief")
    parser.add_argument(
        "--date", default="",
        help="Report date YYYY-MM-DD (default: today)"
    )
    parser.add_argument(
        "--out", default="",
        help="Output file path (default: info/DD_MM_YYYY.txt)"
    )
    parser.add_argument(
        "--no-file", action="store_true",
        help="Print to stdout, do not write file"
    )
    args = parser.parse_args()

    date_str = args.date.strip() if args.date else str(date_cls.today())

    # Validate date format
    try:
        from datetime import datetime
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        print(f"ERROR: --date '{date_str}' must be YYYY-MM-DD format", file=sys.stderr)
        sys.exit(1)

    if args.no_file:
        out_path = ""
    else:
        out_path = args.out.strip() if args.out else _default_out(date_str)

    print(f"[run_brief] date={date_str}  out={out_path or '(stdout only)'}")
    print("[run_brief] Building graph: collect_all → compose_outlook → render_report\n")

    from agents.market_brief_graph import build_brief_graph, make_initial_state

    app = build_brief_graph()
    initial = make_initial_state(date=date_str, output_path=out_path)

    t0 = time.perf_counter()
    final = app.invoke(initial)
    elapsed = time.perf_counter() - t0

    report = final.get("report_text", "[Không có báo cáo]")
    print(report)

    # Summary
    print("\n" + "─" * 60)
    print(f"Date       : {date_str}")
    print(f"Wall time  : {elapsed:.2f}s")
    print(f"Output     : {final.get('output_file') or '(not written)'}")

    missing = final.get("missing_fields", [])
    if missing:
        print(f"Missing    : {', '.join(missing)}")

    history = final.get("history", [])
    synth = next((h for h in history if h.get("step") == "compose_outlook"), {})
    if synth.get("input_tokens"):
        total = synth.get("input_tokens", 0) + synth.get("output_tokens", 0)
        print(f"LLM tokens : {synth['input_tokens']} in + {synth['output_tokens']} out = {total}")
    print("─" * 60)


if __name__ == "__main__":
    main()
