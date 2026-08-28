"""
tools/mcp_server.py — MCP server cho financial tools (bài 21).

Expose 5 tool qua Model Context Protocol:
  - get_price            : giá hiện tại (VN hoặc quốc tế)
  - get_ohlcv            : lịch sử OHLCV
  - get_indicators       : chỉ báo kỹ thuật (RSI, MACD, MA20, MA50)
  - search_news          : tin tức tài chính từ Qdrant
  - get_market_sentiment : phân tích sentiment thị trường

Chạy server:
    python tools/mcp_server.py

Mở MCP Inspector:
    npx @modelcontextprotocol/inspector python tools/mcp_server.py
"""

from __future__ import annotations
import asyncio
import sys
import threading
from pathlib import Path

# Ensure project root is on sys.path when run as a script (python tools/mcp_server.py)
_root = Path(__file__).parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))


from mcp.server.fastmcp import FastMCP

from tools.providers import YFinanceProvider, _detect_provider
from tools.price import (
    analyze_market_sentiment,
    calculate_indicators,
    get_foreign_flows,
    get_historical_ohlcv,
    get_market_breadth,
    get_market_performance,
    get_realtime_price,
    get_sector_performance,
    search_financial_news,
)
from tools.rag_query import ask_report as _ask_report
from tools.qa_agent import answer as _qa_answer

mcp = FastMCP("financial-tools")

# Gate: tool calls block until warmup finishes (cache primed, imports loaded)
_warmup_done = threading.Event()


def _result_to_text(result) -> str:
    """Serialize ToolResult → plain text cho MCP response."""
    if result.status == "ok":
        data = result.data
        if data is None:
            return result.message
        # DataFrame → text table (cột time + OHLCV)
        try:
            import pandas as pd

            if isinstance(data, pd.DataFrame):
                return data.to_string(index=False)
        except ImportError:
            pass
        return str(data)
    # non-ok: trả message hướng dẫn agent
    return f"[{result.status}] {result.message}"


_TIMEOUT_PRICE = 30.0    # seconds — VCI/yfinance HTTP round-trip
_TIMEOUT_OHLCV = 30.0
_TIMEOUT_NEWS = 20.0
_TIMEOUT_SENTIMENT = 35.0  # includes LLM call


async def _run(fn, *args, timeout: float, **kwargs) -> str:
    """Run blocking fn in thread executor with timeout. Return text result."""
    if not _warmup_done.is_set():
        return (
            "[warming_up] Server đang khởi động (cache priming). "
            "Thử lại sau 15 giây kể từ khi khởi động server."
        )
    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: fn(*args, **kwargs)),
            timeout=timeout,
        )
        return _result_to_text(result)
    except asyncio.TimeoutError:
        name = fn.__name__
        return f"[upstream_error] Timeout sau {timeout:.0f}s khi gọi {name}. Thử lại sau."


@mcp.tool()
async def get_price(ticker: str) -> str:
    """
    Trả giá đóng cửa gần nhất của mã chứng khoán.

    Mã VN (2-4 ký tự, không dấu chấm): giá VND, nguồn vnstock.
    Mã quốc tế (có dấu chấm hoặc >4 ký tự như AAPL, TSLA): giá USD, nguồn yfinance.

    Ví dụ: ticker="HPG" → giá VND; ticker="AAPL" → giá USD.
    Trả về giá kèm đơn vị tiền tệ, hoặc thông báo lỗi có hướng dẫn xử lý tiếp.
    """
    provider = _detect_provider(ticker)
    return await _run(get_realtime_price, ticker, provider=provider, timeout=_TIMEOUT_PRICE)


@mcp.tool()
async def get_ohlcv(ticker: str, days: int = 60) -> str:
    """
    Trả dữ liệu lịch sử OHLCV (Open/High/Low/Close/Volume) của mã chứng khoán.

    Mã VN (2-4 ký tự): dữ liệu VND, nguồn vnstock.
    Mã quốc tế (có dấu chấm hoặc >4 ký tự): dữ liệu USD, nguồn yfinance.
    Mặc định lấy 60 phiên gần nhất. days tối thiểu 1.

    Dữ liệu trả về dạng bảng text: time, open, high, low, close, volume.
    Cần ít nhất 50 phiên để tính đầy đủ MA50. Cần ít nhất 26 phiên cho MACD.
    """
    provider = _detect_provider(ticker)
    return await _run(get_historical_ohlcv, ticker, days=days, provider=provider, timeout=_TIMEOUT_OHLCV)


