"""
rag/filter.py — Qdrant filter builder for BCTC collection.

Single collection bctc_structural holds all tickers.
Filters narrow results by ticker, sector, year at query time.
"""
from __future__ import annotations

from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

BCTC_COLLECTION = "bctc_structural"


def build_filter(
    tickers: list[str] | None = None,
    sector: str | None = None,
    year: str | None = None,
) -> Filter | None:
    """Build Qdrant filter from optional constraints.

    tickers: list of stock codes e.g. ["HPG", "VCB"] — matched against payload.ticker
    sector:  sector slug e.g. "steel", "banking"     — matched against payload.sector
    year:    fiscal year e.g. "2025"                 — matched against payload.year

    Returns None when no constraints → search all chunks.
    """
    conditions = []

    if tickers:
        upper = [t.upper() for t in tickers if t]
        if len(upper) == 1:
            conditions.append(
                FieldCondition(key="ticker", match=MatchValue(value=upper[0]))
            )
        elif len(upper) > 1:
            conditions.append(
                FieldCondition(key="ticker", match=MatchAny(any=upper))
            )

    if sector:
        conditions.append(
            FieldCondition(key="sector", match=MatchValue(value=sector.lower()))
        )

    if year:
        # Normalise to plain "2024" string — guards against float "2024.0" from LLM
        try:
            year_str = str(int(float(year)))
        except (ValueError, TypeError):
            year_str = str(year)
        conditions.append(
            FieldCondition(key="year", match=MatchValue(value=year_str))
        )

    return Filter(must=conditions) if conditions else None
