"""
api/advisor.py — hidden, stateless advisor API for the separate portfolio app (vn-portfolio).

The caller owns the ledger and sends positions + cash; this side only reads market data and runs the AI judge.
Not in OpenAPI docs. Every call needs header X-Internal-Key == env ADVISOR_INTERNAL_KEY (fails closed when unset).
"""
from __future__ import annotations

import hmac
import os
from datetime import date

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from portfolio import advisor, evidence, freshness, ledger


def _check_key(x_internal_key: str = Header(default="")) -> None:
    expected = os.getenv("ADVISOR_INTERNAL_KEY", "")
    if not expected:
        raise HTTPException(503, "ADVISOR_INTERNAL_KEY is not configured")
    if not hmac.compare_digest(x_internal_key, expected):
        raise HTTPException(401, "invalid internal key")


router = APIRouter(prefix="/advisor", tags=["advisor"], include_in_schema=False, dependencies=[Depends(_check_key)])


class Position(BaseModel):
    """One open position, same shape as portfolio.ledger.build_positions rows."""
    ticker: str
    quantity: int
    sellable_quantity: int
    avg_cost: float
    last_price: float | None = None
    market_value: float | None = None
    unrealized_pnl: float | None = None
    unrealized_pct: float | None = None
    realized_pnl: float = 0.0
    sector: str = "Unknown"
    weight: float = 0.0


class Cash(BaseModel):
    """Cash summary of the caller's ledger (vn-portfolio /cash)."""
    cash: float
    total_deposited: float = 0.0
    total_withdrawn: float = 0.0
    market_value: float = 0.0
    nav: float
    total_return_pct: float | None = None
    as_of: str


class PortfolioIn(BaseModel):
    positions: list[Position]
    cash: Cash
    user_id: str = "external"        # only used as the report title
    watchlist: list[str] = []        # /advise only: judged as new-idea candidates first


class ReferenceIn(BaseModel):
    tickers: list[str]
    as_of: date | None = None


def _plain(body: PortfolioIn) -> tuple[list[dict], dict]:
    return [p.model_dump() for p in body.positions], body.cash.model_dump()


@router.post("/reference")
def reference(body: ReferenceIn):
    return ledger.reference(body.tickers, body.as_of)


@router.post("/risk")
def risk(body: PortfolioIn):
    return evidence.risk_for(*_plain(body))


class TickersIn(BaseModel):
    tickers: list[str]


@router.post("/market-stats")
def market_stats(body: TickersIn):
    """Volatility, MA50 trend, liquidity, foreign flows per ticker + VNINDEX vs MA50 — inputs for target weights."""
    return evidence.market_stats(body.tickers)


class MarketDataIn(BaseModel):
    metrics: list[str] | None = None     # financial_facts metric codes (None = all)
    since: date | None = None


@router.post("/market-data")
def market_data(body: MarketDataIn):
    """Full price / index / financial / flow history for vn-portfolio's rule engine (read-only dump)."""
    from portfolio.market_data import market_data as dump
    return dump(body.metrics, body.since)


class NewsFactsIn(BaseModel):
    since: date | None = None
    tickers: list[str] | None = None     # None = every ticker


@router.post("/news-facts")
def news_facts(body: NewsFactsIn):
    """Articles (with tagged tickers), their LLM impact scores and insider-trade notices for vn-portfolio's rule
    engine news rules (read-only dump; the caller classifies and applies point-in-time cutoffs)."""
    from portfolio.news_facts import news_facts as dump
    return dump(body.since, body.tickers)


class RefetchIn(BaseModel):
    tickers: list[str]
    datasets: list[str]                  # subset of portfolio.refetch.DATASETS


@router.post("/refetch")
def refetch(body: RefetchIn):
    """Re-run the scheduled fetch + upsert for a few tickers now (vn-portfolio "Dữ liệu thiếu" page). Same functions
    and tables as the Dagster assets; at most refetch.MAX_TICKERS tickers per call."""
    from portfolio import refetch as rf
    try:
        return rf.refetch(body.tickers, body.datasets)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/freshness")
def data_freshness():
    """{expected_session, fresh, datasets, problems, checked_at} — polled by vn-portfolio to alert on stale data."""
    return freshness.check()


@router.post("/advise")
def advise(body: PortfolioIn):
    try:
        return advisor.advise_for(*_plain(body), body.user_id, body.watchlist)
    except ValueError as e:                     # empty portfolio
        raise HTTPException(400, str(e))
    except freshness.StaleDataError as e:       # no advice on data that misses the last finished session
        raise HTTPException(503, f"stale_data: {e}")
    except RuntimeError as e:                   # LLM returned no usable verdicts
        raise HTTPException(503, str(e))


ASK_TENANT = "vn-portfolio"          # keeps the portfolio app's chats apart from the main chat UI's


class AskIn(BaseModel):
    user_id: str
    message: str
    conversation_id: str | None = None   # None starts a new conversation


@router.post("/ask")
def ask(body: AskIn):
    """One turn of the full agent graph (every intent) in a conversation owned by the portfolio app."""
    from memory.conversation import create_conversation, get_conversation
    from memory.turn_handler import run_turn

    cid = body.conversation_id
    if cid:
        conv = get_conversation(cid)
        if not conv or conv["user_id"] != body.user_id or conv["tenant_id"] != ASK_TENANT:
            raise HTTPException(404, f"conversation {cid} not found")
    else:
        cid = create_conversation(body.user_id, ASK_TENANT)
    reply = run_turn(conversation_id=cid, user_id=body.user_id, user_message=body.message,
                     tenant_id=ASK_TENANT, is_first_turn=body.conversation_id is None)
    return {"conversation_id": cid, "reply": reply}
