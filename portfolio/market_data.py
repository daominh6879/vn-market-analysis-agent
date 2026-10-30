"""
portfolio/market_data.py — full market history for vn-portfolio's rule engine (/advisor/market-data).

Read-only. vn-portfolio rebuilds raw prices, point-in-time views and snapshots on its side; this module only dumps
what the database holds, in its native units: stock prices in nghìn đồng (price_unit "kVND"), index levels in
points, index traded value in VND (index_value_unit), flows in tỷ đồng, financial facts in VND.
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


# corporate_actions rows the rule engine reads: share issues (5.9), AGMs, trading-status / margin alerts (12.x)
ACTION_TYPES = ["ISSUE", "schedIssue", "MEETING", "alert", "noticed", "nomargin", "eligibleMargin", "control",
                "halt", "suspend", "delistedHose", "delistedUpcom", "lastTradeHose", "listedHose", "listedHnx",
                "listedUpcom"]
# vnd_financial_items the rule engine reads: (model_type, item_code) → name (units VND)
VND_ITEMS = {
    (33, 21001): "plan_revenue", (33, 23800): "plan_pbt", (33, 23000): "plan_npat_parent", (33, 23003): "plan_npat",
    (89, 700001): "broker_loans",                                     # Các khoản cho vay (margin + ứng trước)
    (412, 21410): "ins_gross_premium", (412, 21440): "ins_net_premium", (412, 21001): "ins_net_revenue",
    (412, 22160): "ins_direct_costs",        # Tổng chi trực tiếp HĐKD BH (claims + reserve changes + commissions + other)
    (412, 22110): "ins_selling", (412, 22200): "ins_admin", (412, 23800): "ins_pbt",
    (411, 11200): "ins_short_investments", (411, 12500): "ins_long_investments", (411, 11110): "ins_cash",
    (411, 11120): "ins_cash_equivalents", (411, 14000): "ins_equity",
}
# fireant_statement_lines → insurer fields, by line name (FireAnt line ids change meaning when the filing template
# changes, e.g. 1010201 "Đầu tư chứng khoán ngắn hạn" before 2022, "Chứng khoán kinh doanh" after); provisions skipped
INSURER_LINES = (
    (2, r"doanh thu (hoạt động|hđ) tài chính", "ins_fin_income"),
    (1, r"^[ivx]+\.\s*tiền( và các khoản tương đương tiền)?$", "ins_cash_total"),
    (1, r"^[ivx]+\.\s*các khoản đầu tư tài chính ngắn hạn$|^[ivx]+\.\s*đầu tư tài chính ngắn hạn$", "ins_invest_st"),
    (1, r"^[ivx]+\.\s*các khoản đầu tư tài chính dài hạn$|^[ivx]+\.\s*đầu tư tài chính dài hạn$", "ins_invest_lt"),
    (1, r"^\d+\.\s*(chứng khoán kinh doanh|đầu tư chứng khoán ngắn hạn)$", "ins_trading_securities"),
    (1, r"^\d+\.\s*đầu tư (khác )?(vào|góp vốn vào) (công cụ vốn|đơn vị khác)$", "ins_equity_other"),
    (1, r"^\d+\.\s*đầu tư vào công ty (liên kết|liên doanh)", "ins_associates"),
)
EXTRA_DAYS = 400          # block trades / foreign room: one year of history is what the rules look back on


def _extras(cur, since: date | None) -> dict:
    """Rule-engine datasets (migrations 050/051): block trades, index valuation, foreign room, shareholders, corporate
    actions, VNDirect items (AGM plan, broker loans, insurer lines), commodities, FireAnt values, BCTC note facts."""
    from datetime import timedelta
    recent = {"recent": max(since or date(1900, 1, 1), date.today() - timedelta(days=EXTRA_DAYS))}
    out: dict = {}
    bt = _rows(cur, "SELECT ticker, date, pt_volume, pt_value, nm_volume, nm_value FROM block_trades "
                    "WHERE date >= %(recent)s ORDER BY ticker, date", recent)
    out["block_trades"] = defaultdict(list)
    for t, d, pv, pval, nv, nval in bt:
        out["block_trades"][t].append([_iso(d), int(pv or 0), _f(pval), int(nv or 0), _f(nval)])
    out["index_valuation"] = defaultdict(list)
    for code, d, pe, pb in _rows(cur, "SELECT index_code, date, pe, pb FROM index_valuation ORDER BY index_code, date", {}):
        out["index_valuation"][code].append([_iso(d), _f(pe), _f(pb)])
    out["foreign_room"] = defaultdict(list)
    for t, d, tot, left in _rows(cur, "SELECT ticker, date, total_room, room_left FROM foreign_room "
                                      "WHERE date >= %(recent)s ORDER BY ticker, date", recent):
        out["foreign_room"][t].append([_iso(d), tot, left])
    out["shareholders"] = defaultdict(list)
    for t, name, typ, role, sh, pct, gpct, eff, seen, src in _rows(
            cur, "SELECT ticker, name, holder_type, role, shares, pct, group_pct, effective_date, first_seen_at, source "
                 "FROM shareholders ORDER BY ticker, pct DESC NULLS LAST", {}):
        out["shareholders"][t].append({"source": src, "name": name, "holder_type": typ, "role": role, "shares": sh,
                                       "pct": _f(pct),
                                       "group_pct": _f(gpct), "effective_date": _iso(eff),
                                       "first_seen": seen.date().isoformat() if seen else None})
    out["corporate_actions"] = defaultdict(list)
    for (aid, t, grp, typ, desc, note, price, ratio, sh, disc, eff, exp, act, trade) in _rows(
            cur, "SELECT id, ticker, action_group, action_type, type_desc, note, price, ratio, shares, disclosure_date, "
                 "effective_date, expired_date, actual_date, trading_start FROM corporate_actions "
                 "WHERE action_type = ANY(%(types)s) ORDER BY ticker, disclosure_date", {"types": ACTION_TYPES}):
        out["corporate_actions"][t].append({
            "id": aid, "group": grp, "type": typ, "type_desc": desc, "note": note, "price": _f(price),
            "ratio": _f(ratio), "shares": sh, "disclosure_date": _iso(disc), "effective_date": _iso(eff),
            "expired_date": _iso(exp), "actual_date": _iso(act), "trading_start": _iso(trade)})
    pairs = [f"({m},{i})" for m, i in VND_ITEMS]
    out["vnd_items"] = defaultdict(lambda: defaultdict(list))
    for t, fd, rt, m, i, v, created in _rows(
            cur, "SELECT ticker, fiscal_date, report_type, model_type, item_code, value, created FROM vnd_financial_items "
                 f"WHERE (model_type, item_code) IN ({','.join(pairs)}) ORDER BY ticker, fiscal_date", {}):
        out["vnd_items"][t][VND_ITEMS[(m, i)]].append([_iso(fd), rt, _f(v), created.date().isoformat() if created else None])
    out["commodities"] = defaultdict(list)
    for code, d, c in _rows(cur, "SELECT code, date, close FROM commodity_daily ORDER BY code, date", {}):
        out["commodities"][code].append([_iso(d), _f(c)])
    out["fireant_data"] = defaultdict(lambda: defaultdict(list))
    for t, y, q, f, v in _rows(cur, "SELECT ticker, year, quarter, field, value FROM fireant_financial_data "
                                    "ORDER BY ticker, year, quarter", {}):
        out["fireant_data"][t][f].append([f"Q{q}/{y}" if q else str(y), _f(v)])
    out["covered_warrants"] = defaultdict(list)
    for sym, und, iss, ltd, mat, seen in _rows(cur, "SELECT symbol, underlying, issuer, last_trading_date, maturity_date, "
                                                    "first_seen_at FROM covered_warrants ORDER BY underlying", {}):
        out["covered_warrants"][und].append({"symbol": sym, "issuer": iss, "last_trading_date": _iso(ltd),
                                             "maturity_date": _iso(mat), "first_seen": seen.date().isoformat()})
    out["board_close"] = defaultdict(list)
    for t, d, ceil, close, vol, bid, bidv in _rows(
            cur, "SELECT ticker, date, ceiling, close, matched_volume, bid1, bid1_volume FROM board_close_snapshot "
                 "WHERE date >= %(recent)s ORDER BY ticker, date", recent):
        out["board_close"][t].append([_iso(d), _f(ceil), _f(close), vol, _f(bid), bidv])
    out["gov_yields"] = defaultdict(list)
    for d, tenor, par, spot in _rows(cur, "SELECT date, tenor_years, par_yield, spot_annual FROM gov_bond_yields "
                                          "WHERE tenor_years IN (1, 5, 10) ORDER BY date", {}):
        out["gov_yields"][f"{float(tenor):g}y"].append([_iso(d), _f(par), _f(spot)])
    out["bctc_notes"] = defaultdict(dict)
    for t, period, m, v, src, quote, at in _rows(
            cur, "SELECT ticker, period, metric_code, value, source_file, quote, extracted_at FROM bctc_note_facts "
                 "ORDER BY ticker, period", {}):
        node = out["bctc_notes"][t].setdefault(period, {"source_file": src, "report_type": None, "values": {},
                                                        "quotes": {}})
        if m == "_read":
            node["report_type"] = quote or None
            node["read_at"] = at.date().isoformat() if at else None
        else:
            node["values"][m] = _f(v)
            node["quotes"][m] = quote
    out.update(_extras_055(cur))
    return {k: (dict(v) if not isinstance(v, list) else v) for k, v in out.items()}


def _extras_055(cur) -> dict:
    """Migration 055: macro figures (NSO, Vietcombank), insurer investment lines (FireAnt full statements), the 14:25
    intraday state + ATC fill, broker forecasts (EPS / NPAT read from research PDFs, see extract_broker_forecasts)."""
    import re
    out: dict = {"macro": defaultdict(list), "insurer_lines": defaultdict(lambda: defaultdict(list)),
                 "intraday": defaultdict(list), "broker_forecasts": defaultdict(list)}
    for code, pe, v, pub, src in _rows(cur, "SELECT code, period_end, value, published_at, source FROM macro_indicators "
                                            "ORDER BY code, period_end, published_at", {}):
        out["macro"][code].append([_iso(pe), pub.isoformat(), _f(v), src])
    rx = [(k, re.compile(p, re.I), name) for k, p, name in INSURER_LINES]
    for t, kind, y, q, name, v in _rows(cur, "SELECT ticker, kind, year, quarter, name, value FROM fireant_statement_lines "
                                             "WHERE quarter > 0 ORDER BY ticker, year, quarter", {}):
        n = (name or "").strip()
        if "dự phòng" in n.lower():
            continue
        for k, r, field in rx:
            if k == kind and r.search(n):
                out["insurer_lines"][t][field].append([f"Q{q}/{y}", _f(v)])
                break
    out["insurer_lines"] = {t: dict(v) for t, v in out["insurer_lines"].items()}
    for row in _rows(cur, "SELECT ticker, date, open, high_1425, low_1425, price_1425, volume_1425, atc_price, atc_volume, "
                          "close, volume FROM intraday_snapshot ORDER BY ticker, date", {}):
        out["intraday"][row[0]].append([_iso(row[1]), *[_f(x) for x in row[2:]]])
    for t, broker, issued, fy, eps, npat, target, rid in _rows(
            cur, "SELECT r.ticker, r.broker, r.issue_date, f.fiscal_year, f.eps, f.npat_parent, r.target_price, r.id "
                 "FROM broker_forecasts f JOIN broker_reports r ON r.id = f.report_id ORDER BY r.ticker, r.issue_date", {}):
        out["broker_forecasts"][t].append({"broker": broker, "issue_date": _iso(issued), "fiscal_year": fy,
                                           "eps": _f(eps), "npat_parent": _f(npat), "target_price": _f(target),
                                           "report_id": rid})
    return out


def _fa_map():
    from ingest.fetch_rule_data import FIREANT_TO_VNSTOCK
    return FIREANT_TO_VNSTOCK


_FA_FIELDS = (*_fa_map(), "Cash", "CashEquivalent")


def _fireant_history(out_fin: dict, fa_rows: list[tuple], metrics: list[str] | None) -> int:
    """Periods vnstock has no figures for (before 2024) get FireAnt values under the vnstock codes (fetch_rule_data
    FIREANT_TO_VNSTOCK, checked on the overlap). Never overwrites a vnstock period. Marked with _source = 'fireant'."""
    m = _fa_map()
    per: dict[tuple[str, str], dict] = defaultdict(dict)
    for t, y, q, f, v in fa_rows:
        per[(t, f"Q{q}/{y}" if q else str(y))][f] = _f(v)
    n = 0
    for (t, period), vals in per.items():
        if period in out_fin.get(t, {}):
            continue
        row = {code: vals[f] * sign for f, (code, sign) in m.items() if vals.get(f) is not None
               and (not metrics or code in metrics)}
        if vals.get("Cash") is not None and (not metrics or "tien_va_tuong_duong_tien" in metrics):
            row["tien_va_tuong_duong_tien"] = vals["Cash"] + (vals.get("CashEquivalent") or 0)
        if row:
            out_fin[t][period] = row
            n += 1
    return n


def market_data(metrics: list[str] | None = None, since: date | None = None) -> dict:
    """{as_of, price_unit, securities, bars, index, financials, foreign, prop, dividends, events, fx, benchmark,
    + rule-engine datasets (_extras)}. metrics: financial_facts metric codes to include (None = all). since: first
    date of daily series (None = all)."""
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
        fx_vcb = _rows(cur, "SELECT snapshot_date, sell FROM daily_snapshots WHERE series = 'vcb_usd' AND sell IS NOT NULL "
                            "AND snapshot_date >= %(since)s ORDER BY snapshot_date", p)
        bench = _rows(cur, "SELECT ticker, date, open, high, low, close, volume FROM benchmark_daily "
                           "WHERE date >= %(since)s ORDER BY ticker, date", p)
        extras = _extras(cur, since)
        fa_rows = _rows(cur, "SELECT ticker, year, quarter, field, value FROM fireant_financial_data "
                             "WHERE field = ANY(%(f)s)", {"f": list(_FA_FIELDS)})

    out_bars: dict[str, list] = defaultdict(list)
    for t, d, o, h, l, c, v in bars:
        out_bars[t].append([_iso(d), _f(o), _f(h), _f(l), _f(c), int(v or 0)])
    out_idx: dict[str, list] = defaultdict(list)
    for code, d, o, h, l, c, v, val, fn in idx:
        out_idx[code].append([_iso(d), _f(o), _f(h), _f(l), _f(c), int(v or 0), _f(val), _f(fn)])
    out_fin: dict[str, dict] = defaultdict(lambda: defaultdict(dict))
    for t, period, m, v in fin:
        out_fin[t][period][m] = _f(v)
    filled = _fireant_history(out_fin, fa_rows, metrics)

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
        "fx_vcb": {_iso(d): _f(v) for d, v in fx_vcb},   # Vietcombank USD sell, that day's last table (from 02/2020)
        "benchmark": out_bench,
        "index_value_unit": "VND",          # market_index_daily.matched_value is VND (SSI and VNDirect rows alike)
        "financials_filled_from_fireant": filled,
        **extras,
    }
