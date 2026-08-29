"""
agents/intents/news_sentiment.py — Nhóm 5: Tin tức & Tâm lý.

Collects: news headlines (3 days), market sentiment score.
LLM synthesizes: explains price anomalies, detects euphoria/panic extremes.
"""

from __future__ import annotations

from llm.factory import create_client
from llm.types import Message
from tools.price import search_financial_news, analyze_market_sentiment
from agents.intents import strip_preamble, strip_thinking


def _fetch_news_text(ticker: str | None, days: int) -> str:
    subject = ticker or "thị trường"

    # Primary: Qdrant news_chunks
    r = search_financial_news(subject, days)
    if r.status == "ok" and r.message.strip():
        return r.message

    # Fallback: CafeF ticker search (bypasses price-validation path)
    # Use max 14-day window — RSS feeds may not have same-day articles
    if ticker:
        try:
            from data.cafef_rss import fetch_ticker_news
            from datetime import date, timedelta
            cutoff = (date.today() - timedelta(days=14)).isoformat()
            arts = fetch_ticker_news(ticker, max_articles=10)
            if arts:
                recent = [a for a in arts if a.get("published_at", "")[:10] >= cutoff]
                target = recent if recent else arts[:3]  # fall back to latest if all old
                lines = [f"[cafef | {a['published_at'][:10]}] {a['title']}" for a in target]
                return "\n".join(lines)
        except Exception:
            pass

    # Fallback: general CafeF market news
    try:
        from data.cafef_rss import fetch_vn_market_news
        arts = fetch_vn_market_news(max_total=6)
        if arts:
            lines = [f"[cafef | {a['published_at'][:10]}] {a['title']}" for a in arts]
            return "Không tìm thấy tin tức đặc thù, tin tức thị trường chung:\n" + "\n".join(lines)
    except Exception:
        pass

    return f"Không có tin tức cho {subject} trong {days} ngày."


def run(ticker: str | None, query: str) -> str:
    subject = ticker or "thị trường"
    days = 3

    news_text = _fetch_news_text(ticker, days)
    sentiment_r = analyze_market_sentiment(subject, days=7)
    sentiment_text = sentiment_r.message if sentiment_r.status == "ok" else "Không có dữ liệu sentiment."

    prompt = f"""Câu hỏi: {query}

Tin tức & Tâm lý thị trường — {subject} ({days} ngày gần nhất):

### Tin tức & Sự kiện
{news_text}

### Sentiment
{sentiment_text}

Logic phân tích:
- Từ khóa rủi ro cao: "bắt giam", "vi phạm", "điều tra", "cưỡng chế", "phát hành thêm", "pha loãng"
- Từ khóa tích cực: "trúng thầu", "cổ tức", "mua lại cổ phiếu", "lợi nhuận kỷ lục", "ký kết hợp đồng"
- Từ khóa cảnh báo insider: "cổ đông lớn đăng ký bán", "ban lãnh đạo thoái vốn"
- 90% bình luận cực kỳ bullish + margin căng → cảnh báo "Phân phối đỉnh"
- Sự kiện doanh nghiệp: cổ tức, tăng vốn, ESOP, M&A, thay CEO → xác định tác động tích cực/tiêu cực

Viết báo cáo Markdown (không văn bản trước báo cáo):
# Tin tức & Tâm lý {subject}
## Tin tức & Sự kiện Doanh nghiệp (cổ tức, tăng vốn, ESOP, insider trading)
## Dòng tiền Tổ chức (Khối ngoại / Tự doanh — gần nhất)
## Quản trị & Rủi ro Phi tài chính (governance, pháp lý, ESG)
## Điểm Sentiment & Cảnh báo
[Nguồn: CafeF/Tavily, LLM sentiment]"""

    client = create_client()
    resp = client.generate(
        [Message(role="user", content=prompt)],
        max_tokens=1500,
        temperature=0,
        system=(
            "Bạn là chuyên gia phân tích tin tức và tâm lý thị trường chứng khoán Việt Nam. "
            "Xuất NGAY báo cáo Markdown. TUYỆT ĐỐI KHÔNG viết suy nghĩ, lý luận, "
            "hay meta-commentary. Chỉ báo cáo cuối cùng. "
            "Nếu không có tin tức mới, ghi 'Không có tin tức mới' và phân tích sentiment có sẵn."
        ),
    )
    return strip_thinking(strip_preamble(resp.text.strip()))
