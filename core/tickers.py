"""
core/tickers.py — Runtime ticker list from securities table.

Primary: SELECT ticker FROM securities WHERE is_active = true ORDER BY ticker
Fallback: data/known_tickers.txt → TICKERS env → empty (never a single default ticker)
"""
from __future__ import annotations

import os
import re
import unicodedata


def get_tickers() -> list[str]:
    """Return active tickers from securities table.

    Fallback order: full known universe on disk → TICKERS env → empty. Never collapse
    to a single default ticker — a `["HPG"]`-only universe silently drops every other
    code (e.g. "giá VNM" → HPG) before tools can even try their DB→API fallback.
    """
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT ticker FROM securities WHERE is_active = true ORDER BY ticker"
                )
                rows = cur.fetchall()
        if rows:
            return [r[0] for r in rows]
    except Exception:
        pass

    from pathlib import Path
    known_path = Path(__file__).resolve().parent.parent / "data" / "known_tickers.txt"
    try:
        if known_path.exists():
            ticks = [
                ln.strip().upper()
                for ln in known_path.read_text(encoding="utf-8").splitlines()
                if ln.strip()
            ]
            if ticks:
                return ticks
    except Exception:
        pass

    env = os.getenv("TICKERS", "")
    return [t.strip().upper() for t in env.split(",") if t.strip()]


def get_ratio_tickers() -> list[str]:
    """Return VN30+VN100 active tickers for daily ratio pre-fetch. Falls back to hose_universe data."""
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ticker FROM securities
                    WHERE is_active = true
                      AND (index_member @> ARRAY['VN30'] OR index_member @> ARRAY['VN100'])
                    ORDER BY
                      CASE WHEN index_member @> ARRAY['VN30'] THEN 0 ELSE 1 END,
                      ticker
                    """
                )
                rows = cur.fetchall()
        if rows:
            return [r[0] for r in rows]
    except Exception:
        pass
    try:
        from data.hose_universe import load_hose_universe
        return [u["ticker"] for u in load_hose_universe() if u.get("index_member") in ("VN30", "VN100")]
    except Exception:
        return []


def _sector_peers_fallback(ticker: str, max_peers: int = 10) -> list[str]:
    """Derive same-sector peers from hose_universe data when securities is unavailable."""
    try:
        from data.hose_universe import load_hose_universe
        universe = load_hose_universe()
        sector = next((u["sector"] for u in universe if u["ticker"] == ticker), "")
        if sector and sector not in ("Unknown", ""):
            peers = [u["ticker"] for u in universe if u["sector"] == sector and u["ticker"] != ticker]
            return ([ticker] + peers)[:max_peers]
    except Exception:
        pass
    return [ticker]


def get_sector_peers(ticker: str, max_peers: int = 10) -> list[str]:
    """Return active VN30/VN100 tickers in the same sector. Falls back to hose_universe data."""
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT sector FROM securities WHERE ticker = %s AND is_active = true",
                    (ticker,),
                )
                row = cur.fetchone()
        if not row or row[0] in ("Unknown", ""):
            return _sector_peers_fallback(ticker, max_peers)
        sector = row[0]
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ticker FROM securities
                    WHERE sector = %s AND is_active = true
                      AND (index_member @> ARRAY['VN30'] OR index_member @> ARRAY['VN100'])
                    ORDER BY
                      CASE WHEN index_member @> ARRAY['VN30'] THEN 0 ELSE 1 END,
                      ticker
                    LIMIT %s
                    """,
                    (sector, max_peers),
                )
                rows = cur.fetchall()
        peers = [r[0] for r in rows]
        if not peers:
            return _sector_peers_fallback(ticker, max_peers)
        # Ensure query ticker is included
        if ticker not in peers:
            peers = [ticker] + peers[: max_peers - 1]
        return peers
    except Exception:
        return _sector_peers_fallback(ticker, max_peers)


def get_sector_tickers(label: str) -> list[str]:
    """Map a sector label (e.g. "thép", "ngân hàng") to active tickers.

    Direct labels match the sector column case-insensitively; alias labels ("thép",
    "năng lượng", "tiêu dùng", …) match a stored `tags` entry. Data-driven — no
    label→sector mapping here.
    """
    label = (label or "").strip().lower()
    if not label:
        return []
    tickers: list[str] = []
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT ticker FROM securities "
                    "WHERE is_active = true AND "
                    "(sector ILIKE %s OR industry ILIKE %s OR tags @> %s) "
                    "ORDER BY ticker",
                    (f"%{label}%", f"%{label}%", [label]),
                )
                tickers = [r[0] for r in cur.fetchall()]
    except Exception:
        pass
    return tickers


