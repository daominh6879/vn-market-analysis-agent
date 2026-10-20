"""Unit tests for portfolio/ledger.py pure logic (no DB)."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from portfolio.ledger import (
    LedgerError, Txn, build_positions, replay, sessions_elapsed, signed_amount,
)

# Mon 2025-01-06 .. Fri 2025-01-24, weekdays only
CAL = [date(2025, 1, 6) + timedelta(days=i) for i in range(19) if (date(2025, 1, 6) + timedelta(days=i)).weekday() < 5]
D1, D2, D3, D4 = date(2025, 1, 6), date(2025, 1, 7), date(2025, 1, 8), date(2025, 1, 9)


def deposit(d, amount):
    return Txn(txn_date=d, kind="deposit", amount=amount)


def buy(d, ticker, qty, price, fee=0.0):
    return Txn(txn_date=d, kind="buy", ticker=ticker, quantity=qty, price=price, fee=fee,
               amount=signed_amount("buy", qty, price, fee, 0.0, None))


def sell(d, ticker, qty, price, fee=0.0, tax=0.0):
    return Txn(txn_date=d, kind="sell", ticker=ticker, quantity=qty, price=price, fee=fee, tax=tax,
               amount=signed_amount("sell", qty, price, fee, tax, None))


def test_avg_cost_includes_fees():
    cash, h = replay([deposit(D1, 100_000_000), buy(D1, "HPG", 1000, 25_000, fee=37_500),
                      buy(D2, "HPG", 1000, 27_000, fee=40_500)], CAL)
    assert h["HPG"].qty == 2000
    assert h["HPG"].cost == pytest.approx(25_000_000 + 37_500 + 27_000_000 + 40_500)
    assert cash == pytest.approx(100_000_000 - 52_078_000)


def test_sell_realized_pnl_with_fee_and_tax():
    txns = [deposit(D1, 100_000_000), buy(D1, "HPG", 1000, 25_000, fee=37_500),
            sell(D4, "HPG", 1000, 30_000, fee=45_000, tax=30_000)]
    cash, h = replay(txns, CAL)
    assert h["HPG"].qty == 0
    assert h["HPG"].realized == pytest.approx(30_000_000 - 45_000 - 30_000 - 25_037_500)
    assert cash == pytest.approx(100_000_000 - 25_037_500 + 30_000_000 - 75_000)


def test_buy_rejected_when_cash_short():
    with pytest.raises(LedgerError, match="insufficient cash"):
        replay([deposit(D1, 1_000_000), buy(D1, "HPG", 1000, 25_000)], CAL)


def test_sell_blocked_by_t_plus_2():
    base = [deposit(D1, 100_000_000), buy(D1, "HPG", 1000, 25_000)]
    with pytest.raises(LedgerError, match="T\\+2"):
        replay(base + [sell(D1, "HPG", 1000, 26_000)], CAL)       # T+0
    with pytest.raises(LedgerError, match="T\\+2"):
        replay(base + [sell(D2, "HPG", 1000, 26_000)], CAL)       # T+1
    replay(base + [sell(D3, "HPG", 1000, 26_000)], CAL)           # T+2 ok


def test_sell_more_than_held_rejected():
    with pytest.raises(LedgerError, match="exceeds held"):
        replay([deposit(D1, 100_000_000), buy(D1, "HPG", 1000, 25_000), sell(D4, "HPG", 2000, 26_000)], CAL)


def test_weekend_does_not_count_as_session():
    fri, mon = date(2025, 1, 10), date(2025, 1, 13)
    assert sessions_elapsed(CAL, fri, mon) == 1


def test_sessions_elapsed_beyond_calendar_counts_weekdays():
    assert sessions_elapsed(CAL, date(2025, 1, 24), date(2025, 1, 29)) == 3   # Mon, Tue, Wed


def test_lot_size_enforced_on_hose_only():
    txns = [deposit(D1, 100_000_000), buy(D1, "HPG", 150, 25_000)]
    with pytest.raises(LedgerError, match="multiple of 100"):
        replay(txns, CAL, {"HPG": "HOSE"})
    replay(txns, CAL, {"HPG": "UPCOM"})


def test_positions_sellable_weight_and_unrealized():
    _, h = replay([deposit(D1, 100_000_000), buy(D1, "HPG", 1000, 25_000), buy(D3, "VCB", 500, 90_000)], CAL)
    rows = build_positions(h, {"HPG": 30_000, "VCB": 90_000}, {"HPG": "Steel"}, CAL, D3)
    by = {r["ticker"]: r for r in rows}
    assert by["HPG"]["sellable_quantity"] == 1000
    assert by["VCB"]["sellable_quantity"] == 0
    assert by["HPG"]["unrealized_pct"] == pytest.approx(20.0)
    assert by["HPG"]["weight"] == pytest.approx(30_000_000 / 75_000_000)
    assert by["VCB"]["sector"] == "Unknown"


def test_backdated_deposit_ordering_by_date():
    # buy listed first but deposit is earlier by date -> valid
    replay([buy(D2, "HPG", 1000, 25_000), deposit(D1, 100_000_000)], CAL)
