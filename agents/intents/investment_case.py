"""
agents/intents/investment_case.py — Nhóm 6: Tổng hợp Khuyến nghị Đầu tư.

Market-brief pattern:
  - Calls all 5 intent modules sequentially (pre-computed reports).
  - LLM writes only 5 prose slots (BULL, BEAR, KHUYEN_NGHI, KHUNG_TG, THEO_DOI).
  - Python assembles final Markdown from fixed structure + LLM slots.
"""

from __future__ import annotations

from langfuse import observe

from llm.factory import create_client
from llm.types import Message
from agents.intents import strip_preamble, strip_thinking, extract_slot


_SYSTEM = (
    "Bạn là chuyên gia phân tích đầu tư chứng khoán Việt Nam với 15 năm kinh nghiệm. "
    "Khuyến nghị PHẢI rõ ràng: MUA / TÍCH LŨY / NẮM GIỮ / BÁN — không được mập mờ. "
    "Mỗi luận điểm Bull/Bear PHẢI dẫn số liệu cụ thể từ các báo cáo. "
    "KHÔNG được nói 'thiếu dữ liệu' — dùng tất cả thông tin có sẵn để kết luận. "
    "TUYỆT ĐỐI không viết quá trình suy nghĩ, không ghi chú nội bộ. "
    "Viết HOÀN TOÀN bằng tiếng Việt. "
    "Output chỉ gồm 5 phần được đánh dấu, không có text nào khác. "
    "BẮT BUỘC bọc toàn bộ output trong thẻ <report>...</report>. Output chỉ gồm: <report>[nội dung]</report>, không có text nào khác."
)


def _safe_run(module_run, ticker: str, query: str, label: str) -> str:
    try:
        return module_run(ticker, query)
    except Exception as exc:
        return f"[{label}: lỗi — {exc}]"


def _assemble_report(ticker: str, bull: str, bear: str, khuyen_nghi: str, khung_tg: str, theo_doi: str) -> str:
    return (
        f"# Khuyến nghị Đầu tư {ticker}\n\n"
        f"## Luận điểm Mua — Bull Case\n{bull}\n\n"
        f"## Luận điểm Thận trọng — Bear Case\n{bear}\n\n"
        f"## Khuyến nghị Hành động\n> {khuyen_nghi}\n\n"
        f"## Khung thời gian & Khẩu vị Rủi ro\n{khung_tg}\n\n"
        f"## Điểm theo dõi (Catalysts & Risks to Watch)\n{theo_doi}\n\n"
        f"[Nguồn: Tổng hợp kỹ thuật + cơ bản + vĩ mô + tin tức]"
    )


@observe(name="intent.investment_case")
def run(ticker: str, query: str) -> str:
    from agents.intents import price_action, technical, fundamentals, macro_sector, news_sentiment

    pa_report    = _safe_run(price_action.run,   ticker, f"Phân tích dòng tiền và hành động giá {ticker}",   "price_action")
    tech_report  = _safe_run(technical.run,      ticker, f"Phân tích kỹ thuật toàn diện {ticker}",           "technical")
    fund_report  = _safe_run(fundamentals.run,   ticker, f"Phân tích cơ bản định giá so ngành {ticker}",     "fundamentals")
    macro_report = _safe_run(macro_sector.run,   ticker, f"Vĩ mô và ngành tác động lên {ticker}",            "macro_sector")
    news_report  = _safe_run(news_sentiment.run, ticker, f"Tin tức sự kiện tâm lý thị trường {ticker}",      "news_sentiment")

    user_prompt = f"""Câu hỏi gốc: {query}

5 báo cáo phân tích chuyên sâu về {ticker}:

---
[1] Hành động giá & Dòng tiền:
{pa_report}

---
[2] Phân tích Kỹ thuật:
{tech_report}

---
[3] Cơ bản & Định giá:
{fund_report}

---
[4] Vĩ mô & Ngành:
{macro_report}

---
[5] Tin tức & Tâm lý:
{news_report}

---

Bọc TOÀN BỘ output trong <report>...</report>.

<report>
BULL:
1. [lý do cốt lõi 1 — dẫn số liệu cụ thể]
2. [lý do cốt lõi 2 — dẫn số liệu cụ thể]
3. [lý do cốt lõi 3 — dẫn số liệu cụ thể]
BEAR:
1. [rủi ro lớn nhất 1 — dẫn số liệu cụ thể]
2. [rủi ro lớn nhất 2 — dẫn số liệu cụ thể]
3. [rủi ro lớn nhất 3 — dẫn số liệu cụ thể]
KHUYEN_NGHI: [MUA / TÍCH LŨY / NẮM GIỮ / BÁN — 1-2 câu giải thích dứt khoát]
KHUNG_TG:
- Dài hạn (>1 năm): [đánh giá]
- Trung hạn (3-6 tháng): [đánh giá]
- Ngắn hạn (<1 tháng): [đánh giá]
- Phù hợp với nhà đầu tư: [mô tả profile rủi ro]
THEO_DOI:
- [catalyst/risk 1]
- [catalyst/risk 2]
- [catalyst/risk 3]
</report>"""

    client = create_client()
    resp = client.generate(
        [Message(role="user", content=user_prompt)],
        max_tokens=2000,
        temperature=0,
        system=_SYSTEM,
    )

    from agents.intents import extract_report
    raw = extract_report(resp.text.strip())
    bull        = strip_thinking(extract_slot(raw, "BULL",        "BEAR"))
    bear        = strip_thinking(extract_slot(raw, "BEAR",        "KHUYEN_NGHI"))
    khuyen_nghi = strip_thinking(extract_slot(raw, "KHUYEN_NGHI", "KHUNG_TG"))
    khung_tg    = strip_thinking(extract_slot(raw, "KHUNG_TG",    "THEO_DOI"))
    theo_doi    = strip_thinking(extract_slot(raw, "THEO_DOI",    None))

    if not bull and not bear:
        return f"Không thể đánh giá đầu tư **{ticker}** — vui lòng thử lại."

    return _assemble_report(ticker, bull, bear, khuyen_nghi, khung_tg, theo_doi)
