"""
ingest/fetch_global_quotes.py — Fetch world indices, commodities, crypto, FX, VN gold → market_quotes.

Best-effort: individual failures don't abort the run.
Mirrors the Dagster asset global_quotes_ingest (pipeline/assets_index.py).

Usage:
    python ingest/fetch_global_quotes.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from core.db import get_conn


def _upsert_market_quotes(rows: list[dict]) -> int:
    """Upsert into market_quotes table. Returns count of rows written."""
    if not rows:
        return 0
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO market_quotes
                        (symbol, asset_class, date, value, change_abs, change_pct,
                         extra, unit, source)
                    VALUES
                        (%(symbol)s, %(asset_class)s, %(date)s, %(value)s,
                         %(change_abs)s, %(change_pct)s,
                         %(extra)s::jsonb, %(unit)s, %(source)s)
                    ON CONFLICT (symbol, date) DO UPDATE SET
                        value      = EXCLUDED.value,
                        change_abs = EXCLUDED.change_abs,
                        change_pct = EXCLUDED.change_pct,
                        extra      = EXCLUDED.extra,
                        fetched_at = NOW()
                    """,
                    [{**r, "extra": json.dumps(r["extra"])} for r in rows],
                )
            conn.commit()
        return len(rows)
    except Exception as e:
        sys.stderr.write(f"[fetch_global_quotes] upsert failed: {e}\n")
        return 0


def fetch_and_upsert() -> dict:
    """Fetch all global quote sources → market_quotes. Returns {"log", "quotes_upserted"}."""
    from tools.global_market import (
        get_commodities,
        get_crypto_prices,
        get_fx_rates,
        get_global_indices,
        get_vn_gold,
    )

    today = str(date.today())
    rows_to_upsert: list[dict] = []
    log: dict[str, str] = {}

    def _try(label: str, fn):
        result = fn()
        if result.status == "ok":
            print(f"{label}: ok")
            log[label] = "ok"
        else:
            print(f"{label}: {result.status} — {result.message}", file=sys.stderr)
            log[label] = result.status
        return result

    # World indices
    idx_result = _try("global_indices", get_global_indices)
    if idx_result.status == "ok" and idx_result.data:
        for item in idx_result.data:
            rows_to_upsert.append({
                "symbol": item["ticker"],
                "asset_class": "equity_index",
                "date": today,
                "value": item["close"],
                "change_abs": 0,
                "change_pct": item["change_pct"],
                "extra": {"name": item["name"]},
                "unit": "points",
                "source": "yfinance",
            })

    # Commodities
    com_result = _try("commodities", get_commodities)
    if com_result.status == "ok" and com_result.data:
        for item in com_result.data:
            rows_to_upsert.append({
                "symbol": item["ticker"],
                "asset_class": "commodity",
                "date": today,
                "value": item["price"],
                "change_abs": 0,
                "change_pct": item["change_pct"],
                "extra": {"name": item["name"]},
                "unit": item["unit"],
                "source": "yfinance",
            })

    # Crypto
    cry_result = _try("crypto", get_crypto_prices)
    if cry_result.status == "ok" and cry_result.data:
        for coin in cry_result.data.get("coins", []):
            rows_to_upsert.append({
                "symbol": coin["symbol"],
                "asset_class": "crypto",
                "date": today,
                "value": coin["price_usd"],
                "change_abs": 0,
                "change_pct": coin["change_24h_pct"],
                "extra": {"market_cap_usd": coin["market_cap_usd"]},
                "unit": "USD",
                "source": "coingecko",
            })

    # FX
    fx_result = _try("fx_rates", get_fx_rates)
    if fx_result.status == "ok" and fx_result.data:
        rows_to_upsert.append({
            "symbol": "USD/VND",
            "asset_class": "fx",
            "date": today,
            "value": fx_result.data["transfer"],
            "change_abs": 0,
            "change_pct": 0,
            "extra": {"buy": fx_result.data["buy"], "sell": fx_result.data["sell"]},
            "unit": "VND",
            "source": "vietcombank",
        })

    # VN gold
    gold_result = _try("vn_gold", get_vn_gold)
    if gold_result.status == "ok" and gold_result.data:
        d = gold_result.data
        rows_to_upsert.append({
            "symbol": "XAU_SJC",
            "asset_class": "gold_vn",
            "date": today,
            "value": d["sell_vnd"],
            "change_abs": 0,
            "change_pct": 0,
            "extra": {"buy_vnd": d["buy_vnd"], "sell_vnd": d["sell_vnd"]},
            "unit": "triệu đồng/lượng",
            "source": "sjc",
        })

    n = _upsert_market_quotes(rows_to_upsert)
    return {"log": log, "quotes_upserted": n}


def main() -> None:
    result = fetch_and_upsert()
    print(f"market_quotes done — {result['quotes_upserted']} quotes upserted")


if __name__ == "__main__":
    main()
