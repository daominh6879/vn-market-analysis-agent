"""
tests/test_tools.py — Unit tests cho 7 tool giá chứng khoán (bài 19 + 19B + 20).

Mock hoàn toàn, không gọi mạng. Mọi tool trả ToolResult.
"""

import math
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pandas as pd
import pytest

from tools.price import (
    PriceProvider,
    YFinanceProvider,
    calculate_indicators,
    get_historical_ohlcv,
    get_historical_ohlcv_intl,
    get_realtime_price,
    get_realtime_price_intl,
)
from tools.result import ToolResult


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _make_ohlcv(n: int = 60) -> pd.DataFrame:
    """Tạo DataFrame OHLCV giả với n phiên."""
    base = datetime(2024, 1, 2)
    dates = [base + timedelta(days=i) for i in range(n)]
    closes = [100_000 + i * 500 for i in range(n)]
    return pd.DataFrame({
        "time": dates,
        "open": [c - 1000 for c in closes],
        "high": [c + 2000 for c in closes],
        "low": [c - 2000 for c in closes],
        "close": closes,
        "volume": [1_000_000] * n,
    })


class MockProvider(PriceProvider):
    def __init__(self, df: pd.DataFrame | None = None, price: float = 80_000.0):
        self._df = df if df is not None else _make_ohlcv()
        self._price = price

    def fetch_price(self, ticker: str) -> float:
        return self._price

    def fetch_history(self, ticker: str, days: int) -> pd.DataFrame:
        return self._df.tail(days).reset_index(drop=True)


# ── get_realtime_price ────────────────────────────────────────────────────────

class TestGetRealtimePrice:
    def test_returns_tool_result(self):
        p = MockProvider(price=95_000.0)
        result = get_realtime_price("FPT", provider=p)
        assert isinstance(result, ToolResult)
        assert result.status == "ok"

    def test_data_is_float(self):
        p = MockProvider(price=95_000.0)
        result = get_realtime_price("FPT", provider=p)
        assert isinstance(result.data, float)
        assert result.data == 95_000.0

    def test_ticker_uppercased(self):
        calls = []

        class TrackingProvider(PriceProvider):
            def fetch_price(self, ticker):
                calls.append(ticker)
                return 1.0

            def fetch_history(self, ticker, days):
                return _make_ohlcv()

        get_realtime_price("fpt", provider=TrackingProvider())
        assert calls[0] == "FPT"

    def test_empty_ticker_returns_invalid_input(self):
        p = MockProvider()
        result = get_realtime_price("", provider=p)
        assert result.status == "invalid_input"
        assert result.data is None

    def test_whitespace_ticker_returns_invalid_input(self):
        p = MockProvider()
        result = get_realtime_price("   ", provider=p)
        assert result.status == "invalid_input"
        assert result.data is None

    def test_provider_value_error_returns_no_data(self):
        class BrokenProvider(PriceProvider):
            def fetch_price(self, ticker):
                raise ValueError("upstream down")

            def fetch_history(self, ticker, days):
                return _make_ohlcv()

        result = get_realtime_price("HPG", provider=BrokenProvider())
        assert result.status == "no_data"
        assert result.data is None

    def test_message_is_non_empty_string(self):
        p = MockProvider(price=80_000.0)
        result = get_realtime_price("HPG", provider=p)
        assert isinstance(result.message, str)
        assert len(result.message) > 0


# ── get_historical_ohlcv ──────────────────────────────────────────────────────

