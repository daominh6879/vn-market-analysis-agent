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

# ── CSS — dark sidebar + clean layout ─────────────────────────────────────────

st.markdown("""
<style>
/* Sidebar background */
[data-testid="stSidebar"] { background-color: #171717; }
[data-testid="stSidebar"] * { color: #ececec !important; }

/* Active conversation button */
.conv-active button {
    background-color: #2a2a2a !important;
    border-left: 3px solid #10a37f !important;
}

/* Make sidebar buttons look like conversation items */
[data-testid="stSidebar"] .stButton button {
    background: transparent;
    border: none;
    text-align: left;
    padding: 6px 10px;
    width: 100%;
    border-radius: 6px;
    font-size: 0.85rem;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    cursor: pointer;
}
[data-testid="stSidebar"] .stButton button:hover {
    background-color: #2a2a2a !important;
}

/* New chat button */
.new-chat-btn button {
    background-color: #10a37f !important;
    color: white !important;
    border-radius: 8px;
    font-weight: 600;
}

/* Status caption */
.status-box { font-size: 0.8rem; color: #888; font-style: italic; }

/* Hide streamlit branding */
#MainMenu, footer { visibility: hidden; }
</style>
""", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _api_get(path: str, **params):
    return httpx.get(f"{API_URL}{path}", params=params, timeout=10)


def _api_post(path: str, **body):
    return httpx.post(f"{API_URL}{path}", json=body, timeout=10)


def _load_conversations(user_id: str, tenant_id: str) -> list[dict]:
    try:
        r = _api_get(f"/users/{user_id}/conversations", tenant_id=tenant_id)
        r.raise_for_status()
        return r.json().get("conversations", [])
    except Exception:
        return []


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
        return f"#{cid[:8]}"
    return title[:45] + "…" if len(title) > 45 else title


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
    "loading_history":        "Đang tải lịch sử...",
    "routing":                "Đang phân tích câu hỏi...",
    "collecting_data":        "Đang thu thập dữ liệu giá...",
    "collecting_market_data": "Đang thu thập dữ liệu thị trường...",
    "collecting_macro_data":  "Đang thu thập dữ liệu vĩ mô...",
    "fetching_news":          "Đang lấy tin tức...",
    "querying_documents":     "Đang tìm kiếm tài liệu...",
    "streaming":              "Đang tạo câu trả lời...",
}


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
                        if agent:
                            status_placeholder.caption(f"{agent_label} · {step_label}")
                        else:
                            status_placeholder.caption(step_label)
                    elif current_event == "done":
                        agent = payload.get("agent", "")
                        agent_label = _AGENT_LABELS.get(agent, agent)
                        status_placeholder.caption(f"✅ {agent_label}")
                    elif current_event == "error":
                        err = payload.get("error", "Lỗi không xác định")
                        status_placeholder.error(f"❌ {err}")
                except json.JSONDecodeError:
                    pass


# ── Login screen ──────────────────────────────────────────────────────────────