@mcp.tool()
async def get_indicators(ticker: str, days: int = 60) -> str:
    """
    Tính chỉ báo kỹ thuật RSI(14), MACD(12,26,9), MA(20), MA(50) cho mã chứng khoán.

    Lấy dữ liệu OHLCV nội bộ rồi tính chỉ báo — không cần truyền DataFrame.
    Mã VN: đơn vị VND. Mã quốc tế (AAPL, TSLA...): đơn vị USD.
    Cần ít nhất 50 phiên (days>=50) để có đầy đủ MA50. Mặc định days=60.

    Trả text mô tả ngữ cảnh: RSI vùng quá mua/quá bán/trung tính, MACD xu hướng tăng/giảm,
    giá đang trên/dưới MA20 và MA50.
    """
    provider = _detect_provider(ticker)
    loop = asyncio.get_running_loop()
    try:
        ohlcv_result = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: get_historical_ohlcv(ticker, days=days, provider=provider)),
            timeout=_TIMEOUT_OHLCV,
        )
    except asyncio.TimeoutError:
        return f"[upstream_error] Timeout sau {_TIMEOUT_OHLCV:.0f}s khi lấy OHLCV '{ticker}'. Thử lại sau."

    if ohlcv_result.status != "ok" or ohlcv_result.data is None:
        return _result_to_text(ohlcv_result)

    currency = "USD" if isinstance(provider, YFinanceProvider) else "VND"
    ind_result = calculate_indicators(ohlcv_result.data, currency=currency)
    return _result_to_text(ind_result)


@mcp.tool()
async def search_news(ticker: str, days: int = 7) -> str:
    """
    Tìm tin tức tài chính gần nhất về mã chứng khoán từ Qdrant news_chunks.

    Trả tối đa 5 tin (dedup theo URL), mỗi tin dạng:
      [nguồn | ngày] tiêu đề — tóm tắt ngắn

    Cần Qdrant đang chạy và news_chunks đã được index (bài 12B).
    days: phạm vi thời gian (1-365). Nếu không có tin, trả thông báo rõ ràng.
    """
    return await _run(search_financial_news, ticker, days, timeout=_TIMEOUT_NEWS)


_TIMEOUT_MARKET = 20.0


@mcp.tool()
async def get_market_perf(period: str = "week", ticker: str = "VNINDEX") -> str:
    """
    Tóm tắt hiệu suất thị trường theo kỳ thời gian.

    period: "today" | "week" | "month" | "quarter" | "year"
            hoặc tiếng Việt: "hôm nay", "tuần này", "quý này", "năm nay"
    ticker: chỉ số hoặc mã CK (mặc định VNINDEX = proxy VN30).

    Trả % thay đổi, high/low kỳ, biên độ, xu hướng (tăng mạnh / giảm nhẹ / đi ngang...).
    Nguồn: ohlcv_daily (Postgres), fallback VCI live API.
    """
    return await _run(get_market_performance, period, ticker, timeout=_TIMEOUT_MARKET)


@mcp.tool()
async def get_breadth() -> str:
    """
    Độ rộng thị trường VN30: số mã tăng/giảm/đứng, top gainers, top losers.

    Dùng để nhận xét phiên hôm nay: thị trường tăng diện rộng hay chỉ 1 nhóm?
    Nguồn: ohlcv_daily (Postgres), fallback batch VCI live API.
    """
    return await _run(get_market_breadth, timeout=_TIMEOUT_MARKET)


@mcp.tool()
async def get_market_sentiment(ticker: str, days: int = 7) -> str:
    """
    Phân tích cảm xúc thị trường (tích cực / tiêu cực / trung tính) về mã chứng khoán
    dựa trên tin tức gần nhất, dùng few-shot LLM (DeepSeek).

    Nội bộ: tìm 5 tiêu đề tin qua search_news, gửi LLM với few-shot examples
    từ data/sentiment_shots_vi.json để phân tích xu hướng tổng thể.

    Trả format: "Xu hướng [NHÃN] — [lý do 1-2 câu]"
    Nếu không đủ tin hoặc LLM lỗi, trả thông báo kèm hướng dẫn xử lý tiếp.
    """
    return await _run(analyze_market_sentiment, ticker, days, timeout=_TIMEOUT_SENTIMENT)


_TIMEOUT_FOREIGN = 10.0
_TIMEOUT_SECTOR = 15.0


@mcp.tool()
async def get_foreign_flow(days: int = 1) -> str:
    """
    Khối ngoại mua/bán ròng toàn thị trường HOSE phiên gần nhất.

    Trả: tổng mua ròng/bán ròng (tỷ đồng), top 5 mua nhiều nhất, top 5 bán nhiều nhất.
    Nguồn: foreign_flows (Postgres). Cần ingest/fetch_foreign_flows.py đã chạy.
    days: số phiên gần nhất (mặc định 1).
    """
    return await _run(get_foreign_flows, days, timeout=_TIMEOUT_FOREIGN)


