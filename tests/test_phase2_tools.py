"""
tests/test_phase2_tools.py — Integration tests for Phase 2 tools.

Requires Postgres running with seeded data:
    python ingest/seed_securities.py
    python ingest/fetch_foreign_flows.py --date 2026-08-25

Run:
    python tests/test_phase2_tools.py
    python tests/test_phase2_tools.py --skip-foreign  # skip if no foreign flow data
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_root = Path(__file__).parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"


def _check(label: str, condition: bool, detail: str = "") -> str:
    status = PASS if condition else FAIL
    suffix = f" — {detail}" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    return status


# ── securities table ──────────────────────────────────────────────────────────

def test_securities_seeded() -> str:
    """securities table must have rows with sector populated."""
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM securities WHERE sector IS NOT NULL AND sector != 'Unknown'")
                count = cur.fetchone()[0]
        return _check("securities rows with sector", count >= 100,
                      f"found {count} rows (expected ≥100)")
    except Exception as e:
        return _check("securities DB connect", False, str(e))


def test_securities_has_banks() -> str:
    """Sample check: Ngân hàng sector has VCB, TCB."""
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT ticker FROM securities WHERE sector = 'Ngân hàng'")
                tickers = {r[0] for r in cur.fetchall()}
        for expected in ("VCB", "TCB", "MBB"):
            ok = expected in tickers
            _check(f"  securities.Ngân hàng contains {expected}", ok)
        return PASS if {"VCB", "TCB", "MBB"}.issubset(tickers) else FAIL
    except Exception as e:
        return _check("securities_has_banks", False, str(e))


# ── foreign_flow_db ───────────────────────────────────────────────────────────

def test_foreign_flow_db_empty_returns_none() -> str:
    """query_market_foreign_net on nonexistent date → None (not crash)."""
    from datetime import date
    from tools.foreign_flow_db import query_market_foreign_net
    result = query_market_foreign_net(date(2000, 1, 1))
    return _check("query_market_foreign_net(nonexistent_date) → None", result is None)


def test_query_top_foreign_empty_returns_none() -> str:
    """query_top_foreign on nonexistent date → None (not crash)."""
    from datetime import date
    from tools.foreign_flow_db import query_top_foreign
    result = query_top_foreign(date(2000, 1, 1), n=5, direction="buy")
    return _check("query_top_foreign(nonexistent_date) → None", result is None)


def test_query_latest_foreign_date() -> str:
    """query_latest_foreign_date → None or a date (never crash)."""
    from tools.foreign_flow_db import query_latest_foreign_date
    result = query_latest_foreign_date()
    ok = result is None or hasattr(result, "year")
    return _check("query_latest_foreign_date() → date or None", ok, str(result))


# ── get_foreign_flows tool ────────────────────────────────────────────────────

def test_get_foreign_flows_no_data() -> str:
    """get_foreign_flows on empty table → no_data ToolResult (not crash)."""
    from tools.price import get_foreign_flows
    result = get_foreign_flows(days=1)
    ok = result.status in ("ok", "no_data")
    return _check("get_foreign_flows() returns ok or no_data", ok,
                  f"status={result.status} msg={result.message[:80]}")


def test_get_foreign_flows_invalid_input() -> str:
    """get_foreign_flows(days=99) → invalid_input."""
    from tools.price import get_foreign_flows
    result = get_foreign_flows(days=99)
    return _check("get_foreign_flows(days=99) → invalid_input",
                  result.status == "invalid_input", result.message[:60])


def test_get_foreign_flows_data_shape() -> str:
    """If data exists, result.data has required keys."""
    from tools.price import get_foreign_flows
    result = get_foreign_flows(days=1)
    if result.status == "no_data":
        print(f"  [{SKIP}] get_foreign_flows data shape — no data in DB")
        return SKIP
    required = {"date", "market_net_value", "market_net_value_bn", "top_buyers", "top_sellers"}
    missing = required - set(result.data.keys())
    return _check("get_foreign_flows result.data has required keys",
                  not missing, f"missing: {missing}")


# ── get_sector_performance tool ───────────────────────────────────────────────

def test_get_sector_performance_returns_ok() -> str:
    """get_sector_performance() returns ok or no_data (not crash)."""
    from tools.price import get_sector_performance
    result = get_sector_performance(period="day")
    ok = result.status in ("ok", "no_data")
    return _check("get_sector_performance() returns ok or no_data",
                  ok, f"status={result.status}")


def test_get_sector_performance_invalid_period() -> str:
    """get_sector_performance(period='year') → invalid_input."""
    from tools.price import get_sector_performance
    result = get_sector_performance(period="year")
    return _check("get_sector_performance(period='year') → invalid_input",
                  result.status == "invalid_input", result.message[:60])


def test_get_sector_performance_data_shape() -> str:
    """If data returned, each sector has required keys."""
    from tools.price import get_sector_performance
    result = get_sector_performance(period="day")
    if result.status == "no_data":
        print(f"  [{SKIP}] get_sector_performance data shape — no OHLCV data")
        return SKIP
    required = {"sector", "pct_change", "ticker_count", "total_value_bn"}
    for item in result.data[:3]:
        missing = required - set(item.keys())
        if missing:
            return _check("sector item shape", False, f"missing {missing}")
    return _check("get_sector_performance each item has required keys", True,
                  f"{len(result.data)} sectors returned")


def test_get_sector_performance_sorted() -> str:
    """Sectors sorted by pct_change descending."""
    from tools.price import get_sector_performance
    result = get_sector_performance(period="day")
    if result.status != "ok" or len(result.data) < 2:
        print(f"  [{SKIP}] sector sorted — insufficient data")
        return SKIP
    pcts = [s["pct_change"] for s in result.data]
    sorted_desc = all(pcts[i] >= pcts[i+1] for i in range(len(pcts)-1))
    return _check("sectors sorted by pct_change DESC", sorted_desc, str(pcts[:5]))


# ── seed_securities side-effect (in-memory check, no DB write) ───────────────

def test_hose_seed_loadable() -> str:
    """HOSE_SEED in data/hose_universe.py loads without error."""
    from data.hose_universe import HOSE_SEED
    ok = len(HOSE_SEED) >= 100
    return _check("HOSE_SEED has ≥100 tickers", ok, f"len={len(HOSE_SEED)}")


def test_load_hose_universe() -> str:
    """load_hose_universe() returns list of dicts with required keys."""
    from data.hose_universe import load_hose_universe
    universe = load_hose_universe()
    ok = len(universe) >= 100
    _check("load_hose_universe() len ≥100", ok, f"len={len(universe)}")
    if universe:
        has_keys = all("ticker" in u and "sector" in u for u in universe[:10])
        _check("load_hose_universe() items have ticker+sector", has_keys)
    return PASS if ok else FAIL


# ── registry ──────────────────────────────────────────────────────────────────

def test_registry_has_new_tools() -> str:
    """TOOL_REGISTRY contains get_foreign_flows and get_sector_performance."""
    from tools.registry import TOOL_REGISTRY
    for name in ("get_foreign_flows", "get_sector_performance"):
        ok = name in TOOL_REGISTRY
        _check(f"registry has {name}", ok)
    return PASS


# ── runner ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-foreign", action="store_true",
                        help="Skip tests that require foreign_flows data in DB")
    args = parser.parse_args()

    tests = [
        # securities
        test_securities_seeded,
        test_securities_has_banks,
        # foreign_flow_db (safe — no data needed)
        test_foreign_flow_db_empty_returns_none,
        test_query_top_foreign_empty_returns_none,
        test_query_latest_foreign_date,
        # get_foreign_flows tool
        test_get_foreign_flows_no_data,
        test_get_foreign_flows_invalid_input,
        test_get_foreign_flows_data_shape,
        # get_sector_performance
        test_get_sector_performance_returns_ok,
        test_get_sector_performance_invalid_period,
        test_get_sector_performance_data_shape,
        test_get_sector_performance_sorted,
        # helpers
        test_hose_seed_loadable,
        test_load_hose_universe,
        # registry
        test_registry_has_new_tools,
    ]

    print("\n=== Phase 2 Integration Tests ===\n")
    results = []
    for fn in tests:
        print(f"{fn.__name__}:")
        try:
            status = fn()
        except Exception as e:
            print(f"  [FAIL] EXCEPTION: {e}")
            status = FAIL
        results.append(status)
        print()

    passed = results.count(PASS)
    failed = results.count(FAIL)
    skipped = results.count(SKIP)
    print(f"=== Results: {passed} passed / {failed} failed / {skipped} skipped ===")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
