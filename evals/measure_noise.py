#!/usr/bin/env python3
"""
Bài 5: Chạy eval N lần, tính std, xác định ngưỡng nhiễu CI.

Usage:
    python evals/measure_noise.py              # 5 lần, fast mode
    python evals/measure_noise.py --runs 3     # 3 lần
    python evals/measure_noise.py --apply      # cập nhật THRESHOLD_DROP trong run.py
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent


def run_once(run_num: int, total: int, extra_args: list[str]) -> dict[str, float]:
    out_file = ROOT / f"evals/noise_run_{run_num}.json"
    cmd = [
        sys.executable, "evals/run.py",
        "--out", str(out_file),
    ] + extra_args

    print(f"\n{'─'*55}")
    print(f"  Run {run_num}/{total}")
    print(f"{'─'*55}")

    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode not in (0, 1):  # exit 1 = regression warning, not crash
        print(f"[WARN] run.py exited with code {result.returncode}")

    data = json.loads(out_file.read_text(encoding="utf-8"))
    scores: dict[str, float] = {
        k: v for k, v in data["scores"].items() if isinstance(v, (int, float))
    }
    print(f"  → scores: { {k: f'{v:.3f}' for k, v in scores.items()} }")
    return scores


def compute_noise(all_runs: list[dict[str, float]]) -> dict[str, dict]:
    metrics = list(all_runs[0].keys())
    noise: dict[str, dict] = {}
    for m in metrics:
        vals = [r[m] for r in all_runs if m in r]
        if len(vals) < 2:
            continue
        std = statistics.stdev(vals)
        noise[m] = {
            "values": vals,
            "mean": statistics.mean(vals),
            "std": std,
            "threshold": round(2 * std, 4),
        }
    return noise


def print_table(all_runs: list[dict], noise: dict) -> None:
    metrics = list(noise.keys())
    header = "| Lần | " + " | ".join(f"{m[:18]}" for m in metrics) + " |"
    sep    = "|-----|" + "|".join(["-" * 20] * len(metrics)) + "|"

    print(f"\n{'='*60}")
    print("  NGƯỠNG NHIỄU — KẾT QUẢ")
    print(f"{'='*60}")
    print(header)
    print(sep)
    for i, r in enumerate(all_runs, 1):
        row = " | ".join(f"{r.get(m, 0):.3f}" for m in metrics)
        print(f"| {i:<3} | {row} |")
    std_row   = " | ".join(f"**{noise[m]['std']:.4f}**"       for m in metrics)
    thr_row   = " | ".join(f"**{noise[m]['threshold']:.4f}**" for m in metrics)
    print(f"| std | {std_row} |")
    print(f"| 2×std | {thr_row} |")
    print()

    for m, d in noise.items():
        print(f"  {m}")
        print(f"    values    : {[f'{v:.3f}' for v in d['values']]}")
        print(f"    std       : {d['std']:.4f}")
        print(f"    threshold : {d['threshold']:.4f}  (2 × std)")
    print()


def update_notes(all_runs: list[dict], noise: dict) -> None:
    notes_path = ROOT / "NOTES.md"
    notes = notes_path.read_text(encoding="utf-8")

    metrics = list(noise.keys())
    col_h = "| Lần chạy | " + " | ".join(metrics) + " |"
    col_s = "|----------|" + "|".join(["-" * (len(m) + 2) for m in metrics]) + "|"
    rows  = [
        "| {:8} | {} |".format(
            str(i),
            " | ".join(f"{r.get(m, 0):.3f}" for m in metrics),
        )
        for i, r in enumerate(all_runs, 1)
    ]
    std_row = "| **std**  | " + " | ".join(f"**{noise[m]['std']:.4f}**"       for m in metrics) + " |"
    thr_row = "| **2×std**| " + " | ".join(f"**{noise[m]['threshold']:.4f}**" for m in metrics) + " |"

    section = (
        "\n\n## Ngưỡng nhiễu (Bài 5)\n\n"
        + col_h + "\n" + col_s + "\n"
        + "\n".join(rows) + "\n"
        + std_row + "\n"
        + thr_row + "\n"
    )

    marker = "## Ngưỡng nhiễu (Bài 5)"
    if marker in notes:
        start = notes.index(marker)
        nxt = notes.find("\n## ", start + 1)
        notes = notes[:start] + section.lstrip("\n") + ("\n\n" + notes[nxt + 1:] if nxt != -1 else "\n")
    else:
        notes = notes.rstrip() + section

    notes_path.write_text(notes, encoding="utf-8")
    print(f"✅  NOTES.md updated")


def update_run_py(primary_threshold: float) -> None:
    run_py = ROOT / "evals/run.py"
    src = run_py.read_text(encoding="utf-8")

    new_src = re.sub(
        r"THRESHOLD_DROP\s*=\s*[\d.]+",
        f"THRESHOLD_DROP = {primary_threshold:.4f}  # 2×std measured by measure_noise.py",
        src,
    )
    if new_src == src:
        print("[WARN] THRESHOLD_DROP pattern not found in run.py — update manually")
        return

    run_py.write_text(new_src, encoding="utf-8")
    print(f"✅  evals/run.py: THRESHOLD_DROP = {primary_threshold:.4f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--apply", action="store_true",
                        help="Apply threshold to run.py after measuring")
    parser.add_argument("--questions", default="evals/golden_hpg.yaml")
    args = parser.parse_args()

    extra = ["--questions", args.questions]

    all_runs: list[dict] = []
    for i in range(1, args.runs + 1):
        scores = run_once(i, args.runs, extra)
        all_runs.append(scores)

    noise = compute_noise(all_runs)
    if not noise:
        print("[ERROR] Không có metric nào đo được.")
        sys.exit(1)

    print_table(all_runs, noise)
    update_notes(all_runs, noise)

    primary = "refusal_pass_rate"
    if primary in noise:
        t = noise[primary]["threshold"]
        print(f"→ Ngưỡng CI đề xuất (refusal_pass_rate): {t:.4f}")
        if args.apply:
            update_run_py(t)
        else:
            print(f"  Chạy lại với --apply để tự động cập nhật evals/run.py")
    else:
        print("[WARN] refusal_pass_rate không có trong kết quả")


if __name__ == "__main__":
    main()
