"""
ui/chat.py — Streamlit streaming chat UI (Bài 31).

Requires API: make api-b31   (uvicorn on :8031)
Run UI:       make ui-b31    (streamlit run ui/chat.py)

Features:
- Login: enter user_id → stored in session_state
- Auto-create conversation on first message → conversation_id in session_state
- st.write_stream() renders SSE chunks token-by-token
- Sidebar: user info, new conversation, logout
- Turn history persists within Streamlit tab session
"""

from __future__ import annotations

import json

import httpx
import streamlit as st

API_URL = "http://localhost:8031"

st.set_page_config(page_title="HPG Chat", page_icon="📈", layout="centered")


# ── Login screen ──────────────────────────────────────────────────────────────

if "user_id" not in st.session_state:
    st.title("📈 HPG Financial Chat")
    st.caption("Phân tích tài chính chứng khoán Việt Nam")

    with st.form("login_form"):
        user_id = st.text_input("User ID", placeholder="vd: hung.dao")
        tenant_id = st.text_input("Tenant", value="default")
        submitted = st.form_submit_button("Vào chat", use_container_width=True)

        if submitted:
            if not user_id.strip():
                st.error("Nhập user ID.")
            else:
                st.session_state.user_id = user_id.strip()
                st.session_state.tenant_id = tenant_id.strip() or "default"
                st.session_state.conversation_id = None
                st.session_state.messages = []
                st.session_state.turn_count = 0
                st.rerun()
    st.stop()


# ── Init state ────────────────────────────────────────────────────────────────

if "messages" not in st.session_state:
    st.session_state.messages = []
if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = None
if "turn_count" not in st.session_state:
    st.session_state.turn_count = 0
if "tenant_id" not in st.session_state:
    st.session_state.tenant_id = "default"


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("### Phiên làm việc")
    st.markdown(f"**User:** `{st.session_state.user_id}`")
    st.markdown(f"**Tenant:** `{st.session_state.tenant_id}`")

    if st.session_state.conversation_id:
        short_id = st.session_state.conversation_id[:8]
        st.markdown(f"**Conversation:** `{short_id}...`")
        st.markdown(f"**Số turn:** {st.session_state.turn_count}")
    else:
        st.caption("Chưa có conversation (tạo khi gửi tin đầu tiên)")

    st.divider()

    if st.button("🆕 Cuộc trò chuyện mới", use_container_width=True):
        st.session_state.conversation_id = None
        st.session_state.messages = []
        st.session_state.turn_count = 0
        st.rerun()

    if st.button("🚪 Đăng xuất", use_container_width=True):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()


# ── Chat header ───────────────────────────────────────────────────────────────

st.title("📈 HPG Financial Chat")
if not st.session_state.conversation_id:
    st.caption("Gửi câu hỏi đầu tiên để bắt đầu conversation mới.")


# ── Render history ────────────────────────────────────────────────────────────

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _create_conversation(user_id: str, tenant_id: str) -> str:
    resp = httpx.post(
        f"{API_URL}/conversations",
        json={"user_id": user_id, "tenant_id": tenant_id},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["conversation_id"]


def _sse_chunks(conversation_id: str, user_id: str, tenant_id: str, message: str, is_first_turn: bool):
    """Sync generator yielding text strings from SSE stream — fed to st.write_stream()."""
    with httpx.stream(
        "POST",
        f"{API_URL}/conversations/{conversation_id}/messages/stream",
        json={
            "user_id": user_id,
            "tenant_id": tenant_id,
            "message": message,
            "is_first_turn": is_first_turn,
        },
        timeout=120,
        headers={"Accept": "text/event-stream"},
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if line.startswith("data: "):
                try:
                    payload = json.loads(line[6:])
                    if "text" in payload:
                        yield payload["text"]
                except json.JSONDecodeError:
                    pass


# ── Chat input ────────────────────────────────────────────────────────────────

if prompt := st.chat_input("Hỏi về HPG, VCB, FPT, thị trường..."):
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

    # Append + display user message
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Stream assistant reply
    with st.chat_message("assistant"):
        try:
            full_reply = st.write_stream(
                _sse_chunks(
                    conversation_id=st.session_state.conversation_id,
                    user_id=st.session_state.user_id,
                    tenant_id=st.session_state.tenant_id,
                    message=prompt,
                    is_first_turn=is_first_turn,
                )
            )
        except Exception as exc:
            st.error(f"Lỗi stream: {exc}\n\nKiểm tra API đang chạy: make api-b31")
            st.stop()

    st.session_state.messages.append({"role": "assistant", "content": full_reply})
    st.session_state.turn_count += 1
