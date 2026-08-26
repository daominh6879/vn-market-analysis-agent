"""
agents/run.py — Entry point cho bài 22 sequential agent.

Usage:
    python -m agents.run FPT
    python -m agents.run "phân tích HPG"
    python -m agents.run VNINDEX
    python -m agents.run --graph-only    # chỉ xuất sơ đồ, không chạy

After running 5 tickers: record benchmark numbers in NOTES.md under "mốc tuần tự".
"""

from __future__ import annotations

import sys
import time


def _print_benchmark(final_state: dict, wall_time: float) -> None:
    history = final_state.get("history", [])
    synth = next((h for h in history if h.get("step") == "synthesize"), {})
    in_tok = synth.get("input_tokens", 0)
    out_tok = synth.get("output_tokens", 0)
    total_tok = in_tok + out_tok

    print("\n" + "─" * 60)
    print(f"Ticker       : {final_state.get('ticker')}")
    print(f"Risk verdict : {final_state.get('risk_verdict', 'N/A')}")
    print(f"Wall time    : {wall_time:.2f}s")
    print(f"LLM tokens   : {in_tok} in + {out_tok} out = {total_tok} total")
    if error := final_state.get("error"):
        print(f"Error        : {error}")
    print("─" * 60)


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("Usage: python -m agents.run <TICKER or QUERY>")
        print("       python -m agents.run --graph-only")
        sys.exit(1)

    from agents.graph import build_graph, save_graph_image

    app = build_graph()

    if "--graph-only" in args:
        ok = save_graph_image(app)
        print("Graph image saved." if ok else "Graph image failed — check mermaid fallback.")
        return

    # Export graph image once (non-fatal)
    save_graph_image(app)

    query = " ".join(args)
    from agents.state import make_initial_state
    initial = make_initial_state(query)

    print(f"[agents.run] Phân tích: {initial['ticker']} "
          f"({'market query' if initial.get('is_market_query') else 'stock query'})")
    print("Running graph: collect → analyze_technical → assess_risk → synthesize\n")

    t0 = time.perf_counter()
    final = app.invoke(initial)
    wall_time = time.perf_counter() - t0

    report = final.get("report") or "[Không có báo cáo]"
    print(report)
    _print_benchmark(final, wall_time)


if __name__ == "__main__":
    main()
