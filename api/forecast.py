"""
api/forecast.py — Ticker Trend Analyzer API (trend/, plan in vn-portfolio docs/plan.md §8.2).

POST /api/v1/forecasts                  {ticker | tickers, as_of?, horizons?} -> trend forecasts (variant D)
GET  /api/v1/forecasts/latest           -> stored forecasts of the latest session (no LLM call)
GET  /api/v1/forecasts/{ticker}/history -> past forecasts with realised outcomes (track record)
GET  /api/v1/forecasts/backtest/latest  -> the latest walk-forward evaluation (backtest_runs)
Same internal key as /advisor (each new forecast is an LLM call). Hidden from the OpenAPI docs.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.advisor import _check_key
from trend import service
from trend.labels import HORIZONS

router = APIRouter(prefix="/api/v1/forecasts", tags=["forecasts"], include_in_schema=False,
                   dependencies=[Depends(_check_key)])

MAX_TICKERS = 30


class ForecastIn(BaseModel):
    ticker: str | None = None
    tickers: list[str] = Field(default_factory=list)
    as_of: str | None = None             # ISO date / timestamp; default = the latest session
    horizons: list[int] = Field(default_factory=lambda: list(HORIZONS))


@router.post("")
def create_forecasts(body: ForecastIn):
    tickers = ([body.ticker] if body.ticker else []) + body.tickers
    if not tickers:
        raise HTTPException(422, "ticker or tickers is required")
    if len(tickers) > MAX_TICKERS:
        raise HTTPException(422, f"at most {MAX_TICKERS} tickers per call")
    if sorted(set(body.horizons)) != sorted(HORIZONS):
        raise HTTPException(422, f"horizons supported: {list(HORIZONS)} (60 sessions comes after the MVP is validated)")
    try:
        items = service.forecast(tickers, body.as_of, log=lambda *_: None)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return items[0] if body.ticker and not body.tickers else {"items": items}


@router.get("/latest")
def latest_forecasts():
    """Stored forecasts of the latest forecast session (no LLM call)."""
    return service.latest()


@router.get("/backtest/latest")
def latest_backtest():
    from core.db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, created_at, config, metrics, calibration FROM backtest_runs ORDER BY id DESC LIMIT 1")
        r = cur.fetchone()
    if not r:
        raise HTTPException(404, "no backtest run yet")
    return {"id": r[0], "created_at": r[1].isoformat(), "config": r[2], "metrics": r[3], "calibration": r[4]}


@router.get("/{ticker}/history")
def forecast_history(ticker: str, limit: int = 60):
    return {"ticker": ticker.upper(), "items": service.history(ticker, min(limit, 250))}