class TestGetHistoricalOhlcv:
    def test_returns_tool_result_ok(self):
        p = MockProvider()
        result = get_historical_ohlcv("VNM", 30, provider=p)
        assert isinstance(result, ToolResult)
        assert result.status == "ok"

    def test_data_is_dataframe(self):
        p = MockProvider()
        result = get_historical_ohlcv("VNM", 30, provider=p)
        assert isinstance(result.data, pd.DataFrame)

    def test_columns_present(self):
        p = MockProvider()
        result = get_historical_ohlcv("VNM", 30, provider=p)
        for col in ["time", "open", "high", "low", "close", "volume"]:
            assert col in result.data.columns

    def test_days_limit_respected(self):
        p = MockProvider(_make_ohlcv(60))
        result = get_historical_ohlcv("VNM", 30, provider=p)
        assert len(result.data) <= 30

    def test_no_duplicate_dates(self):
        df_dup = _make_ohlcv(10)
        df_dup = pd.concat([df_dup, df_dup]).reset_index(drop=True)

        class DupProvider(PriceProvider):
            def fetch_price(self, ticker):
                return 1.0

            def fetch_history(self, ticker, days):
                return df_dup.drop_duplicates(subset=["time"]).tail(days).reset_index(drop=True)

        result = get_historical_ohlcv("FPT", 10, provider=DupProvider())
        assert result.status == "ok"
        assert result.data["time"].duplicated().sum() == 0

    def test_empty_ticker_returns_invalid_input(self):
        p = MockProvider()
        result = get_historical_ohlcv("", 30, provider=p)
        assert result.status == "invalid_input"
        assert result.data is None

    def test_days_zero_returns_invalid_input(self):
        p = MockProvider()
        result = get_historical_ohlcv("FPT", 0, provider=p)
        assert result.status == "invalid_input"

    def test_days_negative_returns_invalid_input(self):
        p = MockProvider()
        result = get_historical_ohlcv("FPT", -5, provider=p)
        assert result.status == "invalid_input"


# ── calculate_indicators ──────────────────────────────────────────────────────

class TestCalculateIndicators:
    def _df(self, n=100):
        return _make_ohlcv(n)

    def test_returns_tool_result_ok(self):
        pytest.importorskip("pandas_ta")
        result = calculate_indicators(self._df())
        assert isinstance(result, ToolResult)
        assert result.status == "ok"

    def test_data_is_string(self):
        pytest.importorskip("pandas_ta")
        result = calculate_indicators(self._df())
        assert isinstance(result.data, str)

    def test_rsi_label_present(self):
        pytest.importorskip("pandas_ta")
        result = calculate_indicators(self._df(100))
        assert "RSI" in result.data

    def test_macd_label_present(self):
        pytest.importorskip("pandas_ta")
        result = calculate_indicators(self._df(100))
        assert "MACD" in result.data

    def test_ma20_label_present(self):
        pytest.importorskip("pandas_ta")
        result = calculate_indicators(self._df(100))
        assert "MA(20)" in result.data

    def test_ma50_label_present(self):
        pytest.importorskip("pandas_ta")
        result = calculate_indicators(self._df(100))
        assert "MA(50)" in result.data

    def test_insufficient_data_still_ok_with_notice(self):
        pytest.importorskip("pandas_ta")
        result = calculate_indicators(self._df(5))
        assert result.status == "ok"
        assert "không đủ dữ liệu" in result.data

    def test_new_listing_under_14_sessions(self):
        """Mã mới lên sàn dưới 14 phiên — trả ok với thông báo thiếu dữ liệu."""
        pytest.importorskip("pandas_ta")
        result = calculate_indicators(self._df(10))
        assert result.status == "ok"
        assert isinstance(result.data, str)
        assert "không đủ dữ liệu" in result.data

    def test_rsi_zone_overbought(self):
        pytest.importorskip("pandas_ta")
        closes = [100 + i * 10 for i in range(100)]
        df = pd.DataFrame({
            "time": [datetime(2024, 1, 1) + timedelta(days=i) for i in range(100)],
            "open": [c - 1 for c in closes],
            "high": [c + 2 for c in closes],
            "low": [c - 2 for c in closes],
            "close": closes,
            "volume": [1_000_000] * 100,
        })
        result = calculate_indicators(df)
        assert "quá mua" in result.data

    def test_rsi_zone_oversold(self):
        pytest.importorskip("pandas_ta")
        closes = [max(1000 - i * 10, 1) for i in range(100)]
        df = pd.DataFrame({
            "time": [datetime(2024, 1, 1) + timedelta(days=i) for i in range(100)],
            "open": [c + 1 for c in closes],
            "high": [c + 2 for c in closes],
            "low": [max(c - 2, 1) for c in closes],
            "close": closes,
            "volume": [1_000_000] * 100,
        })
        result = calculate_indicators(df)
        assert "quá bán" in result.data

    def test_empty_df_returns_invalid_input(self):
        pytest.importorskip("pandas_ta")
        result = calculate_indicators(pd.DataFrame())
        assert result.status == "invalid_input"
        assert result.data is None

    def test_missing_close_column_returns_invalid_input(self):
        pytest.importorskip("pandas_ta")
        df = pd.DataFrame({"open": [1, 2], "high": [2, 3], "low": [0, 1]})
        result = calculate_indicators(df)
        assert result.status == "invalid_input"

    def test_no_nan_in_data_for_sufficient_data(self):
        pytest.importorskip("pandas_ta")
        result = calculate_indicators(self._df(100))
        assert "nan" not in result.data.lower()
        assert "NaN" not in result.data

    def test_currency_tag_vnd(self):
        pytest.importorskip("pandas_ta")
        result = calculate_indicators(self._df(100), currency="VND")
        assert "VND" in result.data

    def test_currency_tag_usd(self):
        pytest.importorskip("pandas_ta")
        result = calculate_indicators(self._df(100), currency="USD")
        assert "USD" in result.data


