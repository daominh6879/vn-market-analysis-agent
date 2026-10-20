"""
portfolio/advisor.py — AI-judged buy/sell advice for a portfolio.

Python gathers the evidence (portfolio/evidence.py, no LLM) and enforces limits; the LLM judges. One LLM call returns a
JSON verdict per ticker (holdings and new-idea candidates) plus short market/ideas/risk summaries. `parse_verdicts`
validates it and `apply_limits` clamps it (T+2 sellable quantity, cash, max ticker weight, 100-share lots, cited news ids
must exist). Quantities are always computed here, never taken from the LLM.
"""
from __future__ import annotations

import json
import math
import re

from portfolio import ledger
from portfolio.evidence import (
    BETA_HIGH, CASH_HIGH_PCT, CASH_LOW_PCT, HIGH_VOL_PCT, MAX_TICKER_WEIGHT, SECTOR_PREFERRED_MAX, gather_evidence,
)

# ── constants ─────────────────────────────────────────────────────────────────
CUT_LOSS_PCT = -8.0              # BUY_MORE is blocked on a holding at or below this unrealized P/L (no averaging down)
PER_TRADE_CASH_PCT = 0.10        # one buy uses at most this share of available cash
TAKE_PROFIT_FRACTION = 0.5       # TAKE_PROFIT sells this share of the sellable quantity (lot-rounded)
LOT = ledger.LOT_SIZE
HOLDING_ACTIONS = ("BUY_MORE", "HOLD", "TAKE_PROFIT", "CUT_LOSS")
CANDIDATE_ACTIONS = ("BUY_NEW", "SKIP")

ACTION_VI = {"BUY_MORE": "MUA THÊM", "HOLD": "GIỮ", "TAKE_PROFIT": "CHỐT LỜI", "CUT_LOSS": "CẮT LỖ",
             "BUY_NEW": "MUA MỚI", "SKIP": "BỎ QUA"}
LIMIT_VI = {
    "invalid_llm_output": "AI không trả kết quả hợp lệ cho mã này nên giữ nguyên",
    "t2_not_sellable": "cổ phiếu chưa về tài khoản (T+2) nên chưa bán được",
    "no_cash_or_weight_cap": "không đủ tiền mặt hoặc đã chạm trần tỷ trọng nên không mua thêm",
    "averaging_down_blocked": f"đang lỗ từ {abs(CUT_LOSS_PCT):g}% trở lên nên không mua thêm (không bình quân giá xuống)",
}
FLAG_VI = {
    "ticker_concentration": f"một mã vượt trần {MAX_TICKER_WEIGHT:.0%} NAV",
    "sector_concentration": f"một ngành vượt {SECTOR_PREFERRED_MAX:.0%} NAV",
    "cash_low": f"tiền mặt dưới {CASH_LOW_PCT:g}% NAV",
    "cash_high": f"tiền mặt trên {CASH_HIGH_PCT:g}% NAV",
    "beta_high": f"beta cổ phiếu trên {BETA_HIGH:g}",
    "vol_high": f"có mã biến động thuộc nhóm cao nhất {1 - HIGH_VOL_PCT:.0%} thị trường",
}


# ── prompt ────────────────────────────────────────────────────────────────────

_SYSTEM = (
    "Bạn là chuyên viên tư vấn đầu tư chứng khoán Việt Nam. Bạn nhận dữ liệu thị trường, kỹ thuật, dòng tiền khối ngoại "
    "và tin tức của từng mã trong danh mục cùng vài mã ứng viên mua mới; nhiệm vụ của bạn là PHÁN ĐOÁN và đưa ra khuyến nghị "
    "cho từng mã. Chỉ dùng dữ liệu được cung cấp: không bịa tin tức, số liệu hay nguyên nhân. Mỗi lý do phải dẫn một con số "
    "trong dữ liệu hoặc một tin (ghi id vào cited_news_ids, KHÔNG viết 'id=' trong câu chữ). Mã không có tin trong dữ liệu thì không được nêu tin tức. "
    "Cân nhắc cả xu hướng kỹ thuật, dòng tiền, tin tức và mức lãi/lỗ hiện tại; khi bằng chứng mâu thuẫn hay yếu, chọn GIỮ/BỎ QUA "
    "và nói rõ vì sao. Không đề xuất khối lượng hay giá mục tiêu — hệ thống tự tính khối lượng. Đây là khuyến nghị chưa thực hiện, "
    "không dùng thì quá khứ. Viết tiếng Việt tự nhiên, không chép tên biến. "
    "Chỉ trả về MỘT đối tượng JSON hợp lệ, không có văn bản nào khác, không bọc markdown."
)


