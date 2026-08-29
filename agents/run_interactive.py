"""
agents/run_interactive.py — Entry point for Bài 27 human-in-the-loop agent.

Usage:
    python -m agents.run_interactive FPT
    python -m agents.run_interactive HPG

Workflow:
  1. Run graph: collect → analyze_technical → assess_risk → [interrupt]
  2. Save session to Postgres (agent_sessions)
  3. Print session_id — user approves/rejects via API or --approve / --reject flags

Flags for testing:
    --approve   automatically approve (skip human wait)
    --reject    automatically reject
"""

from __future__ import annotations

import sys
import uuid
from typing import Any

from dotenv import load_dotenv
load_dotenv()

from langgraph.types import Command

from agents.checkpointer import PostgresCheckpointer, save_checkpoint
from agents.graph_interactive import build_interactive_graph
from agents.state import make_initial_state


def _extract_state_snapshot(app, thread_id: str) -> dict:
    """Load AgentState from the latest checkpoint."""
    t = app.checkpointer.get_tuple({"configurable": {"thread_id": thread_id}})
    if not t:
        return {}
    return dict(t.checkpoint.get("channel_values", {}))


def run(query: str, auto_decision: str | None = None) -> None:
    session_id = str(uuid.uuid4())
    initial = make_initial_state(query)
    ticker = initial["ticker"]

    print(f"[run_interactive] ticker={ticker}  session_id={session_id}")
    print("Phase 1: collect -> analyze_technical -> assess_risk -> request_approval\n")

    checkpointer = PostgresCheckpointer()
    app = build_interactive_graph(checkpointer)
    thread_cfg = {"configurable": {"thread_id": session_id}}

    # Run until interrupt — LangGraph returns a snapshot with __interrupt__ key
    result: Any = app.invoke(initial, config=thread_cfg)

    # After interrupt, result contains the interrupt payload under __interrupt__
    interrupts = result.get("__interrupt__", [])
    if not interrupts:
        # Ran to completion without interrupt (shouldn't happen in normal flow)
        print("\n[run_interactive] Graph completed without interrupt.")
        print(result.get("report", "[No report]"))
        return

    proposal = interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]

    # Save AgentState snapshot to agent_sessions for API / restart recovery
    state_snapshot = _extract_state_snapshot(app, session_id)
    state_snapshot["session_id"] = session_id
    save_checkpoint(session_id, ticker, state_snapshot)

    print("=" * 60)
    print(f"INTERRUPT — waiting for human approval")
    print(f"Session ID : {session_id}")
    print(f"Ticker     : {proposal.get('ticker')}")
    print(f"Risk       : {proposal.get('risk_verdict')}")
    print(f"Tech (preview): {(proposal.get('tech_signals') or '')[:200]}")
    print("=" * 60)
    print("\nApprove via API:  POST /sessions/{id}/approve")
    print("Reject via API:   POST /sessions/{id}/reject")
    print(f"Or run: python -m agents.run_interactive {ticker} --approve")

    if auto_decision is None:
        return  # Wait for API call

    # ── auto decision (for testing) ────────────────────────────────────────
    print(f"\n[auto] decision={auto_decision}")
    resume_val = auto_decision == "approve"

    final = app.invoke(Command(resume=resume_val), config=thread_cfg)

    if resume_val:
        report = final.get("report") or "[Không có báo cáo]"
        print("\n" + report)
        print(f"\nSession {session_id} completed.")
    else:
        print(f"Session {session_id} rejected.")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("Usage: python -m agents.run_interactive <TICKER> [--approve|--reject]")
        sys.exit(1)

    auto = None
    if "--approve" in args:
        auto = "approve"
        args = [a for a in args if a != "--approve"]
    elif "--reject" in args:
        auto = "reject"
        args = [a for a in args if a != "--reject"]

    query = " ".join(args)
    run(query, auto_decision=auto)


if __name__ == "__main__":
    main()
