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
    get_historical_ohlcv,
    get_realtime_price,
    search_financial_news,
)

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
    loop = asyncio.get_event_loop()
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
    loop = asyncio.get_event_loop()
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
