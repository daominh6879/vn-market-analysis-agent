"""
tests/test_fetch_live.py — Live API connectivity tests.

Hit real external APIs and verify data shape. No DB writes.

Run all:   pytest tests/test_fetch_live.py -v -m live
Run one:   pytest tests/test_fetch_live.py::test_financials_vci -v -s

WARNING: Live network. Rate-limited. Do NOT run in CI.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

pytestmark = pytest.mark.live

TICKER = "HPG"
TODAY = date.today().isoformat()
WEEK_AGO = (date.today() - timedelta(days=7)).isoformat()


# ── Financial facts ────────────────────────────────────────────────────────────

def test_financials_vci():
    """fetch_finance_facts VCI → non-empty list with required keys."""
    from ingest.fetch_financials import fetch_finance_facts

    facts = fetch_finance_facts(
        ticker=TICKER,
        report_type="consolidated",
        period_from=2023,
        period_to=2024,
        source="VCI",
    )
    assert isinstance(facts, list), "Expected list"
    assert len(facts) > 0, "VCI returned 0 facts — API down or no data"

    required = {"ticker", "period", "metric_code", "value", "report_type"}
    for f in facts[:3]:
        missing = required - f.keys()
        assert not missing, f"Fact missing keys: {missing}"

    print(f"\n  VCI facts: {len(facts)}, sample metric_codes: "
          f"{list({f['metric_code'] for f in facts[:10]})}")


def test_financials_kbs():
    """fetch_finance_facts KBS via vnstock SDK → non-empty list."""
    from ingest.fetch_financials import fetch_finance_facts

    facts = fetch_finance_facts(
        ticker=TICKER,
        report_type="consolidated",
        period_from=2023,
        period_to=2024,
        source="KBS",
    )
    assert isinstance(facts, list), "Expected list"
    assert len(facts) > 0, "KBS via vnstock returned 0 facts"
    print(f"\n  KBS facts: {len(facts)}")


# ── Stock prices ───────────────────────────────────────────────────────────────

def test_prices_vnstock():
    """vnstock Quote.history → DataFrame with price rows."""
    try:
        from vnstock.api.quote import Quote  # type: ignore[import]
    except ImportError:
        pytest.skip("vnstock not installed")

    from_date = (date.today() - timedelta(days=14)).isoformat()
    to_date = TODAY

    df = None
    for source in ("VCI", "kbs"):
        try:
            q = Quote(symbol=TICKER, source=source)
            df = q.history(start=from_date, end=to_date, interval="1D")
            if df is not None and not df.empty:
                break
        except Exception:
            continue

    assert df is not None and not df.empty, f"vnstock returned no price data for {TICKER}"
    assert "close" in df.columns or "close_adj" in df.columns, f"Missing close column: {df.columns.tolist()}"
    print(f"\n  Price rows: {len(df)}, cols: {df.columns.tolist()}")


# ── FX rate ────────────────────────────────────────────────────────────────────

def test_fx_vcb_usdvnd():
    """fetch_vcb_usdvnd → dict with buy/sell keys."""
    from data.fx_scraper import fetch_vcb_usdvnd

    result = fetch_vcb_usdvnd()
    assert result is not None, "VCB FX scraper returned None — likely 403 or parse error"

    required = {"buy", "sell", "transfer"}
    missing = required - result.keys()
    assert not missing, f"FX result missing keys: {missing}"
    assert result["buy"] > 0 and result["sell"] > 0, f"Invalid rates: {result}"
    print(f"\n  USD/VND buy={result['buy']:,.0f}  sell={result['sell']:,.0f}")


# ── Gold ───────────────────────────────────────────────────────────────────────

def test_gold_sjc():
    """fetch_sjc_gold → dict with buy_vnd/sell_vnd or None (blocked)."""
    from data.gold_vn_scraper import fetch_sjc_gold

    result = fetch_sjc_gold()
    if result is None:
        pytest.xfail("SJC gold scraper blocked (403) — known issue as of 2026-08")

    required = {"buy_vnd", "sell_vnd", "source"}
    missing = required - result.keys()
    assert not missing, f"Gold result missing keys: {missing}"
    assert result["buy_vnd"] > 0, f"Invalid gold price: {result}"
    print(f"\n  Gold buy={result['buy_vnd']} sell={result['sell_vnd']} source={result['source']}")


# ── News RSS ───────────────────────────────────────────────────────────────────

def test_cafef_rss():
    """fetch_rss_news → non-empty list with title/url keys."""
    from data.cafef_rss import fetch_rss_news

    articles = fetch_rss_news(max_age_hours=48)
    assert isinstance(articles, list), "Expected list"
    assert len(articles) > 0, "RSS returned 0 articles — feed down?"

    for a in articles[:3]:
        assert "title" in a and a["title"], f"Article missing title: {a}"
        assert "url" in a and a["url"], f"Article missing url: {a}"

    print(f"\n  RSS articles: {len(articles)}, sample: {articles[0]['title'][:60]}")


def test_cafef_ticker_news():
    """fetch_ticker_news(HPG) → list (may be empty if CafeF blocks)."""
    from data.cafef_rss import fetch_ticker_news

    articles = fetch_ticker_news(ticker=TICKER, max_articles=5)
    assert isinstance(articles, list), "Expected list"
    if not articles:
        pytest.xfail("CafeF ticker news returned 0 — may be rate-limited or layout changed")

    for a in articles[:2]:
        assert "title" in a, f"Missing title: {a}"
    print(f"\n  Ticker news for {TICKER}: {len(articles)} articles")


# ── Foreign flows ──────────────────────────────────────────────────────────────

def test_foreign_flows_vci():
    """fetch_foreign_via_vci → list of row dicts with ticker/net_value."""
    from ingest.fetch_foreign_flows import fetch_foreign_via_vci

    rows = fetch_foreign_via_vci(target_date=date.today(), market="HOSE")
    assert isinstance(rows, list), "Expected list"
    assert len(rows) > 0, "Foreign flows returned 0 rows — API down?"

    for r in rows[:3]:
        assert "ticker" in r, f"Row missing ticker: {r}"

    print(f"\n  Foreign flow rows: {len(rows)}, sample tickers: "
          f"{[r['ticker'] for r in rows[:5]]}")


# ── Corporate events ───────────────────────────────────────────────────────────

def test_corporate_events():
    """scrape_cafef_events → list (may be empty outside event window)."""
    from data.corporate_events_scraper import scrape_cafef_events

    start = date.today()
    end = date.today() + timedelta(days=14)

    events = scrape_cafef_events(start_date=start, end_date=end)
    assert isinstance(events, list), "Expected list"
    if not events:
        pytest.xfail("No corporate events in next 14 days — OK if outside event season")

    for e in events[:3]:
        assert "ticker" in e or "event_type" in e, f"Event missing expected keys: {e}"

    print(f"\n  Corporate events (next 14d): {len(events)}")
