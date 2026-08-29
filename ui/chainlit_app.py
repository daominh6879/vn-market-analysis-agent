"""
ui/chainlit_app.py — Chainlit chat UI (replaces Streamlit).

Run: make ui-chainlit   (or: chainlit run ui/chainlit_app.py -w)
API: make api-b31       (uvicorn on :8031)

Login: username = user_id, password = tenant_id (default: "default")
"""

from __future__ import annotations

import json

import chainlit as cl
import httpx

API_URL = "http://localhost:8031"

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


# ── Auth ──────────────────────────────────────────────────────────────────────

@cl.password_auth_callback
def auth_callback(username: str, password: str) -> cl.User | None:
    if not username.strip():
        return None
    tenant = password.strip() or "default"
    return cl.User(identifier=username.strip(), metadata={"tenant_id": tenant})


# ── Starters ──────────────────────────────────────────────────────────────────

@cl.set_starters
async def set_starters() -> list[cl.Starter]:
    return [
        cl.Starter(
            label="📊 Giá HPG hôm nay",
            message="HPG giá hôm nay và xu hướng kỹ thuật?",
        ),
        cl.Starter(
            label="🏦 VCB P/E",
            message="VCB P/E hiện tại so với ngành ngân hàng?",
        ),
        cl.Starter(
            label="🌏 Thị trường hôm nay",
            message="Thị trường chứng khoán hôm nay thế nào?",
        ),
        cl.Starter(
            label="🔍 Lọc RSI ngành thép",
            message="Lọc cổ phiếu RSI dưới 30 ngành thép?",
        ),
    ]


# ── Session init ──────────────────────────────────────────────────────────────

@cl.on_chat_start
async def on_chat_start() -> None:
    user = cl.user_session.get("user")
    cl.user_session.set("user_id", user.identifier)
    cl.user_session.set("tenant_id", user.metadata.get("tenant_id", "default"))
    cl.user_session.set("conversation_id", None)


# ── Message handler ───────────────────────────────────────────────────────────

@cl.on_message
async def on_message(message: cl.Message) -> None:
    user_id: str = cl.user_session.get("user_id")
    tenant_id: str = cl.user_session.get("tenant_id")
    conversation_id: str | None = cl.user_session.get("conversation_id")
    is_first_turn = conversation_id is None

    # Create conversation on first message
    if is_first_turn:
        try:
            r = httpx.post(
                f"{API_URL}/conversations",
                json={"user_id": user_id, "tenant_id": tenant_id},
                timeout=10,
            )
            r.raise_for_status()
            conversation_id = r.json()["conversation_id"]
            cl.user_session.set("conversation_id", conversation_id)
        except Exception as exc:
            await cl.Message(content=f"❌ Lỗi tạo conversation: {exc}").send()
            return

    response_msg = cl.Message(content="")
    await response_msg.send()

    current_step: cl.Step | None = None

    async with httpx.AsyncClient() as client:
        async with client.stream(
            "POST",
            f"{API_URL}/conversations/{conversation_id}/messages/stream",
            json={
                "user_id": user_id,
                "tenant_id": tenant_id,
                "message": message.content,
                "is_first_turn": is_first_turn,
            },
            timeout=180,
            headers={"Accept": "text/event-stream"},
        ) as resp:
            resp.raise_for_status()
            current_event = ""

            async for line in resp.aiter_lines():
                if line.startswith("event: "):
                    current_event = line[7:].strip()

                elif line.startswith("data: "):
                    try:
                        payload = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue

                    if "text" in payload:
                        # Close any open step before streaming text
                        if current_step:
                            current_step.output = "✓"
                            await current_step.update()
                            current_step = None
                        await response_msg.stream_token(payload["text"])

                    elif current_event == "status":
                        step_key = payload.get("step", "")
                        agent = payload.get("agent", "")
                        agent_label = _AGENT_LABELS.get(agent, agent)
                        step_label = _STEP_LABELS.get(step_key, step_key)
                        label = f"{agent_label} · {step_label}" if agent else step_label

                        if current_step:
                            current_step.output = "✓"
                            await current_step.update()

                        current_step = cl.Step(
                            name=label,
                            type="tool",
                            parent_id=response_msg.id,
                            show_input=False,
                        )
                        await current_step.send()

                    elif current_event == "done":
                        agent = payload.get("agent", "")
                        agent_label = _AGENT_LABELS.get(agent, agent)
                        if current_step:
                            current_step.output = f"✓ {agent_label}"
                            await current_step.update()
                            current_step = None

                    elif current_event == "error":
                        err = payload.get("error", "Lỗi không xác định")
                        await response_msg.stream_token(f"\n\n❌ {err}")

    await response_msg.update()