def _fmt_news(articles: list[dict]) -> str:
    if not articles:
        return "  Tin tức: KHÔNG CÓ tin trong khoảng thời gian xét."
    return "  Tin tức:\n" + "\n".join(
        f"  - [id={a['id']}] {a['published_at']} ({a['source']}) {a['title']} — {a['snippet']}" for a in articles)


def _fmt_flow(f: dict | None) -> str:
    return (f"  Khối ngoại ròng: 5 phiên {f['net_5d_bn']:+,.1f} tỷ, 20 phiên {f['net_20d_bn']:+,.1f} tỷ."
            if f else "  Khối ngoại: không có dữ liệu.")


def _prompt(ev: dict) -> str:
    r = ev["risk"]
    lines = [f"Ngày dữ liệu: {ev['as_of']}.", "", ev["market"], "", "[TIN THỊ TRƯỜNG CHUNG]"]
    lines += [f"- [id={a['id']}] {a['published_at']} ({a['source']}) {a['title']}" for a in ev["market_news"]] \
        or ["(không có)"]
    lines += ["", "[MÃ ĐANG NẮM GIỮ] (action cho phép: BUY_MORE, HOLD, TAKE_PROFIT, CUT_LOSS)"]
    for p in ev["positions"]:
        t = p["ticker"]
        lines += [f"- {t} ({p['sector']}): {p['quantity']:,} cp, giá vốn {p['avg_cost']:,.0f}, giá {p['last_price']:,.0f}, "
                  f"lãi/lỗ {p['unrealized_pct']:+.1f}%, tỷ trọng {p['weight']:.1%} giá trị cổ phiếu, "
                  f"bán được {p['sellable_quantity']:,} cp"
                  + (f" — VƯỢT TRẦN {MAX_TICKER_WEIGHT:.0%} NAV ({p['market_value'] / ev['cash']['nav']:.1%}): "
                     "phải cân nhắc chốt lời/cắt lỗ bớt hoặc giải thích rõ vì sao vẫn giữ"
                     if ev["cash"]["nav"] and (p["market_value"] or 0) / ev["cash"]["nav"] > MAX_TICKER_WEIGHT else ""),
                  ev["technical"].get(t, ""), _fmt_flow(ev["foreign_flows"].get(t)), _fmt_news(ev["news"].get(t, []))]
    lines += ["", "[MÃ ỨNG VIÊN MUA MỚI] (action cho phép: BUY_NEW, SKIP)"]
    for c in ev["candidates"]:
        t = c["ticker"]
        lines += [f"- {t} ({c['sector']}): giá {c['price']:,.0f}, tăng {c['ret_20']:+.1%}/20 phiên, "
                  f"GTGD TB20 {c['avg_value_bn']:,.0f} tỷ, ngành đang chiếm {c['sector_nav_pct']:.0%} NAV",
                  ev["technical"].get(t, ""), _fmt_flow(ev["foreign_flows"].get(t)), _fmt_news(ev["news"].get(t, []))]
    if not ev["candidates"]:
        lines.append("(không có ứng viên)")
    lines += ["", "[RỦI RO DANH MỤC]",
              f"Tiền mặt {r['cash_pct']:.1f}% NAV; mã lớn nhất {r['largest_ticker']} {r['largest_ticker_pct']:.1f}%; "
              f"ngành lớn nhất {r['largest_sector']} {r['largest_sector_pct']:.1f}%; "
              f"beta vs VNINDEX {('%.2f' % r['beta']) if r['beta'] is not None else 'n/a'}; "
              f"cờ: {', '.join(FLAG_VI[f] for f in r['flags']) or 'không'}"]
    for h in r["high_vol"]:
        lines.append(f"Biến động cao: {h['ticker']} ({h['weight_pct']:.1f}% NAV, {h['vol_20']:.1%}/ngày, cao hơn {h['vol_pct']:.0%} mã thanh khoản)")
    schema = {
        "market": "2-3 câu về bối cảnh thị trường, dẫn số liệu/tin",
        "verdicts": [{"ticker": "MÃ", "action": "một action cho phép", "confidence": "0..1",
                      "reasons": ["lý do có dẫn số liệu"], "risks": ["rủi ro chính"], "cited_news_ids": [0]}],
        "ideas_summary": "2-3 câu về các ứng viên mua mới",
        "risk_summary": "2-3 câu về rủi ro danh mục",
    }
    subjects = [p["ticker"] for p in ev["positions"]] + [c["ticker"] for c in ev["candidates"]]
    return ("\n".join(lines) + f"\n\nTrả về JSON theo mẫu, có đúng một verdict cho mỗi mã sau: {', '.join(subjects)}.\n"
            + json.dumps(schema, ensure_ascii=False))


