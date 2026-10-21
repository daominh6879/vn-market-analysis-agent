"""
portfolio/ledger.py — cash ledger + positions derived from transactions.

Pure logic (`replay`, `build_positions`) is separated from DB access so it can be unit-tested.
Prices in transactions are VND/share; ohlcv_daily stores nghìn đồng, so last prices are x1000.
"""
from __future__ import annotations

import os
from bisect import bisect_left
from collections import deque
from dataclasses import dataclass, field
from datetime import date, timedelta

LOT_SIZE = 100
SETTLEMENT_SESSIONS = 2          # VN T+2: shares bought < 2 sessions ago cannot be sold
SELL_TAX_PCT = 0.001             # 0.1% personal income tax on sell value
DEFAULT_FEE_PCT = 0.0015
_CASH_IN = {"deposit", "dividend"}
_LOT_EXCHANGES = {"HOSE", "HNX"}
_PRICE_UNIT = 1000               # ohlcv_daily nghìn đồng -> VND


class LedgerError(ValueError):
    """Transaction rejected (insufficient cash/shares, T+2, bad lot, unknown ticker...)."""


@dataclass
class Txn:
    txn_date: date
    kind: str
    ticker: str | None = None
    quantity: int | None = None
    price: float | None = None
    fee: float = 0.0
    tax: float = 0.0
    amount: float = 0.0
    id: int | None = None
    note: str = ""


@dataclass
class Holding:
    lots: deque = field(default_factory=deque)   # [buy_date, qty], FIFO
    cost: float = 0.0                            # cost basis of remaining shares (fees included)
    realized: float = 0.0

    @property
    def qty(self) -> int:
        return sum(q for _, q in self.lots)


def fee_pct() -> float:
    return float(os.getenv("PORTFOLIO_FEE_PCT", DEFAULT_FEE_PCT))


def signed_amount(kind: str, quantity: int | None, price: float | None,
                  fee: float, tax: float, amount: float | None) -> float:
    """Cash effect of a transaction (see migration 039)."""
    if kind == "buy":
        return -(quantity * price + fee)
    if kind == "sell":
        return quantity * price - fee - tax
    if amount is None or amount <= 0:
        raise LedgerError(f"{kind} requires a positive amount")
    return amount if kind in _CASH_IN else -amount


# ── trading calendar ──────────────────────────────────────────────────────────

def sessions_elapsed(calendar: list[date], start: date, end: date) -> int:
    """Trading sessions in (start, end]. Dates after the known calendar count as weekdays."""
    if end <= start:
        return 0
    known = bisect_left(calendar, end + timedelta(days=1)) - bisect_left(calendar, start + timedelta(days=1))
    last = calendar[-1] if calendar else date.min
    extra, d = 0, max(last, start)
    while d < end:
        d += timedelta(days=1)
        if d.weekday() < 5:
            extra += 1
    return known + extra


# ── pure replay ───────────────────────────────────────────────────────────────

def replay(txns: list[Txn], calendar: list[date],
           exchanges: dict[str, str] | None = None) -> tuple[float, dict[str, Holding]]:
    """Apply txns in (date, id) order; raise LedgerError on the first invalid step.

    Returns (cash, holdings). `exchanges` enables the 100-share lot check for HOSE/HNX.
    """
    cash = 0.0
    holdings: dict[str, Holding] = {}
    for t in sorted(txns, key=lambda t: (t.txn_date, t.id if t.id is not None else 1 << 60)):
        cash += t.amount
        if t.kind in ("buy", "sell"):
            if exchanges and exchanges.get(t.ticker) in _LOT_EXCHANGES and t.quantity % LOT_SIZE:
                raise LedgerError(f"{t.ticker}: quantity must be a multiple of {LOT_SIZE}")
            h = holdings.setdefault(t.ticker, Holding())
            if t.kind == "buy":
                h.lots.append([t.txn_date, t.quantity])
                h.cost += t.quantity * t.price + t.fee
            else:
                _sell(h, t, calendar)
        if cash < -0.5:
            raise LedgerError(f"insufficient cash on {t.txn_date}: balance would be {cash:,.0f}")
    return cash, holdings


def _sell(h: Holding, t: Txn, calendar: list[date]) -> None:
    if t.quantity > h.qty:
        raise LedgerError(f"{t.ticker}: sell {t.quantity} exceeds held {h.qty} on {t.txn_date}")
    if t.quantity > _sellable(h, t.txn_date, calendar):
        raise LedgerError(
            f"{t.ticker}: shares bought within the last {SETTLEMENT_SESSIONS} sessions are not sellable (T+2)")
    avg = h.cost / h.qty
    remaining = t.quantity
    while remaining:
        lot = h.lots[0]
        take = min(lot[1], remaining)
        lot[1] -= take
        remaining -= take
        if lot[1] == 0:
            h.lots.popleft()
    h.cost -= avg * t.quantity
    h.realized += t.quantity * t.price - t.fee - t.tax - avg * t.quantity
    if h.qty == 0:
        h.cost = 0.0