def _load_sector_labels() -> list[str]:
    """Distinct lowercased sector values + tags (longest first).

    Source of truth for focus-sector matching in agents/focus.py — replaces the old
    hardcoded _SECTOR_KEYWORDS gazetteer. Reads securities; falls back to the
    hose_universe data file when the DB is unavailable.
    """
    labels: list[str] = []
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT lower(sector) FROM securities "
                    "WHERE sector IS NOT NULL AND sector != 'Unknown'"
                )
                labels = [r[0] for r in cur.fetchall() if r[0]]
                cur.execute(
                    "SELECT DISTINCT lower(industry) FROM securities "
                    "WHERE industry IS NOT NULL AND industry != 'Unknown'"
                )
                labels.extend(r[0] for r in cur.fetchall() if r[0])
                cur.execute("SELECT DISTINCT unnest(tags) FROM securities")
                labels.extend(r[0] for r in cur.fetchall() if r[0])
    except Exception:
        pass
    if not labels:
        try:
            from data.hose_universe import load_hose_universe
            for u in load_hose_universe():
                s = (u.get("sector") or "").lower()
                if s and s != "unknown":
                    labels.append(s)
                i = (u.get("industry") or "").lower()
                if i and i != "unknown":
                    labels.append(i)
                labels.extend(t for t in (u.get("tags") or []) if t)
        except Exception:
            pass
    return sorted(set(labels), key=len, reverse=True)


def _load_sectors() -> list[str]:
    """Distinct lowercased `sector` values from securities (coarse only, no tags/industry).

    Used where a downstream filter matches the coarse sector taxonomy — e.g. Qdrant
    `payload.sector` (BCTC chunks), which stores the lowercased `securities.sector`.
    """
    sectors: list[str] = []
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT lower(sector) FROM securities "
                    "WHERE sector IS NOT NULL AND sector != 'Unknown'"
                )
                sectors = [r[0] for r in cur.fetchall() if r[0]]
    except Exception:
        pass
    if not sectors:
        try:
            from data.hose_universe import load_hose_universe
            sectors = sorted({
                (u.get("sector") or "").lower()
                for u in load_hose_universe()
                if u.get("sector") and u["sector"] != "Unknown"
            })
        except Exception:
            pass
    return sorted(set(sectors))


# ── Ticker extraction (shared by graph fan-out + cache key) ──────────────────

_TICKER_RE = re.compile(r'\b([A-Z]{2,5})\b')
_TICKER_STOPWORDS = frozenset({
    "VE", "VA", "LA", "CO", "DE", "VS", "ROE", "ROA", "EPS", "PE", "PB",
    # Currency codes — never VN stock tickers. "USD/VND" is an FX query, not a stock pair.
    "USD", "VND", "EUR", "JPY", "GBP", "CNY", "KRW", "CHF", "AUD", "CAD", "SGD",
})


def raw_tickers(query: str) -> list[str]:
    """Return [A-Z]{2,5} tokens from `query`, stopword-filtered.

    Matches tokens already uppercase in the source text first; only falls back to a
    case-insensitive scan when the source has no uppercase token. This keeps ordinary
    Vietnamese words ("tin", "ban", "cho", "nam", "hai") — which all become candidate
    codes once `.upper()` is applied to the whole sentence — from leaking in as tickers
    when the sentence already carries real (uppercase) tickers.
    """
    if not query:
        return []
    hits = _TICKER_RE.findall(query)
    if not hits:
        hits = _TICKER_RE.findall(query.upper())
    return [t for t in hits if t not in _TICKER_STOPWORDS]


def extract_tickers(query: str) -> list[str]:
    """VN-universe tickers named in `query`, order-preserved and deduped.

    Universe filter drops currency codes and other non-VN uppercase tokens. Falls back
    to raw_tickers() (no universe filter) when the securities table is unavailable, so
    a degraded DB never silently drops valid tickers. When no valid ticker code is
    present, falls through to company-name/alias resolution ("vinamilk" → VNM).
    """
    hits = raw_tickers(query)
    try:
        known = set(get_tickers())
    except Exception:
        known = set()
    if known:
        code_hits = [t for t in hits if t in known]
        return list(dict.fromkeys(code_hits)) if code_hits else resolve_name_tickers(query)
    return list(dict.fromkeys(hits)) if hits else resolve_name_tickers(query)


# ── Company-name/alias → ticker resolution ────────────────────────────────────

_MIN_ALIAS_LEN = 4


def _normalize(s: str) -> str:
    """Lowercase + strip diacritics, so "Hòa Phát" and "hoa phat" match."""
    return "".join(c for c in unicodedata.normalize("NFKD", (s or "").lower())
                   if not unicodedata.combining(c))


def _load_name_index() -> dict[str, str]:
    """Map {normalized alias/company_name: ticker} for active securities."""
    index: dict[str, str] = {}
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT ticker, name_aliases, company_name "
                    "FROM securities WHERE is_active = true"
                )
                rows = cur.fetchall()
        for ticker, aliases, company in rows:
            for a in (aliases or []):
                a = _normalize(a)
                if len(a) >= _MIN_ALIAS_LEN:
                    index.setdefault(a, ticker)
            c = _normalize(company)
            if len(c) >= _MIN_ALIAS_LEN:
                index.setdefault(c, ticker)
    except Exception:
        pass
    return index


def resolve_name_tickers(query: str) -> list[str]:
    """Resolve company names/aliases in `query` to tickers (diacritic-insensitive)."""
    q = _normalize(query)
    if not q:
        return []
    index = _load_name_index()
    if not index:
        return []
    matched: list[tuple[int, str]] = []
    for alias in sorted(index, key=len, reverse=True):  # longest phrase wins
        pos = q.find(alias)
        if pos >= 0:
            matched.append((pos, index[alias]))
    out: list[str] = []
    seen: set[str] = set()
    for _, t in sorted(matched):
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out