# ── YFinanceProvider ──────────────────────────────────────────────────────────

class MockYFinanceProvider(PriceProvider):
    def __init__(self, df: pd.DataFrame | None = None, price: float = 150.0):
        self._df = df if df is not None else _make_ohlcv()
        self._price = price

    def fetch_price(self, ticker: str) -> float:
        return self._price

    def fetch_history(self, ticker: str, days: int) -> pd.DataFrame:
        return self._df.tail(days).reset_index(drop=True)


class TestGetRealtimePriceIntl:
    def test_returns_tool_result_ok(self):
        p = MockYFinanceProvider(price=182.5)
        result = get_realtime_price_intl("AAPL", provider=p)
        assert isinstance(result, ToolResult)
        assert result.status == "ok"

    def test_data_is_float(self):
        p = MockYFinanceProvider(price=182.5)
        result = get_realtime_price_intl("AAPL", provider=p)
        assert isinstance(result.data, float)
        assert result.data == 182.5

    def test_ticker_uppercased(self):
        calls = []

        class TrackingProvider(PriceProvider):
            def fetch_price(self, ticker):
                calls.append(ticker)
                return 1.0

            def fetch_history(self, ticker, days):
                return _make_ohlcv()

        get_realtime_price_intl("aapl", provider=TrackingProvider())
        assert calls[0] == "AAPL"

    def test_empty_ticker_returns_invalid_input(self):
        p = MockYFinanceProvider()
        result = get_realtime_price_intl("", provider=p)
        assert result.status == "invalid_input"
        assert result.data is None

    def test_whitespace_ticker_returns_invalid_input(self):
        p = MockYFinanceProvider()
        result = get_realtime_price_intl("   ", provider=p)
        assert result.status == "invalid_input"

    def test_provider_value_error_returns_no_data(self):
        class BrokenProvider(PriceProvider):
            def fetch_price(self, ticker):
                raise ValueError("market closed")

            def fetch_history(self, ticker, days):
                return _make_ohlcv()

        result = get_realtime_price_intl("TSLA", provider=BrokenProvider())
        assert result.status == "no_data"
        assert result.data is None


class TestGetHistoricalOhlcvIntl:
    def test_returns_tool_result_ok(self):
        p = MockYFinanceProvider()
        result = get_historical_ohlcv_intl("AAPL", 30, provider=p)
        assert isinstance(result, ToolResult)
        assert result.status == "ok"

    def test_data_is_dataframe(self):
        p = MockYFinanceProvider()
        result = get_historical_ohlcv_intl("AAPL", 30, provider=p)
        assert isinstance(result.data, pd.DataFrame)

    def test_columns_present(self):
        p = MockYFinanceProvider()
        result = get_historical_ohlcv_intl("AAPL", 30, provider=p)
        for col in ["time", "open", "high", "low", "close", "volume"]:
            assert col in result.data.columns

    def test_days_limit_respected(self):
        p = MockYFinanceProvider(_make_ohlcv(60))
        result = get_historical_ohlcv_intl("TSLA", 30, provider=p)
        assert len(result.data) <= 30

    def test_empty_ticker_returns_invalid_input(self):
        p = MockYFinanceProvider()
        result = get_historical_ohlcv_intl("", 30, provider=p)
        assert result.status == "invalid_input"

    def test_days_zero_returns_invalid_input(self):
        p = MockYFinanceProvider()
        result = get_historical_ohlcv_intl("AAPL", 0, provider=p)
        assert result.status == "invalid_input"