def _sellable(h: Holding, as_of: date, calendar: list[date]) -> int:
    return sum(q for d, q in h.lots if sessions_elapsed(calendar, d, as_of) >= SETTLEMENT_SESSIONS)


def build_positions(holdings: dict[str, Holding], prices: dict[str, float],
                    sectors: dict[str, str], calendar: list[date], as_of: date) -> list[dict]:
    """Open positions with P/L and weights over total market value."""
    rows = []
    for ticker, h in holdings.items():
        if h.qty == 0:
            continue
        last = prices.get(ticker)
        value = h.qty * last if last is not None else None
        rows.append({
            "ticker": ticker,
            "quantity": h.qty,
            "sellable_quantity": _sellable(h, as_of, calendar),
            "avg_cost": h.cost / h.qty,
            "last_price": last,
            "market_value": value,
            "unrealized_pnl": value - h.cost if value is not None else None,
            "unrealized_pct": (value / h.cost - 1) * 100 if value is not None else None,
            "realized_pnl": h.realized,
            "sector": sectors.get(ticker, "Unknown"),
        })
    total = sum(r["market_value"] or 0 for r in rows)
    for r in rows:
        r["weight"] = (r["market_value"] or 0) / total if total else 0.0
    return sorted(rows, key=lambda r: -(r["market_value"] or 0))


# ── DB access ─────────────────────────────────────────────────────────────────

def _load_txns(cur, user_id: str) -> list[Txn]:
    cur.execute(
        "SELECT id, txn_date, kind, ticker, quantity, price, fee, tax, amount, note "
        "FROM portfolio_transactions WHERE user_id = %s ORDER BY txn_date, id", (user_id,))
    return [Txn(id=r[0], txn_date=r[1], kind=r[2], ticker=r[3], quantity=r[4],
                price=float(r[5]) if r[5] is not None else None,
                fee=float(r[6]), tax=float(r[7]), amount=float(r[8]), note=r[9])
            for r in cur.fetchall()]


def _calendar(cur) -> list[date]:
    cur.execute("SELECT date FROM market_index_daily WHERE index_code = 'VNINDEX' ORDER BY date")
    return [r[0] for r in cur.fetchall()]


def _exchanges(cur, tickers: set[str]) -> dict[str, str]:
    if not tickers:
        return {}
    cur.execute("SELECT ticker, exchange FROM securities WHERE ticker = ANY(%s)", (list(tickers),))
    return dict(cur.fetchall())


