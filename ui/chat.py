"""
ui/chat.py — Streamlit chat UI, ChatGPT-style (Bài 31+).

Requires API: make api-b31   (uvicorn on :8031)
Run UI:       make ui-b31    (streamlit run ui/chat.py)
"""

from __future__ import annotations

import json
from datetime import datetime

import httpx
import streamlit as st

API_URL = "http://localhost:8031"

st.set_page_config(page_title="VN Stock Chat", page_icon="📈", layout="wide")

# ── CSS ────────────────────────────────────────────────────────────────────────

st.markdown("""
<style>
/* ── Global ── */
html, body, [data-testid="stAppViewContainer"] {
    background-color: #212121;
    color: #ececec;
    font-family: "Söhne", "ui-sans-serif", system-ui, -apple-system, sans-serif;
}

/* ── Hide Streamlit chrome ── */
#MainMenu, footer, header { visibility: hidden; }
[data-testid="stToolbar"] { display: none; }
.stDeployButton { display: none; }

/* ── Sidebar ── */
[data-testid="stSidebar"] {
    background-color: #171717 !important;
    border-right: 1px solid #2a2a2a;
}
[data-testid="stSidebar"] * { color: #ececec !important; }

/* Conversation list buttons */
[data-testid="stSidebar"] .stButton > button {
    background: transparent !important;
    border: none !important;
    text-align: left !important;
    padding: 8px 12px !important;
    width: 100% !important;
    border-radius: 8px !important;
    font-size: 0.875rem !important;
    color: #c5c5d2 !important;
    white-space: nowrap !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
    transition: background 0.15s ease !important;
}
[data-testid="stSidebar"] .stButton > button:hover {
    background-color: #2a2a2a !important;
    color: #ececec !important;
}

/* New chat button */
.new-chat-btn > div > button {
    background-color: #2a2a2a !important;
    border: 1px solid #3d3d3d !important;
    color: #ececec !important;
    border-radius: 8px !important;
    font-size: 0.875rem !important;
    font-weight: 500 !important;
    padding: 8px 14px !important;
    transition: background 0.15s ease, border-color 0.15s ease !important;
}
.new-chat-btn > div > button:hover {
    background-color: #363636 !important;
    border-color: #555 !important;
}

/* Active conversation highlight */
.conv-active > div > button {
    background-color: #2a2a2a !important;
    border-left: 2px solid #10a37f !important;
    color: #fff !important;
}

/* Logout button */
.logout-btn > div > button {
    background: transparent !important;
    border: 1px solid #3d3d3d !important;
    color: #888 !important;
    border-radius: 8px !important;
    font-size: 0.8rem !important;
}
.logout-btn > div > button:hover {
    background-color: #2a2a2a !important;
    color: #ccc !important;
    border-color: #555 !important;
}

/* Sidebar section label */
.sidebar-section {
    font-size: 0.7rem;
    font-weight: 600;
    color: #666 !important;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    padding: 4px 12px;
    margin-top: 8px;
    margin-bottom: 2px;
}

/* ── Main area ── */
[data-testid="stMain"] {
    background-color: #212121;
}

/* Chat messages container */
[data-testid="stChatMessageContainer"] {
    max-width: 760px;
    margin: 0 auto;
}

/* User bubble */
[data-testid="stChatMessage"][data-testid*="user"],
.stChatMessage[aria-label*="user"] {
    background-color: #2f2f2f;
    border-radius: 18px;
    padding: 14px 18px;
}

/* Assistant bubble */
[data-testid="stChatMessage"] {
    background: transparent;
    padding: 12px 0;
}

/* Avatar size */
[data-testid="stChatMessageAvatar"] {
    width: 32px !important;
    height: 32px !important;
}

/* Chat input bar */
[data-testid="stChatInput"] {
    background-color: #2f2f2f !important;
    border: 1px solid #3d3d3d !important;
    border-radius: 16px !important;
    max-width: 760px;
    margin: 0 auto;
}
[data-testid="stChatInput"] textarea {
    background: transparent !important;
    color: #ececec !important;
    font-size: 1rem !important;
}
[data-testid="stChatInput"] textarea::placeholder {
    color: #666 !important;
}
[data-testid="stChatInputSubmitButton"] button {
    background-color: #10a37f !important;
    border-radius: 8px !important;
}
[data-testid="stChatInputSubmitButton"] button:hover {
    background-color: #0e8f6e !important;
}

/* ── Empty state / welcome ── */
.welcome-title {
    font-size: 2rem;
    font-weight: 600;
    color: #ececec;
    text-align: center;
    margin-bottom: 8px;
    letter-spacing: -0.02em;
}
.welcome-sub {
    font-size: 1rem;
    color: #888;
    text-align: center;
    margin-bottom: 32px;
}

/* Suggestion cards */
.suggestion-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
    max-width: 640px;
    margin: 0 auto 32px auto;
}
.suggestion-card {
    background: #2f2f2f;
    border: 1px solid #3d3d3d;
    border-radius: 12px;
    padding: 14px 16px;
    cursor: pointer;
    transition: background 0.15s, border-color 0.15s;
    font-size: 0.9rem;
    color: #d1d1d1;
    line-height: 1.4;
}
.suggestion-card:hover {
    background: #3a3a3a;
    border-color: #555;
    color: #fff;
}
.suggestion-card .icon {
    font-size: 1.1rem;
    margin-bottom: 4px;
    display: block;
}

/* Suggestion buttons (overlay on cards) */
.suggest-btn > div > button {
    background: #2f2f2f !important;
    border: 1px solid #3d3d3d !important;
    border-radius: 12px !important;
    color: #c5c5d2 !important;
    font-size: 0.875rem !important;
    padding: 14px 16px !important;
    text-align: left !important;
    line-height: 1.4 !important;
    height: auto !important;
    min-height: 70px !important;
    transition: background 0.15s, border-color 0.15s !important;
}
.suggest-btn > div > button:hover {
    background: #363636 !important;
    border-color: #555 !important;
    color: #fff !important;
}

/* ── Status bar ── */
.status-thinking {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    font-size: 0.8rem;
    color: #888;
    padding: 4px 0;
    font-style: italic;
}
.status-thinking::before {
    content: "";
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #10a37f;
    animation: pulse 1.2s ease-in-out infinite;
    flex-shrink: 0;
}
@keyframes pulse {
    0%, 100% { opacity: 0.3; transform: scale(0.8); }
    50% { opacity: 1; transform: scale(1.1); }
}

/* ── Login ── */
.login-container {
    max-width: 400px;
    margin: 80px auto 0 auto;
    padding: 40px;
    background: #2a2a2a;
    border: 1px solid #3d3d3d;
    border-radius: 16px;
}
.login-title {
    font-size: 1.6rem;
    font-weight: 600;
    text-align: center;
    margin-bottom: 6px;
    color: #ececec;
}
.login-sub {
    font-size: 0.9rem;
    color: #888;
    text-align: center;
    margin-bottom: 28px;
}

/* Form labels */
.stTextInput label { color: #c5c5d2 !important; font-size: 0.875rem !important; }
.stTextInput input {
    background: #1a1a1a !important;
    border: 1px solid #3d3d3d !important;
    border-radius: 8px !important;
    color: #ececec !important;
    font-size: 0.95rem !important;
}
.stTextInput input:focus {
    border-color: #10a37f !important;
    box-shadow: 0 0 0 2px rgba(16,163,127,0.2) !important;
}

/* Login submit button */
.login-submit > div > button {
    background-color: #10a37f !important;
    color: white !important;
    border: none !important;
    border-radius: 8px !important;
    font-weight: 600 !important;
    font-size: 0.95rem !important;
    padding: 10px !important;
    transition: background 0.15s !important;
}
.login-submit > div > button:hover {
    background-color: #0e8f6e !important;
}

/* ── Markdown in messages ── */
[data-testid="stMarkdownContainer"] p {
    line-height: 1.7;
    color: #d1d1d1;
}
[data-testid="stMarkdownContainer"] code {
    background: #1a1a1a;
    border-radius: 4px;
    padding: 2px 6px;
    font-size: 0.85em;
    color: #e2b96f;
}
[data-testid="stMarkdownContainer"] pre {
    background: #1a1a1a !important;
    border: 1px solid #3d3d3d;
    border-radius: 10px;
    padding: 16px;
}
[data-testid="stMarkdownContainer"] table {
    border-collapse: collapse;
    width: 100%;
    font-size: 0.875rem;
}
[data-testid="stMarkdownContainer"] th {
    background: #2a2a2a;
    color: #ececec;
    padding: 8px 12px;
    border: 1px solid #3d3d3d;
    font-weight: 600;
}
[data-testid="stMarkdownContainer"] td {
    padding: 7px 12px;
    border: 1px solid #2a2a2a;
    color: #c5c5d2;
}
[data-testid="stMarkdownContainer"] tr:hover td {
    background: #2a2a2a;
}

/* ── Divider ── */
[data-testid="stDivider"] { border-color: #2a2a2a !important; }

/* Conversation row: zero gap, vertically centered */
[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] {
    gap: 0px !important;
    align-items: center !important;
    margin-bottom: 0 !important;
}
[data-testid="stSidebar"] [data-testid="stColumn"] {
    padding: 0 !important;
}

/* Delete button */
.del-btn {
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    height: 100% !important;
    padding: 0 !important;
}
.del-btn > div {
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    width: 100% !important;
    padding: 0 !important;
}
.del-btn > div > button {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    color: #555 !important;
    font-size: 0.8rem !important;
    padding: 0 6px !important;
    border-radius: 6px !important;
    min-height: 34px !important;
    height: 34px !important;
    width: 28px !important;
    line-height: 1 !important;
    transition: color 0.15s, background 0.15s !important;
}
.del-btn > div > button:hover {
    color: #e55 !important;
    background-color: rgba(220,50,50,0.15) !important;
}

/* Memory item row */
.memory-item {
    display: flex;
    align-items: flex-start;
    gap: 6px;
    padding: 6px 12px;
    border-radius: 6px;
    font-size: 0.8rem;
    color: #aaa;
    line-height: 1.4;
}
.memory-item:hover { background: #222; }
.memory-key {
    color: #10a37f;
    font-weight: 600;
    font-size: 0.75rem;
}

/* Scrollbar */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #3d3d3d; border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: #555; }
</style>
""", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _api_get(path: str, **params):
    return httpx.get(f"{API_URL}{path}", params=params, timeout=10)


def _api_post(path: str, **body):
    return httpx.post(f"{API_URL}{path}", json=body, timeout=10)


def _api_delete(path: str):
    return httpx.delete(f"{API_URL}{path}", timeout=10)


def _load_conversations(user_id: str, tenant_id: str) -> list[dict]:
    try:
        r = _api_get(f"/users/{user_id}/conversations", tenant_id=tenant_id)
        r.raise_for_status()
        return r.json().get("conversations", [])
    except Exception:
        return []


def _load_user_memory(user_id: str, tenant_id: str) -> list[dict]:
    try:
        r = _api_get(f"/users/{user_id}/memory", tenant_id=tenant_id, max_items=20)
        r.raise_for_status()
        return r.json().get("memory", [])
    except Exception:
        return []


def _delete_conversation(conversation_id: str) -> bool:
    try:
        r = _api_delete(f"/conversations/{conversation_id}")
        return r.status_code == 200
    except Exception:
        return False


def _delete_memory_item(user_id: str, item_id: str) -> bool:
    try:
        r = _api_delete(f"/users/{user_id}/memory/{item_id}")
        return r.status_code == 200
    except Exception:
        return False


def _load_history(conversation_id: str) -> list[dict]:
    try:
        r = _api_get(f"/conversations/{conversation_id}/history", limit=50)
        r.raise_for_status()
        return r.json().get("messages", [])
    except Exception:
        return []


def _create_conversation(user_id: str, tenant_id: str) -> str:
    r = _api_post("/conversations", user_id=user_id, tenant_id=tenant_id)
    r.raise_for_status()
    return r.json()["conversation_id"]


def _fmt_title(title: str | None, cid: str) -> str:
    if not title:
        return f"Cuộc trò chuyện #{cid[:6]}"
    return title[:42] + "…" if len(title) > 42 else title


def _fmt_date(created_at) -> str:
    if not created_at:
        return ""
    try:
        if isinstance(created_at, str):
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        else:
            dt = created_at
        return dt.strftime("%d/%m %H:%M")
    except Exception:
        return ""


_AGENT_LABELS = {
    "price_action":       "📊 Dòng tiền",
    "technical_analysis": "📈 Kỹ thuật",
    "fundamentals":       "🏦 Tài chính cơ bản",
    "macro_sector":       "🌐 Vĩ mô / Ngành",
    "news_sentiment":     "📰 Tin tức",
    "screening":          "🔍 Lọc cổ phiếu",
    "market_brief":       "🌏 Thị trường",
    "qa_document":        "📄 Tài liệu",
    "conversation":       "💬 Hội thoại",
}

_STEP_LABELS = {
    "loading_history":        "Đang tải lịch sử",
    "routing":                "Đang phân tích câu hỏi",
    "collecting_data":        "Đang thu thập dữ liệu giá",
    "collecting_market_data": "Đang thu thập dữ liệu thị trường",
    "collecting_macro_data":  "Đang thu thập dữ liệu vĩ mô",
    "fetching_news":          "Đang lấy tin tức",
    "querying_documents":     "Đang tìm kiếm tài liệu",
    "streaming":              "Đang soạn câu trả lời",
}

_SUGGESTIONS = [
    ("📊", "HPG giá hôm nay và xu hướng kỹ thuật?"),
    ("🏦", "VCB P/E hiện tại so với ngành ngân hàng?"),
    ("🌏", "Thị trường chứng khoán hôm nay thế nào?"),
    ("🔍", "Lọc cổ phiếu RSI dưới 30 ngành thép?"),
]


def _sse_chunks(conversation_id: str, user_id: str, tenant_id: str,
                message: str, is_first_turn: bool, status_placeholder):
    """Sync generator → text chunks for st.write_stream()."""
    with httpx.stream(
        "POST",
        f"{API_URL}/conversations/{conversation_id}/messages/stream",
        json={
            "user_id": user_id,
            "tenant_id": tenant_id,
            "message": message,
            "is_first_turn": is_first_turn,
        },
        timeout=180,
        headers={"Accept": "text/event-stream"},
    ) as resp:
        resp.raise_for_status()
        current_event = ""
        for line in resp.iter_lines():
            if line.startswith("event: "):
                current_event = line[7:].strip()
            elif line.startswith("data: "):
                try:
                    payload = json.loads(line[6:])
                    if "text" in payload:
                        yield payload["text"]
                    elif current_event == "status":
                        step = payload.get("step", "")
                        agent = payload.get("agent", "")
                        agent_label = _AGENT_LABELS.get(agent, agent)
                        step_label = _STEP_LABELS.get(step, step)
                        label = f"{agent_label} · {step_label}" if agent else step_label
                        status_placeholder.markdown(
                            f'<div class="status-thinking">{label}...</div>',
                            unsafe_allow_html=True,
                        )
                    elif current_event == "done":
                        agent = payload.get("agent", "")
                        agent_label = _AGENT_LABELS.get(agent, agent)
                        status_placeholder.markdown(
                            f'<div class="status-thinking" style="color:#10a37f;">✓ {agent_label}</div>',
                            unsafe_allow_html=True,
                        )
                    elif current_event == "error":
                        err = payload.get("error", "Lỗi không xác định")
                        status_placeholder.error(f"❌ {err}")
                except json.JSONDecodeError:
                    pass


# ── Auto-login from URL params (persists across refresh) ─────────────────────

def _init_session(uid: str, tid: str) -> None:
    st.session_state.user_id = uid
    st.session_state.tenant_id = tid
    st.session_state.conversation_id = None
    st.session_state.messages = []
    st.session_state.turn_count = 0
    st.session_state.conversations = _load_conversations(uid, tid)
    if st.session_state.conversations:
        latest = st.session_state.conversations[0]
        st.session_state.conversation_id = latest["conversation_id"]
        st.session_state.messages = _load_history(latest["conversation_id"])
        st.session_state.turn_count = int(latest.get("turn_count") or 0)


if "user_id" not in st.session_state:
    # Try restoring from URL query params first
    _qp = st.query_params
    _quid = _qp.get("u", "").strip()
    _qtid = _qp.get("t", "default").strip() or "default"

    if _quid:
        _init_session(_quid, _qtid)
        st.rerun()
    else:
        # Show login form
        _, col, _ = st.columns([1, 1.4, 1])
        with col:
            st.markdown("""
            <div class="login-container">
                <div class="login-title">📈 VN Stock Chat</div>
                <div class="login-sub">Trợ lý phân tích tài chính chứng khoán Việt Nam</div>
            </div>
            """, unsafe_allow_html=True)

            with st.form("login_form"):
                user_id = st.text_input("User ID", placeholder="vd: hung.dao")
                tenant_id = st.text_input("Tenant", value="default")
                st.markdown('<div class="login-submit">', unsafe_allow_html=True)
                submitted = st.form_submit_button("Bắt đầu →", use_container_width=True)
                st.markdown("</div>", unsafe_allow_html=True)

                if submitted:
                    if not user_id.strip():
                        st.error("Nhập user ID.")
                    else:
                        uid = user_id.strip()
                        tid = tenant_id.strip() or "default"
                        st.query_params["u"] = uid
                        st.query_params["t"] = tid
                        _init_session(uid, tid)
                        st.rerun()
        st.stop()


# ── Init missing state ────────────────────────────────────────────────────────

if "messages" not in st.session_state:
    st.session_state.messages = []
if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = None
if "turn_count" not in st.session_state:
    st.session_state.turn_count = 0
if "tenant_id" not in st.session_state:
    st.session_state.tenant_id = "default"
if "conversations" not in st.session_state:
    st.session_state.conversations = _load_conversations(
        st.session_state.user_id, st.session_state.tenant_id
    )


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown('<div class="new-chat-btn">', unsafe_allow_html=True)
    if st.button("✏️  Cuộc trò chuyện mới", use_container_width=True, key="new_chat"):
        st.session_state.conversation_id = None
        st.session_state.messages = []
        st.session_state.turn_count = 0
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    convs: list[dict] = st.session_state.conversations
    if convs:
        st.markdown('<div class="sidebar-section">Lịch sử</div>', unsafe_allow_html=True)
        for conv in convs:
            cid = conv["conversation_id"]
            title = _fmt_title(conv.get("title"), cid)
            date_str = _fmt_date(conv.get("created_at"))
            is_active = cid == st.session_state.conversation_id
            turns = conv.get("turn_count") or 0
            help_text = f"{date_str} · {turns} lượt"

            if is_active:
                st.markdown('<div class="conv-active">', unsafe_allow_html=True)

            col_title, col_del = st.columns([6, 1])
            with col_title:
                if st.button(title, key=f"conv_{cid}", use_container_width=True, help=help_text):
                    if cid != st.session_state.conversation_id:
                        st.session_state.conversation_id = cid
                        st.session_state.messages = _load_history(cid)
                        st.session_state.turn_count = int(turns)
                        st.rerun()
            with col_del:
                st.markdown('<div class="del-btn">', unsafe_allow_html=True)
                if st.button("🗑", key=f"del_conv_{cid}", help="Xóa cuộc trò chuyện"):
                    _delete_conversation(cid)
                    if is_active:
                        st.session_state.conversation_id = None
                        st.session_state.messages = []
                        st.session_state.turn_count = 0
                    st.session_state.conversations = _load_conversations(
                        st.session_state.user_id, st.session_state.tenant_id
                    )
                    st.rerun()
                st.markdown("</div>", unsafe_allow_html=True)

            if is_active:
                st.markdown("</div>", unsafe_allow_html=True)
    else:
        st.markdown(
            "<p style='font-size:0.8rem;color:#555;padding:8px 12px;'>Chưa có cuộc trò chuyện nào.</p>",
            unsafe_allow_html=True,
        )

    # ── User memory section ───────────────────────────────────────────────────
    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
    with st.expander("🧠 Bộ nhớ người dùng", expanded=False):
        mem_items = _load_user_memory(st.session_state.user_id, st.session_state.tenant_id)
        if mem_items:
            for item in mem_items:
                iid = item["id"]
                key = item.get("key", "")
                val = item.get("value", "")
                conf = item.get("confidence", 0)
                mcol1, mcol2 = st.columns([6, 1])
                with mcol1:
                    st.markdown(
                        f"<div class='memory-item'>"
                        f"<div><span class='memory-key'>{key}</span><br>{val}"
                        f"<span style='color:#555;font-size:0.7rem;'> ({conf:.0%})</span></div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                with mcol2:
                    st.markdown('<div class="del-btn">', unsafe_allow_html=True)
                    if st.button("🗑", key=f"del_mem_{iid}", help="Xóa ký ức này"):
                        _delete_memory_item(st.session_state.user_id, iid)
                        st.rerun()
                    st.markdown("</div>", unsafe_allow_html=True)
        else:
            st.markdown(
                "<p style='font-size:0.78rem;color:#555;'>Chưa có ký ức nào.</p>",
                unsafe_allow_html=True,
            )

    # Bottom: user info + logout
    st.markdown("<div style='flex:1'></div>", unsafe_allow_html=True)
    for _ in range(8):
        st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='padding:8px 12px;font-size:0.8rem;color:#888;'>"
        f"👤 {st.session_state.user_id}</div>",
        unsafe_allow_html=True,
    )
    st.markdown('<div class="logout-btn">', unsafe_allow_html=True)
    if st.button("Đăng xuất", use_container_width=True, key="logout"):
        st.query_params.clear()
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)