# ── parse & validate ──────────────────────────────────────────────────────────

def _extract_json(text: str) -> dict | None:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    m = re.search(r"<report>(.*?)</report>", text, re.DOTALL)
    text = m.group(1) if m else text
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _clean_list(v) -> list[str]:
    return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []


def parse_verdicts(text: str, holdings: list[str], candidates: list[str], valid_news: dict[str, set[int]]) -> tuple[dict, dict[str, dict]]:
    """-> (summaries, {ticker: verdict}) for tickers with a valid verdict. A verdict is valid when the ticker was asked
    for, the action is allowed for its kind, and it has at least one reason. Unknown cited ids are dropped."""
    obj = _extract_json(text)
    if obj is None:
        return {}, {}
    allowed = {**{t: HOLDING_ACTIONS for t in holdings}, **{t: CANDIDATE_ACTIONS for t in candidates}}
    out: dict[str, dict] = {}
    for v in obj.get("verdicts") or []:
        if not isinstance(v, dict):
            continue
        t = str(v.get("ticker", "")).upper()
        action = str(v.get("action", "")).upper()
        reasons = _clean_list(v.get("reasons"))
        if t not in allowed or t in out or action not in allowed[t] or not reasons:
            continue
        try:
            conf = min(1.0, max(0.0, float(v.get("confidence"))))
        except (TypeError, ValueError):
            conf = 0.5
        ids = [i for i in (v.get("cited_news_ids") or []) if isinstance(i, int) and i in valid_news.get(t, set())]
        out[t] = {"ticker": t, "action": action, "confidence": conf, "reasons": reasons,
                  "risks": _clean_list(v.get("risks")), "cited_news_ids": ids}
    summaries = {k: str(obj.get(k) or "").strip() for k in ("market", "ideas_summary", "risk_summary")}
    return summaries, out


# ── limits ────────────────────────────────────────────────────────────────────

def size_lots(budget: float, price: float) -> int:
    """Shares (multiple of LOT) affordable with `budget` VND including the buy fee."""
    if budget <= 0 or price <= 0:
        return 0
    return int(math.floor(budget / (price * (1 + ledger.fee_pct())) / LOT)) * LOT


