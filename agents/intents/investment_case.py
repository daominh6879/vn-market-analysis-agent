"""
agents/intents/investment_case.py — Nhóm 6: Tổng hợp Khuyến nghị Đầu tư.

Calls all 5 intent modules sequentially, then synthesizes:
  - Bull Case (3 lý do tăng giá)
  - Bear Case (3 rủi ro giảm giá)
  - Khuyến nghị: MUA / BÁN / NẮM GIỮ / TÍCH LŨY
  - Khung thời gian & khẩu vị rủi ro
"""

from __future__ import annotations

from langfuse import observe

from llm.factory import create_client
from llm.types import Message
from agents.intents import strip_preamble, strip_thinking


def _safe_run(module_run, ticker: str, query: str, label: str) -> str:
    try:
        return module_run(ticker, query)
    except Exception as exc:
        return f"[{label}: lỗi — {exc}]"


@observe(name="intent.investment_case")
def run(ticker: str, query: str) -> str:
    from agents.intents import price_action, technical, fundamentals, macro_sector, news_sentiment

    pa_report   = _safe_run(price_action.run,  ticker, f"Phân tích dòng tiền và hành động giá {ticker}", "price_action")
    tech_report = _safe_run(technical.run,     ticker, f"Phân tích kỹ thuật toàn diện {ticker}", "technical")
    fund_report = _safe_run(fundamentals.run,  ticker, f"Phân tích cơ bản định giá so ngành {ticker}", "fundamentals")
    macro_report = _safe_run(macro_sector.run, ticker, f"Vĩ mô và ngành tác động lên {ticker}", "macro_sector")
    news_report = _safe_run(news_sentiment.run, ticker, f"Tin tức sự kiện tâm lý thị trường {ticker}", "news_sentiment")

    prompt = f"""Câu hỏi gốc: {query}

Dưới đây là 5 báo cáo phân tích chuyên sâu về {ticker}.
Tổng hợp tất cả để đưa ra khuyến nghị đầu tư dứt khoát.

---
### [1] Hành động giá & Dòng tiền
{pa_report}

---
### [2] Phân tích Kỹ thuật
{tech_report}

---
### [3] Cơ bản & Định giá
{fund_report}

---
### [4] Vĩ mô & Ngành
{macro_report}

---
### [5] Tin tức & Tâm lý
{news_report}

---

Viết báo cáo Markdown tổng hợp (không văn bản trước báo cáo):
# Khuyến nghị Đầu tư {ticker}

## Luận điểm Mua — Bull Case
1. [lý do cốt lõi 1 — dẫn số liệu cụ thể]
2. [lý do cốt lõi 2 — dẫn số liệu cụ thể]
3. [lý do cốt lõi 3 — dẫn số liệu cụ thể]

## Luận điểm Thận trọng — Bear Case
1. [rủi ro lớn nhất 1 — dẫn số liệu cụ thể]
2. [rủi ro lớn nhất 2 — dẫn số liệu cụ thể]
3. [rủi ro lớn nhất 3 — dẫn số liệu cụ thể]

## Khuyến nghị Hành động
> **[MUA / TÍCH LŨY / NẮM GIỮ / BÁN]** — [1-2 câu giải thích dứt khoát, không mập mờ]

## Khung thời gian & Khẩu vị Rủi ro
- **Dài hạn (>1 năm):** [đánh giá]
- **Trung hạn (3-6 tháng):** [đánh giá]
- **Ngắn hạn (<1 tháng):** [đánh giá]
- **Phù hợp với nhà đầu tư:** [mô tả profile rủi ro phù hợp]

## Điểm theo dõi (Catalysts & Risks to Watch)
- [catalyst/risk 1]
- [catalyst/risk 2]
- [catalyst/risk 3]

[Nguồn: Tổng hợp kỹ thuật + cơ bản + vĩ mô + tin tức]"""

    client = create_client()
    resp = client.generate(
        [Message(role="user", content=prompt)],
        max_tokens=3500,
        temperature=0,
        system=(
            "Bạn là chuyên gia phân tích đầu tư chứng khoán Việt Nam với 15 năm kinh nghiệm. "
            "Xuất NGAY báo cáo Markdown — bắt đầu bằng '# Khuyến nghị Đầu tư'. "
            "TUYỆT ĐỐI KHÔNG viết suy nghĩ, lý luận, hay meta-commentary. "
            "Khuyến nghị PHẢI rõ ràng: MUA / TÍCH LŨY / NẮM GIỮ / BÁN — không được mập mờ. "
            "Mỗi luận điểm Bull/Bear PHẢI dẫn số liệu cụ thể từ các báo cáo. "
            "KHÔNG được nói 'thiếu dữ liệu' — dùng tất cả thông tin có sẵn để kết luận. "
            "Chỉ báo cáo cuối cùng."
        ),
    )
    return strip_thinking(strip_preamble(resp.text.strip()))
