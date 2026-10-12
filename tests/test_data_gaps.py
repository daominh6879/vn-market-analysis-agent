"""
tests/test_data_gaps.py — the 8 "agent could not answer" gaps from vn_stock_agent_test_report.md:
market cap, quarterly facts, dividends + yield, per-year ROE/EPS, day-over-day gold/FX, plus
the sector / block-trade / date-label fixes. Pure unit tests: DB access is faked, no network.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date

import pandas as pd
import pytest


class _FakeCursor:
    def __init__(self, rows, log):
        self._rows, self._log = rows, log

    def execute(self, sql, params=None):
        self._log.append((sql, params))

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_conn(rows, log=None):
    log = log if log is not None else []

    class _Conn:
        def cursor(self):
            return _FakeCursor(rows, log)

    @contextmanager
    def get_conn():
        yield _Conn()

    return get_conn


# ── dividends ────────────────────────────────────────────────────────────────

class TestParseDividendText:
    def test_cash_percent_of_par(self):
        from ingest.fetch_dividends import parse_dividend_text
        r = parse_dividend_text("Trả cổ tức năm 2024 bằng tiền mặt, tỷ lệ 10%")
        assert r == {"cash_per_share": 1000.0, "stock_ratio_pct": None}

    def test_cash_vnd_per_share(self):
        from ingest.fetch_dividends import parse_dividend_text
        assert parse_dividend_text("Cổ tức bằng tiền 1.500 đồng/cổ phiếu")["cash_per_share"] == 1500.0

    def test_stock_dividend_percent(self):
        from ingest.fetch_dividends import parse_dividend_text
        r = parse_dividend_text("Trả cổ tức bằng cổ phiếu, tỷ lệ 20%")
        assert r["stock_ratio_pct"] == 20.0 and r["cash_per_share"] is None

    def test_stock_ratio_colon(self):
        from ingest.fetch_dividends import parse_dividend_text
        assert parse_dividend_text("Phát hành cổ phiếu thưởng tỷ lệ 100:15")["stock_ratio_pct"] == 15.0

    def test_cash_and_stock_in_one_event(self):
        from ingest.fetch_dividends import parse_dividend_text
        r = parse_dividend_text("Cổ tức bằng tiền 5% và bằng cổ phiếu 10%")
        assert r["cash_per_share"] == 500.0 and r["stock_ratio_pct"] == 10.0

    def test_no_figure(self):
        from ingest.fetch_dividends import parse_dividend_text
        assert parse_dividend_text("Họp đại hội cổ đông thường niên") == {
            "cash_per_share": None, "stock_ratio_pct": None}


def test_rows_from_events_splits_cash_and_stock_and_counts_skips():
    from ingest.fetch_dividends import rows_from_events
    df = pd.DataFrame([
        {"event_list_code": "DIV", "event_title": "Cổ tức bằng tiền 10%", "exright_date": "2025-03-12",
         "record_date": "2025-03-13", "issue_date": "2025-04-01"},
        {"event_list_code": "DIV", "event_title": "Cổ tức bằng cổ phiếu 20%", "exright_date": "2025-06-01"},
        {"event_list_code": "DIV", "event_title": "Cổ tức (chưa rõ)", "exright_date": "2025-07-01"},
        {"event_list_code": "AGME", "event_title": "Đại hội cổ đông", "exright_date": "2025-04-20"},
    ])
    rows, skipped = rows_from_events("VNM", df)
    assert [(r["kind"], r["ex_date"]) for r in rows] == [("cash", date(2025, 3, 12)), ("stock", date(2025, 6, 1))]
    assert rows[0]["cash_per_share"] == 1000.0 and rows[0]["payment_date"] == date(2025, 4, 1)
    assert skipped == 1


# ── quarterly statements ─────────────────────────────────────────────────────

def test_quarterly_columns_become_q_slash_year_and_annual_ignores_them():
    from ingest.fetch_financials import _facts_from_kbs
    df = pd.DataFrame({"item_id": ["net_sales"], "item": ["x"], "2025-Q1": [5.0], "2024": [3.0], "2024-Q4": [2.0]})
    q = _facts_from_kbs(df, "FPT", "consolidated", "income_statement", 2020, 2026, quarterly=True)
    a = _facts_from_kbs(df, "FPT", "consolidated", "income_statement", 2020, 2026, quarterly=False)
    assert sorted(f["period"] for f in q) == ["Q1/2025", "Q4/2024"]
    assert [f["period"] for f in a] == ["2024"]


def test_quarterly_block_shows_latest_quarters_with_yoy(monkeypatch):
    from agents.intents import rag_extras
    rows = [
        ("Q2/2026", "doanh_thu_thuan", 12e12), ("Q2/2026", "loi_nhuan_sau_thue", 3e12),
        ("Q2/2025", "doanh_thu_thuan", 10e12), ("Q2/2025", "loi_nhuan_sau_thue", 2e12),
    ]
    monkeypatch.setattr(rag_extras, "_rows", lambda sql, params=(): rows)
    out = rag_extras._quarterly("FPT")
    assert "quý gần nhất có dữ liệu: Q2/2026" in out
    assert "doanh thu thuần 12,000 tỷ (+20.0% so với cùng kỳ Q2/2025)" in out
    assert "LNST 3,000 tỷ (+50.0%" in out


def test_quarterly_block_without_data_refuses_to_guess(monkeypatch):
    from agents.intents import rag_extras
    monkeypatch.setattr(rag_extras, "_rows", lambda sql, params=(): [])
    assert "chưa có báo cáo theo quý" in rag_extras._quarterly("FPT")


# ── per-year ROE / EPS ───────────────────────────────────────────────────────

def test_ratio_history_facts_one_row_per_year_and_ratio():
    from ingest.fetch_ratios import ratio_history_facts
    df = pd.DataFrame({
        "item_id": ["roe", "trailing_eps", "pe_ratio"], "item": ["ROE", "EPS", "PE"],
        "2024-Năm": [12.5, 1900.0, 11.0], "2020": [20.1, 2500.0, float("nan")], "TTM": [1, 2, 3],
    })
    facts = ratio_history_facts("HPG", df)
    got = {(f["period"], f["metric_code"]): f["value"] for f in facts}
    assert got[("2020", "ratio_roe_pct")] == 20.1 and got[("2024", "ratio_eps")] == 1900.0
    assert ("2020", "ratio_pe") not in got                      # NaN skipped
    assert all(f["source"] == "vnstock_ratio" for f in facts)
    assert {f["period"] for f in facts} == {"2020", "2024"}     # non-year columns ignored


def test_yearly_prefers_published_ratios_for_requested_years(monkeypatch):
    from agents.intents import rag_extras
    monkeypatch.setattr(rag_extras, "_rows", lambda sql, params=(): [
        ("2020", "ratio_roe_pct", 20.1), ("2020", "ratio_eps", 2500.0),
        ("2024", "ratio_roe_pct", 12.5), ("2023", "ratio_eps", 1800.0),
    ])
    out = rag_extras._yearly("HPG", ["2020", "2024"])
    assert "2020: ROE 20.1%; EPS 2,500 đ" in out and "2024: ROE 12.5%" in out
    assert "2023" not in out


# ── dividends block + yield ──────────────────────────────────────────────────

def test_dividend_block_reports_ttm_yield(monkeypatch):
    from agents.intents import rag_extras

    def fake_rows(sql, params=()):
        if "SUM(cash_per_share)" in sql:
            return [(2000, "2026-03-12")]
        return [(date(2026, 3, 12), "cash", 2000, None, date(2026, 4, 1)),
                (date(2026, 3, 12), "stock", None, 20, None)]

    monkeypatch.setattr(rag_extras, "_rows", fake_rows)
    monkeypatch.setattr(rag_extras, "_latest_close_vnd", lambda t: (50_000.0, date(2026, 10, 9)))
    out = rag_extras._dividends_from_table("VNM")
    assert "tiền mặt 2,000 đồng/cp" in out and "cổ phiếu tỷ lệ 20%" in out
    assert "= 4.00%" in out


def test_dividends_table_empty_returns_none(monkeypatch):
    from agents.intents import rag_extras
    monkeypatch.setattr(rag_extras, "_rows", lambda sql, params=(): [])
    assert rag_extras._dividends_from_table("VNM") is None


# ── market cap ───────────────────────────────────────────────────────────────

def test_single_ticker_market_cap_shows_inputs(monkeypatch):
    from agents.intents import rag_extras
    monkeypatch.setattr(rag_extras, "_rows", lambda sql, params=(): [(2_090_000_000, "VNM")])
    monkeypatch.setattr(rag_extras, "_latest_close_vnd", lambda t: (57_700.0, date(2026, 10, 9)))
    out = rag_extras._market_cap("VNM")
    assert "120,593 tỷ VND" in out and "2,090,000,000" in out


def test_single_ticker_market_cap_without_shares_does_not_estimate(monkeypatch):
    from agents.intents import rag_extras
    monkeypatch.setattr(rag_extras, "_rows", lambda sql, params=(): [(0, "VNM")])
    monkeypatch.setattr(rag_extras, "_latest_close_vnd", lambda t: (57_700.0, date(2026, 10, 9)))
    assert "không được ước lượng" in rag_extras._market_cap("VNM")


def test_gather_extras_adds_market_cap_block(monkeypatch):
    from agents.intents import rag_extras
    monkeypatch.setattr(rag_extras, "_market_cap", lambda t: f"[VỐN HÓA — {t}]")
    assert "[VỐN HÓA — VNM]" in rag_extras.gather_extras("VNM", "Vốn hóa của VNM là bao nhiêu?")


def test_marketcap_routing():
    from agents.intent_rules import override_intent
    assert override_intent("Vốn hóa của VNM là bao nhiêu?", "valuation", ["VNM"])[0] == "rag_qa"
    assert override_intent("Top 5 cổ phiếu vốn hóa lớn nhất sàn HOSE", "screening", [])[0] == "market_data"


def test_screen_market_cap_filters_by_threshold(monkeypatch):
    import core.db
    from agents.intents import screening
    monkeypatch.setattr(core.db, "get_conn", _fake_conn([("VNM", 120000.0), ("XYZ", 500.0)]))
    hits = screening._screen_market_cap(["VNM", "XYZ"], [{"indicator": "market_cap", "op": ">", "threshold": 10000}])
    assert hits == [("VNM", {"market_cap": 120000.0})]


def test_screening_gather_uses_market_cap_filter(monkeypatch):
    import core.tickers
    from agents.intents import screening
    monkeypatch.setattr(core.tickers, "get_tickers", lambda: ["VNM", "XYZ"])
    monkeypatch.setattr(screening, "_screen_market_cap", lambda t, f: [("VNM", {"market_cap": 120000.0})])
    out = screening.gather_data(None, "cổ phiếu vốn hóa trên 10000 tỷ",
                                screening=[{"indicator": "market_cap", "op": ">", "threshold": 10000}])
    assert "VNM: vốn hóa = 120,000 tỷ VND" in out and "bỏ qua chỉ tiêu" not in out


def test_fetch_shares_charter_fallback_divides_by_par(monkeypatch):
    import ingest.fetch_shares as fs
    log: list = []
    monkeypatch.setattr(fs, "get_conn", _fake_conn([], log))
    monkeypatch.setattr(_FakeCursor, "rowcount", 3, raising=False)
    assert fs.fill_from_charter_capital() == 3
    assert log[0][1] == (fs.PAR_VALUE_VND,) and "von_dieu_le" in log[0][0]


# ── daily snapshots (gold / FX day-over-day) ─────────────────────────────────

@pytest.mark.real_snapshots
def test_change_note_reports_delta_vs_previous_snapshot(monkeypatch):
    import core.db
    from data import snapshots
    monkeypatch.setattr(core.db, "get_conn", _fake_conn([(date(2026, 10, 9), 150.0, 153.0, None)]))
    note = snapshots.change_note("sjc_gold", buy=150.5, sell=153.0, unit="triệu đồng/lượng", decimals=2)
    assert "2026-10-09" in note and "mua +0.50 (+0.33%)" in note and "bán +0.00 (+0.00%)" in note


@pytest.mark.real_snapshots
def test_change_note_without_history_says_so(monkeypatch):
    import core.db
    from data import snapshots
    monkeypatch.setattr(core.db, "get_conn", _fake_conn([]))
    assert "Chưa có dữ liệu lịch sử" in snapshots.change_note("sjc_gold", buy=1.0, sell=2.0)


@pytest.mark.real_snapshots
def test_snapshot_failures_are_non_fatal(monkeypatch):
    import core.db
    from data import snapshots

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(core.db, "get_conn", boom)
    assert snapshots.save_snapshot("sjc_gold", buy=1.0, sell=2.0) is False
    assert snapshots.previous_snapshot("sjc_gold") is None


def test_gold_message_includes_day_over_day(monkeypatch):
    import data.snapshots as snap
    import data.gold_vn_scraper as gold
    monkeypatch.setattr(gold, "fetch_sjc_gold", lambda: {"buy_vnd": 150.5, "sell_vnd": 153.0, "source": "sjc",
                                                         "timestamp": "2026-10-10T07:00:00"})
    monkeypatch.setattr(snap, "change_note", lambda *a, **k: "So với snapshot 2026-10-09: mua +0.50")
    from tools.global_market import get_vn_gold
    assert "So với snapshot 2026-10-09" in get_vn_gold().message


def test_fx_message_flags_missing_central_rate(monkeypatch):
    import data.fx_scraper as fx
    import data.snapshots as snap
    monkeypatch.setattr(fx, "fetch_vcb_usdvnd", lambda: {"buy": 25920.0, "sell": 26330.0, "transfer": 26125.0,
                                                         "timestamp": "t", "source": "vietcombank"})
    monkeypatch.setattr(fx, "fetch_sbv_central_rate", lambda: None)
    monkeypatch.setattr(snap, "change_note", lambda *a, **k: "")
    from tools.global_market import get_fx_rates
    assert "Tỷ giá trung tâm NHNN: chưa lấy được" in get_fx_rates().message


def test_central_rate_mirror_rejects_stale_page(monkeypatch):
    import httpx
    import data.fx_scraper as fx

    class _R:
        text = "<div>Tỷ giá trung tâm 24/04/2025 1 Đô la Mỹ = 24,928</div>"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _R())
    assert fx._fetch_central_rate_mirror() is None


# ── sector table, block trades, date label ───────────────────────────────────

def test_sector_overrides_fix_misclassified_tickers():
    from data.hose_universe import load_hose_universe
    by = {u["ticker"]: u for u in load_hose_universe()}
    assert by["VRE"]["sector"] == "Bất động sản"
    assert "ngân hàng" not in by["EVF"]["tags"] and by["EVF"]["industry"] != "Ngân hàng"
    assert by["PNJ"]["sector"] != "Bán lẻ" and "bán lẻ" not in by["PNJ"]["tags"]
    assert by["HDG"]["sector"] == "Tiện ích"


def test_foreign_ranking_excludes_block_trades_and_lists_them_separately(monkeypatch):
    import tools.foreign_flow_db as ff
    from agents.intents import market_data

    def row(t, net, block):
        return {"ticker": t, "net_value": net, "buy_value": max(net, 0), "sell_value": max(-net, 0),
                "block_trade": block}

    monkeypatch.setattr(ff, "query_latest_foreign_date", lambda: date(2026, 10, 9))
    monkeypatch.setattr(ff, "query_market_foreign_net", lambda d: None)
    monkeypatch.setattr(ff, "query_top_foreign", lambda d, n, direction: [
        row("MCH", -9593.0, True), row("VHM", -300.0, False), row("HPG", -200.0, False)])
    out = market_data._foreign("khối ngoại bán ròng nhiều nhất")
    ranked, _, blocks = out.partition("Giao dịch thỏa thuận")
    assert "MCH" not in ranked and "1. VHM" in ranked and "2. HPG" in ranked
    assert "MCH ròng -9,593.0 tỷ" in blocks


def test_session_label_is_consistent_and_names_weekday():
    from agents.intents.market_data import _session_label
    assert _session_label(date(2026, 10, 9)) == "phiên giao dịch gần nhất (Thứ Sáu 09/10/2026)"
    assert _session_label("2026-10-09") == _session_label(date(2026, 10, 9))


# ── Dagster wiring: every new dataset is a scheduled asset + bootstrap ───────

def test_dagster_defs_register_all_market_data_jobs_schedules_and_sensor():
    from pipeline.assets import defs
    repo = defs.get_repository_def()
    assert {"securities_shares_job", "stock_dividends_job", "ratio_history_job",
            "financials_quarterly_job", "daily_snapshots_job"} <= set(repo.job_names)
    scheduled_jobs = {s.job_name for s in repo.schedule_defs}
    assert {"securities_shares_job", "stock_dividends_job", "ratio_history_job",
            "financials_quarterly_job", "daily_snapshots_job"} <= scheduled_jobs
    assert "data_bootstrap_sensor" in {s.name for s in repo.sensor_defs}


def _run_sensor(empty_flags):
    import core.db
    from dagster import build_sensor_context
    from pipeline.assets_market_data import data_bootstrap_sensor

    class _Cur(_FakeCursor):
        def __init__(self):
            super().__init__([], [])
            self._i = -1

        def execute(self, sql, params=None):
            self._i += 1

        def fetchone(self):
            return (empty_flags[self._i],)

    class _Conn:
        def cursor(self):
            return _Cur()

    @contextmanager
    def get_conn():
        yield _Conn()

    orig = core.db.get_conn
    core.db.get_conn = get_conn
    try:
        return data_bootstrap_sensor(build_sensor_context())
    finally:
        core.db.get_conn = orig


def test_bootstrap_sensor_requests_only_empty_datasets():
    out = _run_sensor([True, False, True, False, False, True, False])
    assert sorted(r.job_name for r in out) == ["ratio_history_job", "securities_shares_job", "vnstock_ratios_job"]
    assert all(r.run_key.endswith(date.today().isoformat()) for r in out)   # one retry per day


def test_bootstrap_sensor_skips_when_everything_populated():
    from dagster import SkipReason
    assert isinstance(_run_sensor([False] * 7), SkipReason)


def test_data_jobs_cover_all_active_securities_not_index_labels():
    """Data jobs must not be scoped to the hand-maintained VN30/VN100 labels (only 63 of 431 securities)."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    files = [*root.glob("ingest/*.py"), *root.glob("pipeline/*.py"), *root.glob("scripts/*.py")]
    offenders = [str(f.relative_to(root)) for f in files
                 if f.name != "assets_market_data.py" and re.search(r"get_ratio_tickers\(\)", f.read_text(encoding="utf-8"))]
    assert not offenders, offenders
