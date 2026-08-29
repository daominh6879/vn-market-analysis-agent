"""
agents/graph_interactive.py — Graph with human-approval interrupt for Bài 27.

Flow: collect → analyze_technical → assess_risk → request_approval → synthesize
                                                         ↑
                                               interrupt() pauses here
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from agents.graph import (
    analyze_technical,
    assess_risk,
    collect,
    synthesize,
)
from agents.state import AgentState


def request_approval(state: AgentState) -> dict:
    """Pause for human review. interrupt() suspends the graph until resumed."""
    proposal = {
        "ticker": state.get("ticker"),
        "risk_verdict": state.get("risk_verdict", "N/A"),
        "tech_signals": (state.get("tech_signals") or "")[:500],
        "news_preview": (state.get("news_data") or "")[:300],
    }
    # Raises GraphInterrupt — LangGraph saves state and returns to caller.
    # On resume (Command(resume=...)), execution continues from next line.
    decision = interrupt(proposal)
    if decision is False:
        # Rejected — mark in state so synthesize can emit partial report
        return {"error": "rejected_by_user"}
    return {}


def build_interactive_graph(checkpointer) -> "CompiledGraph":
    g = StateGraph(AgentState)
    g.add_node("collect", collect)
    g.add_node("analyze_technical", analyze_technical)
    g.add_node("assess_risk", assess_risk)
    g.add_node("request_approval", request_approval)
    g.add_node("synthesize", synthesize)

    g.set_entry_point("collect")
    g.add_edge("collect", "analyze_technical")
    g.add_edge("analyze_technical", "assess_risk")
    g.add_edge("assess_risk", "request_approval")
    g.add_edge("request_approval", "synthesize")
    g.add_edge("synthesize", END)

    return g.compile(checkpointer=checkpointer)