def add_transaction(user_id: str, txn_date: date, kind: str, ticker: str | None = None,
                    quantity: int | None = None, price: float | None = None,
                    fee: float | None = None, tax: float | None = None,
                    amount: float | None = None, note: str = "", tenant_id: str = "default") -> int:
    """Validate against the full ledger (replay) and insert. Returns the new id."""
    from core.db import get_conn

    if kind in ("buy", "sell"):
        ticker = (ticker or "").upper()
        if not ticker or not quantity or quantity <= 0 or not price or price <= 0:
            raise LedgerError(f"{kind} requires ticker, positive quantity and price")
        value = quantity * price
        fee = value * fee_pct() if fee is None else fee
        tax = (value * SELL_TAX_PCT if kind == "sell" else 0.0) if tax is None else tax
    else:
        ticker = quantity = price = None
        fee = tax = 0.0
    amt = signed_amount(kind, quantity, price, fee, tax, amount)
    new = Txn(txn_date=txn_date, kind=kind, ticker=ticker, quantity=quantity, price=price,
              fee=fee, tax=tax, amount=amt, note=note)

    with get_conn() as conn:
        with conn.cursor() as cur:
            if ticker:
                cur.execute("SELECT 1 FROM securities WHERE ticker = %s", (ticker,))
                if not cur.fetchone():
                    raise LedgerError(f"unknown ticker {ticker}")
            ledger = _load_txns(cur, user_id) + [new]
            replay(ledger, _calendar(cur), _exchanges(cur, {t.ticker for t in ledger if t.ticker}))
            cur.execute(
                "INSERT INTO portfolio_transactions "
                "(user_id, tenant_id, txn_date, kind, ticker, quantity, price, fee, tax, amount, note) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (user_id, tenant_id, txn_date, kind, ticker, quantity, price, fee, tax, amt, note))
            return cur.fetchone()[0]


def list_transactions(user_id: str) -> list[dict]:
    from core.db import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            txns = _load_txns(cur, user_id)
    return [{**t.__dict__, "txn_date": t.txn_date.isoformat()} for t in txns]


def delete_transaction(user_id: str, txn_id: int) -> None:
    """Delete only if the remaining ledger stays valid (e.g. not a deposit that funds a buy)."""
    from core.db import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            ledger = _load_txns(cur, user_id)
            if not any(t.id == txn_id for t in ledger):
                raise LedgerError(f"transaction {txn_id} not found")
            rest = [t for t in ledger if t.id != txn_id]
            replay(rest, _calendar(cur), _exchanges(cur, {t.ticker for t in rest if t.ticker}))
            cur.execute("DELETE FROM portfolio_transactions WHERE id = %s AND user_id = %s", (txn_id, user_id))


def _state(user_id: str, as_of: date | None):
    from core.db import get_conn
    as_of = as_of or date.today()
    with get_conn() as conn:
        with conn.cursor() as cur:
            txns = [t for t in _load_txns(cur, user_id) if t.txn_date <= as_of]
            calendar = _calendar(cur)
            tickers = {t.ticker for t in txns if t.ticker}
            cash, holdings = replay(txns, calendar)
            prices: dict[str, float] = {}
            sectors: dict[str, str] = {}
            if tickers:
                cur.execute(
                    "SELECT DISTINCT ON (ticker) ticker, close FROM ohlcv_daily "
                    "WHERE ticker = ANY(%s) AND date <= %s ORDER BY ticker, date DESC",
                    (list(tickers), as_of))
                prices = {t: float(c) * _PRICE_UNIT for t, c in cur.fetchall()}
                cur.execute("SELECT ticker, sector FROM securities WHERE ticker = ANY(%s)", (list(tickers),))
                sectors = dict(cur.fetchall())
    return txns, cash, holdings, prices, sectors, calendar, as_of


def reference(tickers: list[str], as_of: date | None = None) -> dict:
    """Market reference data for a ledger kept elsewhere: last close (VND), exchange, sector, trading calendar."""
    from core.db import get_conn
    as_of = as_of or date.today()
    tickers = sorted({t.upper() for t in tickers})
    with get_conn() as conn:
        with conn.cursor() as cur:
            calendar = _calendar(cur)
            prices: dict[str, float] = {}
            meta: dict[str, tuple[str, str]] = {}
            if tickers:
                cur.execute(
                    "SELECT DISTINCT ON (ticker) ticker, close FROM ohlcv_daily "
                    "WHERE ticker = ANY(%s) AND date <= %s ORDER BY ticker, date DESC", (tickers, as_of))
                prices = {t: float(c) * _PRICE_UNIT for t, c in cur.fetchall()}
                cur.execute("SELECT ticker, exchange, sector FROM securities WHERE ticker = ANY(%s)", (tickers,))
                meta = {t: (e, s) for t, e, s in cur.fetchall()}
    return {
        "known": sorted(meta),
        "prices": prices,
        "exchanges": {t: e for t, (e, _) in meta.items()},
        "sectors": {t: s for t, (_, s) in meta.items()},
        "calendar": [d.isoformat() for d in calendar if d <= as_of],
        "as_of": as_of.isoformat(),
    }


def get_positions(user_id: str, as_of: date | None = None) -> list[dict]:
    _, _, holdings, prices, sectors, calendar, as_of = _state(user_id, as_of)
    return build_positions(holdings, prices, sectors, calendar, as_of)


def get_cash(user_id: str, as_of: date | None = None) -> dict:
    """Cash balance, deposits, NAV and total return."""
    txns, cash, holdings, prices, sectors, calendar, as_of = _state(user_id, as_of)
    positions = build_positions(holdings, prices, sectors, calendar, as_of)
    market_value = sum(p["market_value"] or 0 for p in positions)
    deposited = sum(t.amount for t in txns if t.kind == "deposit")
    withdrawn = -sum(t.amount for t in txns if t.kind == "withdraw")
    net_deposited = deposited - withdrawn
    nav = cash + market_value
    return {
        "cash": cash,
        "total_deposited": deposited,
        "total_withdrawn": withdrawn,
        "market_value": market_value,
        "nav": nav,
        "total_return_pct": (nav / net_deposited - 1) * 100 if net_deposited > 0 else None,
        "as_of": as_of.isoformat(),
    }
