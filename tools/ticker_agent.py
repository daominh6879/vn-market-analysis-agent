"""
tools/ticker_agent.py — Single-ticker analysis agent.

LLM decides which tools to call, then synthesizes a coherent analysis.
Tools available to the agent:
  get_price        → current price
  get_indicators   → RSI, MACD, MA, ADX
  search_news      → recent headlines (30 days)
  ask_bctc         → qualitative context from financial reports

Usage:
  from tools.ticker_agent import analyze
  result = analyze("phân tích HPG hôm nay")
  print(result)
"""
from __future__ import annotations

import json

from llm.types import Message

_SYSTEM = """\
Bạn là chuyên gia phân tích cổ phiếu Việt Nam. Trả lời ngắn gọn, đi thẳng vào trọng tâm.
TUYỆT ĐỐI không viết quá trình suy nghĩ, không ghi chú nội bộ, không giải thích bước phân tích.
Không mở đầu bằng 'Được rồi', 'Tôi sẽ', 'Hãy', hay bất kỳ câu dẫn nào.

Quy tắc gọi tools:
- Gọi get_price, get_indicators, search_news, ask_bctc trong CÙNG 1 lượt đầu tiên.
- Nếu ask_bctc trả về "Không có trong tài liệu" → KHÔNG gọi lại, bỏ qua phần BCTC.
- Sau round đầu: chỉ gọi thêm tool nếu THỰC SỰ cần dữ liệu mới.

Khi viết phân tích cuối — NGẮN GỌN, không dài dòng:
- Giá + so sánh MA20/MA50 (1 câu)
- Tín hiệu kỹ thuật chính: RSI, MACD, ADX (2-3 câu)
- Tin tức nổi bật nếu có (1 câu)
- Điểm mạnh/rủi ro BCTC nếu có (1 câu)
- Kết luận (1 câu)
- Không khuyến nghị mua/bán
"""

_TOOLS = [
    {
        "name": "get_price",
        "description": "Lấy giá hiện tại của mã chứng khoán.",
        "parameters": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Mã CK, ví dụ: HPG, VCB"},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_indicators",
        "description": "Tính chỉ báo kỹ thuật: RSI(14), MACD, MA20, MA50, ADX cho mã CK.",
        "parameters": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "search_news",
        "description": "Tìm tin tức tài chính gần đây (30 ngày) về mã CK.",
        "parameters": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "ask_bctc",
        "description": "Hỏi nội dung báo cáo tài chính (BCTC): doanh thu, lợi nhuận, nợ vay, chiến lược.",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Câu hỏi cụ thể về BCTC"},
                "ticker": {"type": "string"},
            },
            "required": ["question", "ticker"],
        },
    },
]


def _run_tool(name: str, args: dict) -> str:
    """Execute a tool and return string result."""
    try:
        if name == "get_price":
            from tools.price import get_realtime_price
            from tools.providers import _detect_provider
            ticker = args["ticker"].upper()
            r = get_realtime_price(ticker, provider=_detect_provider(ticker))
            return f"{ticker}: {r.data:,.0f} VND" if r.status == "ok" else r.message

        if name == "get_indicators":
            from tools.price import get_historical_ohlcv, calculate_indicators
            from tools.providers import _detect_provider, YFinanceProvider
            ticker = args["ticker"].upper()
            provider = _detect_provider(ticker)
            currency = "USD" if isinstance(provider, YFinanceProvider) else "VND"
            ohlcv = get_historical_ohlcv(ticker, days=60, provider=provider)
            if ohlcv.status != "ok":
                return ohlcv.message
            ind = calculate_indicators(ohlcv.data, currency=currency)
            return str(ind.data) if ind.status == "ok" else ind.message

        if name == "search_news":
            from rag.news_index import search_news_by_text
            ticker = args["ticker"].upper()
            items = search_news_by_text(ticker, days=30, limit=5, ticker=ticker)
            if not items:
                items = search_news_by_text(ticker, days=30, limit=5, ticker=None)
            if not items:
                return f"Không tìm thấy tin tức về {ticker} trong 30 ngày."
            lines = []
            for item in items:
                title = item.get("title") or item.get("text", "")[:100]
                date = item.get("published_at", "")[:10]
                lines.append(f"[{date}] {title}")
            return "\n".join(lines)

        if name == "ask_bctc":
            from tools.rag_query import ask_report
            ticker = args["ticker"].upper()
            r = ask_report(args["question"], tickers=[ticker])
            return str(r.data) if r.status == "ok" and r.data else r.message

    except Exception as e:
        return f"[lỗi tool {name}]: {e}"

    return f"[unknown tool: {name}]"


def analyze(question: str, client=None, max_rounds: int = 6) -> str:
    """Run ticker analysis agent. Returns synthesized analysis string."""
    if client is None:
        from llm.factory import create_client
        client = create_client()

    messages: list[Message] = [Message(role="user", content=question)]

    for _ in range(max_rounds):
        resp = client.generate(
            messages=messages,
            system=_SYSTEM,
            tools=_TOOLS,
            max_tokens=2048,
        )

        if not resp.tool_calls:
            from llm.utils import strip_thinking
            return strip_thinking(resp.text.strip())

        # Append assistant turn
        messages.append(Message(role="assistant", content=resp.text or ""))

        # Execute each tool call and append results
        for tc in resp.tool_calls:
            result = _run_tool(tc.name, tc.input)
            messages.append(Message(
                role="user",
                content=f"[Tool result: {tc.name}({json.dumps(tc.input, ensure_ascii=False)})]\n{result}",
            ))

    # Max rounds exceeded — ask for final synthesis
    messages.append(Message(role="user", content="Hãy tổng hợp phân tích dựa trên dữ liệu đã thu thập."))
    resp = client.generate(messages=messages, system=_SYSTEM, max_tokens=2048)
    from llm.utils import strip_thinking
    return strip_thinking(resp.text.strip())
