"""
tests/test_phase6.py — Phase 6: market brief graph + template + CLI.

All tests use mocks — no live network, no DB, no LLM required.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agents.market_brief_state import MarketBriefState
from tools.result import ToolResult

# ── helpers ───────────────────────────────────────────────────────────────────

_OK_INDICES = ToolResult(
    status="ok",
    data=[{"name": "S&P 500", "close": 7677.28, "change_pct": 0.32}],
    message="• S&P 500: 7,677.28 (+0.32%)\n• Dow Jones: 53,577.40 (+0.30%)",
)
_OK_COMMODITIES = ToolResult(
    status="ok",
    data=[
        {"ticker": "GC=F", "name": "Gold", "price": 4624.87, "change_pct": -0.6, "unit": "USD/oz"},
        {"ticker": "CL=F", "name": "WTI Crude", "price": 85.09, "change_pct": -0.3, "unit": "USD/bbl"},
        {"ticker": "BZ=F", "name": "Brent Crude", "price": 90.35, "change_pct": -0.2, "unit": "USD/bbl"},
    ],
    message="• Gold: 4,624.87 USD/oz (-0.60%)\n• WTI: 85.09 (-0.30%)\n• Brent: 90.35 (-0.20%)",
)
_OK_CRYPTO = ToolResult(
    status="ok",
    data={"coins": [{"symbol": "BTC", "price_usd": 80700, "change_24h_pct": 5.0}],
          "total_market_cap_trillion_usd": 2.66},
    message="• BTC: 80,700.00 USD (+5.00%)\n• Total market cap: ~2.66 nghìn tỷ USD",
)
_OK_FX = ToolResult(
    status="ok",
    data={"buy": 25920, "sell": 26330, "transfer": 26125},
    message="USD/VND — VCB mua: 25,920 | bán: 26,330 | CK: 26,125 VND",
)
_OK_VN_GOLD = ToolResult(
    status="ok",
    data={"buy_vnd": 147.6, "sell_vnd": 150.6},
    message="Vàng SJC: mua 147.6 – bán 150.6 triệu đồng/lượng (chênh vs thế giới ~2.6 triệu đồng/lượng)",
)
_ERR = ToolResult(status="upstream_error", data=None, message="err")


# ═══ 1. State TypedDict ═══════════════════════════════════════════════════════

class TestMarketBriefState:
    def test_can_instantiate_empty(self):
        s = MarketBriefState()
        assert s == {}

    def test_can_instantiate_with_fields(self):
        s = MarketBriefState(date="2026-08-26", step_count=0, history=[], missing_fields=[])
        assert s["date"] == "2026-08-26"

    def test_optional_fields_absent(self):
        s = MarketBriefState(date="2026-08-26")
        assert "report_text" not in s
        assert "outlook_text" not in s

    def test_make_initial_state(self):
        from agents.market_brief_graph import make_initial_state
        s = make_initial_state(date="2026-08-26", output_path="info/test.txt")
        assert s["date"] == "2026-08-26"
        assert s["output_path"] == "info/test.txt"
        assert s["step_count"] == 0


# ═══ 2. Template rendering ════════════════════════════════════════════════════

class TestRenderReport:
    def _make_state(self, **overrides) -> MarketBriefState:
        base = MarketBriefState(
            date="2026-08-26",
            output_path="",
            world_block="• S&P 500: 7,677.28 (+0.32%)",
            gold_oil_block="Vàng: 4,624 USD\nSJC: 147.6-150.6 triệu\nWTI: 85.09",
            crypto_block="• BTC: 80,700 (+5.00%)",
            fx_block="USD/VND — VCB mua: 25,920 | bán: 26,330",
            vn_index_text="VN-Index đóng cửa 1,791.41 điểm (+2.63đ, +0.15%)",
            breadth_text="246 mã tăng / 439 mã giảm",
            movers_text="Dẫn dắt: VIC 2,300tỷ.",
            foreign_text="Mua ròng 188 tỷ đồng",
            news_text="FTSE Russell chính thức nâng hạng Việt Nam",
            events_text="TN1 giao dịch không hưởng quyền cổ tức",
            outlook_text="VN-Index thử thách vùng kháng cự 1.800 điểm.",
            step_count=3,
            history=[],
            missing_fields=[],
        )
        base.update(overrides)
        return base

    def test_render_produces_report(self):
        from agents.market_brief_graph import render_report
        state = self._make_state()
        result = render_report(state)
        report = result["report_text"]
        assert "26/08/2026" in report
        assert "🌍 THỊ TRƯỜNG THẾ GIỚI QUA ĐÊM" in report
        assert "💛 VÀNG & DẦU" in report
        assert "₿ Bitcoin" in report
        assert "💵 TỶ GIÁ" in report
        assert "📌 TIN ĐÁNG CHÚ Ý HÔM NAY" in report
        assert "🎯 NHẬN ĐỊNH PHIÊN HÔM NAY" in report
        assert "⚠️ Thông tin mang tính tham khảo" in report
        assert "#VNIndex" in report

    def test_render_includes_data_sections(self):
        from agents.market_brief_graph import render_report
        state = self._make_state()
        result = render_report(state)
        r = result["report_text"]
        assert "S&P 500" in r
        assert "VN-Index đóng cửa 1,791.41" in r
        assert "246 mã tăng" in r
        assert "Mua ròng 188 tỷ" in r
        assert "thử thách vùng kháng cự" in r

    def test_render_missing_field_placeholder(self):
        from agents.market_brief_graph import render_report
        state = self._make_state(world_block="(không có dữ liệu)")
        result = render_report(state)
        assert "(không có dữ liệu)" in result["report_text"]

    def test_render_writes_file(self, tmp_path):
        from agents.market_brief_graph import render_report
        out = str(tmp_path / "brief_test.txt")
        state = self._make_state(output_path=out)
        result = render_report(state)
        assert result["output_file"] == out
        assert Path(out).exists()
        content = Path(out).read_text(encoding="utf-8")
        assert "26/08/2026" in content

    def test_render_no_file_when_path_empty(self, tmp_path):
        from agents.market_brief_graph import render_report
        state = self._make_state(output_path="")
        result = render_report(state)
        assert result["output_file"] == ""

    def test_date_formatting_dd_mm_yyyy(self):
        from agents.market_brief_graph import render_report
        state = self._make_state(date="2026-08-24")
        result = render_report(state)
        assert "24/08/2026" in result["report_text"]

    def test_step_count_incremented(self):
        from agents.market_brief_graph import render_report
        state = self._make_state(step_count=2)
        result = render_report(state)
        assert result["step_count"] == 3


# ═══ 3. _collect_world (unit) ═════════════════════════════════════════════════

class TestCollectWorld:
    def test_all_ok(self):
        from agents.market_brief_graph import _collect_world
        with (
            patch("agents.market_brief_graph.get_global_indices", return_value=_OK_INDICES),
            patch("agents.market_brief_graph.get_commodities", return_value=_OK_COMMODITIES),
            patch("agents.market_brief_graph.get_crypto_prices", return_value=_OK_CRYPTO),
            patch("agents.market_brief_graph.get_fx_rates", return_value=_OK_FX),
            patch("agents.market_brief_graph.get_vn_gold", return_value=_OK_VN_GOLD),
        ):
            r = _collect_world()
        assert "S&P 500" in r["world_block"]
        assert "missing" in r
        assert "world_indices" not in r["missing"]

    def test_indices_failure_uses_placeholder(self):
        from agents.market_brief_graph import _collect_world
        with (
            patch("agents.market_brief_graph.get_global_indices", return_value=_ERR),
            patch("agents.market_brief_graph.get_commodities", return_value=_ERR),
            patch("agents.market_brief_graph.get_crypto_prices", return_value=_ERR),
            patch("agents.market_brief_graph.get_fx_rates", return_value=_ERR),
            patch("agents.market_brief_graph.get_vn_gold", return_value=_ERR),
        ):
            r = _collect_world()
        assert r["world_block"] == "(không có dữ liệu)"
        assert "world_indices" in r["missing"]

    def test_partial_failure_still_returns(self):
        from agents.market_brief_graph import _collect_world
        with (
            patch("agents.market_brief_graph.get_global_indices", return_value=_OK_INDICES),
            patch("agents.market_brief_graph.get_commodities", return_value=_ERR),
            patch("agents.market_brief_graph.get_crypto_prices", return_value=_OK_CRYPTO),
            patch("agents.market_brief_graph.get_fx_rates", return_value=_ERR),
            patch("agents.market_brief_graph.get_vn_gold", return_value=_ERR),
        ):
            r = _collect_world()
        assert "S&P 500" in r["world_block"]  # indices ok
        assert "BTC" in r["crypto_block"]     # crypto ok
        assert r["fx_block"] == "(không có dữ liệu)"


# ═══ 4. _collect_vn (unit) ════════════════════════════════════════════════════

class TestCollectVn:
    def _make_index_row(self):
        return {
            "index_code": "VNINDEX",
            "date": "2026-08-26",
            "close": 1791.41,
            "change_pts": 2.63,
            "change_pct": 0.15,
            "matched_value": 21400e9,
            "matched_volume": 500000000,
            "foreign_net": 188e9,
        }

    def test_vn_index_from_db(self):
        from agents.market_brief_graph import _collect_vn
        mock_breadth = ToolResult(
            status="ok",
            data={"advances": 246, "declines": 439, "unchanged": 10, "top_gainers": [], "top_losers": [], "all_changes": [], "summary": ""},
            message="HOSE breadth: 246 tăng / 10 đứng / 439 giảm",
        )
        mock_movers = ToolResult(
            status="ok",
            data=[{"ticker": "VIC", "close": 220500, "volume": 10000000, "traded_value": 2300e9, "pct_change": 2.8}],
            message="Top thanh khoản:\n• VIC: 220,500 (+2.8%) — value ~2,300 tỷ",
        )
        mock_ff = ToolResult(status="ok", data={"market_net_value_bn": 188.0}, message="Khối ngoại: Mua ròng 188 tỷ")
        mock_sec = ToolResult(
            status="ok",
            data=[{"sector": "Ngân hàng", "pct_change": 1.2, "ticker_count": 20, "total_value_bn": 5000}],
            message="Ngân hàng +1.2%",
        )
        with (
            patch("agents.market_brief_graph.query_index_latest", return_value=self._make_index_row()),
            patch("agents.market_brief_graph.get_market_breadth", return_value=mock_breadth),
            patch("agents.market_brief_graph.get_top_movers", return_value=mock_movers),
            patch("agents.market_brief_graph.get_foreign_flows", return_value=mock_ff),
            patch("agents.market_brief_graph.get_sector_performance", return_value=mock_sec),
        ):
            r = _collect_vn("2026-08-26")
        assert "1,791.41" in r["vn_index_text"]
        assert "21,400" in r["vn_index_text"] or "21400" in r["vn_index_text"]
        assert "246" in r["breadth_text"]
        assert "439" in r["breadth_text"]

    def test_vn_index_db_missing_uses_missing_placeholder(self):
        from agents.market_brief_graph import _collect_vn
        mock_mp = ToolResult(status="upstream_error", data=None, message="err")
        mock_breadth = ToolResult(status="upstream_error", data=None, message="err")
        mock_movers = ToolResult(status="upstream_error", data=None, message="err")
        mock_ff = ToolResult(status="upstream_error", data=None, message="err")
        mock_sec = ToolResult(status="upstream_error", data=None, message="err")
        with (
            patch("agents.market_brief_graph.query_index_latest", return_value=None),
            patch("agents.market_brief_graph.get_market_performance", return_value=mock_mp),
            patch("agents.market_brief_graph.get_market_breadth", return_value=mock_breadth),
            patch("agents.market_brief_graph.get_top_movers", return_value=mock_movers),
            patch("agents.market_brief_graph.get_foreign_flows", return_value=mock_ff),
            patch("agents.market_brief_graph.get_sector_performance", return_value=mock_sec),
        ):
            r = _collect_vn("2026-08-26")
        assert r["vn_index_text"] == "(không có dữ liệu)"
        assert "vn_index" in r["missing"]

    def test_breadth_missing_uses_placeholder(self):
        from agents.market_brief_graph import _collect_vn
        mock_breadth = ToolResult(status="upstream_error", data=None, message="err")
        mock_movers = ToolResult(status="no_data", data=None, message="no data")
        mock_ff = ToolResult(status="ok", data={}, message="Mua ròng 0 tỷ")
        mock_sec = ToolResult(status="ok", data=[], message="")
        with (
            patch("agents.market_brief_graph.query_index_latest", return_value=self._make_index_row()),
            patch("agents.market_brief_graph.get_market_breadth", return_value=mock_breadth),
            patch("agents.market_brief_graph.get_top_movers", return_value=mock_movers),
            patch("agents.market_brief_graph.get_foreign_flows", return_value=mock_ff),
            patch("agents.market_brief_graph.get_sector_performance", return_value=mock_sec),
        ):
            r = _collect_vn("2026-08-26")
        assert r["breadth_text"] == "(không có dữ liệu)"
        assert "breadth" in r["missing"]


# ═══ 5. _collect_news (unit) ══════════════════════════════════════════════════

class TestCollectNews:
    def test_all_ok(self):
        from agents.market_brief_graph import _collect_news
        mock_news = ToolResult(
            status="ok", data="news", message="[CafeF | 2026-08-26] FTSE nâng hạng Việt Nam"
        )
        mock_ev = ToolResult(
            status="ok",
            data=[{"ticker": "TN1", "event_type": "dividend", "ex_date": "2026-08-26", "ratio": 5.0, "note": "cổ tức 5%"}],
            message="• TN1    | dividend     | 2026-08-26 (5.0%) — cổ tức 5%",
        )
        mock_bv = ToolResult(
            status="ok",
            data=[{"broker": "TPS", "stance": "buy", "target": 1900.0}],
            message="• TPS        → buy | target 1,900",
        )
        with (
            patch("agents.market_brief_graph.search_financial_news", return_value=mock_news),
            patch("agents.market_brief_graph.get_corporate_events", return_value=mock_ev),
            patch("agents.market_brief_graph.get_broker_views", return_value=mock_bv),
        ):
            r = _collect_news("2026-08-26")
        assert "FTSE" in r["news_text"]
        assert "TN1" in r["events_text"]
        assert "TPS" in r["broker_text"]
        assert r["missing"] == []

    def test_no_events_returns_note(self):
        from agents.market_brief_graph import _collect_news
        mock_news = ToolResult(status="ok", data="news", message="market news")
        mock_ev = ToolResult(status="no_data", data=[], message="Không có sự kiện")
        mock_bv = ToolResult(status="no_data", data=[], message="Không có nhận định")
        with (
            patch("agents.market_brief_graph.search_financial_news", return_value=mock_news),
            patch("agents.market_brief_graph.get_corporate_events", return_value=mock_ev),
            patch("agents.market_brief_graph.get_broker_views", return_value=mock_bv),
        ):
            r = _collect_news("2026-08-26")
        assert "Không có sự kiện" in r["events_text"]
        assert "Không có nhận định" in r["broker_text"]
        assert r["missing"] == []

    def test_all_error_returns_placeholders(self):
        from agents.market_brief_graph import _collect_news
        with (
            patch("agents.market_brief_graph.search_financial_news", return_value=_ERR),
            patch("agents.market_brief_graph.get_corporate_events", return_value=_ERR),
            patch("agents.market_brief_graph.get_broker_views", return_value=_ERR),
        ):
            r = _collect_news("2026-08-26")
        assert r["news_text"] == "(không có dữ liệu)"
        assert "news" in r["missing"]


# ═══ 6. _collect_technical (unit) ════════════════════════════════════════════

class TestCollectTechnical:
    def _make_df(self):
        import pandas as pd
        import numpy as np
        n = 250
        rng = pd.date_range("2025-10-01", periods=n)
        closes = 1700 + np.cumsum(np.random.randn(n) * 5)
        return pd.DataFrame({
            "time": rng.strftime("%Y-%m-%d"),
            "open": closes - 2,
            "high": closes + 5,
            "low": closes - 5,
            "close": closes,
            "volume": np.random.randint(1_000_000, 10_000_000, n).astype(float),
        })

    def test_with_good_data(self):
        from agents.market_brief_graph import _collect_technical
        df = self._make_df()
        ok_ohlcv = ToolResult(status="ok", data=df, message=f"Lấy được {len(df)} phiên")
        ok_ind = ToolResult(status="ok", data="signals", message="RSI(14) = 55.0 → vùng trung tính")
        ok_candle = ToolResult(status="ok", data="Doji", message="Mẫu nến (phiên cuối): Doji")
        ok_lvl = ToolResult(status="ok", data={}, message="Hỗ trợ: 1,750 | Kháng cự: 1,800")
        with (
            patch("agents.market_brief_graph.get_historical_ohlcv", return_value=ok_ohlcv),
            patch("agents.market_brief_graph.calculate_indicators", return_value=ok_ind),
            patch("agents.market_brief_graph.detect_candle_pattern", return_value=ok_candle),
            patch("agents.market_brief_graph.find_support_resistance", return_value=ok_lvl),
        ):
            r = _collect_technical("2026-08-26")
        assert "RSI" in r["tech_signals"]
        assert "Doji" in r["candle_pattern"]
        assert "Hỗ trợ" in r["levels_text"]
        assert r["missing"] == []

    def test_ohlcv_fail_returns_all_missing(self):
        from agents.market_brief_graph import _collect_technical
        with patch("agents.market_brief_graph.get_historical_ohlcv", return_value=_ERR):
            r = _collect_technical("2026-08-26")
        assert r["tech_signals"] == "(không có dữ liệu)"
        assert r["candle_pattern"] == "(không có dữ liệu)"
        assert r["levels_text"] == "(không có dữ liệu)"
        assert "tech_signals" in r["missing"]


# ═══ 7. collect_all node (integration mock) ══════════════════════════════════

class TestCollectAll:
    def _world_result(self):
        return {
            "world_block": "• S&P 500: 7,677",
            "gold_oil_block": "Gold: 4,624",
            "crypto_block": "• BTC: 80,700",
            "fx_block": "USD/VND: 25,920",
            "missing": [],
        }

    def _vn_result(self):
        return {
            "vn_index_text": "VN-Index 1,791.41",
            "breadth_text": "246 / 439",
            "movers_text": "VIC 2,300tỷ",
            "foreign_text": "Mua ròng 188 tỷ",
            "sector_text": "Ngân hàng +1.2%",
            "missing": [],
        }

    def _news_result(self):
        return {
            "news_text": "FTSE nâng hạng Việt Nam",
            "events_text": "TN1 cổ tức 5%",
            "broker_text": "TPS → target 1,900",
            "missing": [],
        }

    def _tech_result(self):
        return {
            "tech_signals": "RSI(14) = 55.0",
            "candle_pattern": "Doji",
            "levels_text": "Hỗ trợ: 1,750",
            "missing": [],
        }

    def test_merges_all_collectors(self):
        from agents.market_brief_graph import collect_all
        state = MarketBriefState(date="2026-08-26", step_count=0, history=[], missing_fields=[])
        with (
            patch("agents.market_brief_graph._collect_world", return_value=self._world_result()),
            patch("agents.market_brief_graph._collect_vn", return_value=self._vn_result()),
            patch("agents.market_brief_graph._collect_news", return_value=self._news_result()),
            patch("agents.market_brief_graph._collect_technical", return_value=self._tech_result()),
        ):
            result = collect_all(state)
        assert result["world_block"] == "• S&P 500: 7,677"
        assert result["vn_index_text"] == "VN-Index 1,791.41"
        assert result["news_text"] == "FTSE nâng hạng Việt Nam"
        assert result["tech_signals"] == "RSI(14) = 55.0"
        assert result["missing_fields"] == []
        assert result["step_count"] == 1

    def test_aggregates_missing_fields(self):
        from agents.market_brief_graph import collect_all
        state = MarketBriefState(date="2026-08-26", step_count=0, history=[], missing_fields=[])
        world_r = {**self._world_result(), "missing": ["world_indices", "fx_rates"]}
        vn_r = {**self._vn_result(), "missing": ["vn_index"]}
        with (
            patch("agents.market_brief_graph._collect_world", return_value=world_r),
            patch("agents.market_brief_graph._collect_vn", return_value=vn_r),
            patch("agents.market_brief_graph._collect_news", return_value=self._news_result()),
            patch("agents.market_brief_graph._collect_technical", return_value=self._tech_result()),
        ):
            result = collect_all(state)
        assert "world_indices" in result["missing_fields"]
        assert "fx_rates" in result["missing_fields"]
        assert "vn_index" in result["missing_fields"]


# ═══ 8. compose_outlook (unit) ════════════════════════════════════════════════

class TestComposeOutlook:
    def _make_state(self) -> MarketBriefState:
        return MarketBriefState(
            date="2026-08-26",
            tech_signals="RSI(14) = 55.0, MA(50)=1,770, trên MA50",
            candle_pattern="Doji — thân nến rất nhỏ, do dự",
            levels_text="Hỗ trợ: 1,750 | Kháng cự: 1,800",
            broker_text="TPS → target 1,900",
            news_text="FTSE nâng hạng",
            vn_index_text="VN-Index 1,791.41 (+0.15%)",
            breadth_text="246 / 439",
            foreign_text="Mua ròng 188 tỷ",
            sector_text="Ngân hàng +1.2%",
            step_count=2,
            history=[],
            missing_fields=[],
        )

    def test_calls_llm_and_sets_outlook(self):
        from agents.market_brief_graph import compose_outlook

        mock_resp = MagicMock()
        mock_resp.text = "VN-Index thử thách kháng cự 1.800.\nDòng tiền phân hóa."
        mock_resp.input_tokens = 400
        mock_resp.output_tokens = 120

        mock_client = MagicMock()
        mock_client.generate.return_value = mock_resp

        with patch("agents.market_brief_graph.create_client", return_value=mock_client):
            result = compose_outlook(self._make_state())

        assert "1.800" in result["outlook_text"]
        assert result["step_count"] == 3
        assert any(h["step"] == "compose_outlook" for h in result["history"])

    def test_llm_failure_returns_placeholder(self):
        from agents.market_brief_graph import compose_outlook

        with patch("agents.market_brief_graph.create_client", side_effect=RuntimeError("LLM down")):
            result = compose_outlook(self._make_state())

        assert "(không thể tạo nhận định" in result["outlook_text"]
        history = result["history"]
        assert any("error" in h for h in history)


# ═══ 9. Full graph (smoke test, all mocked) ═══════════════════════════════════

class TestFullGraph:
    def test_graph_compiles(self):
        from agents.market_brief_graph import build_brief_graph
        app = build_brief_graph()
        assert app is not None

    def test_graph_runs_end_to_end(self, tmp_path):
        from agents.market_brief_graph import build_brief_graph, make_initial_state

        mock_outlook = "(outlook text)"
        mock_resp = MagicMock()
        mock_resp.text = mock_outlook
        mock_resp.input_tokens = 300
        mock_resp.output_tokens = 100
        mock_client = MagicMock()
        mock_client.generate.return_value = mock_resp

        ok_idx = ToolResult(status="ok", data=[], message="• S&P 500: 7,677")
        ok_com = ToolResult(status="ok", data=[], message="• Gold: 4,624")
        ok_cry = ToolResult(status="ok", data={"coins": [], "total_market_cap_trillion_usd": 2.6}, message="• BTC: 80,700")
        ok_fx = ToolResult(status="ok", data={}, message="VCB: 25,920 – 26,330")
        ok_gold = ToolResult(status="ok", data={}, message="SJC: 147.6 – 150.6")
        ok_mp = ToolResult(
            status="ok",
            data={"last_close": 1791.41, "pct_change": 0.15, "trend": "tăng nhẹ"},
            message="VNINDEX today: tăng nhẹ (+0.15%)",
        )
        ok_breadth = ToolResult(
            status="ok",
            data={"advances": 246, "declines": 439, "unchanged": 10,
                  "top_gainers": [], "top_losers": [], "all_changes": [], "summary": ""},
            message="246 tăng / 439 giảm",
        )
        ok_movers = ToolResult(
            status="ok",
            data=[{"ticker": "VIC", "close": 220500, "volume": 10000, "traded_value": 2300e9, "pct_change": 2.8}],
            message="VIC 2,300 tỷ",
        )
        ok_ff = ToolResult(status="ok", data={}, message="Khối ngoại: Mua ròng 188 tỷ")
        ok_sec = ToolResult(status="ok", data=[{"sector": "NH", "pct_change": 1.2, "ticker_count": 10, "total_value_bn": 100}], message="NH +1.2%")
        ok_news = ToolResult(status="ok", data="news", message="[CafeF] FTSE")
        ok_ev = ToolResult(status="no_data", data=[], message="Không có sự kiện")
        ok_bv = ToolResult(status="no_data", data=[], message="Không có nhận định")

        import pandas as pd, numpy as np
        n = 250
        df = pd.DataFrame({
            "time": pd.date_range("2025-10-01", periods=n).strftime("%Y-%m-%d"),
            "open": 1790.0 + np.zeros(n),
            "high": 1795.0 + np.zeros(n),
            "low": 1785.0 + np.zeros(n),
            "close": 1791.41 + np.zeros(n),
            "volume": np.ones(n) * 1_000_000,
        })
        ok_ohlcv = ToolResult(status="ok", data=df, message=f"{n} phiên")
        ok_ind = ToolResult(status="ok", data="ind", message="RSI=55, MA50=1770, trên MA50")
        ok_cnd = ToolResult(status="ok", data="Doji", message="Mẫu nến: Doji")
        ok_lvl = ToolResult(status="ok", data={}, message="Hỗ trợ: 1,750")

        out_file = str(tmp_path / "brief_e2e.txt")
        initial = make_initial_state(date="2026-08-26", output_path=out_file)

        with (
            patch("agents.market_brief_graph.get_global_indices", return_value=ok_idx),
            patch("agents.market_brief_graph.get_commodities", return_value=ok_com),
            patch("agents.market_brief_graph.get_crypto_prices", return_value=ok_cry),
            patch("agents.market_brief_graph.get_fx_rates", return_value=ok_fx),
            patch("agents.market_brief_graph.get_vn_gold", return_value=ok_gold),
            patch("agents.market_brief_graph.query_index_latest", return_value=None),
            patch("agents.market_brief_graph.get_market_performance", return_value=ok_mp),
            patch("agents.market_brief_graph.get_market_breadth", return_value=ok_breadth),
            patch("agents.market_brief_graph.get_top_movers", return_value=ok_movers),
            patch("agents.market_brief_graph.get_foreign_flows", return_value=ok_ff),
            patch("agents.market_brief_graph.get_sector_performance", return_value=ok_sec),
            patch("agents.market_brief_graph.search_financial_news", return_value=ok_news),
            patch("agents.market_brief_graph.get_corporate_events", return_value=ok_ev),
            patch("agents.market_brief_graph.get_broker_views", return_value=ok_bv),
            patch("agents.market_brief_graph.get_historical_ohlcv", return_value=ok_ohlcv),
            patch("agents.market_brief_graph.calculate_indicators", return_value=ok_ind),
            patch("agents.market_brief_graph.detect_candle_pattern", return_value=ok_cnd),
            patch("agents.market_brief_graph.find_support_resistance", return_value=ok_lvl),
            patch("agents.market_brief_graph.create_client", return_value=mock_client),
        ):
            app = build_brief_graph()
            final = app.invoke(initial)

        assert final.get("report_text"), "report_text should not be empty"
        assert "26/08/2026" in final["report_text"]
        assert mock_outlook in final["report_text"]
        assert Path(out_file).exists()
        assert final["step_count"] == 3


# ═══ 10. run_brief CLI ════════════════════════════════════════════════════════

class TestRunBriefCli:
    def test_invalid_date_exits(self):
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "agents/run_brief.py", "--date", "not-a-date"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )
        assert result.returncode != 0
        assert "YYYY-MM-DD" in result.stderr

    def test_help_flag(self):
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "agents/run_brief.py", "--help"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )
        assert result.returncode == 0
        assert "--date" in result.stdout


# ═══ 11. Template file exists and has required placeholders ══════════════════

class TestTemplateFile:
    def test_template_exists(self):
        tmpl = Path("agents/templates/market_brief.txt")
        assert tmpl.exists(), "Template file not found"

    def test_template_has_required_placeholders(self):
        tmpl = Path("agents/templates/market_brief.txt").read_text(encoding="utf-8")
        required = [
            "{date_display}", "{world_block}", "{gold_oil_block}", "{crypto_block}",
            "{fx_block}", "{vn_index_text}", "{breadth_text}", "{foreign_text}",
            "{news_text}", "{events_text}", "{outlook_text}",
        ]
        for key in required:
            assert key in tmpl, f"Template missing placeholder: {key}"

    def test_template_has_section_headers(self):
        tmpl = Path("agents/templates/market_brief.txt").read_text(encoding="utf-8")
        assert "🌍 THỊ TRƯỜNG THẾ GIỚI QUA ĐÊM" in tmpl
        assert "💛 VÀNG & DẦU" in tmpl
        assert "₿ Bitcoin" in tmpl
        assert "💵 TỶ GIÁ" in tmpl
        assert "📌 TIN ĐÁNG CHÚ Ý HÔM NAY" in tmpl
        assert "🎯 NHẬN ĐỊNH PHIÊN HÔM NAY" in tmpl
        assert "#VNIndex" in tmpl
