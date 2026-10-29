"""
portfolio/market_data.py — full market history for vn-portfolio's rule engine (/advisor/market-data).

Read-only. vn-portfolio rebuilds raw prices, point-in-time views and snapshots on its side; this module only dumps
what the database holds, in its native units: stock prices in nghìn đồng (price_unit "kVND"), index levels in
points, traded value / flows in tỷ đồng, financial facts in VND.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date


def _rows(cur, sql: str, params: dict) -> list[tuple]:
    cur.execute(sql, params)
    return cur.fetchall()


def _iso(d) -> str | None:
    return d.isoformat() if d else None


def _f(x):
    return None if x is None else float(x)


def market_data(metrics: list[str] | None = None, since: date | None = None) -> dict:
    """{as_of, price_unit, securities, bars, index, financials, foreign, prop, dividends, events, fx, benchmark}.
    metrics: financial_facts metric codes to include (None = all). since: first date of daily series (None = all)."""
    from core.db import get_conn

    p = {"since": since or date(1900, 1, 1), "metrics": metrics}
    with get_conn() as conn, conn.cursor() as cur:
        sec = _rows(cur, "SELECT ticker, exchange, sector, industry, listed_shares, free_float, index_member, "
                         "is_active FROM securities", p)
        bars = _rows(cur, "SELECT ticker, date, open, high, low, close, volume FROM ohlcv_daily "
                          "WHERE date >= %(since)s ORDER BY ticker, date", p)
        idx = _rows(cur, "SELECT index_code, date, open, high, low, close, matched_volume, matched_value, foreign_net "
                         "FROM market_index_daily WHERE date >= %(since)s ORDER BY index_code, date", p)
        fin_sql = ("SELECT ticker, period, metric_code, value FROM financial_facts "
                   "WHERE source = 'vnstock' AND report_type = 'consolidated'")
        if metrics:
            fin_sql += " AND metric_code = ANY(%(metrics)s)"
        fin = _rows(cur, fin_sql, p)
        ff = _rows(cur, "SELECT ticker, date, buy_value, sell_value, net_value FROM foreign_flows "
                        "WHERE date >= %(since)s ORDER BY ticker, date", p)
        pf = _rows(cur, "SELECT ticker, date, buy_value, sell_value, net_value FROM proprietary_flows "
                        "WHERE date >= %(since)s ORDER BY ticker, date", p)
        div = _rows(cur, "SELECT ticker, ex_date, kind, cash_per_share, stock_ratio_pct FROM dividends "
                         "ORDER BY ticker, ex_date", p)
        ev = _rows(cur, "SELECT ticker, event_type, ex_date, record_date, ratio, note, source_url, fetched_at "
                        "FROM corporate_events ORDER BY ticker, ex_date", p)
        fx = _rows(cur, "SELECT snapshot_date, value FROM daily_snapshots WHERE series = 'sbv_central_rate' "
                        "ORDER BY snapshot_date", p)
        bench = _rows(cur, "SELECT ticker, date, open, high, low, close, volume FROM benchmark_daily "
                           "WHERE date >= %(since)s ORDER BY ticker, date", p)

    out_bars: dict[str, list] = defaultdict(list)
    for t, d, o, h, l, c, v in bars:
        out_bars[t].append([_iso(d), _f(o), _f(h), _f(l), _f(c), int(v or 0)])
    out_idx: dict[str, list] = defaultdict(list)
    for code, d, o, h, l, c, v, val, fn in idx:
        out_idx[code].append([_iso(d), _f(o), _f(h), _f(l), _f(c), int(v or 0), _f(val), _f(fn)])
    out_fin: dict[str, dict] = defaultdict(lambda: defaultdict(dict))
    for t, period, m, v in fin:
        out_fin[t][period][m] = _f(v)

    def flows(rows):
        out = defaultdict(list)
        for t, d, b, s, n in rows:
            out[t].append([_iso(d), _f(b), _f(s), _f(n)])
        return out

    out_div: dict[str, list] = defaultdict(list)
    for t, ex, kind, cash, ratio in div:
        out_div[t].append([_iso(ex), kind, _f(cash), _f(ratio)])
    out_ev: dict[str, list] = defaultdict(list)
    for t, typ, ex, rec, ratio, note, url, fetched in ev:
        out_ev[t].append({"type": typ, "ex_date": _iso(ex), "record_date": _iso(rec), "ratio": _f(ratio),
                          "note": note, "source_url": url, "fetched_at": fetched.isoformat() if fetched else None})
    out_bench: dict[str, list] = defaultdict(list)
    for t, d, o, h, l, c, v in bench:
        out_bench[t].append([_iso(d), _f(o), _f(h), _f(l), _f(c), int(v or 0)])

    as_of = max((r[0] for r in out_idx.get("VNINDEX", [])), default=None)
    return {
        "as_of": as_of,
        "price_unit": "kVND",
        "securities": {t: {"exchange": ex, "sector": s, "industry": ind, "listed_shares": int(ls or 0),
                           "free_float": _f(ffl), "index_member": list(im or []), "is_active": act}
                       for t, ex, s, ind, ls, ffl, im, act in sec},
        "bars": out_bars,
        "index": out_idx,
        "financials": {t: dict(v) for t, v in out_fin.items()},
        "foreign": flows(ff),
        "prop": flows(pf),
        "dividends": out_div,
        "events": out_ev,
        "fx": {_iso(d): _f(v) for d, v in fx},
        "benchmark": out_bench,
    }