def apply_limits(verdicts: dict[str, dict], ev: dict) -> dict:
    """Quantities and downgrades. Holdings first (cash is consumed across buys in order), then candidates."""
    nav, cash_left = ev["cash"]["nav"], ev["cash"]["cash"]
    actions, ideas = [], []
    for p in ev["positions"]:
        v = verdicts.get(p["ticker"])
        action, qty, codes = (v["action"], 0, []) if v else ("HOLD", 0, ["invalid_llm_output"])
        pnl, price = p["unrealized_pct"], p["last_price"]
        if action == "CUT_LOSS":
            qty = p["sellable_quantity"]
        elif action == "TAKE_PROFIT":
            qty = int(p["sellable_quantity"] * TAKE_PROFIT_FRACTION) // LOT * LOT or min(LOT, p["sellable_quantity"])
        if action in ("CUT_LOSS", "TAKE_PROFIT") and qty == 0:
            action, codes = "HOLD", codes + ["t2_not_sellable"]
        if action == "BUY_MORE":
            if pnl is not None and pnl <= CUT_LOSS_PCT:
                action, codes = "HOLD", codes + ["averaging_down_blocked"]
            else:
                room = MAX_TICKER_WEIGHT * nav - (p["market_value"] or 0)
                qty = size_lots(min(cash_left * PER_TRADE_CASH_PCT, room), price)
                if qty == 0:
                    action, codes = "HOLD", codes + ["no_cash_or_weight_cap"]
                else:
                    cash_left -= qty * price * (1 + ledger.fee_pct())
        actions.append({"ticker": p["ticker"], "action": action, "quantity": qty, "price_ref": price,
                        "confidence": v["confidence"] if v else 0.0, "reasons": v["reasons"] if v else [],
                        "risks": v["risks"] if v else [], "cited_news_ids": v["cited_news_ids"] if v else [],
                        "limit_codes": codes, "unrealized_pct": pnl,
                        "weight_pct": (p["market_value"] or 0) / nav * 100 if nav else 0.0})
    for c in ev["candidates"]:
        v = verdicts.get(c["ticker"])
        if not v or v["action"] != "BUY_NEW":
            continue
        qty = size_lots(min(cash_left * PER_TRADE_CASH_PCT, MAX_TICKER_WEIGHT * nav), c["price"])
        if qty == 0:
            continue
        cash_left -= qty * c["price"] * (1 + ledger.fee_pct())
        ideas.append({"ticker": c["ticker"], "action": "BUY_NEW", "quantity": qty, "price_ref": c["price"],
                      "sector": c["sector"], "avg_value_bn": c["avg_value_bn"], "confidence": v["confidence"],
                      "reasons": v["reasons"], "risks": v["risks"], "cited_news_ids": v["cited_news_ids"]})
    return {"actions": actions, "new_ideas": ideas, "cash_after_pct": cash_left / nav * 100 if nav else 0.0}


# ── report ────────────────────────────────────────────────────────────────────

def _news_index(ev: dict) -> dict[int, dict]:
    idx = {a["id"]: a for a in ev["market_news"]}
    for arts in ev["news"].values():
        idx.update({a["id"]: a for a in arts})
    return idx


def _cited(item: dict, idx: dict[int, dict]) -> list[dict]:
    return [{"id": i, "title": idx[i]["title"], "url": idx[i]["url"], "published_at": idx[i]["published_at"]}
            for i in item["cited_news_ids"] if i in idx]


def _bullets(item: dict, idx: dict[int, dict]) -> list[str]:
    out = [f"- **{item['ticker']}** — {ACTION_VI[item['action']]} (độ tin cậy {item['confidence']:.0%})"]
    out += [f"  - {r}" for r in item["reasons"]]
    out += [f"  - Rủi ro: {r}" for r in item["risks"]]
    out += [f"  - Dẫn tin: [{a['title']}]({a['url']}) ({a['published_at']})" for a in _cited(item, idx)]
    out += [f"  - Giới hạn: {LIMIT_VI[c]}" for c in item.get("limit_codes", [])]
    return out


