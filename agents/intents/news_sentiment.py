"""
agents/intents/news_sentiment.py — Nhóm 5: Tin tức & Tâm lý.

Market-brief pattern:
  - Python fetches news + sentiment data.
  - LLM writes only 4 prose slots (TIN_TUC, DONG_TIEN_TC, QUAN_TRI, SENTIMENT).
  - Python assembles final Markdown from fixed structure + LLM slots.
"""

from __future__ import annotations

from langfuse import observe

from llm.factory import create_client
from llm.types import Message
from tools.price import search_financial_news, analyze_market_sentiment
from agents.intents import strip_preamble, strip_thinking, extract_slot


_SYSTEM = (
    "Bạn là chuyên gia phân tích tin tức và tâm lý thị trường chứng khoán Việt Nam. "
    "Nếu không có tin tức mới, ghi 'Không có tin tức mới' và phân tích sentiment có sẵn. "
    "KHÔNG tự bịa thông tin — chỉ dùng dữ liệu đã cung cấp. "
    "TUYỆT ĐỐI không viết quá trình suy nghĩ, không ghi chú nội bộ, "
    "không giải thích bước phân tích. "
    "Viết HOÀN TOÀN bằng tiếng Việt. "
    "Output chỉ gồm 4 phần được đánh dấu, không có text nào khác. "
    "BẮT BUỘC bọc toàn bộ output trong thẻ <report>...</report>. Output chỉ gồm: <report>[nội dung]</report>, không có text nào khác."
)


def _fetch_news_text(ticker: str | None, days: int) -> str:
    subject = ticker or "thị trường"

    r = search_financial_news(subject, days)
    if r.status == "ok" and r.message.strip():
        return r.message

    if ticker:
        try:
            from data.cafef_rss import fetch_ticker_news
            from datetime import date, timedelta
            cutoff = (date.today() - timedelta(days=14)).isoformat()
            arts = fetch_ticker_news(ticker, max_articles=10)
            if arts:
                recent = [a for a in arts if a.get("published_at", "")[:10] >= cutoff]
                target = recent if recent else arts[:3]
                lines = [f"[cafef | {a['published_at'][:10]}] {a['title']}" for a in target]
                return "\n".join(lines)
        except Exception:
            pass

    try:
        from data.cafef_rss import fetch_vn_market_news
        arts = fetch_vn_market_news(max_total=6)
        if arts:
            lines = [f"[cafef | {a['published_at'][:10]}] {a['title']}" for a in arts]
            return "Không tìm thấy tin tức đặc thù, tin tức thị trường chung:\n" + "\n".join(lines)
    except Exception:
        pass

    return f"Không có tin tức cho {subject} trong {days} ngày."


def _assemble_report(
    subject: str,
    tin_tuc: str,
    dong_tien_tc: str,
    quan_tri: str,
    sentiment: str,
) -> str:
    return (
        f"# Tin tức & Tâm lý {subject}\n\n"
        f"## Tin tức & Sự kiện Doanh nghiệp\n{tin_tuc}\n\n"
        f"## Dòng tiền Tổ chức\n{dong_tien_tc}\n\n"
        f"## Quản trị & Rủi ro Phi tài chính\n{quan_tri}\n\n"
        f"## Điểm Sentiment & Cảnh báo\n{sentiment}\n\n"
        f"[Nguồn: CafeF/Tavily, LLM sentiment]"
    )


@observe(name="intent.news_sentiment")
def run(ticker: str | None, query: str) -> str:
    subject = ticker or "thị trường"
    days = 3

    news_text     = _fetch_news_text(ticker, days)
    sentiment_r   = analyze_market_sentiment(subject, days=7)
    sentiment_text = sentiment_r.message if sentiment_r.status == "ok" else "Không có dữ liệu sentiment."

    user_prompt = f"""Câu hỏi: {query}

Tin tức & Tâm lý thị trường — {subject} ({days} ngày gần nhất):

Tin tức:
{news_text}

Sentiment:
{sentiment_text}

Từ khóa rủi ro: "bắt giam", "vi phạm", "điều tra", "cưỡng chế", "phát hành thêm", "pha loãng"
Từ khóa tích cực: "trúng thầu", "cổ tức", "mua lại cổ phiếu", "lợi nhuận kỷ lục", "ký kết hợp đồng"
Cảnh báo insider: "cổ đông lớn đăng ký bán", "ban lãnh đạo thoái vốn"
Dấu hiệu đỉnh: 90% bình luận cực kỳ bullish + margin căng → "Phân phối đỉnh"

Bọc TOÀN BỘ output trong <report>...</report>.

<report>
TIN_TUC: [2-3 câu về tin tức doanh nghiệp: cổ tức, tăng vốn, ESOP, insider trading — tác động tích cực/tiêu cực]
DONG_TIEN_TC: [1-2 câu về dòng tiền khối ngoại / tự doanh gần nhất]
QUAN_TRI: [1-2 câu về rủi ro quản trị, pháp lý, ESG nếu có — ghi "Không phát hiện rủi ro" nếu không có]
SENTIMENT: [2-3 câu về điểm sentiment tổng thể và cảnh báo nếu có]
</report>"""

    client = create_client()
    resp = client.generate(
        [Message(role="user", content=user_prompt)],
        max_tokens=900,
        temperature=0,
        system=_SYSTEM,
    )

    from agents.intents import extract_report
    raw = extract_report(resp.text.strip())
    tin_tuc      = strip_thinking(extract_slot(raw, "TIN_TUC",      "DONG_TIEN_TC"))
    dong_tien_tc = strip_thinking(extract_slot(raw, "DONG_TIEN_TC", "QUAN_TRI"))
    quan_tri     = strip_thinking(extract_slot(raw, "QUAN_TRI",     "SENTIMENT"))
    sentiment    = strip_thinking(extract_slot(raw, "SENTIMENT",    None))

    if not tin_tuc and not sentiment:
        return f"Không thể lấy tin tức & sentiment cho **{ticker or subject}** — vui lòng thử lại."

    return _assemble_report(subject, tin_tuc, dong_tien_tc, quan_tri, sentiment)