if "user_id" not in st.session_state:
    col = st.columns([1, 2, 1])[1]
    with col:
        st.markdown("<br><br>", unsafe_allow_html=True)
        st.title("📈 VN Stock Chat")
        st.caption("Trợ lý phân tích tài chính chứng khoán Việt Nam")
        st.markdown("<br>", unsafe_allow_html=True)

        with st.form("login_form"):
            user_id = st.text_input("User ID", placeholder="vd: hung.dao")
            tenant_id = st.text_input("Tenant", value="default")
            submitted = st.form_submit_button("Bắt đầu →", use_container_width=True)

            if submitted:
                if not user_id.strip():
                    st.error("Nhập user ID.")
                else:
                    uid = user_id.strip()
                    tid = tenant_id.strip() or "default"
                    st.session_state.user_id = uid
                    st.session_state.tenant_id = tid
                    st.session_state.conversation_id = None
                    st.session_state.messages = []
                    st.session_state.turn_count = 0
                    # Load conversation list
                    st.session_state.conversations = _load_conversations(uid, tid)
                    # Resume latest if exists
                    if st.session_state.conversations:
                        latest = st.session_state.conversations[0]
                        st.session_state.conversation_id = latest["conversation_id"]
                        st.session_state.messages = _load_history(latest["conversation_id"])
                        st.session_state.turn_count = int(latest.get("turn_count") or 0)
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
    # New chat button
    st.markdown('<div class="new-chat-btn">', unsafe_allow_html=True)
    if st.button("✏️  Cuộc trò chuyện mới", use_container_width=True, key="new_chat"):
        st.session_state.conversation_id = None
        st.session_state.messages = []
        st.session_state.turn_count = 0
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # Conversation list
    convs: list[dict] = st.session_state.conversations
    if convs:
        st.markdown("**Lịch sử trò chuyện**")
        for conv in convs:
            cid = conv["conversation_id"]
            title = _fmt_title(conv.get("title"), cid)
            date_str = _fmt_date(conv.get("created_at"))
            is_active = cid == st.session_state.conversation_id
            label = f"{'▶ ' if is_active else ''}{title}"
            # Tooltip shows date + turn count
            turns = conv.get("turn_count") or 0
            help_text = f"{date_str} · {turns} turn"
            if st.button(label, key=f"conv_{cid}", use_container_width=True, help=help_text):
                if cid != st.session_state.conversation_id:
                    st.session_state.conversation_id = cid
                    st.session_state.messages = _load_history(cid)
                    st.session_state.turn_count = int(turns)
                    st.rerun()
    else:
        st.caption("Chưa có cuộc trò chuyện nào.")

    # Push logout to bottom
    st.markdown("<br>" * 3, unsafe_allow_html=True)
    st.divider()
    st.markdown(f"👤 **{st.session_state.user_id}**")
    if st.button("Đăng xuất", use_container_width=True, key="logout"):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()


# ── Main chat area ────────────────────────────────────────────────────────────

# Empty state — no conversation yet
if not st.session_state.conversation_id and not st.session_state.messages:
    st.markdown("<br>" * 4, unsafe_allow_html=True)
    st.markdown(
        "<h2 style='text-align:center;'>📈 Hôm nay bạn muốn phân tích gì?</h2>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<p style='text-align:center;color:#888;'>Hỏi về giá cổ phiếu, phân tích kỹ thuật, "
        "tài chính doanh nghiệp, tin tức thị trường...</p>",
        unsafe_allow_html=True,
    )
    # Quick-start suggestions
    cols = st.columns(2)
    suggestions = [
        "HPG giá hôm nay và xu hướng kỹ thuật?",
        "VCB P/E hiện tại so với ngành ngân hàng?",
        "Thị trường chứng khoán hôm nay thế nào?",
        "Lọc cổ phiếu RSI dưới 30 ngành thép?",
    ]
    for i, s in enumerate(suggestions):
        with cols[i % 2]:
            if st.button(s, key=f"suggest_{i}", use_container_width=True):
                st.session_state._pending_prompt = s
                st.rerun()

# Render message history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])


# ── Chat input ────────────────────────────────────────────────────────────────

# Handle suggestion-button click (pending prompt from empty state)
_pending = st.session_state.pop("_pending_prompt", None)
prompt = st.chat_input("Hỏi về HPG, VCB, FPT, thị trường...") or _pending

if prompt:
    is_first_turn = st.session_state.conversation_id is None

    # Create conversation on first message
    if is_first_turn:
        try:
            st.session_state.conversation_id = _create_conversation(
                st.session_state.user_id,
                st.session_state.tenant_id,
            )
        except Exception as exc:
            st.error(f"Lỗi tạo conversation: {exc}")
            st.stop()

    # Show user message
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Stream assistant reply
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

        # Refresh conversation list (title appears after first message)
        if is_first_turn or st.session_state.turn_count == 1:
            st.session_state.conversations = _load_conversations(
                st.session_state.user_id, st.session_state.tenant_id
            )
            st.rerun()
