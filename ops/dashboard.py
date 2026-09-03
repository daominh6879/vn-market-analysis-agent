"""
ops/dashboard.py — Local observability dashboard.

Usage:
    python -m ops.dashboard            # http://localhost:8888
    python -m ops.dashboard --port 8888

Reads:
    traces/latest.jsonl   — event log (tool, llm, turn_start, turn_end)
    traces/usage.jsonl    — LLM cost ledger
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

load_dotenv()

_ROOT = Path(__file__).parent.parent
_TRACES_DIR = _ROOT / "traces"
_LATEST = _TRACES_DIR / "latest.jsonl"
_USAGE = _TRACES_DIR / "usage.jsonl"


# ── Data collection ───────────────────────────────────────────────────────────

def _read_jsonl(path: Path, limit: int = 5000) -> list[dict]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
        result = []
        for line in lines:
            try:
                result.append(json.loads(line))
            except Exception:
                pass
        return result
    except Exception:
        return []


def collect() -> dict:
    raw = _read_jsonl(_LATEST)
    usage_raw = _read_jsonl(_USAGE)

    by_rid: dict[str, list[dict]] = defaultdict(list)
    for ev in raw:
        rid = ev.get("request_id", "")
        if rid:
            by_rid[rid].append(ev)

    turns = []
    for rid, events in by_rid.items():
        turn_start = next((e for e in events if e.get("type") == "turn_start"), None)
        turn_end = next((e for e in events if e.get("type") == "turn_end"), None)
        llm_events = [e for e in events if e.get("type") == "llm"]
        tool_events = [e for e in events if e.get("type") == "tool"]
        gate_events = [e for e in events if e.get("type") == "gate"]

        ts = (turn_start or {}).get("ts", 0) or (events[0].get("ts", 0) if events else 0)
        ts_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else ""

        # derive latency from turn_start/turn_end timestamps if not stored
        latency_ms = (turn_end or {}).get("latency_ms", 0)
        if not latency_ms and turn_start and turn_end:
            latency_ms = round((turn_end.get("ts", 0) - turn_start.get("ts", 0)) * 1000)

        # route taken from gate events
        route_event = next((e for e in gate_events if e.get("node") == "route_after_clarify"), None)
        route = (route_event or {}).get("route", "")

        turns.append({
            "request_id": rid,
            "ts": ts,
            "ts_str": ts_str,
            "query": (turn_start or {}).get("query", "")[:100],
            "intent": (turn_start or turn_end or {}).get("intent", ""),
            "ticker": (turn_start or turn_end or {}).get("ticker", ""),
            "route": route,
            "latency_ms": latency_ms,
            "cache_hit": (turn_end or {}).get("cache_hit", False),
            "report_len": (turn_end or {}).get("report_len", 0),
            "llm_calls": len(llm_events),
            "tokens_in": sum(e.get("tokens_in", 0) for e in llm_events),
            "tokens_out": sum(e.get("tokens_out", 0) for e in llm_events),
            "cost_usd": round(sum(e.get("cost_usd", 0) for e in llm_events), 6),
            "gates": [
                {"node": e.get("node", ""), "route": e.get("route", ""),
                 "sub_tasks": e.get("sub_tasks", 0)}
                for e in gate_events
            ],
            "tools": [
                {
                    "name": e.get("tool", ""),
                    "status": e.get("status", "ok"),
                    "duration_ms": e.get("duration_ms", 0),
                    "preview": e.get("preview", "")[:80],
                }
                for e in tool_events
            ],
        })

    turns.sort(key=lambda x: x["ts"], reverse=True)

    tool_stats: dict[str, dict] = defaultdict(lambda: {"calls": 0, "errors": 0, "total_ms": 0})
    for ev in raw:
        if ev.get("type") == "tool":
            n = ev.get("tool", "unknown")
            tool_stats[n]["calls"] += 1
            if ev.get("status") == "error":
                tool_stats[n]["errors"] += 1
            tool_stats[n]["total_ms"] += ev.get("duration_ms", 0)

    tools = [
        {
            "name": n,
            "calls": s["calls"],
            "errors": s["errors"],
            "error_rate": round(s["errors"] / s["calls"] * 100, 1) if s["calls"] else 0,
            "avg_ms": round(s["total_ms"] / s["calls"]) if s["calls"] else 0,
        }
        for n, s in tool_stats.items()
    ]
    tools.sort(key=lambda x: x["calls"], reverse=True)

    usage_by_model: dict[str, dict] = defaultdict(
        lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    )
    for u in usage_raw:
        key = f"{u.get('provider', '?')}/{u.get('model', '?')}"
        usage_by_model[key]["calls"] += 1
        usage_by_model[key]["input_tokens"] += u.get("input_tokens", 0)
        usage_by_model[key]["output_tokens"] += u.get("output_tokens", 0)
        usage_by_model[key]["cost_usd"] += u.get("cost_usd", 0.0)

    usage = [
        {"model": k, **v, "cost_usd": round(v["cost_usd"], 4)}
        for k, v in usage_by_model.items()
    ]
    usage.sort(key=lambda x: x["cost_usd"], reverse=True)

    # daily breakdown by day + model
    daily: dict[str, dict] = defaultdict(
        lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    )
    for u in usage_raw:
        ts = u.get("ts", 0)
        day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "unknown"
        key = f"{day}/{u.get('model', '?')}"
        daily[key]["calls"] += 1
        daily[key]["input_tokens"] += u.get("input_tokens", 0)
        daily[key]["output_tokens"] += u.get("output_tokens", 0)
        daily[key]["cost_usd"] += u.get("cost_usd", 0.0)

    usage_daily = [
        {"day_model": k, **v, "cost_usd": round(v["cost_usd"], 4)}
        for k, v in sorted(daily.items(), reverse=True)
    ]

    settings = {
        "provider": os.environ.get("LLM_PROVIDER", "deepseek"),
        "model": (
            os.environ.get("DEEPSEEK_MODEL")
            or os.environ.get("ANTHROPIC_MODEL")
            or os.environ.get("OPENAI_MODEL")
            or "default"
        ),
        "trace_file": str(_LATEST),
        "usage_file": str(_USAGE),
        "total_turns": len(turns),
        "total_tool_calls": sum(t["calls"] for t in tools),
        "total_cost_usd": round(sum(u["cost_usd"] for u in usage), 4),
    }

    return {
        "turns": turns[:100],
        "tools": tools,
        "usage_summary": usage,
        "usage_daily": usage_daily,
        "recent_usage": list(reversed(usage_raw[-50:])),
        "settings": settings,
        "ts": time.time(),
    }


def graph_topology() -> dict:
    """Return LangGraph node/edge topology + mermaid diagram for the Graph tab."""
    try:
        from agents.graph import build_graph
        graph = build_graph()
        gg = graph.get_graph()
        nodes = [n for n in gg.nodes.keys() if n not in ("__start__", "__end__")]
        edges = [
            {"from": e.source, "to": e.target, "label": e.data or ""}
            for e in gg.edges
        ]
        mermaid = ""
        try:
            mermaid = gg.draw_mermaid()
        except Exception:
            pass
        return {"nodes": nodes, "edges": edges, "mermaid": mermaid}
    except Exception as e:
        return {"nodes": [], "edges": [], "mermaid": "", "error": str(e)}


# ── Evals collection ──────────────────────────────────────────────────────────

_EVAL_FILES = [
    _ROOT / "evals" / "results.json",
    _ROOT / "evals" / "arch_compare.json",
    _ROOT / "evals" / "router_eval.json",
    _ROOT / "evals" / "rag_fusion_eval.json",
    _ROOT / "evals" / "reranker_results.json",
]

_EVAL_METRIC_KEYS = frozenset({
    "faithfulness", "answer_relevancy", "answer_correctness",
    "context_precision", "context_recall", "context_relevancy",
    "context_entity_recall", "semantic_similarity", "rouge_score",
    "bleu_score", "precision", "recall", "f1", "f1_score", "accuracy",
    "mrr", "ndcg", "hit_rate", "hit@5",
    "recall_at_k", "recall_at_k_avg", "recall@k",
    # custom eval files in this repo
    "refusal_pass_rate", "news_pipeline_pass_rate", "pass_rate",
    "failure_rate", "quality_score",
})

# per-question/per-sample detail lists — skip them, keep only summary/aggregate scores
_SKIP_SUBTREES = frozenset({"results", "samples", "refusal_results", "raw"})


def _extract_scores(obj, out: list | None = None, depth: int = 0, path: str = "") -> list[dict]:
    """Walk nested JSON and collect {metric, score} pairs for known metric keys."""
    if out is None:
        out = []
    if depth > 6:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in _SKIP_SUBTREES:
                continue
            if str(k).lower() in _EVAL_METRIC_KEYS and isinstance(v, (int, float)):
                out.append({"metric": path + str(k), "score": round(float(v), 4)})
            else:
                _extract_scores(v, out, depth + 1, path + str(k) + ".")
    elif isinstance(obj, list):
        for i, item in enumerate(obj[:20]):
            _extract_scores(item, out, depth + 1, path + f"[{i}].")
    return out


def collect_evals() -> dict:
    """Read eval result files and return per-report metrics + mtime."""
    reports: dict = {}
    for f in _EVAL_FILES:
        if not f.exists():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            reports[f.stem] = {
                "mtime": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "metrics": _extract_scores(data),
            }
        except Exception:
            continue
    return reports


# ── Inline HTML ───────────────────────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI Engineer Dashboard</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/mermaid/10.9.1/mermaid.min.js"></script>
<style>
  :root {
    --bg:#0f1117;--surface:#1a1d27;--border:#2d3148;
    --text:#e2e8f0;--muted:#718096;--accent:#6366f1;
    --green:#48bb78;--red:#fc8181;--yellow:#f6e05e;
  }
  [data-theme=light]{
    --bg:#f7f8fc;--surface:#ffffff;--border:#e2e8f0;
    --text:#1a202c;--muted:#718096;--accent:#6366f1;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--text);font-family:system-ui,sans-serif;font-size:14px}
  header{padding:16px 24px;background:var(--surface);border-bottom:1px solid var(--border);
         display:flex;align-items:center;justify-content:space-between}
  header h1{font-size:18px;font-weight:700;color:var(--accent)}
  .actions{display:flex;gap:8px;align-items:center}
  nav{display:flex;gap:4px;padding:12px 24px;border-bottom:1px solid var(--border);background:var(--surface)}
  nav button{padding:6px 16px;border:1px solid var(--border);background:transparent;
             color:var(--muted);border-radius:6px;cursor:pointer;font-size:13px}
  nav button.active{background:var(--accent);color:#fff;border-color:var(--accent)}
  .btn{padding:6px 14px;border:1px solid var(--border);background:var(--surface);
       color:var(--text);border-radius:6px;cursor:pointer;font-size:12px}
  .btn:hover{border-color:var(--accent);color:var(--accent)}
  main{padding:20px 24px}
  .tab{display:none}.tab.active{display:block}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th{text-align:left;padding:10px 12px;color:var(--muted);font-weight:600;
     border-bottom:1px solid var(--border);font-size:11px;text-transform:uppercase}
  td{padding:10px 12px;border-bottom:1px solid var(--border);vertical-align:top}
  tr:hover td{background:var(--surface)}
  .badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:600}
  .badge-green{background:rgba(72,187,120,.15);color:var(--green)}
  .badge-red{background:rgba(252,129,129,.15);color:var(--red)}
  .badge-blue{background:rgba(99,102,241,.15);color:var(--accent)}
  .tools-list{display:flex;flex-wrap:wrap;gap:4px}
  .tool-chip{font-size:11px;padding:2px 6px;border-radius:4px;background:var(--surface);border:1px solid var(--border)}
  .tool-chip.err{border-color:var(--red);color:var(--red)}
  .stats-row{display:flex;gap:16px;margin-bottom:20px;flex-wrap:wrap}
  .stat-card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:16px 20px;min-width:140px}
  .stat-card .val{font-size:24px;font-weight:700;color:var(--accent)}
  .stat-card .lbl{font-size:11px;color:var(--muted);margin-top:4px}
  .graph-viz{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:20px;min-height:300px}
  .node-list{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px}
  .node-chip{padding:6px 12px;border-radius:6px;border:1px solid var(--accent);
             background:rgba(99,102,241,.1);color:var(--accent);font-size:12px;font-family:monospace}
  .edge-row{font-size:12px;color:var(--muted);font-family:monospace;padding:2px 0}
  pre.query{font-family:monospace;font-size:12px;white-space:pre-wrap;color:var(--muted);max-width:280px}
  .empty{color:var(--muted);text-align:center;padding:40px}
  .meta{color:var(--muted);font-size:12px}
  .chat-bar{display:flex;gap:8px;margin-bottom:16px}
  .chat-bar input{flex:1;padding:10px 14px;border:1px solid var(--border);border-radius:6px;
                  background:var(--surface);color:var(--text);font-size:14px}
  .chat-output{background:var(--surface);border:1px solid var(--border);border-radius:8px;
               padding:16px;min-height:300px;max-height:70vh;overflow-y:auto;
               font-family:ui-monospace,monospace;font-size:12px;white-space:pre-wrap}
  .chat-q{color:var(--accent);margin-bottom:8px;font-weight:600}
  .chat-llm{color:var(--muted);margin-bottom:4px}
  .chat-tool{color:var(--text);margin-bottom:4px}
  .chat-meta{color:var(--muted);margin-bottom:4px}
  .chat-done{color:var(--green);margin-top:8px;font-weight:600}
  .chat-err{color:var(--red);margin-top:8px}
  .chat-stream{color:var(--text);white-space:pre-wrap;margin-top:4px}
  details.eval{background:var(--surface);border:1px solid var(--border);border-radius:8px;
               padding:12px 16px;margin-bottom:12px}
  details.eval summary{cursor:pointer;font-weight:600;color:var(--accent)}
  .score-good{color:var(--green);font-weight:600}
  .score-mid{color:var(--yellow);font-weight:600}
  .score-bad{color:var(--red);font-weight:600}
  .subtab{display:inline-flex;gap:2px;margin-bottom:12px;border:1px solid var(--border);border-radius:6px;overflow:hidden}
  .subtab button{padding:6px 14px;background:transparent;border:none;color:var(--muted);cursor:pointer;font-size:12px}
  .subtab button.active{background:var(--accent);color:#fff}
</style>
</head>
<body>
<header>
  <h1>AI Engineer Dashboard</h1>
  <div class="actions">
    <span class="meta" id="last-updated">–</span>
    <button class="btn" onclick="refresh()">↻ Refresh</button>
    <button class="btn" onclick="toggleTheme()">◐ Theme</button>
  </div>
</header>
<nav>
  <button onclick="showTab('chat',this)">Chat</button>
  <button class="active" onclick="showTab('turns',this)">Turns</button>
  <button onclick="showTab('tools',this)">Tools</button>
  <button onclick="showTab('usage',this)">Usage</button>
  <button onclick="showTab('graph',this)">Graph</button>
  <button onclick="showTab('evals',this)">Evals</button>
</nav>
<main>
  <div id="chat" class="tab">
    <div class="chat-bar">
      <input id="chat-input" type="text" placeholder="Nhập câu hỏi… (vd: phân tích HPG)" />
      <button class="btn" onclick="sendChat()">Send</button>
    </div>
    <div id="chat-output" class="chat-output">Nhập câu hỏi rồi nhấn Send để xem sự kiện tool/LLM chạy real-time.</div>
  </div>
  <div id="turns" class="tab active"><div class="empty">Loading…</div></div>
  <div id="tools" class="tab"><div class="empty">Loading…</div></div>
  <div id="usage" class="tab"><div class="empty">Loading…</div></div>
  <div id="graph" class="tab"><div class="empty">Loading…</div></div>
  <div id="evals" class="tab"><div class="empty">Loading…</div></div>
</main>
<script>
let _data=null,_graph=null,_evals=null,_streamEl=null,_cid=null;
let _theme=localStorage.getItem('theme')||'dark';
document.documentElement.setAttribute('data-theme',_theme);

function toggleTheme(){
  _theme=_theme==='dark'?'light':'dark';
  document.documentElement.setAttribute('data-theme',_theme);
  localStorage.setItem('theme',_theme);
}
function showTab(id,btn){
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('nav button').forEach(b=>b.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  btn.classList.add('active');
  if(id==='graph'&&!_graph)loadGraph();
  if(id==='evals'&&!_evals)loadEvals();
}
async function refresh(){
  const res=await fetch('/api/data');
  _data=await res.json();
  renderAll();
  const d=new Date(_data.ts*1000);
  document.getElementById('last-updated').textContent='Updated '+d.toLocaleTimeString();
}
async function loadGraph(){
  const res=await fetch('/api/graph');
  _graph=await res.json();
  renderGraph();
}
function badge(t,c){return`<span class="badge badge-${c}">${t}</span>`}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function renderAll(){if(!_data)return;renderTurns();renderTools();renderUsage();}

function renderTurns(){
  const s=_data.settings;
  let h=`<div class="stats-row">
    <div class="stat-card"><div class="val">${s.total_turns}</div><div class="lbl">Total Turns</div></div>
    <div class="stat-card"><div class="val">${s.total_tool_calls}</div><div class="lbl">Tool Calls</div></div>
    <div class="stat-card"><div class="val">$${s.total_cost_usd}</div><div class="lbl">Total Cost</div></div>
    <div class="stat-card"><div class="val">${s.provider}</div><div class="lbl">Provider</div></div>
  </div>
  <table><thead><tr><th>Time</th><th>Query</th><th>Intent</th><th>Ticker</th><th>Route</th>
    <th>Steps</th><th>Tools</th><th>LLM</th><th>Tokens</th><th>Latency</th><th>Cost</th><th>Cache</th></tr></thead><tbody>`;
  for(const t of _data.turns){
    const chips=t.tools.map(tl=>`<span class="tool-chip${tl.status==='error'?' err':''}">${esc(tl.name)}</span>`).join('');
    const gateChips=(t.gates||[]).filter(g=>g.node).map(g=>{
      const lbl=g.route?`${g.node}→${g.route}`:g.node;
      return `<span class="tool-chip">${esc(lbl)}</span>`;
    }).join('');
    const routeBadge=t.route?badge(t.route,'blue'):'—';
    h+=`<tr>
      <td>${t.ts_str}</td>
      <td><pre class="query">${esc(t.query)}</pre></td>
      <td>${t.intent?badge(t.intent,'blue'):''}</td>
      <td>${t.ticker||'—'}</td>
      <td>${routeBadge}</td>
      <td><div class="tools-list">${gateChips||'—'}</div></td>
      <td><div class="tools-list">${chips||'—'}</div></td>
      <td>${t.llm_calls}</td>
      <td>${t.tokens_in}↑ ${t.tokens_out}↓</td>
      <td>${t.latency_ms?t.latency_ms+'ms':'—'}</td>
      <td>${t.cost_usd?'$'+t.cost_usd:'—'}</td>
      <td>${t.cache_hit?badge('HIT','green'):''}</td>
    </tr>`;
  }
  h+='</tbody></table>';
  document.getElementById('turns').innerHTML=h||'<div class="empty">No turns yet</div>';
}

function renderTools(){
  let h='<table><thead><tr><th>Tool</th><th>Calls</th><th>Errors</th><th>Error %</th><th>Avg ms</th></tr></thead><tbody>';
  for(const t of _data.tools){
    h+=`<tr><td><code>${esc(t.name)}</code></td><td>${t.calls}</td>
      <td>${t.errors>0?badge(t.errors,'red'):'0'}</td>
      <td>${t.error_rate>10?badge(t.error_rate+'%','red'):t.error_rate+'%'}</td>
      <td>${t.avg_ms}</td></tr>`;
  }
  h+='</tbody></table>';
  document.getElementById('tools').innerHTML=_data.tools.length?h:'<div class="empty">No tool calls yet</div>';
}

function renderUsage(){
  let byModel='<table><thead><tr><th>Model</th><th>Calls</th><th>Tokens In</th><th>Tokens Out</th><th>Cost USD</th></tr></thead><tbody>';
  for(const r of _data.usage_summary){
    byModel+=`<tr><td><code>${esc(r.model)}</code></td><td>${r.calls}</td>
      <td>${r.input_tokens.toLocaleString()}</td><td>${r.output_tokens.toLocaleString()}</td>
      <td>$${r.cost_usd}</td></tr>`;
  }
  byModel+='</tbody></table>';
  let byDay='<table><thead><tr><th>Day / Model</th><th>Calls</th><th>Tokens In</th><th>Tokens Out</th><th>Cost USD</th></tr></thead><tbody>';
  for(const r of (_data.usage_daily||[])){
    byDay+=`<tr><td><code>${esc(r.day_model)}</code></td><td>${r.calls}</td>
      <td>${r.input_tokens.toLocaleString()}</td><td>${r.output_tokens.toLocaleString()}</td>
      <td>$${r.cost_usd}</td></tr>`;
  }
  byDay+='</tbody></table>';
  const h=`<div class="subtab">
    <button class="active" onclick="usageSub('model',this)">By Model</button>
    <button onclick="usageSub('day',this)">By Day</button>
  </div>
  <div id="usage-model">${_data.usage_summary.length?byModel:'<div class="empty">No usage data yet</div>'}</div>
  <div id="usage-day" style="display:none">${(_data.usage_daily||[]).length?byDay:'<div class="empty">No daily data yet</div>'}</div>`;
  document.getElementById('usage').innerHTML=h;
}
function usageSub(which,btn){
  document.querySelectorAll('.subtab button').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('usage-model').style.display=which==='model'?'block':'none';
  document.getElementById('usage-day').style.display=which==='day'?'block':'none';
}

function renderGraph(){
  if(!_graph)return;
  const gdiv=document.getElementById('graph');
  if(_graph.error){gdiv.innerHTML=`<p style="color:var(--red);margin-top:12px;font-size:12px">Error: ${esc(_graph.error)}</p>`;return;}
  if(_graph.mermaid && window.mermaid){
    window.mermaid.initialize({startOnLoad:false, theme:_theme==='dark'?'dark':'default'});
    gdiv.innerHTML='<div class="graph-viz" style="overflow-x:auto"></div>';
    const box=gdiv.querySelector('.graph-viz');
    const pre=document.createElement('pre');
    pre.className='mermaid';
    pre.textContent=_graph.mermaid;
    box.appendChild(pre);
    window.mermaid.run({nodes:[pre]}).catch(function(){});
    return;
  }
  // fallback: node + edge list when mermaid unavailable (CDN blocked)
  const n=_graph.nodes||[],e=_graph.edges||[];
  let h='<div class="graph-viz">';
  h+='<h3 style="margin-bottom:12px;font-size:14px;color:var(--muted)">Nodes ('+n.length+')</h3>';
  h+='<div class="node-list">'+n.map(nd=>`<span class="node-chip">${esc(String(nd))}</span>`).join('')+'</div>';
  h+='<h3 style="margin:16px 0 8px;font-size:14px;color:var(--muted)">Edges ('+e.length+')</h3>';
  h+=e.map(ed=>`<div class="edge-row">${esc(String(ed.from))} → ${esc(String(ed.to))}${ed.label?` <span class="meta">[${esc(String(ed.label))}]</span>`:''}</div>`).join('')
     ||'<div class="edge-row" style="color:var(--muted)">No edge data available</div>';
  h+='</div>';
  gdiv.innerHTML=h;
}

function sendChat(){
  const inp=document.getElementById('chat-input');
  const q=inp.value.trim();
  if(!q)return;
  const out=document.getElementById('chat-output');
  out.innerHTML='';
  out.innerHTML+='<div class="chat-q">👤 '+esc(q)+'</div>';
  inp.value='';
  _streamEl=null;
  const url='/api/chat/stream?q='+encodeURIComponent(q)+(_cid?('&cid='+encodeURIComponent(_cid)):'');
  const es=new EventSource(url);
  // default (data: only) events — text chunks + cid handshake
  es.onmessage=function(e){
    let m;
    try{m=JSON.parse(e.data);}catch(_){return;}
    if(m.type==='cid'){_cid=m.cid;return;}
    if(m.text!==undefined){
      if(!_streamEl){_streamEl=document.createElement('div');_streamEl.className='chat-stream';out.appendChild(_streamEl);}
      _streamEl.textContent+=m.text;
    }
    out.scrollTop=out.scrollHeight;
  };
  // named status events (routing / cache / streaming / clarification)
  es.addEventListener('status',function(e){
    let m;try{m=JSON.parse(e.data);}catch(_){return;}
    const step=m.step||'';
    if(step==='routing'&&m.agent){out.innerHTML+='<div class="chat-meta">🔀 route → '+esc(m.agent)+(m.ticker?' ('+esc(m.ticker)+')':'')+'</div>';}
    else if(step==='cache_hit'){out.innerHTML+='<div class="chat-meta">♻️ cache hit ('+esc(m.tier||'')+')</div>';}
    else if(step==='streaming'){out.innerHTML+='<div class="chat-meta">📝 '+esc(m.agent||'')+'</div>';}
    else{out.innerHTML+='<div class="chat-meta">• '+esc(step)+'</div>';}
    out.scrollTop=out.scrollHeight;
  });
  es.addEventListener('tool',function(e){
    let m;try{m=JSON.parse(e.data);}catch(_){return;}
    out.innerHTML+='<div class="chat-tool">🛠 '+esc(m.name||'')+' <span class="badge '+(m.status==='error'?'badge-red':'badge-green')+'">'+esc(m.status||'ok')+'</span>'+(m.duration_ms?' '+m.duration_ms+'ms':'')+'</div>';
    out.scrollTop=out.scrollHeight;
  });
  es.addEventListener('gate',function(e){
    let m;try{m=JSON.parse(e.data);}catch(_){return;}
    out.innerHTML+='<div class="chat-meta">🔀 '+esc(m.node||'')+(m.route?' → '+esc(m.route):'')+'</div>';
    out.scrollTop=out.scrollHeight;
  });
  es.addEventListener('done',function(e){
    let m;try{m=JSON.parse(e.data);}catch(_){return;}
    out.innerHTML+='<div class="chat-done">✅ done ('+m.length+' chars, agent='+esc(m.agent||'')+')</div>';
    es.close();
  });
  es.addEventListener('error',function(e){
    if(e.data){
      let m;try{m=JSON.parse(e.data);}catch(_){return;}
      out.innerHTML+='<div class="chat-err">❌ '+esc(m.error||'')+'</div>';
    }
    es.close();
  });
}
function loadEvals(){
  fetch('/api/evals').then(r=>r.json()).then(d=>{_evals=d;renderEvals();});
}
function scoreClass(s){if(s>1||s<0)return '';return s>=0.8?'score-good':(s>=0.6?'score-mid':'score-bad')}
function renderEvals(){
  if(!_evals)return;
  const names=Object.keys(_evals);
  if(!names.length){
    document.getElementById('evals').innerHTML='<div class="empty">No eval results found. Run python evals/run.py first.</div>';
    return;
  }
  let h='';
  for(const name of names){
    const rep=_evals[name];
    const rows=(rep.metrics||[]).map(m=>`<tr><td>${esc(m.metric)}</td><td class="${scoreClass(m.score)}">${m.score}</td></tr>`).join('');
    h+=`<details class="eval" open>
      <summary>${esc(name)} <span class="meta">(${esc(rep.mtime)})</span></summary>
      ${rows?`<table style="margin-top:8px"><thead><tr><th>Metric</th><th>Score</th></tr></thead><tbody>${rows}</tbody></table>`:'<div class="empty">No known metrics in this report</div>'}
    </details>`;
  }
  document.getElementById('evals').innerHTML=h;
}

refresh();
setInterval(refresh,30000);
</script>
</body>
</html>"""


