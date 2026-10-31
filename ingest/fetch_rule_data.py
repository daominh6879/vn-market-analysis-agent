"""
ingest/fetch_rule_data.py — datasets for vn-portfolio's rule engine (khung v2.2) that were missing there.

Every function is incremental (latest stored date minus a small overlap) unless `backfill`, and raises when a run
produced nothing so Dagster retries / alerts. Sources: VNDirect finfo through vnmarket, yfinance for commodities.

  block_trades        put-through vs matched volume/value per session        (12.4 giao dịch thỏa thuận bất thường)
  size_indices        VN100 / VNMID / VNSML + older VNINDEX/VN30/HNX30 days   (3.7 chỉ số theo vốn hóa, MA200 history)
  index_valuation     VNINDEX P/E, P/B per session from 2017-12               (3.3 P/E thị trường vùng thấp)
  foreign_room        total room / room left per session                     (7.6-B, 12.5 room ngoại)
  shareholders        major holders and insiders                             (12.4 cổ đông tập trung)
  corporate_actions   issues with price, AGMs, trading / margin alerts       (5.9 pha loãng, 12.x trạng thái)
  vnd_items           AGM business plan, broker loans, insurer statements    (5.4 kế hoạch, 3.3 margin, 5.8 bảo hiểm)
  commodities         Brent, HRC, iron ore, gas, urea, sugar                 (4.4 / 9.6 ngành chu kỳ)
  macro               CPI, core inflation, credit / deposit growth (NSO), VCB 12-month deposit rate   (3.3, 3.5, 5.8)
  insurer_lines       insurers' full statements: investment lines            (5.8 lợi suất đầu tư, tài sản rủi ro)
  intraday            14:25 state and ATC fill per session (DNSE 1m)         (2.3-D same_day_atc)
  broker_reports      broker research list with target prices (Simplize)     (6.5-B consensus, with extract_broker_forecasts)

Usage (thin wrapper around the functions the Dagster assets call, pipeline/assets_rule_data.py):
    python ingest/fetch_rule_data.py block_trades --tickers HPG,VCB
    python ingest/fetch_rule_data.py commodities --backfill
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from core.db import get_conn

HISTORY_START = date(2017, 1, 1)        # VNDirect index / ratio history starts 2017; stock prices go back to 2013
OVERLAP_DAYS = 7
MIN_TICKER_COVERAGE = 0.5               # a per-ticker run where fewer than half the tickers answered is a failure
SIZE_INDICES = ("VN100", "VNMID", "VNSML")
BACKFILL_INDICES = ("VNINDEX", "VN30", "HNX30")   # stored from SSI since 2025-05; older sessions come from VNDirect
VALUATION_INDICES = ("VNINDEX",)

# vn-portfolio sector.yaml commodity_by_sector codes → yfinance front-month futures (2.1 hàng hóa)
COMMODITIES = {
    "brent": ("BZ=F", "USD/bbl"),
    "hrc": ("HRC=F", "USD/short ton"),          # US Midwest HRC steel
    "iron_ore": ("TIO=F", "USD/t"),             # SGX 62% Fe
    "natgas": ("NG=F", "USD/MMBtu"),
    "urea": ("UFV=F", "USD/short ton"),         # granular urea FOB US Gulf (from 2020; the Middle East contract
    "urea_me": ("UME=F", "USD/t"),              # UME=F stopped updating on 2026-07-30, kept for its 2017–2026 history)
    "sugar": ("SB=F", "USc/lb"),
}

# VNDirect statement models read into vnd_financial_items: (model types, report type, sectors to ask; None = all)
VND_ITEM_SETS = {
    "business_plan": ((33,), None, None),                        # AGM plan: revenue, PBT, NPAT (parent) per year
    "broker": ((89, 90), "QUARTER", ("Chứng khoán",)),           # 89/700001 Các khoản cho vay (margin + ứng trước)
    "insurer": ((411, 412), "QUARTER", ("Bảo hiểm",)),           # premiums, claims, expenses, investments
}


# ── helpers ──────────────────────────────────────────────────────────────────

def _active(sectors: tuple[str, ...] | None = None) -> list[str]:
    with get_conn() as conn, conn.cursor() as cur:
        if sectors:
            cur.execute("SELECT ticker FROM securities WHERE is_active AND sector = ANY(%s) ORDER BY ticker",
                        (list(sectors),))
        else:
            cur.execute("SELECT ticker FROM securities WHERE is_active ORDER BY ticker")
        return [r[0] for r in cur.fetchall()]


def _latest(table: str, key: str, date_col: str = "date") -> dict[str, date]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {key}, MAX({date_col}) FROM {table} GROUP BY {key}")
        return dict(cur.fetchall())


def _start(latest: dict, key: str, backfill: bool, floor: date = HISTORY_START) -> date:
    if backfill or key not in latest or latest[key] is None:
        return floor
    return max(floor, latest[key] - timedelta(days=OVERLAP_DAYS))


def _num(x):
    return None if x is None or pd.isna(x) else float(x)


def _int(x):
    return None if x is None or pd.isna(x) else int(round(float(x)))


def _day(x):
    return None if x is None or pd.isna(x) else pd.Timestamp(x).date()


def _executemany(sql: str, rows: list[tuple]) -> int:
    if not rows:
        return 0
    with get_conn() as conn, conn.cursor() as cur:
        cur.executemany(sql, rows)
    return len(rows)


def _per_ticker(name: str, tickers: list[str], fn) -> dict:
    """Run fn(vn, ticker) -> rows written for each ticker; NoData is normal, a run under the coverage floor raises."""
    from vnmarket import VnMarket
    from vnmarket.errors import NoData

    rows, ok, no_data, failed = 0, 0, 0, []
    with VnMarket() as vn:
        for t in tickers:
            try:
                rows += fn(vn, t)
                ok += 1
            except NoData:
                no_data += 1
            except Exception as exc:                       # one ticker failing must not stop the others
                failed.append((t, repr(exc)[:120]))
    if tickers and ok + no_data < MIN_TICKER_COVERAGE * len(tickers):
        raise RuntimeError(f"{name}: only {ok}/{len(tickers)} tickers answered; failed={failed[:10]}")
    if tickers and rows == 0 and ok == 0 and no_data == 0:
        raise RuntimeError(f"{name}: no rows for {len(tickers)} tickers; failed={failed[:10]}")
    return {"rows": rows, "tickers_ok": ok, "no_data": no_data, "failed": len(failed), "failed_sample": failed[:5]}


# ── block trades ─────────────────────────────────────────────────────────────

SESSION_EXCHANGE = {"HSX": "HOSE", "HOSE": "HOSE", "HNX": "HNX", "UPCOM": "UPCOM"}   # VNDirect `floor` of a session


def run_block_trades(tickers: list[str] | None = None, backfill: bool = False) -> dict:
    latest = _latest("block_trades", "ticker")

    def one(vn, t):
        df = vn.market.daily(t, start=str(_start(latest, t, backfill)))
        rows = [(t, _day(r.date), _int(r.pt_volume) or 0, _num(r.pt_value) or 0.0, _int(r.nm_volume) or 0,
                 _num(r.nm_value) or 0.0, SESSION_EXCHANGE.get(getattr(r, "exchange", None)))
                for r in df.itertuples(index=False)]
        return _executemany("""
            INSERT INTO block_trades (ticker, date, pt_volume, pt_value, nm_volume, nm_value, exchange)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (ticker, date) DO UPDATE SET pt_volume=EXCLUDED.pt_volume, pt_value=EXCLUDED.pt_value,
                nm_volume=EXCLUDED.nm_volume, nm_value=EXCLUDED.nm_value,
                exchange=COALESCE(EXCLUDED.exchange, block_trades.exchange), fetched_at=NOW()""", rows)

    return _per_ticker("block_trades", tickers or _active(), one)


# ── long price / foreign-flow history (before the VCI/HOSE feeds start) ─────

PRICE_HISTORY_START = date(2017, 1, 1)   # dividends (raw-price rebuild in vn-portfolio) are stored from 2016-11
FOREIGN_HISTORY_START = date(2018, 9, 30)  # VNDirect foreigns reach back to 2018-08-30


def _vnd_foreign(vn, t: str, end: date) -> pd.DataFrame | None:
    """VNDirect foreign trading up to `end`, 1000 rows per page (the vnmarket fallback chain pages 100 at a time)."""
    from vnmarket.sources import vnd
    parts, page = [], 1
    while True:
        try:
            total, df = vn.market._x(_one(vnd.foreign(t, page, 1000)))
        except Exception:                              # no foreign trading history is normal for small names
            break
        parts.append(df[df["date"] <= pd.Timestamp(end)])
        if df.empty or sum(len(x) for x in parts) >= total or df["date"].min() <= pd.Timestamp(PRICE_HISTORY_START):
            break
        page += 1
    return pd.concat(parts, ignore_index=True) if parts else None


def _one(ep):
    """A one-step vnmarket plan around a single endpoint."""
    return (yield ep)


def run_price_history(tickers: list[str] | None = None) -> dict:
    """ohlcv_daily and foreign_flows sessions before each ticker's first stored day, from VNDirect: adjusted OHLC in
    nghìn đồng and matched volume (identical to the stored VCI series on the overlap: checked 8 tickers × 251
    sessions, max deviation 0.09%), foreign buy/sell in tỷ đồng. Existing rows are never touched (DO NOTHING)."""
    first_px = {}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker, MIN(date) FROM ohlcv_daily GROUP BY ticker")
        first_px = dict(cur.fetchall())
        cur.execute("SELECT ticker, MIN(date) FROM foreign_flows GROUP BY ticker")
        first_ff = dict(cur.fetchall())

    def one(vn, t):
        n = 0
        end = (first_px.get(t) or date.today()) - timedelta(days=1)
        if end > PRICE_HISTORY_START:
            df = vn.market.daily(t, start=str(PRICE_HISTORY_START), end=str(end))
            rows = [(t, _day(r.date), _num(r.adj_open), _num(r.adj_high), _num(r.adj_low), _num(r.adj_close),
                     _int(r.nm_volume) or 0) for r in df.itertuples(index=False)
                    if r.adj_close is not None and not pd.isna(r.adj_close)]
            n += _executemany("""INSERT INTO ohlcv_daily (ticker, date, open, high, low, close, volume)
                                 VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""", rows)
        fend = (first_ff.get(t) or date.today()) - timedelta(days=1)
        if fend > FOREIGN_HISTORY_START:
            ff = _vnd_foreign(vn, t, fend)
            if ff is not None and not ff.empty:
                rows = [(t, _day(r.date), round(float(r.buy_value or 0) / 1e9, 4), round(float(r.sell_value or 0) / 1e9, 4),
                         round(float(r.net_value or 0) / 1e9, 4), _int(r.buy_volume) or 0, _int(r.sell_volume) or 0,
                         _int(r.net_volume) or 0) for r in ff.itertuples(index=False)]
                n += _executemany("""INSERT INTO foreign_flows (ticker, date, buy_value, sell_value, net_value,
                                     buy_volume, sell_volume, net_volume) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                                     ON CONFLICT DO NOTHING""", rows)
        return n

    return _per_ticker("price_history", tickers or _active(), one)


# ── closing board snapshot + covered warrants (no history: collected from 2026-10-29 on) ─

MEMBERSHIP_INDICES = ("VN30", "VN100", "VNMID", "VNSML", "VNX50", "VNDIAMOND", "VNFINLEAD", "VNFINSELECT")


def run_board_close() -> dict:
    """After the close: HOSE stock board (best bid/offer, ceiling, admin status) → board_close_snapshot, covered
    warrants (underlying, last trading / maturity date) → covered_warrants. Raises when the board is not today's."""
    from vnmarket import VnMarket

    with VnMarket() as vn:
        st = vn.market.board_detail("s", "hose")
        cw = vn.market.board_detail("w", "hose")
    day = st["trading_date"].max()
    if pd.isna(day):
        raise RuntimeError("board_close: no trading date on the board")
    rows = [(r.symbol, _day(r.trading_date), _num(r.ref), _num(r.ceiling), _num(r.floor), _num(r.close),
             _int(r.matched_volume), _num(r.bid1), _int(r.bid1_volume), _num(r.offer1), _int(r.offer1_volume),
             r.admin_status or "") for r in st.itertuples(index=False) if r.trading_date == day]
    n = _executemany("""INSERT INTO board_close_snapshot (ticker, date, ref, ceiling, floor, close, matched_volume, bid1,
                            bid1_volume, offer1, offer1_volume, admin_status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (ticker, date) DO UPDATE SET close=EXCLUDED.close,
                            matched_volume=EXCLUDED.matched_volume, bid1=EXCLUDED.bid1, bid1_volume=EXCLUDED.bid1_volume,
                            offer1=EXCLUDED.offer1, offer1_volume=EXCLUDED.offer1_volume,
                            admin_status=EXCLUDED.admin_status, fetched_at=NOW()""", rows)
    w = _executemany("""INSERT INTO covered_warrants (symbol, underlying, issuer, cw_type, exercise_price, exercise_ratio,
                            first_trading_date, last_trading_date, maturity_date) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (symbol) DO UPDATE SET last_trading_date=EXCLUDED.last_trading_date,
                            maturity_date=EXCLUDED.maturity_date, last_seen_at=NOW()""",
                     [(r.symbol, r.underlying or "", r.issuer or "", r.cw_type or "", _num(r.exercise_price),
                       str(r.exercise_ratio or ""), _day(r.first_trading_date), _day(r.last_trading_date),
                       _day(r.maturity_date)) for r in cw.itertuples(index=False) if r.underlying])
    if n == 0:
        raise RuntimeError("board_close: empty stock board")
    members = 0
    with VnMarket() as vn:
        for code in MEMBERSHIP_INDICES:
            try:
                syms = vn.listing.symbols(code)
            except Exception:                              # one basket failing does not stop the snapshot
                continue
            members += _executemany("INSERT INTO index_membership (date, index_code, ticker) VALUES (%s,%s,%s) "
                                    "ON CONFLICT DO NOTHING", [(_day(day), code, t) for t in syms])
    return {"date": str(_day(day)), "stocks": n, "warrants": w, "index_members": members}