class TestDetectProvider:
    """_detect_provider chọn đúng provider theo format ticker."""

    def test_vn_ticker_3_chars(self):
        from tools.providers import _detect_provider, VciDirectProvider
        assert isinstance(_detect_provider("FPT"), VciDirectProvider)

    def test_vn_ticker_4_chars_no_dot(self):
        from tools.providers import _detect_provider, VciDirectProvider
        assert isinstance(_detect_provider("VNM"), VciDirectProvider)

    def test_vn_index_routes_to_vci_via_proxy(self):
        from tools.providers import _detect_provider, VciDirectProvider, resolve_ticker
        # VNINDEX/HOSE/VN30 → proxy to VN30 → VciDirectProvider
        assert resolve_ticker("VNINDEX") == "VN30"
        assert resolve_ticker("HOSE") == "VN30"
        assert resolve_ticker("VN30") == "VN30"  # passthrough
        assert isinstance(_detect_provider("VNINDEX"), VciDirectProvider)
        assert isinstance(_detect_provider("HOSE"), VciDirectProvider)
        assert isinstance(_detect_provider("VN30"), VciDirectProvider)

    def test_intl_ticker_4_chars_aapl_is_yfinance(self):
        from tools.price import _detect_provider
        assert isinstance(_detect_provider("GOOGL"), YFinanceProvider)

    def test_intl_ticker_with_dot(self):
        from tools.price import _detect_provider
        assert isinstance(_detect_provider("BRK.B"), YFinanceProvider)

    def test_intl_ticker_5_chars(self):
        from tools.price import _detect_provider
        assert isinstance(_detect_provider("GOOGL"), YFinanceProvider)


class TestYFinanceProviderMock:
    """YFinanceProvider internals — provider level masih raises (hợp lệ)."""

    def test_fetch_price_returns_float(self, monkeypatch):
        import types
        mock_yf = types.ModuleType("yfinance")
        hist_df = _make_ohlcv(5)
        hist_df = hist_df.rename(columns={"close": "Close"})

        class MockTicker:
            def history(self, **kwargs):
                return hist_df

        mock_yf.Ticker = lambda ticker: MockTicker()
        monkeypatch.setitem(__import__("sys").modules, "yfinance", mock_yf)
        p = YFinanceProvider()
        price = p.fetch_price("AAPL")
        assert isinstance(price, float)

    def test_fetch_price_empty_raises(self, monkeypatch):
        import types
        mock_yf = types.ModuleType("yfinance")

        class MockTicker:
            def history(self, **kwargs):
                return pd.DataFrame()

        mock_yf.Ticker = lambda ticker: MockTicker()
        monkeypatch.setitem(__import__("sys").modules, "yfinance", mock_yf)
        p = YFinanceProvider()
        with pytest.raises(ValueError, match="Không có dữ liệu"):
            p.fetch_price("FAKE")

    def test_fetch_history_returns_dataframe(self, monkeypatch):
        import types
        mock_yf = types.ModuleType("yfinance")
        raw = pd.DataFrame({
            "Date": [datetime(2024, 1, i + 1) for i in range(30)],
            "Open": [100.0] * 30,
            "High": [105.0] * 30,
            "Low": [95.0] * 30,
            "Close": [102.0] * 30,
            "Volume": [1_000_000] * 30,
        })

        class MockTicker:
            def history(self, **kwargs):
                return raw

        mock_yf.Ticker = lambda ticker: MockTicker()
        monkeypatch.setitem(__import__("sys").modules, "yfinance", mock_yf)
        p = YFinanceProvider()
        df = p.fetch_history("AAPL", 20)
        assert isinstance(df, pd.DataFrame)
        for col in ["time", "open", "high", "low", "close", "volume"]:
            assert col in df.columns
        assert len(df) <= 20


