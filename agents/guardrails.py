"""
agents/guardrails.py — deterministic input/output guards (no LLM, no DB).

  is_off_topic(query)        creative writing / coding / weather ... → decline before any LLM call
  is_social_turn(query)      greeting / thanks / capability question → direct_reply is allowed
  has_finance_terms(query)   query touches finance vocabulary (lets a conceptual question through)
  risk_flags(query)          {"advice", "personal", "prediction"} → disclaimer + prompt rules
  detect_language(query)     "vi" | "en" — reply language follows the question
  disclaimer_for(flags, lang) fixed risk text appended in Python (never left to the LLM)
  scrub_internal(text)       drop internal table/marker names that must not reach the user
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

# ── off-topic / social ────────────────────────────────────────────────────────

_OFF_TOPIC_RE = re.compile(
    r"(viết|làm|sáng tác|soạn)\s+(cho tôi\s+)?(một\s+)?(bài\s+)?(thơ|văn|bài hát|ca khúc|truyện|email|thư)"
    r"|\b(thơ|bài thơ|truyện cười|truyện ngắn|bài hát)\b"
    r"|kể\s+(cho tôi\s+)?(một\s+)?(câu\s+)?(chuyện|truyện)"
    r"|viết\s+(code|mã nguồn|chương trình|hàm|script)|lập trình|\bpython\b|\bjavascript\b"
    r"|dịch\s+(giúp|sang|câu|đoạn|bài)|\btranslate\b"
    r"|công thức nấu|nấu ăn|món ăn"
    r"|thời tiết|dự báo thời tiết|\bweather\b"
    r"|bóng đá|world cup|\bwrite (me )?a (poem|story|song|joke)\b|\btell me a joke\b",
    re.IGNORECASE,
)

_SOCIAL_RE = re.compile(
    r"^\W*(xin\s+)?(chào|hello|hi|hey|alo)\b"
    r"|\b(cảm ơn|cám ơn|thanks|thank you|tks)\b"
    r"|^\W*(ok|oke|okay|vâng|dạ|ừ|ừm|được rồi|hiểu rồi|rõ rồi|cũng hợp lý|hợp lý|tuyệt|tốt)\W*$"
    r"|\b(tạm biệt|bye|hẹn gặp lại)\b"
    r"|bạn\s+(là ai|làm được gì|giúp được gì|có thể làm gì|khỏe không|thế nào)"
    r"|\b(what can you do|who are you|how are you)\b",
    re.IGNORECASE,
)

_FINANCE_TERMS = (
    "cổ phiếu", "chứng khoán", "thị trường", "vn-index", "vnindex", "p/e", "p/b", "roe", "eps",
    "rsi", "macd", "cổ tức", "lãi suất", "tỷ giá", "ngân hàng", "đầu tư", "lợi nhuận", "doanh thu",
    "vốn hóa", "khối ngoại", "stock", "market", "invest", "dividend", "earnings",
)


def is_off_topic(query: str) -> bool:
    return bool(_OFF_TOPIC_RE.search(query or ""))


def is_social_turn(query: str) -> bool:
    q = (query or "").strip()
    return len(q) <= 80 and bool(_SOCIAL_RE.search(q))


def has_finance_terms(query: str) -> bool:
    q = (query or "").lower()
    return any(t in q for t in _FINANCE_TERMS)


def off_topic_reply(query: str) -> str:
    return OFF_TOPIC_REPLY_EN if detect_language(query) == "en" else OFF_TOPIC_REPLY


# ── language ──────────────────────────────────────────────────────────────────

_VI_DIACRITICS_RE = re.compile(
    r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", re.IGNORECASE
)
_EN_WORDS_RE = re.compile(
    r"\b(what|which|how|why|is|are|the|of|price|stock|share|buy|sell|should|compare|vs|"
    r"today|latest|about|tell|show|me|please|and)\b",
    re.IGNORECASE,
)


def detect_language(query: str) -> str:
    """'en' when the question has no Vietnamese diacritics and ≥2 common English words."""
    q = query or ""
    if _VI_DIACRITICS_RE.search(q):
        return "vi"
    return "en" if len(_EN_WORDS_RE.findall(q)) >= 2 else "vi"


# ── risk flags + disclaimers ──────────────────────────────────────────────────

_ADVICE_RE = re.compile(
    r"nên\s+(mua|bán|giữ|cắt lỗ|chốt lời|đầu tư)|có nên|mua hay bán|bán hay giữ|nên hay không"
    r"|khuyến nghị|cắt lỗ|chốt lời|\bshould i\b|\bbuy or sell\b|\bbuy, sell\b",
    re.IGNORECASE,
)
_PERSONAL_RE = re.compile(
    r"toàn bộ (tiền|số tiền|vốn|tài sản)|bỏ (hết|toàn bộ)|dồn (hết|toàn bộ)|all[- ]?in"
    r"|tiền tiết kiệm|tiền (hưu|học|cưới)|vay (tiền|ngân hàng|margin)|margin|đòn bẩy"
    r"|\b(my|all my) (savings|money)\b",
    re.IGNORECASE,
)
_PREDICTION_RE = re.compile(
    r"dự đoán|dự báo|giá mục tiêu|sẽ (tăng|giảm|lên|xuống|về)|(tuần|tháng|quý|năm)\s+(sau|tới|tiếp theo)"
    r"|ngày mai|\bpredict|\bforecast|\bwill .* (rise|fall|go up|go down)\b|next (week|month)",
    re.IGNORECASE,
)


def risk_flags(query: str) -> set[str]:
    q = query or ""
    flags: set[str] = set()
    if _PERSONAL_RE.search(q):
        flags |= {"personal", "advice"}
    if _ADVICE_RE.search(q):
        flags.add("advice")
    if _PREDICTION_RE.search(q):
        flags.add("prediction")
    return flags


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