# ── government bond yield curve (HNX) ─────────────────────────────────────

YIELD_HISTORY_START = date(2019, 1, 4)


def run_yield_curve(backfill: bool = False) -> dict:
    """HNX yield curve → gov_bond_yields. Backfill: every Friday from 2019 plus the last 10 days; daily otherwise
    the days since the latest stored one. Holidays / weekends answer NoData and are skipped."""
    from vnmarket import VnMarket
    from vnmarket.errors import NoData

    latest = _latest("gov_bond_yields", "1")
    last = max(latest.values()) if latest else None
    today = date.today()
    if backfill or last is None:
        days = [YIELD_HISTORY_START + timedelta(days=7 * k) for k in range((today - YIELD_HISTORY_START).days // 7 + 1)]
        days += [today - timedelta(days=k) for k in range(10)]
    else:
        days = [last + timedelta(days=k) for k in range(1, (today - last).days + 1)]
    n, hit = 0, 0
    with VnMarket() as vn:
        for d in sorted(set(x for x in days if x.weekday() < 5)):
            try:
                df = vn.macro.yield_curve(d)
            except NoData:
                continue
            hit += 1
            n += _executemany("""INSERT INTO gov_bond_yields (date, tenor_years, par_yield, spot_annual, spot_continuous)
                                 VALUES (%s,%s,%s,%s,%s) ON CONFLICT (date, tenor_years) DO UPDATE SET
                                 par_yield=EXCLUDED.par_yield, spot_annual=EXCLUDED.spot_annual,
                                 spot_continuous=EXCLUDED.spot_continuous, fetched_at=NOW()""",
                              [(d, float(r.tenor_years), _num(r.par_yield), _num(r.spot_annual), _num(r.spot_continuous))
                               for r in df.itertuples(index=False)])
    if days and hit == 0 and (backfill or last is None):
        raise RuntimeError("yield_curve: no day answered")
    return {"days": hit, "rows": n}


# ── former HOSE stocks (delisted or moved): backtests without survivorship bias ─

def run_former_hose() -> dict:
    """Stocks VNDirect knows that are not in securities but traded on HOSE at some point since 2017 (delisted, or
    moved to HNX/UPCOM): securities row (is_active = false, tag former_hose), their HOSE sessions in ohlcv_daily
    (adjusted, like the rest) and their dividends (VNDirect events) so vn-portfolio can rebuild raw prices."""
    from vnmarket import VnMarket
    from vnmarket.errors import NoData

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker FROM securities")
        have = {r[0] for r in cur.fetchall()}
    added, sessions, divs, checked = [], 0, 0, 0
    with VnMarket() as vn:
        allst = vn.listing.every_stock()
        cand = allst[(~allst["symbol"].isin(have)) & (allst["status"] != "OTC")
                     & (allst["delisted_date"].isna() | (allst["delisted_date"] >= pd.Timestamp(PRICE_HISTORY_START)))]
        for r in cand.itertuples(index=False):
            checked += 1
            try:
                df = vn.market.daily(r.symbol, start=str(PRICE_HISTORY_START))
            except NoData:
                continue
            hose = df[df["exchange"] == "HSX"]
            if hose.empty:
                continue
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("""INSERT INTO securities (ticker, exchange, company_name, is_active, tags)
                               VALUES (%s, 'HOSE', %s, false, ARRAY['former_hose']) ON CONFLICT (ticker) DO NOTHING""",
                            (r.symbol, r.name_vi or ""))
            sessions += _executemany("""INSERT INTO ohlcv_daily (ticker, date, open, high, low, close, volume)
                                        VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                                     [(r.symbol, _day(b.date), _num(b.adj_open), _num(b.adj_high), _num(b.adj_low),
                                       _num(b.adj_close), _int(b.nm_volume) or 0) for b in hose.itertuples(index=False)
                                      if b.adj_close is not None and not pd.isna(b.adj_close)])
            try:
                acts = vn.company.actions(r.symbol)
            except NoData:
                acts = pd.DataFrame()
            rows = []
            for a in acts.itertuples(index=False) if not acts.empty else []:
                ex = _day(a.effective_date)
                if ex is None:
                    continue
                if a.type == "DIVIDEND" and a.dividend:
                    rows.append((r.symbol, "cash", ex, _day(a.expired_date), float(a.dividend), None, a.note or ""))
                elif a.type in ("STOCKDIV", "KINDDIV") and a.ratio:
                    rows.append((r.symbol, "stock", ex, None, None, float(a.ratio), a.note or ""))
            divs += _executemany("""INSERT INTO dividends (ticker, kind, ex_date, payment_date, cash_per_share,
                                    stock_ratio_pct, note, source) VALUES (%s,%s,%s,%s,%s,%s,%s,'vndirect')
                                    ON CONFLICT DO NOTHING""", rows)
            added.append(r.symbol)
    return {"checked": checked, "former_hose": len(added), "sessions": sessions, "dividends": divs,
            "sample": added[:15]}


# ── indices: size indices + history before the SSI feed + valuation ─────────

def run_size_indices(backfill: bool = False) -> dict:
    """VN100/VNMID/VNSML upserted in full; VNINDEX/VN30/HNX30 only fill sessions the table does not have (SSI stays the
    primary for days it already holds). Values in VND, like the SSI rows."""
    from vnmarket import VnMarket

    latest = _latest("market_index_daily", "index_code")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT index_code, MIN(date) FROM market_index_daily GROUP BY index_code")
        first = dict(cur.fetchall())
    out = {}
    with VnMarket() as vn:
        for code in SIZE_INDICES + BACKFILL_INDICES:
            fill_only = code in BACKFILL_INDICES
            if fill_only and not backfill and first.get(code) and first[code] <= HISTORY_START + timedelta(days=60):
                out[code] = 0                                # history already there
                continue
            start = HISTORY_START if fill_only else _start(latest, code, backfill)
            df = vn.market.index_history(code, start=str(start))
            df = df.sort_values("date")
            prev = df["close"].shift(1)
            rows = [(code, _day(r.date), _num(r.open), _num(r.high), _num(r.low), _num(r.close),
                     round(r.close - p, 4) if pd.notna(p) else 0.0, round((r.close / p - 1) * 100, 4) if pd.notna(p) and p else 0.0,
                     _int(r.volume) or 0, _num(r.value))
                    for r, p in zip(df.itertuples(index=False), prev)]
            conflict = "DO NOTHING" if fill_only else """DO UPDATE SET open=EXCLUDED.open, high=EXCLUDED.high,
                low=EXCLUDED.low, close=EXCLUDED.close, change_pts=EXCLUDED.change_pts, change_pct=EXCLUDED.change_pct,
                matched_volume=EXCLUDED.matched_volume, matched_value=EXCLUDED.matched_value, fetched_at=NOW()"""
            out[code] = _executemany(f"""
                INSERT INTO market_index_daily (index_code, date, open, high, low, close, change_pts, change_pct,
                    matched_volume, matched_value) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (index_code, date) {conflict}""", rows)
    if not any(out[c] for c in SIZE_INDICES) and backfill:
        raise RuntimeError(f"size_indices: nothing written {out}")
    return out


def run_index_valuation(backfill: bool = False) -> dict:
    from vnmarket import VnMarket

    latest = _latest("index_valuation", "index_code")
    out = {}
    with VnMarket() as vn:
        for code in VALUATION_INDICES:
            df = vn.market.valuation(code, start=str(_start(latest, code, backfill)))
            rows = [(code, _day(r.date), _num(getattr(r, "pe", None)), _num(getattr(r, "pb", None)))
                    for r in df.itertuples(index=False)]
            out[code] = _executemany("""
                INSERT INTO index_valuation (index_code, date, pe, pb) VALUES (%s,%s,%s,%s)
                ON CONFLICT (index_code, date) DO UPDATE SET pe=EXCLUDED.pe, pb=EXCLUDED.pb, fetched_at=NOW()""", rows)
    if not any(out.values()):
        raise RuntimeError(f"index_valuation: nothing written {out}")
    return out


# ── foreign room ─────────────────────────────────────────────────────────────

def run_foreign_room(tickers: list[str] | None = None, backfill: bool = False) -> dict:
    latest = _latest("foreign_room", "ticker")

    def one(vn, t):
        df = vn.market.foreign_room(t, start=str(_start(latest, t, backfill)))
        rows = [(t, _day(r.date), _int(r.total_room), _int(r.room_left)) for r in df.itertuples(index=False)
                if r.total_room is not None or r.room_left is not None]
        return _executemany("""
            INSERT INTO foreign_room (ticker, date, total_room, room_left) VALUES (%s,%s,%s,%s)
            ON CONFLICT (ticker, date) DO UPDATE SET total_room=EXCLUDED.total_room, room_left=EXCLUDED.room_left,
                fetched_at=NOW()""", rows)

    return _per_ticker("foreign_room", tickers or _active(), one)


# ── shareholders ─────────────────────────────────────────────────────────────

def run_shareholders(tickers: list[str] | None = None) -> dict:
    def one(vn, t):
        df = vn.company.shareholders(t)
        rows = [(t, (r.name or "").strip(), r.holder_type or "", r.role or "", _int(r.shares), _num(r.pct),
                 _num(r.group_pct), _day(r.effective_date)) for r in df.itertuples(index=False) if (r.name or "").strip()]
        return _executemany("""
            INSERT INTO shareholders (ticker, name, holder_type, role, shares, pct, group_pct, effective_date)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (ticker, source, name, effective_date) DO UPDATE SET holder_type=EXCLUDED.holder_type,
                role=EXCLUDED.role, shares=EXCLUDED.shares, pct=EXCLUDED.pct, group_pct=EXCLUDED.group_pct,
                updated_at=NOW()""", rows)

    return _per_ticker("shareholders", tickers or _active(), one)


# ── corporate actions (market-wide, by disclosure date) ──────────────────────

def _upsert_actions(df: pd.DataFrame) -> int:
    rows = [(r.id, r.symbol, r.group or "", r.type or "", r.type_desc or "", r.note or "", _num(r.price),
             _num(r.ratio), _int(r.shares), _day(r.disclosure_date), _day(r.effective_date),
             _day(r.expired_date), _day(r.actual_date), _day(r.trading_start))
            for r in df.itertuples(index=False) if r.id and r.symbol]
    return _executemany("""
        INSERT INTO corporate_actions (id, ticker, action_group, action_type, type_desc, note, price, ratio,
            shares, disclosure_date, effective_date, expired_date, actual_date, trading_start)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (id) DO UPDATE SET note=EXCLUDED.note, price=EXCLUDED.price, ratio=EXCLUDED.ratio,
            shares=EXCLUDED.shares, effective_date=EXCLUDED.effective_date, expired_date=EXCLUDED.expired_date,
            actual_date=EXCLUDED.actual_date, trading_start=EXCLUDED.trading_start, fetched_at=NOW()""", rows)


def run_corporate_actions(tickers: list[str] | None = None, backfill: bool = False, days_back: int = 30) -> dict:
    """Daily: market-wide rows disclosed in the last `days_back` days. Backfill: every ticker's full history (the
    market-wide query only reaches recent years; per-ticker queries go back to ~2016)."""
    if backfill or tickers:
        return _per_ticker("corporate_actions", tickers or _active(),
                           lambda vn, t: _upsert_actions(vn.company.actions(t)))
    from vnmarket import VnMarket
    with VnMarket() as vn:
        df = vn.company.actions(None, start=str(date.today() - timedelta(days=days_back)),
                                end=str(date.today() + timedelta(days=60)))
    n = _upsert_actions(df)
    if n == 0:
        raise RuntimeError("corporate_actions: no rows in the window")
    return {"rows": n}


# ── VNDirect statement items (plans, brokers, insurers) ──────────────────────

def run_vnd_items(sets: tuple[str, ...] = tuple(VND_ITEM_SETS), tickers: list[str] | None = None) -> dict:
    out = {}
    for name in sets:
        models, report_type, sectors = VND_ITEM_SETS[name]

        def one(vn, t, models=models, report_type=report_type):
            df = vn.finance.items(t, list(models), report_type)
            rows = [(t, _day(r.fiscal_date), r.report_type or "", int(r.model_type), int(r.item_code), _num(r.value),
                     None if pd.isna(r.created) else r.created.to_pydatetime(),
                     None if pd.isna(r.modified) else r.modified.to_pydatetime()) for r in df.itertuples(index=False)]
            return _executemany("""
                INSERT INTO vnd_financial_items (ticker, fiscal_date, report_type, model_type, item_code, value, created,
                    modified) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (ticker, fiscal_date, report_type, model_type, item_code) DO UPDATE SET value=EXCLUDED.value,
                    created=COALESCE(vnd_financial_items.created, EXCLUDED.created), modified=EXCLUDED.modified,
                    fetched_at=NOW()""", rows)

        out[name] = _per_ticker(f"vnd_items:{name}", tickers or _active(sectors), one)
    return out


# ── FireAnt structured values (bank loan groups, broker loans) ──────────────

FIREANT_BANK_FIELDS = ("StandardDebt", "WatchlistDebt", "SubstandardDebt", "DoubtfulDebt", "BadDebt", "CustomerLoan",
                       "DepositOfCustomer", "ProvisionForCustomerLoanLoss", "NPLToLoan", "LoanlossReservesToNPL",
                       "LoanlossReservesToLoan", "ProvisionChargesToLoan")
FIREANT_BROKER_FIELDS = ("Loan", "SectorLoan", "ShorttermIssuedBond", "LongtermIssuedBond")
# FireAnt field → (financial_facts vnstock metric code vn-portfolio reads, sign); portfolio/market_data.py fills the
# periods vnstock does not have (history before 2024) with these. Checked against vnstock on the 2024–2026 overlap
# (10 tickers, 50–244 quarter pairs per field, ±1% or ±1 tỷ): equal in 87–100% of pairs, the rest Q4 restatements.
# Left out: CorporateIncomeTax (11% equal — different definition), GoodWill (0%), StockHolderEquity (bank = capital).
FIREANT_TO_VNSTOCK = {
    "NetSale": ("doanh_thu_thuan", 1), "CostOfGoodSold": ("gia_von_hang_ban", -1), "GrossProfit": ("loi_nhuan_gop", 1),
    "ProfitBeforeTax": ("loi_nhuan_truoc_thue", 1), "ProfitAfterTax": ("loi_nhuan_sau_thue", 1),
    "ParentCompanyShareholderProfitAfterTax": ("loi_nhuan_cua_co_ong_cua_cong_ty_me", 1),
    "InterestExpense": ("chi_phi_lai_vay", 1), "OtherProfit": ("thu_nhap_khac_rong", 1),
    "TotalAsset": ("tong_tai_san", 1), "TotalStockHolderEquity": ("von_chu_so_huu", 1),
    "ShortTermInterestBearingDebt": ("vay_ngan_han", 1), "LongTermInterestBearingDebt": ("vay_dai_han", 1),
    "ShortTermAccountReceivable": ("phai_thu_khach_hang", 1), "TotalInventory": ("hang_ton_kho_rong", 1),
    "CashflowFromOperatingActivity": ("dong_tien_hoat_dong", 1), "PaidInCapital": ("von_dieu_le", 1),
    "NetInterestIncome": ("thu_nhap_lai_thuan", 1), "TotalOperatingIncome": ("tong_thu_nhap_hoat_ong", 1),
    "CreditRiskProvisionsExpense": ("trich_lap_du_phong_ton_that_tin_dung", 1),
    "DepositOfCustomer": ("tien_gui_cua_khach_hang", 1),
}
FIREANT_EXTRA = ("Cash", "CashEquivalent", "StockHolderEquity")   # cash = Cash + CashEquivalent (tien_va_tuong_duong_tien)


def run_fireant_data(tickers: list[str] | None = None, count: int = 60) -> dict:
    """FireAnt values per quarter (from 2011) and year → fireant_financial_data (needs a FireAnt token): the mapped
    statement lines for every ticker, loan groups for banks, loans / issued bonds for brokers."""
    return {"all": _per_ticker("fireant_data", tickers or _active(), lambda vn, t: fireant_data_one(vn, t, count))}


FIREANT_FIELDS = set(FIREANT_TO_VNSTOCK) | set(FIREANT_EXTRA) | set(FIREANT_BANK_FIELDS) | set(FIREANT_BROKER_FIELDS)


def fireant_data_one(vn, t: str, count: int = 60) -> int:
    """One ticker: `count` quarters and count/4 + 2 years of FireAnt values → fireant_financial_data (rows upserted).
    Raises NoData when FireAnt has nothing for the ticker."""
    n = 0
    for period, cnt in (("Q", count), ("Y", count // 4 + 2)):
        df = vn.finance.data(t, period, cnt)
        rows = [(t, int(r["year"]), int(r["quarter"]) if period == "Q" else 0, f, _num(r.get(f)))
                for _, r in df.iterrows() for f in FIREANT_FIELDS if f in df.columns and _num(r.get(f)) is not None]
        n += _executemany("""
            INSERT INTO fireant_financial_data (ticker, year, quarter, field, value) VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT (ticker, year, quarter, field) DO UPDATE SET value=EXCLUDED.value, fetched_at=NOW()""", rows)
    return n


def run_fireant_holders(tickers: list[str] | None = None) -> dict:
    def one(vn, t):
        df = vn.company.holders(t)
        rows = [(t, (r.name or "").strip(), "COMPANY" if r.is_organization else "PERSON", r.position or "",
                 _int(r.shares), None if r.ownership is None or pd.isna(r.ownership) else float(r.ownership) * 100,
                 _day(r.reported), "fireant") for r in df.itertuples(index=False) if (r.name or "").strip()]
        return _executemany("""
            INSERT INTO shareholders (ticker, name, holder_type, role, shares, pct, effective_date, source)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (ticker, source, name, effective_date) DO UPDATE SET holder_type=EXCLUDED.holder_type,
                role=EXCLUDED.role, shares=EXCLUDED.shares, pct=EXCLUDED.pct, updated_at=NOW()""", rows)

    return _per_ticker("fireant_holders", tickers or _active(), one)


# ── commodities ──────────────────────────────────────────────────────────────

def run_commodities(backfill: bool = False) -> dict:
    import yfinance as yf

    latest = _latest("commodity_daily", "code")
    out = {}
    for code, (ticker, unit) in COMMODITIES.items():
        start = _start(latest, code, backfill, floor=date(2015, 1, 1))
        try:
            hist = yf.Ticker(ticker).history(start=str(start), auto_adjust=False)
        except Exception as exc:
            out[code] = f"error: {exc!r}"[:120]
            continue
        rows = [(code, pd.Timestamp(d).date(), float(c), unit, f"yfinance:{ticker}")
                for d, c in hist["Close"].dropna().items()] if not hist.empty else []
        out[code] = _executemany("""
            INSERT INTO commodity_daily (code, date, close, unit, source) VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT (code, date) DO UPDATE SET close=EXCLUDED.close, fetched_at=NOW()""", rows)
    if not any(isinstance(v, int) and v for v in out.values()):
        raise RuntimeError(f"commodities: nothing written {out}")
    return out


# ── macro: Cục Thống kê releases + Vietcombank deposit rate ──────────────────

NSO_SEARCHES = (("chỉ số giá tiêu dùng", r"^Chỉ số giá tiêu dùng"),
                ("tình hình kinh tế xã hội", r"(Thông cáo báo chí|Báo cáo).*tình hình kinh tế\s*[–-]\s*xã hội"))
NSO_HISTORY_START = date(2019, 1, 1)
VN_MONTHS = {"một": 1, "hai": 2, "ba": 3, "tư": 4, "năm": 5, "sáu": 6, "bảy": 7, "tám": 8, "chín": 9, "mười": 10,
             "mười một": 11, "mười hai": 12}
_PCT = r"([\d]+(?:[.,]\d+)?)\s*%"


def _pct(x: str) -> float:
    return float(x.replace(",", "."))


def _month_of(title: str, published: date) -> tuple[int, int] | None:
    """(year, month) a CPI release is about: 'tháng Chín … năm 2026', 'tháng 12 năm 2023', 'tháng Mười Hai, quý IV và
    năm 2025'. Without a year in the title: the month before publication."""
    t = title.lower()
    m = re.search(r"tháng\s+(mười hai|mười một|một|hai|ba|tư|năm|sáu|bảy|tám|chín|mười|\d{1,2})\b", t)
    if not m:
        return None
    w = m.group(1)
    month = int(w) if w.isdigit() else VN_MONTHS[w]
    y = re.search(r"năm\s+(\d{4})", t)
    year = int(y.group(1)) if y else (published.year if month <= published.month else published.year - 1)
    return year, month


def _month_end(year: int, month: int) -> date:
    return (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1))


def parse_cpi(text: str) -> dict[str, tuple[float, str]]:
    """The month's CPI sentences → {cpi_mom, cpi_ytd, cpi_yoy: (percent, sentence)}; core_yoy from 'Lạm phát cơ bản
    tháng X … so với cùng kỳ'. The figures may sit in one sentence or several ('So với tháng 12/2025, CPI tháng Chín
    tăng 4,20%; tăng 5,08% so với cùng kỳ'). Only sentences about 'CPI tháng …'; period figures (bình quân, quý, 'tính
    chung 5 tháng', 'CPI sáu tháng') are skipped — they were read as the month before (05/2026, 08/2021, 06/2024).
    Signs from tăng / giảm."""
    out: dict[str, tuple[float, str]] = {}
    for sent in re.split(r"(?<=\.)\s+", text):
        low = sent.lower()
        if (re.search(r"cpi\)?\s+tháng\s+(?:\d|[^\W\d_])", low)
                and not re.search(r"bình quân|quý|lạm phát cơ bản|tính chung|\d+\s+tháng\s+(?:đầu\s+)?năm|"
                                  r"(?:sáu|chín|mười)\s+tháng|tại mỹ|của mỹ|trung quốc|nhật bản|hàn quốc|khu vực đồng euro|"
                                  r"eurozone|thế giới|nước anh|tại anh|ấn độ|thái lan|indonesia", low)):
            clauses = re.findall(r"(tăng|giảm)\s+" + _PCT + r"\s*(so với [^;,.]*?(?:trước|\d{4}))", sent)
            # 'So với tháng 12/2025, CPI tháng Chín tăng 4,20%' — the comparison comes first
            lead = re.match(r"\s*So với (tháng 12/\d{4}|cùng kỳ năm trước|tháng trước),\s*CPI[^%]*?(tăng|giảm)\s+" + _PCT, sent)
            if lead:
                clauses.insert(0, (lead.group(2), lead.group(3), "so với " + lead.group(1)))
            for verb, num, ctx in clauses:
                key = ("cpi_mom" if "tháng trước" in ctx else "cpi_ytd" if "tháng 12" in ctx else
                       "cpi_yoy" if "cùng kỳ" in ctx else None)
                if key and key not in out:
                    out[key] = (_pct(num) * (-1 if verb == "giảm" else 1), sent.strip()[:400])
        if re.match(r"\s*Lạm phát cơ bản(\s*\[\d+\])?\s+tháng", sent) and "core_yoy" not in out:
            m = re.search(r"(tăng|giảm)\s+" + _PCT + r"\s*so với cùng kỳ", sent)
            if m:
                out["core_yoy"] = (_pct(m.group(2)) * (-1 if m.group(1) == "giảm" else 1), sent.strip()[:400])
    return out


def parse_credit(text: str) -> tuple[date, dict[str, tuple[float, str]]] | None:
    """'Tính đến thời điểm 28/9/2026, huy động vốn của các tổ chức tín dụng tăng 9,78% (cùng thời điểm năm trước tăng
    9,99%); tăng trưởng tín dụng của nền kinh tế đạt 10,89% (cùng thời điểm năm trước tăng 13,37%).'"""
    m = re.search(r"Tính đến (?:thời điểm |ngày )?(\d{1,2})/(\d{1,2})/(\d{4})[^.]*?tín dụng[^.]*\.", text)
    if not m:
        return None
    sent, day = m.group(0), date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    out: dict[str, tuple[float, str]] = {}
    prev = r"(?:\s*so với (?:cuối năm|tháng 12/)\s*\d{4})?(?:\s*\(cùng (?:thời điểm|kỳ) năm trước (?:tăng|đạt)\s+" + _PCT + r"\))?"
    d = re.search(r"huy động vốn[^%;]{0,90}?(?:tăng|đạt)\s+" + _PCT + prev, sent)
    c = re.search(r"(?:tăng trưởng tín dụng|dư nợ tín dụng|tín dụng (?:của )?(?:nền kinh tế|toàn hệ thống))[^%;]{0,60}?"
                  r"(?:đạt|tăng)\s+" + _PCT + prev, sent)
    for key, mm in (("deposit_growth_ytd", d), ("credit_ytd", c)):
        if mm:
            out[key] = (_pct(mm.group(1)), sent.strip()[:400])
            if mm.group(2):
                out[key + "_prev"] = (_pct(mm.group(2)), sent.strip()[:400])
    return (day, out) if out else None


def _store_macro(rows: list[tuple]) -> int:
    return _executemany("""INSERT INTO macro_indicators (code, period_end, value, published_at, source, quote)
                           VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (code, period_end, source) DO UPDATE SET
                           value=EXCLUDED.value, published_at=LEAST(macro_indicators.published_at, EXCLUDED.published_at),
                           quote=EXCLUDED.quote, fetched_at=NOW()""", rows)


VBMA_TENORS = ("2w", "3w", "1m", "2m", "3m", "6m", "9m", "1y")


def vbma_rows(df: pd.DataFrame, since: date | None) -> list[tuple]:
    """VBMA short-term benchmark rates → macro_indicators rows stb_<tenor>. The file has day D by 09:20 of the next
    morning (checked 30/10/2026); published_at = D + 1 day 09:00, i.e. usable from the next session, never D itself."""
    tz = "Asia/Ho_Chi_Minh"
    out = []
    for r in df.to_dict("records"):
        d = r["date"].date()
        if since and d < since:
            continue
        pub = (pd.Timestamp(d) + pd.Timedelta(days=1, hours=9)).tz_localize(tz).to_pydatetime()
        for tenor in VBMA_TENORS:
            v = r.get(tenor)
            if v is not None and not pd.isna(v):
                out.append((f"stb_{tenor}", d, float(v), pub, "vbma", "VBMA lãi suất tham chiếu ngắn hạn " + tenor))
    return out


VBMA_MACRO = {"Lạm phát": ("cpi_yoy", 10), "Lạm phát cơ bản": ("core_yoy", 10), "Tăng trưởng tín dụng": ("credit_ytd", 35)}


def vbma_macro_rows(df: pd.DataFrame) -> list[tuple]:
    """VBMA monthly macro table → macro_indicators (source vbma). The table has no release dates, so each month is
    usable only `lag` days after its end (CPI: NSO publishes ~6th of the next month → 10; credit: SBV ~mid next month
    → 35). credit_ytd_prev = the same month a year earlier, as the NSO rows carry it."""
    tz = "Asia/Ho_Chi_Minh"
    out, credit = [], {}
    for r in df.to_dict("records"):
        if r["indicator"] not in VBMA_MACRO:
            continue
        code, lag = VBMA_MACRO[r["indicator"]]
        pe = r["period"].date()
        pub = (pd.Timestamp(pe) + pd.Timedelta(days=lag, hours=9)).tz_localize(tz).to_pydatetime()
        out.append((code, pe, float(r["value"]), pub, "vbma", f"VBMA tổng quan vĩ mô: {r['indicator']} ({r['unit']})"))
        if code == "credit_ytd":
            credit[pe] = (float(r["value"]), pub)
    for pe, (_, pub) in credit.items():
        prev = credit.get((pd.Timestamp(pe) - pd.DateOffset(years=1) + pd.offsets.MonthEnd(0)).date())
        if prev:
            out.append(("credit_ytd_prev", pe, prev[0], pub, "vbma", "VBMA tổng quan vĩ mô: tín dụng cùng kỳ năm trước"))
    return out


def run_macro(backfill: bool = False) -> dict:
    """NSO CPI releases (monthly) and socio-economic press releases (credit / deposit growth) → macro_indicators, with
    the post time as published_at and the sentence as quote; plus today's Vietcombank 12-month VND deposit rate and
    VBMA's short-term benchmark rates (all since 06/2020 on backfill, else the last 30 days).
    Backfill pages back to 2019; otherwise the newest 20 posts of each search."""
    from vnmarket import VnMarket
    from vnmarket.errors import NoData, SourceError

    tz = "Asia/Ho_Chi_Minh"
    rows, posts, unparsed = [], 0, []
    with VnMarket() as vn:
        seen: set[int] = set()
        for search, title_rx in NSO_SEARCHES:
            for page in range(1, 40 if backfill else 2):
                try:
                    df = vn.macro.statistics_posts(search, page, 100 if backfill else 30)
                except (NoData, SourceError):             # WordPress answers 400 past the last page
                    break
                for r in df.itertuples(index=False):
                    if r.id in seen or not re.search(title_rx, r.title, re.I):
                        continue
                    seen.add(r.id)
                    post = vn.macro.statistics_post(int(r.id))
                    pub = pd.Timestamp(post["published"])
                    pub = pub.tz_localize(tz) if pub.tzinfo is None else pub
                    posts += 1
                    found = 0
                    ym = _month_of(r.title, pub.date()) if title_rx.startswith("^Chỉ số") else None
                    if ym:
                        for code, (v, q) in parse_cpi(post["text"]).items():
                            rows.append((code, _month_end(*ym), v, pub.to_pydatetime(), f"nso:{r.id}", q)); found += 1
                    cr = parse_credit(post["text"])
                    if cr:
                        for code, (v, q) in cr[1].items():
                            rows.append((code, cr[0], v, pub.to_pydatetime(), f"nso:{r.id}", q)); found += 1
                    if not found:
                        unparsed.append(r.title[:80])
                if df["published"].min().date() < NSO_HISTORY_START:
                    break
        rows += vbma_rows(vn.macro.short_term_rates(), None if backfill else date.today() - timedelta(days=30))
        rows += vbma_macro_rows(vn.macro.macro_overview())
        try:
            dep = vn.macro.deposit_rates()
            x = dep[(dep.currency == "VND") & (dep.tenor_type == "Savings") & (dep.tenor == "12-months")]
            if not x.empty:
                upd = pd.Timestamp(x.updated.iloc[0])
                rows.append(("deposit_12m_vcb", upd.tz_convert(tz).date(), float(x.rate.iloc[0]) * 100,
                             upd.to_pydatetime(), "vcb", "Vietcombank, tiết kiệm 12 tháng VND (cá nhân)"))
        except NoData:
            pass
    n = _store_macro(rows)
    if n == 0:
        raise RuntimeError(f"macro: nothing parsed from {posts} posts; unparsed={unparsed[:5]}")
    return {"posts": posts, "rows": n, "unparsed": len(unparsed), "unparsed_sample": unparsed[:5]}


WAYBACK_CDX = "http://web.archive.org/cdx/search/cdx"
VCB_RATE_PAGES = ("portal.vietcombank.com.vn/UserControls/TVPortal.TyGia/pListLaiSuat.aspx*",
                  "vietcombank.com.vn/vi-VN/KHCN/Cong-cu-Tien-ich/KHCN---Lai-suat")


def vcb_rate_from_page(text: str) -> tuple[float, str | None] | None:
    """12-month VND savings rate (%) from an archived Vietcombank page: the current page's hidden JSON
    (currentDataInterestRate, with UpdatedDate) or the old pListLaiSuat table ('12 months 7.40%')."""
    import json as _json
    m = re.search(r'id="currentDataInterestRate" value="([^"]+)"', text)
    if m:
        j = _json.loads(html_unescape(m.group(1)))
        for x in j.get("Data") or []:
            if x.get("tenorType") == "Savings" and x.get("tenor") == "12-months" and x.get("currencyCode") == "VND":
                return float(x["rates"]) * 100, j.get("UpdatedDate")
        return None
    plain = re.sub(r"\s+", " ", html_unescape(re.sub(r"<[^>]+>", " ", text)))
    m = re.search(r"Savings.*?12 (?:months|tháng)\s+([\d.,]+)\s*%", plain)
    return (float(m.group(1).replace(",", ".")), None) if m else None


def html_unescape(s: str) -> str:
    import html as _html
    return _html.unescape(s)


def run_deposit_history() -> dict:
    """Vietcombank's 12-month VND savings rate from archive.org captures of its rate pages (2021–, ~monthly) →
    macro_indicators deposit_12m_vcb, source vcb_wayback. Known from VCB's own update time when the page carries it,
    else from the capture time. The daily collector (run_macro) stores today's rate from now on."""
    import httpx
    tz = "Asia/Ho_Chi_Minh"
    rows, pages, failed = [], 0, []
    for url in VCB_RATE_PAGES:
        caps = httpx.get(WAYBACK_CDX, params={"url": url, "output": "json", "filter": "statuscode:200",
                                              "collapse": "timestamp:8"}, timeout=120).json()[1:]
        for cap in caps:
            ts, orig = cap[1], cap[2]
            if "CusstomType" in orig and "CusstomType=4" not in orig and "CusstomType=1" not in orig:
                continue
            time.sleep(2)
            try:
                text = httpx.get(f"http://web.archive.org/web/{ts}id_/{orig}", timeout=120).text
                got = vcb_rate_from_page(text)
            except Exception as e:
                failed.append((ts, repr(e)[:60]))
                continue
            pages += 1
            if not got:
                continue
            captured = pd.Timestamp(ts, tz="UTC")
            known = pd.Timestamp(got[1]) if got[1] else captured
            known = min(known, captured)
            rows.append(("deposit_12m_vcb", known.tz_convert(tz).date(), got[0], known.to_pydatetime(), "vcb_wayback",
                         f"archive.org {ts}: Vietcombank tiết kiệm 12 tháng VND {got[0]:g}%"))
    return {"pages": pages, "rows": _store_macro(rows), "failed": failed[:5]}


FX_HISTORY_START = date(2020, 2, 3)       # first Vietcombank table its website API returns


def run_fx_history(start: date | None = None) -> dict:
    """Vietcombank USD/VND of every past weekday (that day's last table) → daily_snapshots vcb_usd (buy = cash buy, as
    the daily collector in tools/global_market.py stores it). Fills only missing days; the collector's own rows stay."""
    from vnmarket import VnMarket
    from vnmarket.errors import NoData, SourceError

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT snapshot_date FROM daily_snapshots WHERE series = 'vcb_usd'")
        have = {r[0] for r in cur.fetchall()}
    d, end, rows, empty = start or FX_HISTORY_START, date.today() - timedelta(days=1), [], 0
    with VnMarket() as vn:
        while d <= end:
            if d.weekday() < 5 and d not in have:
                try:
                    usd = vn.macro.fx_on(d)
                    usd = usd[usd.currency == "USD"]
                    if not usd.empty and usd.sell.iloc[0]:
                        rows.append((d, _num(usd.buy_cash.iloc[0]), _num(usd.sell.iloc[0])))
                except (NoData, SourceError):
                    empty += 1
            d += timedelta(days=1)
    n = _executemany("""INSERT INTO daily_snapshots (snapshot_date, series, buy, sell, unit, source)
                        VALUES (%s, 'vcb_usd', %s, %s, 'VND/USD', 'vietcombank') ON CONFLICT DO NOTHING""", rows)
    return {"rows": n, "empty_days": empty}


# ── insurers: full statements (investment lines) ─────────────────────────────

def run_insurer_lines(tickers: list[str] | None = None, backfill: bool = False) -> dict:
    """FireAnt full balance sheet + income statement of the insurers (40 quarters on backfill, the last 6 otherwise)
    → fireant_statement_lines: investment yield (financial income / investments) and risky-asset share (khung 5.8)."""
    tickers = tickers or _active(("Bảo hiểm",))
    limit = 40 if backfill else 6

    def one(vn, t: str) -> int:
        n = 0
        for kind in (1, 2):
            df = vn.finance.full_report(t, kind, date.today().year, 4, limit)
            df = df[df["value"].notna()]
            n += _executemany("""INSERT INTO fireant_statement_lines (ticker, kind, year, quarter, item_id, parent_id,
                                 level, name, value) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                                 ON CONFLICT (ticker, kind, year, quarter, item_id) DO UPDATE SET value=EXCLUDED.value,
                                 name=EXCLUDED.name, fetched_at=NOW()""",
                              [(t, kind, int(r.year), int(r.quarter), int(r.item_id), _int(r.parent_id), _int(r.level),
                                r.name, _num(r.value)) for r in df.itertuples(index=False)])
        return n

    return _per_ticker("insurer_lines", tickers, one)


# ── intraday: 14:25 state and the ATC fill (entry_mode same_day_atc) ─────────

SNAPSHOT_AT = "14:25"
ATC_AT = "14:45"
INTRADAY_HISTORY_START = date(2023, 9, 11)  # first 1-minute bar Vietcap keeps (DNSE / SSI: about three months)
INTRADAY_CHUNK_DAYS = 150                   # ≈ 110 sessions ≈ 30k bars, half of one Vietcap call's limit
INTRADAY_INDICES = ("VNINDEX", "VN30")      # index state at 14:25 (Vietcap has no VNMID / VNSML minutes)
INTRADAY_DAY_DONE = "14:50"                 # today's session counts once the ATC bar (14:45) is out


def intraday_rows(t: str, bars: pd.DataFrame, source: str = "dnse") -> list[tuple]:
    """1-minute bars of several sessions → one row per session: what was known before 14:25 and the ATC fill."""
    out = []
    bars = bars.copy()
    bars["d"] = bars["time"].dt.date
    for d, g in bars.groupby("d"):
        hm = g["time"].dt.strftime("%H:%M")
        pre = g[hm < SNAPSHOT_AT]
        atc = g[hm == ATC_AT]
        if pre.empty:
            continue
        out.append((t, d, _num(g["open"].iloc[0]), _num(pre["high"].max()), _num(pre["low"].min()),
                    _num(pre["close"].iloc[-1]), int(pre["volume"].sum()),
                    _num(atc["close"].iloc[0]) if not atc.empty else None,
                    int(atc["volume"].iloc[0]) if not atc.empty else None,
                    _num(g["close"].iloc[-1]), int(g["volume"].sum()), source))
    return out


def run_intraday(tickers: list[str] | None = None, backfill: bool = False, since: date | None = None) -> dict:
    """After the close: every active ticker's and INTRADAY_INDICES' 1-minute bars (last 5 days, or from 11/09/2023 on
    backfill, in INTRADAY_CHUNK_DAYS pieces) → intraday_snapshot. Today's session is stored only after the ATC
    (INTRADAY_DAY_DONE), so a run during trading hours never saves a morning price as the 14:25 state."""
    tickers = tickers or [*_active(), *INTRADAY_INDICES]
    now = pd.Timestamp.now(tz="Asia/Ho_Chi_Minh")
    end = now.date() if now.strftime("%H:%M") >= INTRADAY_DAY_DONE else now.date() - timedelta(days=1)
    start = since or (INTRADAY_HISTORY_START if backfill else end - timedelta(days=5))
    chunks = []
    while start <= end:
        chunks.append((start, min(end, start + timedelta(days=INTRADAY_CHUNK_DAYS - 1))))
        start = chunks[-1][1] + timedelta(days=1)

    def one(vn, t: str) -> int:
        from vnmarket.errors import NoData, SourceError
        n, answered, errors = 0, False, []
        for a, b in chunks:
            try:
                bars = vn.prices.history(t, start=a, end=b, interval="1m")
            except NoData:
                continue
            except SourceError as e:                       # every source empty (not listed yet / suspended) or down
                if "HTTP" in str(e):
                    errors.append(str(e)[:80])
                continue
            answered = True
            n += _executemany("""INSERT INTO intraday_snapshot (ticker, date, open, high_1425, low_1425, price_1425,
                                 volume_1425, atc_price, atc_volume, close, volume, source)
                                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (ticker, date) DO UPDATE SET
                                 open=EXCLUDED.open, high_1425=EXCLUDED.high_1425, low_1425=EXCLUDED.low_1425,
                                 price_1425=EXCLUDED.price_1425, volume_1425=EXCLUDED.volume_1425,
                                 atc_price=EXCLUDED.atc_price, atc_volume=EXCLUDED.atc_volume, close=EXCLUDED.close,
                                 volume=EXCLUDED.volume, source=EXCLUDED.source, fetched_at=NOW()""",
                              intraday_rows(t, bars, bars.attrs.get("source", "unknown")))
        if errors:
            raise SourceError(f"intraday {t}: {len(errors)} piece(s) failed, {n} rows kept: {errors[0]}")
        if not answered:
            raise NoData(f"intraday: no 1-minute bars for {t}")
        return n

    return _per_ticker("intraday", tickers, one)


# ── index board at 14:25 / ETF baskets ───────────────────────────────────────

SNAPSHOT_INDICES = ("VNINDEX", "VN30", "VNMID", "VNSML", "VN100", "HNXINDEX", "UPCOMINDEX")
SNAPSHOT_WINDOW = ("14:20", "14:30")        # a capture outside it is not "the 14:25 state" and is not stored


def run_index_snapshot() -> dict:
    """Index board at ~14:25 (scheduled 14:25 weekdays) → index_snapshot_1425: level, open/high/low, matched and
    block-trade value, breadth, foreign. No history exists anywhere (VNMID / VNSML have no minute bars), so this is
    stored day by day; it gives the 14:25 decision a same-day index value."""
    from vnmarket import VnMarket

    now = pd.Timestamp.now(tz="Asia/Ho_Chi_Minh")
    if not SNAPSHOT_WINDOW[0] <= now.strftime("%H:%M") <= SNAPSHOT_WINDOW[1]:
        return {"rows": 0, "skipped": f"outside {SNAPSHOT_WINDOW} ({now:%H:%M})"}
    rows, failed = [], []
    with VnMarket() as vn:
        for code in SNAPSHOT_INDICES:
            try:
                x = vn.market.index(code)
            except Exception as e:
                failed.append((code, repr(e)[:80]))
                continue
            t = pd.Timestamp(int(x.get("time") or now.timestamp() * 1000), unit="ms", tz="UTC").tz_convert("Asia/Ho_Chi_Minh")
            if t.date() != now.date():                       # a holiday: the board still shows the last session
                continue
            rows.append((code, t.date(), t.to_pydatetime(), _num(x.get("chart_open")), _num(x.get("chart_high")),
                         _num(x.get("chart_low")), _num(x.get("index_value")), _num(x.get("prev_index_value")),
                         _int(x.get("total_qtty")), _num(x.get("total_value")), _num(x.get("total_value_pt")),
                         _int(x.get("advances")), _int(x.get("declines")), _int(x.get("nochanges")),
                         _int(x.get("total_buy_foreign_qtty")), _int(x.get("total_sell_foreign_qtty")), "vnmarket"))
    n = _executemany("""INSERT INTO index_snapshot_1425 (index_code, date, captured_at, open, high, low, price, prev_close,
                        volume, value, value_pt, advances, declines, unchanged, foreign_buy_qty, foreign_sell_qty, source)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (index_code, date) DO NOTHING""",
                     rows)
    if failed and not rows:
        raise RuntimeError(f"index_snapshot: no index answered; {failed[:3]}")
    return {"rows": n, "failed": failed}


ETF_FUNDS = ("FUESSV30", "FUESSVFL", "FUESSV50")
ETF_HISTORY_START = date(2022, 8, 1)        # first SSIAM basket file (FUESSV30 ~05/08/2022)


def run_etf_baskets(backfill: bool = False) -> dict:
    """SSIAM ETF creation baskets → etf_holdings kind 'basket' (weight, quantity per creation unit). Daily: the last 5
    days; backfill: every Friday from 08/2022 (composition only changes at reviews and drifts with prices)."""
    from vnmarket import VnMarket
    from vnmarket.errors import NoData, SourceError

    end = date.today()
    if backfill:
        days = [ETF_HISTORY_START + timedelta(days=i) for i in range((end - ETF_HISTORY_START).days + 1)]
        days = [d for d in days if d.weekday() == 4]
    else:
        days = [end - timedelta(days=i) for i in range(5) if (end - timedelta(days=i)).weekday() < 5]
    rows, empty = [], 0
    with VnMarket() as vn:
        for fund in ETF_FUNDS:
            for d in days:
                try:
                    df = vn.market.etf_basket(fund, d)
                except (NoData, SourceError):
                    empty += 1
                    continue
                for r in df.to_dict("records"):
                    rows.append((fund, r["date"].date(), "basket", r["ticker"], _num(r["weight"]), _int(r["quantity"]),
                                 f"ssiam:{fund}", None))
    n = _executemany("""INSERT INTO etf_holdings (fund, as_of, kind, ticker, weight, shares, source, quote)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (fund, as_of, kind, ticker) DO UPDATE SET
                        weight=EXCLUDED.weight, shares=EXCLUDED.shares, fetched_at=NOW()""", rows)
    return {"rows": n, "empty_days": empty, "index_events": derive_index_events()}


# ── index reviews from compositions ──────────────────────────────────────────

ETF_INDEX = {"FUESSV30": "VN30", "FUESSVFL": "VNFINLEAD", "FUESSV50": "VNX50"}   # SSIAM ETFs replicate these
TRANSIENT_DAYS = 14          # a change undone within this many days is a basket glitch, not a review


def composition_events(snapshots: list[tuple[date, set[str]]], transient_days: int = TRANSIENT_DAYS) -> list[tuple]:
    """(ticker, action, effective_date) from consecutive compositions [(date, tickers)] of one index: tickers that
    appear are 'add', that disappear 'remove', effective the first date of the new composition. A change reversed
    within transient_days (FUESSV50 07/2025: 5 banks added, removed a week later) is dropped. Pure."""
    snaps = sorted(snapshots)
    raw = []
    for (d0, a), (d1, b) in zip(snaps, snaps[1:]):
        raw += [(t, "add", d1) for t in sorted(b - a)] + [(t, "remove", d1) for t in sorted(a - b)]
    out = []
    for t, act, d in raw:
        undo = "remove" if act == "add" else "add"
        if any(t2 == t and a2 == undo and abs((d2 - d).days) <= transient_days for t2, a2, d2 in raw):
            continue
        out.append((t, act, d))
    return out


def derive_index_events() -> int:
    """Index reviews as they took effect: ETF creation baskets (from 08/2022, weekly) and SSI index membership (daily,
    from 29/10/2026) → index_membership_events (source etf_basket:<fund> / ssi_membership). CafeF articles cover too
    little (none for 2025–2026 reviews; vn-portfolio data audit 30/10/2026); an article row keeps its earlier
    announcement and quote (ON CONFLICT DO NOTHING). announced_at = the effective date (conservative)."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Ho_Chi_Minh")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT fund, as_of, array_agg(ticker) FROM etf_holdings WHERE kind = 'basket' GROUP BY 1, 2")
        baskets = cur.fetchall()
        cur.execute("SELECT index_code, date, array_agg(ticker) FROM index_membership GROUP BY 1, 2")
        members = cur.fetchall()
    series: dict[tuple[str, str], list] = {}
    for fund, d, ts in baskets:
        if fund in ETF_INDEX:
            series.setdefault((ETF_INDEX[fund], f"etf_basket:{fund}"), []).append((d, set(ts)))
    for idx, d, ts in members:
        series.setdefault((idx, "ssi_membership"), []).append((d, set(ts)))
    rows = []
    for (idx, src), snaps in series.items():
        for t, act, d in composition_events(snaps):
            rows.append((idx, t, act, d, datetime.combine(d, dtime(8, 0), tz), src,
                         f"{src}: {t} {'vào' if act == 'add' else 'ra khỏi'} rổ từ {d:%d/%m/%Y}"))
    return _executemany("""INSERT INTO index_membership_events (index_code, ticker, action, effective_date, announced_at,
                           source, quote, model) VALUES (%s,%s,%s,%s,%s,%s,%s,NULL)
                           ON CONFLICT (index_code, ticker, action, effective_date) DO NOTHING""", rows)


# ── broker research list (Simplize) ──────────────────────────────────────────

BROKER_HISTORY_START = date(2023, 1, 1)


def run_broker_reports(tickers: list[str] | None = None, backfill: bool = False) -> dict:
    """Simplize broker reports per ticker → broker_reports (newest 50, or every page back to 2023 on backfill). The
    EPS / NPAT forecasts inside each PDF are read by ingest/extract_broker_forecasts.py."""
    tickers = tickers or _active()

    def one(vn, t: str) -> int:
        n = 0
        for page in range(0, 40 if backfill else 1):
            df = vn.company.broker_reports(t, page, 50)
            n += _executemany("""INSERT INTO broker_reports (id, ticker, broker, issue_date, title, target_price,
                                 recommendation, pdf_url) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                                 ON CONFLICT (id) DO UPDATE SET target_price=EXCLUDED.target_price,
                                 recommendation=EXCLUDED.recommendation, title=EXCLUDED.title""",
                              [(int(r.id), t, r.broker, _day(r.issue_date), r.title, _num(r.target_price),
                                r.recommendation, r.pdf_url if isinstance(r.pdf_url, str) else None)
                               for r in df.itertuples(index=False)])
            if len(df) < 50 or df["issue_date"].min().date() < BROKER_HISTORY_START:
                break
        return n

    return _per_ticker("broker_reports", tickers, one)


RUNNERS = {
    "block_trades": lambda a: run_block_trades(a.tickers, a.backfill),
    "size_indices": lambda a: run_size_indices(a.backfill),
    "index_valuation": lambda a: run_index_valuation(a.backfill),
    "foreign_room": lambda a: run_foreign_room(a.tickers, a.backfill),
    "shareholders": lambda a: run_shareholders(a.tickers),
    "corporate_actions": lambda a: run_corporate_actions(a.tickers, a.backfill),
    "vnd_items": lambda a: run_vnd_items(tickers=a.tickers),
    "commodities": lambda a: run_commodities(a.backfill),
    "fireant_data": lambda a: run_fireant_data(a.tickers),
    "price_history": lambda a: run_price_history(a.tickers),
    "former_hose": lambda a: run_former_hose(),
    "board_close": lambda a: run_board_close(),
    "yield_curve": lambda a: run_yield_curve(a.backfill),
    "fireant_holders": lambda a: run_fireant_holders(a.tickers),
    "macro": lambda a: run_macro(a.backfill),
    "fx_history": lambda a: run_fx_history(),
    "deposit_history": lambda a: run_deposit_history(),
    "index_snapshot": lambda a: run_index_snapshot(),
    "etf_baskets": lambda a: run_etf_baskets(a.backfill),
    "insurer_lines": lambda a: run_insurer_lines(a.tickers, a.backfill),
    "intraday": lambda a: run_intraday(a.tickers, a.backfill),
    "broker_reports": lambda a: run_broker_reports(a.tickers, a.backfill),
}


def main() -> None:
    ap = argparse.ArgumentParser(description="Rule-engine datasets -> Postgres")
    ap.add_argument("dataset", choices=sorted(RUNNERS))
    ap.add_argument("--tickers", default="")
    ap.add_argument("--backfill", action="store_true")
    a = ap.parse_args()
    a.tickers = [t.strip().upper() for t in a.tickers.split(",") if t.strip()] or None
    print(RUNNERS[a.dataset](a))


if __name__ == "__main__":
    main()