# ── search_financial_news ─────────────────────────────────────────────────────

def _make_news_payloads(n: int = 3, ticker: str = "HPG") -> list[dict]:
    return [
        {
            "url": f"https://example.com/news/{i}",
            "title": f"{ticker} tin số {i}",
            "source": "CafeF",
            "published_at": "2025-08-20T10:00:00Z",
            "text": f"{ticker} tin số {i}\nNội dung bài báo số {i}.",
            "tickers": [ticker],
        }
        for i in range(n)
    ]


class TestSearchFinancialNews:
    def test_returns_tool_result_ok(self, monkeypatch):
        monkeypatch.setattr("rag.news_index.search_news_by_text", lambda *a, **kw: _make_news_payloads(3))
        from tools.price import search_financial_news
        result = search_financial_news("HPG", 7)
        assert isinstance(result, ToolResult)
        assert result.status == "ok"

    def test_data_contains_formatted_news(self, monkeypatch):
        monkeypatch.setattr("rag.news_index.search_news_by_text", lambda *a, **kw: _make_news_payloads(3))
        from tools.price import search_financial_news
        result = search_financial_news("HPG", 7)
        assert "HPG" in result.data
        assert "CafeF" in result.data

    def test_format_source_and_date(self, monkeypatch):
        monkeypatch.setattr("rag.news_index.search_news_by_text", lambda *a, **kw: _make_news_payloads(1))
        from tools.price import search_financial_news
        result = search_financial_news("HPG", 7)
        assert "[CafeF | 2025-08-20]" in result.data

    def test_no_news_returns_no_data(self, monkeypatch):
        monkeypatch.setattr("rag.news_index.search_news_by_text", lambda *a, **kw: [])
        monkeypatch.setattr("tools.price._auto_fetch_ticker_news", lambda *a, **kw: None)
        from tools.price import search_financial_news
        result = search_financial_news("HPG", 7)
        assert result.status == "no_data"
        assert result.data is None
        assert "HPG" in result.message

    def test_dedup_by_url(self, monkeypatch):
        payloads = _make_news_payloads(2) * 3  # same URLs repeated
        monkeypatch.setattr("rag.news_index.search_news_by_text", lambda *a, **kw: payloads)
        from tools.price import search_financial_news
        result = search_financial_news("HPG", 7)
        lines = [l for l in result.data.strip().split("\n") if l]
        assert len(lines) == 2

    def test_top_5_max(self, monkeypatch):
        payloads = [
            {
                "url": f"https://example.com/news/{i}",
                "title": f"HPG tin {i}",
                "source": "VnExpress",
                "published_at": "2025-08-20T10:00:00Z",
                "text": f"HPG tin {i}",
                "tickers": ["HPG"],
            }
            for i in range(10)
        ]
        monkeypatch.setattr("rag.news_index.search_news_by_text", lambda *a, **kw: payloads)
        from tools.price import search_financial_news
        result = search_financial_news("HPG", 7)
        lines = [l for l in result.data.strip().split("\n") if l]
        assert len(lines) <= 5

    def test_empty_ticker_returns_invalid_input(self):
        from tools.price import search_financial_news
        result = search_financial_news("", 7)
        assert result.status == "invalid_input"
        assert result.data is None

    def test_days_zero_returns_invalid_input(self):
        from tools.price import search_financial_news
        result = search_financial_news("HPG", 0)
        assert result.status == "invalid_input"

    def test_days_over_365_returns_invalid_input(self):
        from tools.price import search_financial_news
        result = search_financial_news("HPG", 366)
        assert result.status == "invalid_input"

    def test_ticker_uppercased_in_query(self, monkeypatch):
        calls: list[str] = []

        def fake_search(query, **kw):
            calls.append(query)
            return []

        monkeypatch.setattr("rag.news_index.search_news_by_text", fake_search)
        from tools.price import search_financial_news
        search_financial_news("hpg", 7)
        assert calls[0] == "HPG"