# ── Main chat area ────────────────────────────────────────────────────────────

# Empty / welcome state
if not st.session_state.conversation_id and not st.session_state.messages:
    st.markdown("<div style='height:80px'></div>", unsafe_allow_html=True)
    st.markdown(
        '<div class="welcome-title">Hôm nay bạn muốn phân tích gì?</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="welcome-sub">Hỏi về giá cổ phiếu, phân tích kỹ thuật, tài chính doanh nghiệp, tin tức thị trường</div>',
        unsafe_allow_html=True,
    )
    st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)

    cols = st.columns(2, gap="medium")
    for i, (icon, text) in enumerate(_SUGGESTIONS):
        with cols[i % 2]:
            st.markdown('<div class="suggest-btn">', unsafe_allow_html=True)
            if st.button(f"{icon}  {text}", key=f"suggest_{i}", use_container_width=True):
                st.session_state._pending_prompt = text
                st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div style='height:40px'></div>", unsafe_allow_html=True)

# Render message history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])


# ── Chat input ────────────────────────────────────────────────────────────────

_pending = st.session_state.pop("_pending_prompt", None)
prompt = st.chat_input("Hỏi về HPG, VCB, FPT, thị trường...") or _pending

if prompt:
    is_first_turn = st.session_state.conversation_id is None

    if is_first_turn:
        try:
            st.session_state.conversation_id = _create_conversation(
                st.session_state.user_id,
                st.session_state.tenant_id,
            )
        except Exception as exc:
            st.error(f"Lỗi tạo conversation: {exc}")
            st.stop()

    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        status_box = st.empty()
        try:
            full_reply = st.write_stream(
                _sse_chunks(
                    conversation_id=st.session_state.conversation_id,
                    user_id=st.session_state.user_id,
                    tenant_id=st.session_state.tenant_id,
                    message=prompt,
                    is_first_turn=is_first_turn,
                    status_placeholder=status_box,
                )
            )
            status_box.empty()
        except Exception as exc:
            st.error(f"Lỗi stream: {exc}\n\nKiểm tra API đang chạy: make api-b31")
            st.stop()

    if full_reply:
        st.session_state.messages.append({"role": "assistant", "content": full_reply})
        st.session_state.turn_count += 1

        if is_first_turn or st.session_state.turn_count == 1:
            st.session_state.conversations = _load_conversations(
                st.session_state.user_id, st.session_state.tenant_id
            )
            st.rerun()