@mcp.tool()
async def get_sector_perf(period: str = "day") -> str:
    """
    Hiệu suất theo nhóm ngành (% thay đổi, weighted theo giá trị giao dịch).

    Nguồn: ohlcv_daily × securities.sector (Postgres).
    Fallback: hose_universe seed (~140 mã, ~19 ngành) nếu bảng securities rỗng.
    period: "day" (hiện tại chỉ hỗ trợ phiên gần nhất).

    Kết quả: danh sách ngành sắp xếp từ tăng mạnh → giảm mạnh.
    """
    return await _run(get_sector_performance, period, timeout=_TIMEOUT_SECTOR)


_TIMEOUT_RAG = 60.0


@mcp.tool()
async def ask_report(
    question: str,
    tickers: str = "",
    sector: str = "",
    year: str = "",
) -> str:
    """
    Trả lời câu hỏi về nội dung báo cáo tài chính (BCTC).

    question: câu hỏi về số liệu, sự kiện trong báo cáo tài chính.
    tickers:  mã CK cách nhau bằng dấu phẩy — "" = tất cả công ty.
              Ví dụ: "HPG" hoặc "HPG,VCB" để so sánh.
    sector:   lọc theo ngành — "steel", "banking", "real_estate", ...
              "" = không lọc ngành.
    year:     năm tài chính — "2025", "2024", ... "" = tất cả năm.

    Ví dụ:
      ask_report("Tổng tài sản HPG 2025?", tickers="HPG", year="2025")
      ask_report("So sánh lợi nhuận HPG và VCB", tickers="HPG,VCB")
      ask_report("Ngành thép lãi như thế nào năm 2025?", sector="steel", year="2025")
      ask_report("Công ty nào có nợ vay cao nhất?")

    Dùng khi: câu hỏi về nội dung BCTC (bảng cân đối, kết quả kinh doanh, thuyết minh).
    Không dùng cho: giá cổ phiếu, chỉ báo kỹ thuật, tin tức — dùng get_price/get_indicators/search_news.
    """
    ticker_list = [t.strip() for t in tickers.split(",") if t.strip()] if tickers else None
    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: _ask_report(
                    question,
                    tickers=ticker_list,
                    sector=sector or None,
                    year=year or None,
                ),
            ),
            timeout=_TIMEOUT_RAG,
        )
        return _result_to_text(result)
    except asyncio.TimeoutError:
        return f"[upstream_error] Timeout sau {_TIMEOUT_RAG:.0f}s khi query BCTC. Thử lại sau."


@mcp.tool()
async def answer_financial_question(question: str) -> str:
    """
    Trả lời câu hỏi tài chính với auto-routing thông minh.

    Tự động phân tích câu hỏi và chọn nguồn dữ liệu phù hợp:
      - ask_report  → câu hỏi định tính từ BCTC (chiến lược, giải thích, ngữ cảnh)
      - sql_query   → số liệu cụ thể (doanh thu, lợi nhuận, bảng xếp hạng)
      - both        → cần cả số liệu lẫn giải thích từ tài liệu
      - out_of_scope→ không thuộc phạm vi hệ thống

    Ví dụ:
      answer_financial_question("So sánh HPG và VCB năm 2024")
      answer_financial_question("Phân tích chiến lược của Hòa Phát")
      answer_financial_question("Ngành thép Việt Nam có triển vọng gì?")
      answer_financial_question("Doanh thu HPG 2025 là bao nhiêu?")

    Dùng thay cho ask_report khi không biết trước cần RAG hay SQL.
    """
    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: _qa_answer(question)),
            timeout=_TIMEOUT_RAG,
        )
        return _result_to_text(result)
    except asyncio.TimeoutError:
        return f"[upstream_error] Timeout sau {_TIMEOUT_RAG:.0f}s. Thử lại sau."


def _warmup() -> None:
    """Pre-import heavy libs and prime price cache before first MCP call."""
    try:
        import pandas  # noqa: F401
        import pandas_ta  # noqa: F401
        from tools.providers import VciDirectProvider
        p = VciDirectProvider()
        price = p.get_price("HPG")
        sys.stderr.write(f"[mcp warmup] HPG={price} — cache primed\n")
    except Exception as e:
        sys.stderr.write(f"[mcp warmup] failed (non-fatal): {e}\n")
    finally:
        _warmup_done.set()  # always ungate, even on failure


if __name__ == "__main__":
    threading.Thread(target=_warmup, daemon=True).start()
    mcp.run()
