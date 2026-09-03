"""
ops/show_trace.py — Terminal trace viewer.

Usage:
    python -m ops.show_trace                        # traces/latest.jsonl
    python -m ops.show_trace traces/2026-09-03.jsonl
    python -m ops.show_trace --tail 50              # last 50 events
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_LATEST = _ROOT / "traces" / "latest.jsonl"

_TYPE_COLOR = {
    "turn_start": "\033[1;36m",
    "turn_end":   "\033[1;32m",
    "llm":        "\033[1;35m",
    "tool":       "\033[1;34m",
    "gate":       "\033[1;33m",
}
_RESET = "\033[0m"
_RED = "\033[31m"
_MUTED = "\033[2m"


def _color(kind: str) -> str:
    return _TYPE_COLOR.get(kind, "\033[37m")


def format_event(ev: dict) -> str:
    kind = ev.get("type", ev.get("tool", "?"))
    ts_raw = ev.get("ts", 0)
    ts = datetime.fromtimestamp(ts_raw).strftime("%H:%M:%S") if ts_raw else "--:--:--"
    rid = ev.get("request_id", "")[:12]
    status = ev.get("status", "")
    err = ev.get("error")

    c = _color(kind)
    status_str = f" {_RED}[ERROR]{_RESET}" if (status == "error" or err) else ""
    parts = [f"{_MUTED}{ts}{_RESET} {c}{kind:<14}{_RESET} {_MUTED}{rid}{_RESET}{status_str}"]

    if kind == "turn_start":
        q = ev.get("query", "")[:80]
        intent = ev.get("intent", "")
        parts.append(f"  query={q!r} intent={intent}")
    elif kind == "turn_end":
        lat = ev.get("latency_ms", 0)
        cache = " CACHE_HIT" if ev.get("cache_hit") else ""
        parts.append(f"  latency={lat}ms{cache} report_len={ev.get('report_len', 0)}")
    elif kind == "llm":
        tin = ev.get("tokens_in", 0)
        tout = ev.get("tokens_out", 0)
        dur = ev.get("duration_ms", 0)
        cost = ev.get("cost_usd", 0)
        model = (ev.get("args") or {}).get("model", "")[:30]
        cost_str = f" ${cost:.6f}" if cost else ""
        parts.append(f"  model={model} tokens={tin}↑{tout}↓ dur={dur}ms{cost_str}")
        if err:
            parts.append(f"  {_RED}error: {err[:120]}{_RESET}")
        else:
            preview = ev.get("preview", "")[:100]
            parts.append(f"  {_MUTED}{preview}{_RESET}")
    elif kind == "tool":
        tool = ev.get("tool", "")
        dur = ev.get("duration_ms", 0)
        parts.append(f"  tool={tool} dur={dur}ms")
        if err:
            parts.append(f"  {_RED}error: {err[:120]}{_RESET}")
        else:
            preview = ev.get("preview", "")[:100]
            parts.append(f"  {_MUTED}{preview}{_RESET}")

    return "\n".join(parts)


def show(path: Path, tail: int = 0) -> None:
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)
    lines = path.read_text(encoding="utf-8").splitlines()
    if tail:
        lines = lines[-tail:]
    events: list[dict] = []
    for line in lines:
        try:
            events.append(json.loads(line))
        except Exception:
            pass
    try:
        from rich.console import Console
        console = Console()
        for ev in events:
            console.print(format_event(ev))
    except ImportError:
        for ev in events:
            print(format_event(ev))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Show trace JSONL in terminal")
    parser.add_argument("file", nargs="?", type=Path, default=_LATEST)
    parser.add_argument("--tail", "-n", type=int, default=0, help="Last N events (0=all)")
    args = parser.parse_args()
    show(args.file, args.tail)
