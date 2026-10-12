"""
agents/guardrails.py — fixed output texts and the output scrubber (no LLM, no DB).

Deciding WHAT a question is (off-topic, social, risky, which language) belongs to the router LLM
(agents/conversation_router.py, agents/classifier.py) — there is no keyword/regex detector here.

  off_topic_reply(lang)       fixed decline text
  disclaimer_for(flags, lang) fixed risk text appended in Python (never left to the LLM)
  prompt_rules_for(flags, lang) extra synthesis rules for the router-chosen risk flags
  scrub_internal(text)        drop internal table/marker names that must not reach the user
"""

from __future__ import annotations

import re

OFF_TOPIC_REPLY = (
    "Xin lỗi, tôi là trợ lý phân tích chứng khoán Việt Nam nên không hỗ trợ yêu cầu này "
    "(sáng tác, dịch thuật, lập trình, thời tiết, nấu ăn...). "
    "Bạn có thể hỏi về giá, phân tích kỹ thuật, định giá, tin tức, lọc cổ phiếu hoặc tổng quan thị trường."
)
OFF_TOPIC_REPLY_EN = (
    "Sorry, I am a Vietnamese stock-market assistant and cannot help with this request "
    "(creative writing, translation, coding, weather...). "
    "You can ask about prices, technical analysis, valuation, news, stock screening or the market overview."
)


def off_topic_reply(lang: str = "vi") -> str:
    return OFF_TOPIC_REPLY_EN if lang == "en" else OFF_TOPIC_REPLY


# ── risk disclaimers (flags come from the router LLM) ─────────────────────────

_DISCLAIMERS_VI = {
    "personal": (
        "⚠️ Tôi không thể tư vấn cá nhân hóa việc dùng toàn bộ tiền tiết kiệm hay vay mượn để đầu tư. "
        "Dồn toàn bộ vốn vào một cổ phiếu (all-in) hoặc dùng đòn bẩy có thể gây mất vốn lớn. "
        "Nguyên tắc chung: chỉ dùng tiền nhàn rỗi, đa dạng hóa danh mục, giữ quỹ dự phòng và cân nhắc "
        "tham vấn chuyên gia tài chính được cấp phép."
    ),
    "prediction": (
        "⚠️ Không ai có thể dự đoán chính xác giá trong tương lai. Nội dung trên chỉ là phân tích dựa trên "
        "dữ liệu quá khứ/hiện tại, không phải dự báo hay cam kết lợi nhuận."
    ),
    "advice": (
        "⚠️ Thông tin chỉ mang tính tham khảo, không phải khuyến nghị đầu tư. "
        "Quyết định mua/bán thuộc về bạn — hãy tự đánh giá khẩu vị rủi ro và đa dạng hóa danh mục."
    ),
}
_DISCLAIMERS_EN = {
    "personal": (
        "⚠️ I cannot give personalised advice on putting all your savings or borrowed money into a stock. "
        "Going all-in on one stock or using leverage can cause large losses. General principles: only invest "
        "money you can afford to lose, diversify, keep an emergency fund and consider a licensed financial adviser."
    ),
    "prediction": (
        "⚠️ Nobody can predict future prices accurately. The content above is analysis of past/current data, "
        "not a forecast or a promise of returns."
    ),
    "advice": (
        "⚠️ For reference only — not investment advice. Buy/sell decisions are yours; assess your own risk "
        "tolerance and diversify."
    ),
}


def disclaimer_for(flags: set[str], lang: str = "vi") -> str:
    """Fixed risk text for the flags (personal > prediction > advice, joined)."""
    table = _DISCLAIMERS_EN if lang == "en" else _DISCLAIMERS_VI
    return "\n\n".join(table[k] for k in ("personal", "prediction", "advice") if k in flags)


def prompt_rules_for(flags: set[str], lang: str = "vi") -> str:
    """Extra synthesis-prompt rules for risky questions."""
    rules: list[str] = []
    if "advice" in flags:
        rules.append(
            "Câu hỏi xin khuyến nghị mua/bán: nêu nhận định rõ ràng (ví dụ nghiêng về tích lũy / thận trọng / "
            "chờ tín hiệu) kèm 2-3 lý do dẫn số liệu và rủi ro chính; không nói mập mờ."
        )
    if "personal" in flags:
        rules.append(
            "Người dùng định dồn toàn bộ tiền tiết kiệm/vay mượn: câu ĐẦU TIÊN của câu trả lời phải nói thẳng "
            "rằng dồn toàn bộ vốn vào MỘT cổ phiếu là rủi ro tập trung rất cao và không nên; sau đó chỉ nêu ngắn "
            "gọn nguyên tắc đa dạng hóa/quỹ dự phòng, chỉ dẫn 1-2 số liệu rủi ro của mã (biến động, định giá); "
            "không đưa tư vấn cá nhân hóa và không liệt kê dài dữ liệu."
        )
    if "prediction" in flags:
        rules.append(
            "Câu hỏi yêu cầu dự đoán tương lai: mở đầu bằng việc nói rõ KHÔNG thể dự đoán chính xác giá; chỉ nêu "
            "hiện trạng, các mốc hỗ trợ/kháng cự và kịch bản có điều kiện từ dữ liệu."
        )
    if lang == "en":
        rules.append("The question is in English: answer in English.")
    return " ".join(rules)


# ── internal-name scrubber ────────────────────────────────────────────────────

_INTERNAL_BRACKET_RE = re.compile(r"\[[^\]\n]*(TEMPLATE|TEMPLATE SQL|DỮ LIỆU SQL|SQL|metric_code)[^\]\n]*\]", re.IGNORECASE)
_INTERNAL_NAME_RE = re.compile(
    r"`?\b(financial_facts|stock_prices|ohlcv_daily|stock_ratios|market_index_daily|foreign_flows|"
    r"corporate_events|securities|metric_code|market_quotes)\b`?(\s*/\s*`?\b(financial_facts|stock_prices)\b`?)*"
)


def scrub_internal(text: str) -> str:
    """Remove internal markers/table names so they never reach the user."""
    if not text:
        return text
    text = _INTERNAL_BRACKET_RE.sub("", text)
    text = _INTERNAL_NAME_RE.sub("cơ sở dữ liệu", text)
    return re.sub(r"[ \t]{2,}", " ", text)
