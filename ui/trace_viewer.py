"""
ui/trace_viewer.py — Visual audit tool for agent tool calls.

Run: streamlit run ui/trace_viewer.py
Reads: traces/latest.jsonl (written by tracing.instrument_tool)

Shows per-request waterfall: tool name | status | duration bar | args preview | result preview
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import streamlit as st

TRACE_FILE = Path(__file__).parent.parent / "traces" / "latest.jsonl"

st.set_page_config(page_title="Tool Trace Viewer", page_icon="🔍", layout="wide")

st.markdown("""
<style>
html, body, [data-testid="stAppViewContainer"] {
    background-color: #111;
    color: #ddd;
    font-family: ui-monospace, "Cascadia Code", monospace;
    font-size: 13px;
}
#MainMenu, footer, header { visibility: hidden; }
[data-testid="stToolbar"] { display: none; }
[data-testid="stSidebar"] { background-color: #0d0d0d !important; }

.req-header {
    background: #1a1a2e;
    border-left: 3px solid #7c5cbf;
    padding: 8px 14px;
    border-radius: 4px;
    margin-bottom: 6px;
    font-size: 0.78rem;
    color: #bbb;
}
.req-id { color: #9b8fd4; font-weight: 600; }
.tool-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 5px 14px;
    border-radius: 4px;
    border-left: 2px solid #222;
    margin-bottom: 3px;
    font-size: 0.77rem;
    background: #181818;
}
.tool-row:hover { background: #1f1f1f; border-left-color: #555; }
.tool-name { color: #7ec8e3; font-weight: 600; min-width: 220px; }
.status-ok   { color: #4ec94e; }
.status-err  { color: #e05252; }
.status-nodata { color: #e0a252; }
.dur-bar { height: 8px; background: #7c5cbf; border-radius: 3px; min-width: 2px; }
.dur-label { color: #888; min-width: 60px; text-align: right; font-size: 0.72rem; }
.preview { color: #999; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 480px; }
</style>
""", unsafe_allow_html=True)

st.title("🔍 Tool Trace Viewer")

# ── Auto-refresh toggle ────────────────────────────────────────────────────────
col_refresh, col_clear, col_info = st.columns([2, 2, 8])
with col_refresh:
    auto_refresh = st.toggle("Auto-refresh (5s)", value=False)
with col_clear:
    if st.button("🗑 Clear traces"):
        if TRACE_FILE.exists():
            TRACE_FILE.write_text("")
        st.rerun()

if auto_refresh:
    import time
    time.sleep(5)
    st.rerun()


# ── Load traces ────────────────────────────────────────────────────────────────

def load_traces() -> list[dict]:
    if not TRACE_FILE.exists():
        return []
    lines = TRACE_FILE.read_text(encoding="utf-8").splitlines()
    entries = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except Exception:
            pass
    return entries


def group_by_request(entries: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for e in entries:
        rid = e.get("request_id") or "no-request-id"
        groups.setdefault(rid, []).append(e)
    return groups


entries = load_traces()

if not entries:
    st.info(f"No traces yet. File: `{TRACE_FILE}`\n\nAsk a question in the chat to generate traces.")
    st.stop()

groups = group_by_request(entries)
# Sort groups by first entry timestamp, newest first
sorted_rids = sorted(groups.keys(), key=lambda r: groups[r][0]["ts"], reverse=True)

st.caption(f"{len(entries)} tool calls · {len(groups)} requests · `{TRACE_FILE}`")
st.divider()

# ── Sidebar: request selector ──────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### Requests")
    selected_rid = st.radio(
        "Select request",
        options=sorted_rids,
        format_func=lambda r: (
            f"{datetime.fromtimestamp(groups[r][0]['ts']).strftime('%H:%M:%S')}  "
            f"[{r}]  ({len(groups[r])} tools)"
        ),
        label_visibility="collapsed",
    )

# ── Main: waterfall for selected request ──────────────────────────────────────

tools = groups[selected_rid]
t_start = tools[0]["ts"]
max_dur = max((t.get("duration_ms", 0) or 0) for t in tools) or 1

ts_fmt = datetime.fromtimestamp(t_start).strftime("%Y-%m-%d %H:%M:%S")
total_ms = sum(t.get("duration_ms", 0) or 0 for t in tools)

st.markdown(
    f'<div class="req-header">'
    f'<span class="req-id">{selected_rid}</span>'
    f' &nbsp;·&nbsp; {ts_fmt}'
    f' &nbsp;·&nbsp; {len(tools)} tools'
    f' &nbsp;·&nbsp; {total_ms:,} ms total'
    f'</div>',
    unsafe_allow_html=True,
)

# Header row
h1, h2, h3, h4, h5 = st.columns([3, 1, 3, 2, 5])
h1.markdown("**Tool**")
h2.markdown("**Status**")
h3.markdown("**Duration**")
h4.markdown("**ms**")
h5.markdown("**Result preview**")
st.markdown("<hr style='margin:4px 0;border-color:#222'>", unsafe_allow_html=True)

for i, t in enumerate(tools):
    tool_name = t.get("tool", "?")
    status = t.get("status", "ok")
    dur_ms = t.get("duration_ms", 0) or 0
    preview = t.get("preview", "")
    args = t.get("args", {})
    error = t.get("error")

    status_color = "status-ok" if status == "ok" else ("status-err" if status == "error" else "status-nodata")
    bar_w = max(4, int(dur_ms / max_dur * 200))

    col1, col2, col3, col4, col5 = st.columns([3, 1, 3, 2, 5])
    col1.markdown(f'`{tool_name}`')
    col2.markdown(f'<span class="{status_color}">●&nbsp;{status}</span>', unsafe_allow_html=True)
    col3.markdown(
        f'<div style="padding-top:6px"><div class="dur-bar" style="width:{bar_w}px"></div></div>',
        unsafe_allow_html=True,
    )
    col4.markdown(f'<span style="color:#888;font-size:0.75rem">{dur_ms:,} ms</span>', unsafe_allow_html=True)
    col5.markdown(f'<span style="color:#999;font-size:0.75rem">{preview[:120]}</span>', unsafe_allow_html=True)

    # Expandable detail
    with st.expander(f"↳ {tool_name} detail", expanded=False):
        if args:
            st.markdown("**Args**")
            st.json(args)
        st.markdown("**Result**")
        if error:
            st.error(error)
        else:
            st.markdown(f"```\n{preview}\n```")

st.divider()

# ── Summary table (all requests) ───────────────────────────────────────────────
with st.expander("📊 All requests summary", expanded=False):
    rows = []
    for rid in sorted_rids:
        ts = groups[rid][0]["ts"]
        n_tools = len(groups[rid])
        total = sum(t.get("duration_ms", 0) or 0 for t in groups[rid])
        n_err = sum(1 for t in groups[rid] if t.get("status") == "error")
        rows.append({
            "Time": datetime.fromtimestamp(ts).strftime("%H:%M:%S"),
            "Request ID": rid,
            "Tools": n_tools,
            "Total ms": total,
            "Errors": n_err,
        })
    import pandas as pd
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
