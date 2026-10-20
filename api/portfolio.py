"""
api/portfolio.py — hidden portfolio API (not in OpenAPI docs, no auth, local use).

Mounted in api/main.py but deliberately not listed in its endpoint docstring.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from portfolio import advisor, ledger, tcbs

router = APIRouter(prefix="/portfolio", tags=["portfolio"], include_in_schema=False)

_KINDS = ("deposit", "withdraw", "buy", "sell", "dividend", "fee")


class TransactionIn(BaseModel):
    txn_date: date
    kind: str
    ticker: str | None = None
    quantity: int | None = None
    price: float | None = None       # VND per share
    fee: float | None = None         # default: PORTFOLIO_FEE_PCT of value
    tax: float | None = None         # default on sell: 0.1% of value
    amount: float | None = None      # deposit / withdraw / dividend / fee (positive)
    note: str = ""


@router.post("/{user_id}/transactions")
def add_transaction(user_id: str, body: TransactionIn):
    if body.kind not in _KINDS:
        raise HTTPException(400, f"invalid kind {body.kind}")
    try:
        txn_id = ledger.add_transaction(user_id, **body.model_dump())
    except ledger.LedgerError as e:
        raise HTTPException(400, str(e))
    return {"id": txn_id}


@router.get("/{user_id}/transactions")
def list_transactions(user_id: str):
    return ledger.list_transactions(user_id)


@router.delete("/{user_id}/transactions/{txn_id}")
def delete_transaction(user_id: str, txn_id: int):
    try:
        ledger.delete_transaction(user_id, txn_id)
    except ledger.LedgerError as e:
        raise HTTPException(400, str(e))
    return {"deleted": txn_id}


@router.get("/{user_id}/positions")
def positions(user_id: str):
    return ledger.get_positions(user_id)


@router.get("/{user_id}/cash")
def cash(user_id: str):
    return ledger.get_cash(user_id)


class TcbsSyncIn(BaseModel):
    otp: str                          # Smart OTP from the TCInvest app


@router.post("/{user_id}/tcbs/sync")
def tcbs_sync(user_id: str, body: TcbsSyncIn):
    """Mirror the TCBS account (holdings + cash, read-only) into this user's ledger."""
    try:
        return tcbs.sync(user_id, body.otp)
    except tcbs.TcbsError as e:
        raise HTTPException(502, str(e))
    except ledger.LedgerError as e:
        raise HTTPException(400, str(e))


@router.post("/{user_id}/advise")
def advise(user_id: str):
    try:
        return advisor.advise(user_id)
    except ValueError as e:                     # empty portfolio
        raise HTTPException(400, str(e))
    except RuntimeError as e:                   # LLM returned no usable verdicts
        raise HTTPException(503, str(e))