# ── analyze_market_sentiment ──────────────────────────────────────────────────

class _FakeLLMResponse:
    def __init__(self, text: str):
        self.text = text


class _FakeLLMClient:
    def __init__(self, response_text: str = "Xu hướng TÍCH CỰC — kết quả kinh doanh tốt."):
        self._text = response_text

    def generate(self, messages, **kw):
        return _FakeLLMResponse(self._text)


class TestAnalyzeMarketSentiment:
    def _patch(self, monkeypatch, payloads, llm_text="Xu hướng TÍCH CỰC — kết quả kinh doanh tốt."):
        monkeypatch.setattr("rag.news_index.search_news_by_text", lambda *a, **kw: payloads)
        monkeypatch.setattr("llm.factory.create_client", lambda: _FakeLLMClient(llm_text))

    def test_returns_tool_result_ok(self, monkeypatch):
        self._patch(monkeypatch, _make_news_payloads(3))
        from tools.price import analyze_market_sentiment
        result = analyze_market_sentiment("HPG", 7)
        assert isinstance(result, ToolResult)
        assert result.status == "ok"

    def test_data_is_string(self, monkeypatch):
        self._patch(monkeypatch, _make_news_payloads(3))
        from tools.price import analyze_market_sentiment
        result = analyze_market_sentiment("HPG", 7)
        assert isinstance(result.data, str)
        assert len(result.data) > 0

    def test_no_news_returns_no_data(self, monkeypatch):
        monkeypatch.setattr("rag.news_index.search_news_by_text", lambda *a, **kw: [])
        monkeypatch.setattr("tools.price._auto_fetch_ticker_news", lambda *a, **kw: None)
        from tools.price import analyze_market_sentiment
        result = analyze_market_sentiment("HPG", 7)
        assert result.status == "no_data"
        assert result.data is None

    def test_positive_label_in_data(self, monkeypatch):
        self._patch(monkeypatch, _make_news_payloads(3), "Xu hướng TÍCH CỰC — cổ phiếu tăng mạnh.")
        from tools.price import analyze_market_sentiment
        result = analyze_market_sentiment("HPG")
        assert "TÍCH CỰC" in result.data or "tích cực" in result.data.lower()

    def test_negative_label_in_data(self, monkeypatch):
        self._patch(monkeypatch, _make_news_payloads(3), "Xu hướng TIÊU CỰC — lợi nhuận giảm mạnh.")
        from tools.price import analyze_market_sentiment
        result = analyze_market_sentiment("HPG")
        assert "TIÊU CỰC" in result.data or "tiêu cực" in result.data.lower()

    def test_llm_error_returns_upstream_error(self, monkeypatch):
        monkeypatch.setattr("rag.news_index.search_news_by_text", lambda *a, **kw: _make_news_payloads(2))

        class BrokenClient:
            def generate(self, *a, **kw):
                raise RuntimeError("LLM unreachable")

        monkeypatch.setattr("llm.factory.create_client", lambda: BrokenClient())
        from tools.price import analyze_market_sentiment
        result = analyze_market_sentiment("HPG")
        assert result.status == "upstream_error"
        assert result.data is None

    def test_empty_ticker_returns_invalid_input(self):
        from tools.price import analyze_market_sentiment
        result = analyze_market_sentiment("", 7)
        assert result.status == "invalid_input"
        assert result.data is None

    def test_dedup_payloads_used(self, monkeypatch):
        payloads = _make_news_payloads(2) * 2
        captured_prompts: list[str] = []

        class CapturingClient:
            def generate(self, messages, **kw):
                captured_prompts.append(messages[0].content)
                return _FakeLLMResponse("Xu hướng TRUNG TÍNH — ổn định.")

        monkeypatch.setattr("rag.news_index.search_news_by_text", lambda *a, **kw: payloads)
        monkeypatch.setattr("llm.factory.create_client", lambda: CapturingClient())
        from tools.price import analyze_market_sentiment
        analyze_market_sentiment("HPG")
        assert captured_prompts, "LLM not called"
        prompt = captured_prompts[0]
        assert "1." in prompt and "2." in prompt
        assert "3." not in prompt
