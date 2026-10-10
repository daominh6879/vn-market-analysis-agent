"""
ingest/fetch_index.py — Fetch VN market index OHLCV from SSI iBoard and upsert to DB.

Usage:
    python ingest/fetch_index.py                    # all default indices, 30 days
    python ingest/fetch_index.py --indices VNINDEX HNX --days 60
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")  # FIREANT_* for the HNX30 foreign-net lookup

_DEFAULT_INDICES = ["VNINDEX", "HNX", "UPCOM", "VN30", "HNX30"]
# Stored index_code → symbol the VCI chart endpoint knows. "HNX"/"UPCOM" return no data;
# the exchange indices are HNXINDEX / UPCOMINDEX.
_PROVIDER_SYMBOL = {"HNX": "HNXINDEX", "UPCOM": "UPCOMINDEX"}
_DEFAULT_DAYS = 30


def fetch_and_upsert(index_code: str, days: int = _DEFAULT_DAYS) -> int:
    """
    Fetch `days` candles for `index_code` from SSI iBoard, upsert to market_index_daily.
    Returns count of rows upserted (0 on error).
    """
    from tools.providers import SsiIndexProvider
    from tools.index_db import upsert_index_rows

    provider = SsiIndexProvider()
    try:
        df = provider.fetch_history(index_code, days)
    except Exception as e:
        df = _fetch_via_vnstock(index_code, days) if index_code.upper() in _PROVIDER_SYMBOL else None
        if df is None:
            sys.stderr.write(f"[fetch_index] {index_code}: SSI fetch failed: {e}\n")
            return 0

    if df.empty:
        sys.stderr.write(f"[fetch_index] {index_code}: empty response\n")
        return 0

    rows: list[dict] = []
    prev_close: float | None = None
    foreign_by_date = _foreign_net_by_date(
        index_code, str(df["time"].min())[:10], str(df["time"].max())[:10])
    for _, row in df.iterrows():
        close = float(row["close"])
        change_pts = round(close - prev_close, 2) if prev_close is not None else 0.0
        change_pct = round(change_pts / prev_close * 100, 2) if prev_close else 0.0
        rows.append({
            "index_code":      index_code.upper(),
            "date":            str(row["time"]),
            "open":            float(row["open"]),
            "high":            float(row["high"]),
            "low":             float(row["low"]),
            "close":           close,
            "change_pts":      change_pts,
            "change_pct":      change_pct,
            "matched_volume":  int(row.get("volume", 0)),
            "matched_value":   (float(row["accumulated_value_vnd"])
                                    if "accumulated_value_vnd" in df.columns else None),
            "foreign_net":     (foreign_by_date or {}).get(str(row["time"])[:10])  # NULL when no flow data that day,
        })
        prev_close = close

    from tools.index_db import upsert_index_rows
    n = upsert_index_rows(rows)
    return n


def _fetch_via_vnstock(index_code: str, days: int):
    """HNX / UPCOM are not served by the VCI chart endpoint; vnstock has them as
    HNXINDEX / UPCOMINDEX (OHLC + volume only — no traded value)."""
    from datetime import date, timedelta
    try:
        from vnstock import Quote
        df = Quote(symbol=_PROVIDER_SYMBOL[index_code.upper()], source="VCI").history(
            start=str(date.today() - timedelta(days=days + 10)), end=str(date.today()), interval="1D")
        if df is None or df.empty:
            return None
        df = df.copy()
        df["time"] = df["time"].astype(str).str[:10]
        return df.drop_duplicates(subset=["time"]).sort_values("time").tail(days).reset_index(drop=True)
    except Exception as e:
        sys.stderr.write(f"[fetch_index] {index_code}: vnstock fallback failed: {e}\n")
        return None


def _index_members(index_code: str) -> list[str]:
    """Current constituents of `index_code` (vnstock group list); [] if unavailable."""
    try:
        from vnstock import Listing
        return [str(t).upper() for t in Listing(source="VCI").symbols_by_group(index_code)]
    except Exception as e:
        sys.stderr.write(f"[fetch_index] {index_code}: constituents lookup failed: {e}\n")
        return []


def _foreign_net_by_date(index_code: str, start: str, end: str) -> dict[str, float] | None:
    """Foreign net buy (tỷ đồng) per date summed over the index constituents.

    VNINDEX = every HOSE ticker in foreign_flows; VN30 = its constituents in foreign_flows;
    HNX30 = constituents fetched from Fireant (HNX tickers are not stored in foreign_flows).
    Returns None when the index has no computable constituent set (HNX, UPCOM, failures), so
    the caller stores NULL instead of a misleading 0.
    """
    code = index_code.upper()
    try:
        if code in ("VNINDEX", "VN30"):
            from core.db import get_conn
            members = _index_members(code) if code == "VN30" else None
            if code == "VN30" and not members:
                return None
            sql = "SELECT date, SUM(net_value) FROM foreign_flows WHERE date BETWEEN %s AND %s"
            params: list = [start, end]
            if members:
                sql += " AND ticker = ANY(%s)"
                params.append(members)
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(sql + " GROUP BY date", params)
                return {str(d): float(v) for d, v in cur.fetchall()}
        if code == "HNX30":
            members = _index_members(code)
            if not members:
                return None
            from tools.providers import FireantProvider
            fa = FireantProvider()
            totals: dict[str, float] = {}
            for t in members:
                df = fa.fetch_history_range(t, start, end)
                if df.empty or "foreign_buy_vol" not in df.columns:
                    continue
                for _, r in df.iterrows():
                    # Fireant close is nghìn đồng → volume × close / 1e6 = tỷ đồng
                    net = (int(r["foreign_buy_vol"]) - int(r["foreign_sell_vol"])) * float(r["close"]) / 1e6
                    d = str(r["time"])[:10]
                    totals[d] = totals.get(d, 0.0) + net
            return {d: round(v, 4) for d, v in totals.items()} or None
    except Exception as e:
        sys.stderr.write(f"[fetch_index] {code}: foreign net failed: {e}\n")
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch VN index OHLCV → Postgres")
    parser.add_argument("--indices", nargs="+", default=_DEFAULT_INDICES,
                        help="Index codes to fetch (default: all 5)")
    parser.add_argument("--days", type=int, default=_DEFAULT_DAYS,
                        help="Calendar days of history (default: 30)")
    args = parser.parse_args()

    total = 0
    for code in args.indices:
        n = fetch_and_upsert(code, args.days)
        print(f"  {code}: {n} rows upserted")
        total += n
    print(f"Done — {total} total rows")


if __name__ == "__main__":
    main()
