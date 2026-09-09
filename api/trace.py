"""
api/trace.py — observability endpoints: trace drill-down, dashboard stats, graph topology.

Langfuse keys stay server-side: these endpoints query Langfuse and return plain JSON,
so the browser never sees credentials. Stats + graph topology are reused from the local
JSONL dashboard (ops/dashboard.py) so the React UI has one backend.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["observability"])


def _langfuse():
    """Return the Langfuse client, or None when keys are not configured."""
    try:
        from langfuse import get_client
        return get_client()
    except Exception:
        return None


def _dump(v):
    """Serialize a pydantic/datetime value to a JSON-safe plain object."""
    if v is None:
        return None
    for meth in ("model_dump", "dict"):
        m = getattr(v, meth, None)
        if callable(m):
            try:
                return m()
            except Exception:
                pass
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def _obs_summary(o) -> dict:
    return {
        "id": o.id,
        "parent_observation_id": o.parent_observation_id,
        "name": o.name,
        "type": o.type,
        "start_time": _dump(o.start_time),
        "end_time": _dump(o.end_time),
        "latency": o.latency,
        "model": o.model,
        "usage": _dump(o.usage_details),
        "cost": o.calculated_total_cost,
        "level": o.level,
        "status": o.status_message,
        "input": o.input,
        "output": o.output,
        "metadata": _dump(o.metadata),
    }


@router.get("/trace/runs")
def trace_runs(limit: int = 50, name: str | None = None):
    """Recent traces from Langfuse, lean summary rows."""
    c = _langfuse()
    if c is None:
        return {"runs": [], "error": "langfuse not configured"}
    try:
        res = c.api.trace.list(limit=min(limit, 100), name=name)
        runs = [
            {
                "id": t.id,
                "name": t.name,
                "timestamp": _dump(t.timestamp),
                "latency": t.latency,
                "total_cost": t.total_cost,
                "user_id": t.user_id,
                "session_id": t.session_id,
                "input": t.input,
                "output": t.output,
                "metadata": _dump(t.metadata),
            }
            for t in (res.data or [])
        ]
        return {"runs": runs}
    except Exception as exc:
        return {"runs": [], "error": f"{type(exc).__name__}: {exc}"}


@router.get("/trace/runs/{trace_id}")
def trace_run_detail(trace_id: str):
    """One trace's full observation (span) tree for drill-down."""
    c = _langfuse()
    if c is None:
        raise HTTPException(503, "langfuse not configured")
    try:
        full = c.api.trace.get(trace_id)
    except Exception as exc:
        raise HTTPException(404, f"trace {trace_id}: {type(exc).__name__}: {exc}") from exc

    return {
        "id": full.id,
        "name": full.name,
        "timestamp": _dump(full.timestamp),
        "latency": full.latency,
        "total_cost": full.total_cost,
        "input": full.input,
        "output": full.output,
        "metadata": _dump(full.metadata),
        "observations": [_obs_summary(o) for o in (full.observations or [])],
    }


@router.get("/graph")
def graph_topology_endpoint():
    """LangGraph node/edge topology (reused from the JSONL dashboard)."""
    from ops.dashboard import graph_topology
    return graph_topology()


@router.get("/dashboard/stats")
def dashboard_stats():
    """Turns/tools/usage stats from the local JSONL trace (reused from ops.dashboard)."""
    from ops.dashboard import collect
    return collect()
