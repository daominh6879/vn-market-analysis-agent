"""
ingest/fetch_shares.py — Fill securities.listed_shares (số cổ phiếu lưu hành) for market cap.

market_cap = listed_shares × latest close, used by market_data (top vốn hóa) and screening.

Sources, in order:
  1. vnmarket company.profile() → `listed_shares`, then company.overview() → `issue_share`
  2. Fallback for tickers still at 0: charter capital in financial_facts (`von_dieu_le`)
     ÷ 10,000 VND par value (all VN listed shares are par 10,000) — flagged as approximate.

Usage:
    python ingest/fetch_shares.py                    # all active tickers with listed_shares = 0
    python ingest/fetch_shares.py --all              # refresh every active ticker
    python ingest/fetch_shares.py --tickers VNM,HPG
    python ingest/fetch_shares.py --charter-only     # skip vnmarket, DB fallback only
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from core.db import get_conn

PAR_VALUE_VND = 10_000


def _positive_int(v) -> int | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if not math.isnan(f) and f > 0 else None


ROOM_LIMITS = (1.0, 0.5, 0.49, 0.3, 0.2, 0.1, 0.0)   # foreign ownership limits: room total / shares lands on one


def _agrees(a: float, b: float, tol: float) -> bool:
    return abs(a / b - 1) <= tol


def pick_shares(cands: dict[str, int], room_total: float | None, statement: float | None) -> tuple[int, str] | None:
    """(shares, why) from the sources' answers, which disagree for some stocks (30/10/2026, vn-portfolio data audit:
    TYA KBS / VCI 6.13M vs FireAnt and statements 30.68M; TCR alike; ECO FireAnt 129M vs KBS 30M, whose VNDirect
    foreign room is exactly 50% of 30M). Pure. In order:
      0. a VCI figure equal to the foreign room is the room, not a share count (TYA, TCR) → dropped;
      1. FireAnt and KBS agreeing within 1% → that;
      2. candidates plausible against the statements (charter capital / 10,000, × 0.6–1.7: statements lag bonus
         issues) — one → it; several → the one whose room / shares lands exactly on a foreign limit, else the closest;
      3. no statements → the exact room match, else FireAnt, KBS, VCI."""
    vals = {k: v for k, v in cands.items() if v}
    if room_total and vals.get("vci") and abs(vals["vci"] / room_total - 1) < 1e-4:
        vals.pop("vci")
    if not vals:
        return None
    if "fireant" in vals and "kbs" in vals and _agrees(vals["fireant"], vals["kbs"], 0.01):
        return vals["fireant"], "fireant=kbs"

    def on_limit(v: float) -> bool:
        return bool(room_total) and any(abs(room_total / v - lim) < 0.0005 for lim in ROOM_LIMITS)

    pool = {k: v for k, v in vals.items() if statement and 0.6 <= v / statement <= 1.7} if statement else {}
    if len(pool) == 1:
        k = next(iter(pool))
        return pool[k], f"statement:{k}"
    if pool:
        room_hits = [k for k in pool if on_limit(pool[k])]
        k = room_hits[0] if room_hits else min(pool, key=lambda x: abs(pool[x] / statement - 1))
        return pool[k], f"{'room' if room_hits else 'closest'}:{k}"
    room_hits = [k for k in vals if on_limit(vals[k])]
    k = room_hits[0] if room_hits else next((x for x in ("fireant", "kbs", "vci") if x in vals), next(iter(vals)))
    return vals[k], f"{'room' if room_hits else 'first'}:{k}"


def _evidence(ticker: str) -> tuple[float | None, float | None]:
    """(latest VNDirect total foreign room, latest statement shares = charter capital / 10,000)."""
    from core.db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT total_room FROM foreign_room WHERE ticker = %s ORDER BY date DESC LIMIT 1", (ticker,))
        r = cur.fetchone()
        cur.execute("SELECT period, value FROM financial_facts WHERE ticker = %s AND metric_code = 'von_dieu_le' "
                    "AND period LIKE 'Q%%'", (ticker,))
        caps = [(int(p[3:]), int(p[1]), v) for p, v in cur.fetchall() if v]
    return (float(r[0]) if r and r[0] else None), (max(caps)[2] / 10_000 if caps else None)


def fetch_issue_share(ticker: str) -> int | None:
    """Shares outstanding: every source vnmarket has (FireAnt, KBS listed, VCI issued), picked by pick_shares. Until
    30/10/2026 KBS alone came first (TYA, TCR ×4–5 off). None when unavailable."""
    from core.vnmarket_client import get_client
    try:
        cands = get_client().company.shares_outstanding(ticker)["candidates"]
    except Exception:
        return None
    room, statement = _evidence(ticker)
    best = pick_shares(cands, room, statement)
    return _positive_int(best[0]) if best else None


def update_shares(ticker: str, shares: int) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE securities SET listed_shares = %s, updated_at = NOW() WHERE ticker = %s",
                        (shares, ticker))


def fill_from_charter_capital() -> int:
    """listed_shares = latest charter capital / 10,000 for tickers still at 0. Returns rows updated."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE securities s
                SET listed_shares = ROUND(f.value / %s), updated_at = NOW()
                FROM (
                    SELECT DISTINCT ON (ticker) ticker, value
                    FROM financial_facts
                    WHERE metric_code = 'von_dieu_le' AND period ~ '^[0-9]{4}$' AND value > 0
                    ORDER BY ticker, period DESC
                ) f
                WHERE s.ticker = f.ticker AND COALESCE(s.listed_shares, 0) = 0
                """,
                (PAR_VALUE_VND,),
            )
            return cur.rowcount


def _target_tickers(args) -> list[str]:
    if args.tickers:
        return [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    where = "is_active" if args.all else "is_active AND COALESCE(listed_shares, 0) = 0"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT ticker FROM securities WHERE {where} ORDER BY ticker")
            return [r[0] for r in cur.fetchall()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Fill securities.listed_shares")
    parser.add_argument("--tickers", default="")
    parser.add_argument("--all", action="store_true", help="Refresh every active ticker, not just missing")
    parser.add_argument("--charter-only", action="store_true", help="Skip vnmarket; use charter capital only")
    args = parser.parse_args()

    ok, failed = 0, []
    if not args.charter_only:
        tickers = _target_tickers(args)
        print(f"listed_shares: {len(tickers)} tickers via vnmarket")
        for t in tickers:
            try:
                n = fetch_issue_share(t)
                if n:
                    update_shares(t, n)
                    ok += 1
                    print(f"  {t}: {n:,}")
                else:
                    failed.append(t)
            except Exception as exc:
                print(f"  {t}: FAILED — {exc}", file=sys.stderr)
                failed.append(t)

    approx = fill_from_charter_capital()
    print(f"\nvnmarket: {ok} updated, {len(failed)} missing. "
          f"Charter-capital fallback (approx, ÷{PAR_VALUE_VND:,}): {approx} updated.")
    if failed:
        print(f"Missing from vnmarket: {', '.join(failed)}", file=sys.stderr)


if __name__ == "__main__":
    main()