# ── HTTP handler ──────────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send_json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", ""):
            self._send_html(_HTML)
        elif self.path == "/api/data":
            self._send_json(collect())
        elif self.path == "/api/graph":
            self._send_json(graph_topology())
        elif self.path == "/api/evals":
            self._send_json(collect_evals())
        elif self.path.startswith("/api/chat/stream"):
            q = parse_qs(urlparse(self.path).query).get("q", [""])[0]
            self._stream_chat(q)
        else:
            self.send_error(404)

    def _stream_chat(self, query: str) -> None:
        """SSE stream of one full turn — delegates to memory.turn_handler.stream_turn
        so the Chat tab behaves like the real app (memory, clarify, persistence)."""
        import queue as _queue
        import threading as _threading
        import asyncio as _asyncio

        params = parse_qs(urlparse(self.path).query)
        cid = params.get("cid", [""])[0]
        user_id = "dashboard"
        is_first = not bool(cid)
        if not cid:
            try:
                from memory.conversation import create_conversation
                cid = create_conversation(user_id, "default")
            except Exception:
                cid = f"dashboard-{int(time.time())}"

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        # echo conversation id so the browser reuses it for follow-up turns
        try:
            self.wfile.write(
                f"data: {json.dumps({'type': 'cid', 'cid': cid})}\n\n".encode("utf-8")
            )
            self.wfile.flush()
        except Exception:
            pass

        q: _queue.Queue = _queue.Queue()

        async def _drain() -> None:
            from memory.turn_handler import stream_turn
            try:
                async for line in stream_turn(
                    conversation_id=cid,
                    user_id=user_id,
                    user_message=query,
                    tenant_id="default",
                    is_first_turn=is_first,
                ):
                    q.put(line)
            except Exception as exc:
                q.put(f"event: error\ndata: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n")
            finally:
                q.put(None)

        def _run() -> None:
            _asyncio.run(_drain())

        _threading.Thread(target=_run, daemon=True).start()

        while True:
            item = q.get()
            if item is None:
                break
            try:
                self.wfile.write(item.encode("utf-8"))
                self.wfile.flush()
            except Exception:
                break

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()


# ── Entry point ───────────────────────────────────────────────────────────────

def run(port: int = 8888) -> None:
    server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    print(f"Dashboard → http://localhost:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Engineer observability dashboard")
    parser.add_argument("--port", type=int, default=8888)
    args = parser.parse_args()
    run(args.port)