def _assemble(ev: dict, decided: dict, summ: dict) -> str:
    c, r, idx = ev["cash"], ev["risk"], _news_index(ev)
    out = [f"# Tư vấn danh mục — {ev['user_id']} ({ev['as_of']})", "",
           f"NAV {c['nav']:,.0f} đ · tiền mặt {c['cash']:,.0f} đ ({r['cash_pct']:.1f}%) · "
           f"tổng lợi suất {('%+.1f%%' % c['total_return_pct']) if c['total_return_pct'] is not None else 'n/a'}", "",
           "## 1. Bối cảnh thị trường", summ.get("market") or "(không có nhận định)", "", "## 2. Hành động theo từng mã", "",
           "| Mã | KL | Giá vốn | Giá | Lãi/lỗ | Tỷ trọng | Hành động | KL đề xuất | Tin cậy |", "|---|---|---|---|---|---|---|---|---|"]
    for a, p in zip(decided["actions"], ev["positions"]):
        out.append(f"| {p['ticker']} | {p['quantity']:,} | {p['avg_cost']:,.0f} | {p['last_price']:,.0f} | "
                   f"{p['unrealized_pct']:+.1f}% | {a['weight_pct']:.1f}% | **{ACTION_VI[a['action']]}** | {a['quantity']:,} | {a['confidence']:.0%} |")
    out.append("")
    for a in decided["actions"]:
        out += _bullets(a, idx) if a["reasons"] else [f"- **{a['ticker']}** — GIỮ", f"  - Giới hạn: {LIMIT_VI['invalid_llm_output']}"]
    out += ["", "## 3. Ý tưởng mua mới (chỉ tham khảo)", ""]
    if decided["new_ideas"]:
        out += ["| Mã | Ngành | Giá tham chiếu | KL đề xuất | GTGD TB20 (tỷ) | Tin cậy |", "|---|---|---|---|---|---|"]
        out += [f"| {i['ticker']} | {'Chưa phân ngành' if i['sector'] == 'Unknown' else i['sector']} | {i['price_ref']:,.0f} | "
                f"{i['quantity']:,} | {i['avg_value_bn']:,.0f} | {i['confidence']:.0%} |" for i in decided["new_ideas"]]
        out += ["", summ.get("ideas_summary") or ""] + [l for i in decided["new_ideas"] for l in _bullets(i, idx)]
    else:
        out.append("AI không đề xuất mã mua mới nào (hoặc không đủ tiền mặt / không có ứng viên đạt lọc).")
    out += ["", "## 4. Rủi ro danh mục", "",
            f"- Mã lớn nhất: {r['largest_ticker']} {r['largest_ticker_pct']:.1f}% NAV (trần {MAX_TICKER_WEIGHT:.0%})",
            f"- Ngành lớn nhất: {r['largest_sector']} {r['largest_sector_pct']:.1f}% NAV",
            f"- Beta cổ phiếu vs VNINDEX: {('%.2f' % r['beta']) if r['beta'] is not None else 'n/a'}",
            f"- Cờ: {', '.join(FLAG_VI[f] for f in r['flags']) or 'không'}", "", summ.get("risk_summary") or ""]
    return "\n".join(out)


def advise(user_id: str) -> dict:
    """Full advice. Raises ValueError for an empty portfolio, RuntimeError if the LLM never returned usable JSON."""
    from agents.guardrails import disclaimer_for, scrub_internal
    from llm import Message, create_client

    ev = gather_evidence(user_id)
    holdings = [p["ticker"] for p in ev["positions"]]
    cands = [c["ticker"] for c in ev["candidates"]]
    valid_news = {t: {a["id"] for a in ev["news"].get(t, [])} | {a["id"] for a in ev["market_news"]} for t in holdings + cands}
    client, prompt = create_client(), _prompt(ev)
    summ, verdicts = {}, {}
    for _attempt in range(2):                      # retry once when the JSON is unparsable or misses a ticker
        resp = client.generate([Message(role="user", content=prompt)], max_tokens=4000, temperature=0, system=_SYSTEM)
        summ, verdicts = parse_verdicts(resp.text, holdings, cands, valid_news)
        if all(t in verdicts for t in holdings + cands):
            break
    if not verdicts and not summ:
        raise RuntimeError("LLM returned no parsable JSON verdicts")
    decided = apply_limits(verdicts, ev)
    report = scrub_internal(_assemble(ev, decided, summ) + "\n\n" + disclaimer_for({"advice"}))
    idx = _news_index(ev)
    for item in decided["actions"] + decided["new_ideas"]:
        item["cited_news"] = _cited(item, idx)
    return {"report_md": report, "actions": decided["actions"], "new_ideas": decided["new_ideas"],
            "risk": ev["risk"], "as_of": ev["as_of"],
            "llm_missing": [t for t in holdings + cands if t not in verdicts]}
