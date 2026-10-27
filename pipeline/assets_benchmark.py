"""
pipeline/assets_benchmark.py — benchmark ETF bars -> benchmark_daily (weekdays 18:30).

E1VFVN30 (tracks VN30) is the buy-and-hold alternative the paper portfolio must beat (vn-portfolio
docs/notes/live-criteria.md, condition 4). Same source as ingest/fetch_ohlcv.py (Fireant), raw prices; incremental
from the latest stored date, BACKFILL_DAYS when the ticker has no rows. Fails when a weekday run leaves the latest
bar older than STALE_DAYS, so the retry policy / alerts fire.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from dagster import AssetExecutionContext, DefaultScheduleStatus, RetryPolicy, ScheduleDefinition, asset, define_asset_job

BENCHMARK_TICKERS = ["E1VFVN30"]
BACKFILL_DAYS = 365
STALE_DAYS = 5


def bars(df) -> list[tuple]:
    """Provider frame (time, open, high, low, close, volume; thousand VND) -> rows without the ticker."""
    return [(str(r["time"])[:10], float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
             int(r.get("volume", 0) or 0)) for _, r in df.iterrows()]


def ingest(ticker: str, today: date, log=print) -> tuple[int, date | None]:
    """-> (rows upserted, latest stored date)."""
    from core.db import get_conn
    from tools.providers import FireantProvider, resolve_ticker
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(date) FROM benchmark_daily WHERE ticker = %s", (ticker,))
        latest = cur.fetchone()[0]
    start = latest + timedelta(days=1) if latest else today - timedelta(days=BACKFILL_DAYS)
    if start > today:
        return 0, latest
    df = FireantProvider().fetch_history_range(resolve_ticker(ticker), str(start), str(today))
    rows = bars(df) if df is not None and not df.empty else []
    with get_conn() as conn, conn.cursor() as cur:
        for r in rows:
            cur.execute(
                "INSERT INTO benchmark_daily (ticker, date, open, high, low, close, volume) VALUES (%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (ticker, date) DO UPDATE SET open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, "
                "close = EXCLUDED.close, volume = EXCLUDED.volume, fetched_at = NOW()", (ticker, *r))
        cur.execute("SELECT max(date) FROM benchmark_daily WHERE ticker = %s", (ticker,))
        latest = cur.fetchone()[0]
    log(f"benchmark {ticker}: {len(rows)} rows from {start}, latest {latest}")
    return len(rows), latest


@asset(
    group_name="market_data",
    description="Benchmark ETF daily bars (E1VFVN30) -> benchmark_daily. Weekdays 18:30; 365-day backfill when empty.",
    retry_policy=RetryPolicy(max_retries=3, delay=300),
)
def benchmark_ingest(context: AssetExecutionContext) -> dict:
    today = date.today()
    out = {}
    for t in BENCHMARK_TICKERS:
        n, latest = ingest(t, today, log=context.log.info)
        if latest is None or (today - latest).days > STALE_DAYS:
            raise RuntimeError(f"benchmark {t}: latest bar {latest} is older than {STALE_DAYS} days")
        out[t] = {"rows": n, "latest": latest.isoformat()}
    return out


benchmark_ingest_job = define_asset_job(name="benchmark_ingest_job", selection=[benchmark_ingest])

benchmark_ingest_schedule = ScheduleDefinition(
    job=benchmark_ingest_job,
    cron_schedule="30 18 * * 1-5",   # weekdays 18:30, with OHLCV
    name="benchmark_ingest_1830",
    default_status=DefaultScheduleStatus.RUNNING,
    execution_timezone="Asia/Ho_Chi_Minh",
)
