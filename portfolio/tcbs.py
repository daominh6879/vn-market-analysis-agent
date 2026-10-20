"""
portfolio/tcbs.py — read-only sync of a TCBS (iFlash Open API v1.0.0) cash account into the portfolio ledger.

Docs: https://developers.tcbs.com.vn/docs/v1.0.0/introduction/. Login needs the API key plus a Smart OTP typed by the
user, so the sync is user-triggered (the OTP cannot be scheduled). TCBS reports holdings (symbol, quantity, avgPrice)
and cash, with no trade history, so the ledger is mirrored: the user's previous `tcbs_sync` rows are replaced by an
opening deposit + one buy per holding (fee 0, price = avgPrice) dated a few sessions back so the shares are sellable.
No order endpoints are used. The token and API key are never logged or stored.
"""
from __future__ import annotations

import os
from datetime import date

import httpx

from portfolio import ledger

BASE_URL = "https://openapi.tcbs.com.vn"
SYNC_NOTE = "tcbs_sync"
OPENING_SESSIONS_BACK = 5        # opening buys are dated this many sessions back (T+2 safe)
TIMEOUT = 20.0


class TcbsError(RuntimeError):
    """TCBS call failed or returned something unusable."""


def _config() -> tuple[str, str]:
    key, account = os.getenv("TCBS_API_KEY", ""), os.getenv("TCBS_ACCOUNT_NO", "")
    if not key or not account:
        raise TcbsError("TCBS_API_KEY and TCBS_ACCOUNT_NO must be set in .env")
    return key, account


def get_token(api_key: str, otp: str) -> str:
    r = httpx.post(f"{BASE_URL}/gaia/v1/oauth2/openapi/token", json={"apiKey": api_key, "otp": otp}, timeout=TIMEOUT)
    if r.status_code != 200:
        raise TcbsError(f"token exchange failed: HTTP {r.status_code}")
    token = (r.json() or {}).get("token")
    if not token:
        raise TcbsError("token exchange returned no token")
    return token


def _get(token: str, path: str) -> dict:
    r = httpx.get(f"{BASE_URL}{path}", headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                  timeout=TIMEOUT)
    if r.status_code != 200:
        raise TcbsError(f"GET {path.rsplit('/', 1)[-1]} failed: HTTP {r.status_code}")
    return r.json()


def parse_snapshot(assets: dict, cash: dict, account_no: str) -> tuple[dict[str, tuple[int, float]], float]:
    """-> ({symbol: (quantity, avg_price)}, cash_balance). Duplicate symbols are merged by weighted average."""
    holdings: dict[str, tuple[int, float]] = {}
    for a in assets.get("assets") or []:
        sym, qty, avg = str(a.get("symbol", "")).upper(), int(a.get("quantity") or 0), float(a.get("avgPrice") or 0)
        if not sym or qty <= 0:
            continue
        if avg <= 0:
            raise TcbsError(f"{sym}: missing average price")
        old_q, old_p = holdings.get(sym, (0, 0.0))
        holdings[sym] = (old_q + qty, (old_q * old_p + qty * avg) / (old_q + qty))
    rows = cash.get("data") or []
    row = next((r for r in rows if r.get("accountNo") == account_no), rows[0] if rows else None)
    if row is None or row.get("cashBalance") is None:
        raise TcbsError("cash balance missing in TCBS response")
    return holdings, float(row["cashBalance"])


def build_txns(holdings: dict[str, tuple[int, float]], cash: float, opening: date) -> list[ledger.Txn]:
    """Opening deposit sized so that cash after the opening buys equals the TCBS cash balance."""
    cost = sum(q * p for q, p in holdings.values())
    deposit = cash + cost
    if deposit <= 0:
        raise TcbsError("nothing to sync: empty account")
    txns = [ledger.Txn(txn_date=opening, kind="deposit", amount=deposit, note=SYNC_NOTE)]
    for sym, (q, p) in sorted(holdings.items()):
        txns.append(ledger.Txn(txn_date=opening, kind="buy", ticker=sym, quantity=q, price=p,
                               amount=-(q * p), note=SYNC_NOTE))
    return txns


def apply_snapshot(user_id: str, holdings: dict[str, tuple[int, float]], cash: float) -> dict:
    """Replace the user's tcbs_sync rows with the snapshot. Refuses if the user also has manual transactions."""
    from core.db import get_conn

    with get_conn() as conn:
        with conn.cursor() as cur:
            existing = ledger._load_txns(cur, user_id)
            if any(t.note != SYNC_NOTE for t in existing):
                raise ledger.LedgerError(f"{user_id} has manual transactions; use a dedicated user_id for TCBS sync")
            cur.execute("SELECT ticker FROM securities WHERE ticker = ANY(%s)", (list(holdings),))
            known = {r[0] for r in cur.fetchall()}
            skipped = sorted(set(holdings) - known)                 # funds, bonds, delisted: not in securities
            holdings = {s: v for s, v in holdings.items() if s in known}
            calendar = ledger._calendar(cur)
            opening = calendar[-1 - OPENING_SESSIONS_BACK] if len(calendar) > OPENING_SESSIONS_BACK else date.today()
            txns = build_txns(holdings, cash, opening)
            cash_after, _ = ledger.replay(txns, calendar)            # validates; odd lots allowed (no exchange check)
            cur.execute("DELETE FROM portfolio_transactions WHERE user_id = %s AND note = %s", (user_id, SYNC_NOTE))
            for t in txns:
                cur.execute(
                    "INSERT INTO portfolio_transactions (user_id, txn_date, kind, ticker, quantity, price, fee, tax, amount, note) "
                    "VALUES (%s,%s,%s,%s,%s,%s,0,0,%s,%s)",
                    (user_id, t.txn_date, t.kind, t.ticker, t.quantity, t.price, t.amount, SYNC_NOTE))
    return {"holdings": len(holdings), "skipped_symbols": skipped, "cash": cash_after, "opening_date": opening.isoformat()}


def sync(user_id: str, otp: str) -> dict:
    key, account = _config()
    token = get_token(key, otp)
    assets = _get(token, f"/aion/v1/accounts/{account}/se")
    cash = _get(token, f"/aion/v1/accounts/{account}/cashInvestments")
    holdings, cash_balance = parse_snapshot(assets, cash, account)
    return apply_snapshot(user_id, holdings, cash_balance)
