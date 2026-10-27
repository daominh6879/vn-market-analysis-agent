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
    BETA_HIGH, CASH_HIGH_PCT, CASH_LOW_PCT, HIGH_VOL_PCT, MAX_TICKER_WEIGHT, SECTOR_PREFERRED_MAX,
    gather_evidence_for,
)

# ── constants ─────────────────────────────────────────────────────────────────
CUT_LOSS_PCT = -8.0              # BUY_MORE is blocked on a holding at or below this unrealized P/L (no averaging down)
PER_TRADE_CASH_PCT = 0.10        # one buy uses at most this share of available cash
TAKE_PROFIT_FRACTION = 0.5       # TAKE_PROFIT sells this share of the sellable quantity (lot-rounded)
REDUCE_FRACTION = 0.5            # REDUCE (thesis weakening, any P/L) sells this share of the sellable quantity
MAX_OUTPUT_TOKENS = 8000         # deepseek-chat allows 8K; 4000 truncated the JSON at 2 holdings + 7 candidates
DEFAULT_STOP_PCT = 7.0           # buy without a valid AI stop: stop = price x (1 - 7%), levels_source "default"
MIN_STOP_PCT = 2.0               # an AI stop must sit 2%..30% below the price (outside -> wrong unit or no real level)
MAX_STOP_PCT = 30.0
MAX_TARGET_MULT = 2.0            # an AI target must be above the price and at most 2x it
LOT = ledger.LOT_SIZE
HOLDING_ACTIONS = ("BUY_MORE", "HOLD", "REDUCE", "TAKE_PROFIT", "CUT_LOSS")
SELL_FRACTIONS = {"CUT_LOSS": 1.0, "TAKE_PROFIT": TAKE_PROFIT_FRACTION, "REDUCE": REDUCE_FRACTION}
CANDIDATE_ACTIONS = ("BUY_NEW", "SKIP")

ACTION_VI = {"BUY_MORE": "MUA THÊM", "HOLD": "GIỮ", "REDUCE": "BÁN BỚT", "TAKE_PROFIT": "CHỐT LỜI", "CUT_LOSS": "CẮT LỖ",
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
    "Bạn là chuyên viên tư vấn đầu tư chứng khoán Việt Nam. Bạn nhận dữ liệu thị trường, kỹ thuật, cơ bản/định giá, tín hiệu "
    "breakout, dòng tiền khối ngoại "
    "và tin tức của từng mã trong danh mục cùng vài mã ứng viên mua mới; nhiệm vụ của bạn là PHÁN ĐOÁN và đưa ra khuyến nghị "
    "cho từng mã. Chỉ dùng dữ liệu được cung cấp: không bịa tin tức, số liệu hay nguyên nhân. Mỗi lý do phải dẫn một con số "
    "trong dữ liệu hoặc một tin (ghi id vào cited_news_ids, KHÔNG viết 'id=' trong câu chữ). Mã không có tin trong dữ liệu thì không được nêu tin tức. "
    "Mỗi mã có tin của mã, tin ngành và tin ngành liên quan/vĩ mô: đánh giá mã dựa trên cả ba. Tin ngành và tin ngành "
    "liên quan không phải tin của doanh nghiệp: khi dẫn phải nói rõ nó tác động tới mã qua kênh nào (ví dụ giá khí → chi "
    "phí đầu vào) và theo chiều nào; dòng 'tác động' là đánh giá sơ bộ, tự kiểm tra lại với đoạn trích. "
    "Cân nhắc xu hướng kỹ thuật, định giá và tăng trưởng so với ngành, dòng tiền và tin tức. Với mã đang nắm giữ, đánh giá như "
    "thể đang quyết định mua mới ở giá hiện tại: giá vốn và mức lãi/lỗ hiện tại KHÔNG phải lý do để giữ (không chờ gỡ lỗ, không "
    "chờ giá chạm stop mới bán). Phần lớn bằng chứng xấu (ví dụ giá dưới MA50 và MA200, khối ngoại bán ròng kéo dài, tin tiêu cực, "
    "kết quả kinh doanh suy giảm) thì chọn REDUCE (bán bớt); xấu rõ ràng và không còn hỗ trợ gần thì chọn CUT_LOSS (bán hết); "
    "đang lãi và giá chạm kháng cự/quá mua thì có thể chọn TAKE_PROFIT; chỉ chọn HOLD khi bằng chứng thật sự cân bằng hoặc còn "
    "tích cực. Với mã ứng viên, bằng chứng mâu thuẫn hay yếu thì chọn SKIP. Luôn nói rõ vì sao. Không đề xuất khối lượng — hệ thống tự tính. Với BUY_MORE/BUY_NEW, đề xuất stop_price (dưới giá) và "
    "target_price (trên giá) bằng VND cùng đơn vị với giá của mã, chỉ lấy từ mức có trong dữ liệu (hỗ trợ/kháng cự, MA, "
    "Fibonacci, đáy/đỉnh gần đây, pivot/SL/TP của tín hiệu breakout) và ghi căn cứ vào levels_basis; không có căn cứ thì để null. Giá mục tiêu "
    "của công ty chứng khoán trong tin chỉ là ý kiến bên ngoài: không dùng làm target_price và không phải căn cứ chính để mua. Đây là khuyến nghị chưa thực hiện, "
    "không dùng thì quá khứ. Viết tiếng Việt tự nhiên, không chép tên biến. "
    "Chỉ trả về MỘT đối tượng JSON hợp lệ, không có văn bản nào khác, không bọc markdown."
)


def _fmt_news(articles: list[dict], context: list[dict] | None = None) -> str:
    def line(a: dict) -> str:
        impact = f" (tác động: {a['impact']})" if a.get("impact") else ""
        return f"  - [id={a['id']}] {a['published_at']} ({a['source']}) {a['title']} — {a['snippet']}{impact}"
    out = ("  Tin của mã:\n" + "\n".join(line(a) for a in articles) if articles
           else "  Tin của mã: KHÔNG CÓ tin trong khoảng thời gian xét.")
    sector = [a for a in context or [] if a["kind"] == "sector"]
    related = [a for a in context or [] if a["kind"] == "related"]
    if sector:
        out += f"\n  Tin ngành ({sector[0]['group']}):\n" + "\n".join(line(a) for a in sector)
    if related:
        out += "\n  Tin ngành liên quan / vĩ mô tác động tới mã:\n" + "\n".join(
            f"  - [id={a['id']}] {a['published_at']} ({a['source']}) [kênh: {a['group']}] {a['title']} — {a['snippet']}"
            + (f" (tác động: {a['impact']})" if a.get("impact") else "") for a in related)
    return out


def _context(ev: dict, t: str) -> list[dict]:
    return ev.get("context_news", {}).get(t, [])


def _fmt_flow(f: dict | None) -> str:
    return (f"  Khối ngoại ròng: 5 phiên {f['net_5d_bn']:+,.1f} tỷ, 20 phiên {f['net_20d_bn']:+,.1f} tỷ."
            if f else "  Khối ngoại: không có dữ liệu.")


def _data_dates(ev: dict) -> str:
    """Which session each part of the evidence describes (prices can be newer than the daily tables intraday)."""
    data, price = ev.get("data_session"), ev.get("price_session")
    if not data:
        return ""
    if price and price > data:
        return (f"Chỉ số, độ rộng, ngành, dòng tiền khối ngoại và bộ lọc ứng viên là của phiên đã đóng {data}; giá từng mã "
                f"(và chỉ báo kỹ thuật) tính đến phiên {price} đang/vừa giao dịch — đừng mô tả thị trường phiên {data} như thể là hôm nay.")
    return f"Mọi số liệu tính đến phiên {data}."


def _prompt(ev: dict) -> str:
    r = ev["risk"]
    lines = [f"Ngày tư vấn: {ev['as_of']}. {_data_dates(ev)}", "", ev["market"], "", "[TIN THỊ TRƯỜNG CHUNG]"]
    lines += [f"- [id={a['id']}] {a['published_at']} ({a['source']}) {a['title']}" for a in ev["market_news"]] \
        or ["(không có)"]
    lines += ["", "[MÃ ĐANG NẮM GIỮ] (action cho phép: BUY_MORE, HOLD, REDUCE, TAKE_PROFIT, CUT_LOSS)"]
    for p in ev["positions"]:
        t = p["ticker"]
        lines += [f"- {t} ({p['sector']}): {p['quantity']:,} cp, giá vốn {p['avg_cost']:,.0f}, giá {p['last_price']:,.0f}, "
                  f"lãi/lỗ {p['unrealized_pct']:+.1f}%, tỷ trọng {p['weight']:.1%} giá trị cổ phiếu, "
                  f"bán được {p['sellable_quantity']:,} cp"
                  + (f" — VƯỢT TRẦN {MAX_TICKER_WEIGHT:.0%} NAV ({p['market_value'] / ev['cash']['nav']:.1%}): "
                     "phải cân nhắc chốt lời/cắt lỗ bớt hoặc giải thích rõ vì sao vẫn giữ"
                     if ev["cash"]["nav"] and (p["market_value"] or 0) / ev["cash"]["nav"] > MAX_TICKER_WEIGHT else ""),
                  ev["technical"].get(t, ""), ev["fundamentals"].get(t, ""), ev["signals"].get(t, ""),
                  _fmt_flow(ev["foreign_flows"].get(t)), _fmt_news(ev["news"].get(t, []), _context(ev, t))]
    lines += ["", "[MÃ ỨNG VIÊN MUA MỚI] (action cho phép: BUY_NEW, SKIP)"]
    for c in ev["candidates"]:
        t = c["ticker"]
        lines += [f"- {t} ({c['sector']}): giá {c['price']:,.0f}, tăng {c['ret_20']:+.1%}/20 phiên, "
                  f"GTGD TB20 {c['avg_value_bn']:,.0f} tỷ, ngành đang chiếm {c['sector_nav_pct']:.0%} NAV"
                  + (" — mã trong DANH SÁCH QUAN TÂM của người dùng (chưa qua bộ lọc ứng viên)" if c.get("watch") else ""),
                  ev["technical"].get(t, ""), ev["fundamentals"].get(t, ""), ev["signals"].get(t, ""),
                  _fmt_flow(ev["foreign_flows"].get(t)), _fmt_news(ev["news"].get(t, []), _context(ev, t))]
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
                      "reasons": ["lý do có dẫn số liệu"], "risks": ["rủi ro chính"], "cited_news_ids": [0],
                      "stop_price": "chỉ BUY_MORE/BUY_NEW: số VND hoặc null", "target_price": "số VND hoặc null",
                      "levels_basis": "căn cứ của stop/target, dẫn số liệu; hoặc null"}],
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


_NEWS_ID_RE = re.compile(r"\s*\((?:\s*(?:news[_ ]?)?id\s*[=:]\s*\d+\s*,?)+\)|\s*\b(?:news[_ ]?)?id\s*[=:]\s*\d+"
                         r"|\s*\(\s*tin\s*(?:id\s*)?[=:]?\s*\d+(?:\s*[,;]\s*(?:tin\s*)?\d+)*\s*\)", re.IGNORECASE)


def _strip_ids(s: str) -> str:
    """Drop inline news ids ("(id=171436, id=171437)", "(tin 272483)") the model writes despite the prompt; citations
    are rendered from cited_news_ids instead."""
    return _NEWS_ID_RE.sub("", s).strip()


def _clean_list(v) -> list[str]:
    return [s for s in (_strip_ids(str(x)) for x in v) if s] if isinstance(v, list) else []


def _price(v) -> float | None:
    try:
        p = float(str(v).replace(",", "")) if v is not None else None
    except ValueError:
        return None
    return p if p and p > 0 else None


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
        buy = action in ("BUY_MORE", "BUY_NEW")
        out[t] = {"ticker": t, "action": action, "confidence": conf, "reasons": reasons,
                  "risks": _clean_list(v.get("risks")), "cited_news_ids": ids,
                  "stop_price": _price(v.get("stop_price")) if buy else None,
                  "target_price": _price(v.get("target_price")) if buy else None,
                  "levels_basis": (_strip_ids(str(v.get("levels_basis") or "")) or None) if buy else None}
    summaries = {k: _strip_ids(str(obj.get(k) or "")) for k in ("market", "ideas_summary", "risk_summary")}
    return summaries, out


# ── limits ────────────────────────────────────────────────────────────────────

def size_lots(budget: float, price: float) -> int:
    """Shares (multiple of LOT) affordable with `budget` VND including the buy fee."""
    if budget <= 0 or price <= 0:
        return 0
    return int(math.floor(budget / (price * (1 + ledger.fee_pct())) / LOT)) * LOT


def buy_levels(v: dict, price: float) -> dict:
    """Validated stop/target for a buy. An AI stop outside 2%..30% below the price -> default stop (7% below);
    a target not in (price, 2x price] -> none."""
    stop, target = v.get("stop_price"), v.get("target_price")
    ok_stop = stop is not None and price * (1 - MAX_STOP_PCT / 100) <= stop < price * (1 - MIN_STOP_PCT / 100)
    ok_target = target is not None and price < target <= price * MAX_TARGET_MULT
    return {"stop_price": stop if ok_stop else round(price * (1 - DEFAULT_STOP_PCT / 100)),
            "target_price": target if ok_target else None,
            "levels_source": "ai" if ok_stop else "default",
            "levels_basis": v.get("levels_basis") if ok_stop else None}      # basis describes the AI stop


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
        elif action in SELL_FRACTIONS:
            qty = int(p["sellable_quantity"] * SELL_FRACTIONS[action]) // LOT * LOT or min(LOT, p["sellable_quantity"])
        if action in SELL_FRACTIONS and qty == 0:
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
                        "weight_pct": (p["market_value"] or 0) / nav * 100 if nav else 0.0,
                        **(buy_levels(v, price) if action == "BUY_MORE" else {})})
    skipped = []
    for c in ev["candidates"]:
        v = verdicts.get(c["ticker"])
        base = {"ticker": c["ticker"], "price_ref": c["price"], "sector": c["sector"], "avg_value_bn": c["avg_value_bn"],
                "ret_20": c.get("ret_20"), "watch": bool(c.get("watch")),
                "confidence": v["confidence"] if v else 0.0, "reasons": v["reasons"] if v else [],
                "risks": v["risks"] if v else [], "cited_news_ids": v["cited_news_ids"] if v else []}
        qty = size_lots(min(cash_left * PER_TRADE_CASH_PCT, MAX_TICKER_WEIGHT * nav), c["price"]) \
            if v and v["action"] == "BUY_NEW" else 0
        if qty == 0:        # kept so the caller can score what the AI passed on, not only what it picked
            code = "invalid_llm_output" if not v else "ai_skip" if v["action"] != "BUY_NEW" else "no_cash_or_weight_cap"
            skipped.append({**base, "action": "SKIP", "skip_code": code,
                            **(buy_levels(v, c["price"]) if v and v["action"] == "BUY_NEW" else {})})
            continue
        cash_left -= qty * c["price"] * (1 + ledger.fee_pct())
        ideas.append({**base, "action": "BUY_NEW", "quantity": qty, **buy_levels(v, c["price"])})
    return {"actions": actions, "new_ideas": ideas, "skipped_ideas": skipped,
            "cash_after_pct": cash_left / nav * 100 if nav else 0.0}


# ── report ────────────────────────────────────────────────────────────────────

def _news_index(ev: dict) -> dict[int, dict]:
    idx = {a["id"]: a for a in ev["market_news"]}
    for arts in [*ev["news"].values(), *ev.get("context_news", {}).values()]:
        idx.update({a["id"]: a for a in arts})
    return idx


def _cited(item: dict, idx: dict[int, dict]) -> list[dict]:
    """Cited articles, one per title (the same story is often ingested from several sources)."""
    out, titles = [], set()
    for i in item["cited_news_ids"]:
        if i in idx and idx[i]["title"] not in titles:
            titles.add(idx[i]["title"])
            out.append({"id": i, "title": idx[i]["title"], "url": idx[i]["url"], "published_at": idx[i]["published_at"]})
    return out


def _link(article: dict) -> str:
    """Markdown link, or the bare title for old FireAnt rows whose url is a fake title-hash key (404)."""
    if article["url"].startswith("https://fireant.vn/news/"):
        return article["title"]
    return f"[{article['title']}]({article['url']})"


def _bullets(item: dict, idx: dict[int, dict]) -> list[str]:
    out = [f"- **{item['ticker']}** — {ACTION_VI[item['action']]} (độ tin cậy {item['confidence']:.0%})"]
    out += [f"  - {r}" for r in item["reasons"]]
    out += [f"  - Rủi ro: {r}" for r in item["risks"]]
    if item.get("stop_price"):
        target = f", mục tiêu {item['target_price']:,.0f}" if item.get("target_price") else ", chưa có mục tiêu"
        basis = (f" — {item['levels_basis']}" if item.get("levels_basis")
                 else f" — stop mặc định {DEFAULT_STOP_PCT:g}% dưới giá" if item["levels_source"] == "default" else "")
        out.append(f"  - Kế hoạch: cắt lỗ {item['stop_price']:,.0f}{target}{basis}")
    out += [f"  - Dẫn tin: {_link(a)} ({a['published_at']})" for a in _cited(item, idx)]
    out += [f"  - Giới hạn: {LIMIT_VI[c]}" for c in item.get("limit_codes", [])]
    return out


def _assemble(ev: dict, decided: dict, summ: dict) -> str:
    c, r, idx = ev["cash"], ev["risk"], _news_index(ev)
    out = [f"# Tư vấn danh mục — {ev['user_id']} ({ev['as_of']})", ""]
    if ev.get("data_session"):
        out += [f"Dữ liệu thị trường: phiên {ev['data_session']} · giá các mã: phiên {ev.get('price_session') or ev['data_session']}", ""]
    out += [f"NAV {c['nav']:,.0f} đ · tiền mặt {c['cash']:,.0f} đ ({r['cash_pct']:.1f}%) · "
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
    out += ["", "## 4. Rủi ro danh mục", ""]
    out += ([f"- Mã lớn nhất: {r['largest_ticker']} {r['largest_ticker_pct']:.1f}% NAV (trần {MAX_TICKER_WEIGHT:.0%})",
             f"- Ngành lớn nhất: {r['largest_sector']} {r['largest_sector_pct']:.1f}% NAV"]
            if r["largest_ticker"] else ["- Chưa nắm giữ cổ phiếu nào"])
    out += [f"- Beta cổ phiếu vs VNINDEX: {('%.2f' % r['beta']) if r['beta'] is not None else 'n/a'}",
            f"- Cờ:{', '.join(FLAG_VI[f] for f in r['flags']) or 'không'}", "", summ.get("risk_summary") or ""]
    return "\n".join(out)


_NUM_RE = re.compile(r"\d{1,3}(?:[,.]\d{3})+(?:\.\d+)?|\d{4,}(?:\.\d+)?")
LEVEL_MATCH_PCT = 0.3            # an AI level "comes from the data" when a number in the evidence is within 0.3% of it


def _numbers(text: str) -> list[float]:
    """VND figures in an evidence/basis text ("33,447", "75.800", "70100")."""
    out = []
    for s in _NUM_RE.findall(text or ""):
        if "," in s:                                    # comma thousands, optional dot decimals
            s = s.replace(",", "")
        elif "." in s and len(s.split(".")[-1]) == 3:   # dot thousands (Vietnamese style)
            s = s.replace(".", "")
        try:
            out.append(float(s))
        except ValueError:
            pass
    return out


def levels_check(item: dict, evidence_text: str) -> dict:
    """Flags for an AI stop/target (report only — they change nothing): does each level appear in the ticker's evidence
    (the prompt says levels must come from the data), does levels_basis name the levels it explains, and the R:R the
    levels imply. Pure."""
    price, stop, target = item.get("price_ref"), item.get("stop_price"), item.get("target_price")
    found, basis = _numbers(evidence_text), _numbers(item.get("levels_basis") or "")

    def near(v, nums):
        return v is not None and any(abs(n - v) <= v * LEVEL_MATCH_PCT / 100 for n in nums)
    return {"stop_in_evidence": near(stop, found), "target_in_evidence": near(target, found) if target else None,
            "basis_names_levels": near(stop, basis) and (target is None or near(target, basis)),
            "rr": round((target - price) / (price - stop), 2) if target and price and stop and price > stop else None}


def evidence_record(ev: dict) -> dict:
    """What the LLM was shown, per ticker (the prompt's blocks, flows, news with dates) plus the market text and news —
    returned with the advice so the caller can audit a verdict against its input later. Pure."""
    def arts(articles: list[dict]) -> list[dict]:
        return [{k: a[k] for k in ("id", "published_at", "source", "title", "url", "snippet", "kind", "group", "score",
                                   "impact") if k in a}
                for a in articles]
    subjects = [p["ticker"] for p in ev["positions"]] + [c["ticker"] for c in ev["candidates"]]
    return {"data_session": ev.get("data_session"), "price_session": ev.get("price_session"),
            "market": ev["market"], "market_news": arts(ev["market_news"]), "news_scoring": ev.get("news_scoring"),
            "candidates": [{k: c.get(k) for k in ("ticker", "price", "ret_20", "avg_value_bn", "sector_nav_pct", "watch")}
                           for c in ev["candidates"]],
            "tickers": {t: {"technical": ev["technical"].get(t, ""), "fundamentals": ev["fundamentals"].get(t, ""),
                            "signals": ev["signals"].get(t, ""), "foreign_flows": ev["foreign_flows"].get(t),
                            "news": arts(ev["news"].get(t, [])), "context_news": arts(_context(ev, t))}
                  for t in subjects}}


def advise_for(positions: list[dict], cash: dict, user_id: str = "external", watchlist: list[str] | None = None) -> dict:
    """Full advice for positions/cash supplied by the caller (the vn-portfolio app owns the ledger). Watchlist tickers
    are judged as new-idea candidates first.

    Raises ValueError for an empty portfolio, RuntimeError if the LLM never returned usable JSON.
    """
    return _advise(gather_evidence_for(positions, cash, user_id, watchlist))


def _advise(ev: dict) -> dict:
    from agents.guardrails import disclaimer_for, scrub_internal
    from llm import Message, create_client

    holdings = [p["ticker"] for p in ev["positions"]]
    cands = [c["ticker"] for c in ev["candidates"]]
    valid_news = {t: {a["id"] for a in ev["news"].get(t, []) + _context(ev, t)} | {a["id"] for a in ev["market_news"]}
                  for t in holdings + cands}
    client, prompt = create_client(), _prompt(ev)
    summ, verdicts = {}, {}
    for _attempt in range(2):                      # retry once when the JSON is unparsable or misses a ticker
        resp = client.generate([Message(role="user", content=prompt)], max_tokens=MAX_OUTPUT_TOKENS, temperature=0,
                               system=_SYSTEM)
        summ, verdicts = parse_verdicts(resp.text, holdings, cands, valid_news)
        if all(t in verdicts for t in holdings + cands):
            break
    if not verdicts and not summ:
        raise RuntimeError("LLM returned no parsable JSON verdicts")
    decided = apply_limits(verdicts, ev)
    report = scrub_internal(_assemble(ev, decided, summ) + "\n\n" + disclaimer_for({"advice"}))
    idx = _news_index(ev)
    for item in decided["actions"] + decided["new_ideas"] + decided["skipped_ideas"]:
        item["cited_news"] = _cited(item, idx)
        if item.get("levels_source") == "ai":
            t = item["ticker"]
            item["levels_check"] = levels_check(item, "\n".join(ev[k].get(t, "") for k in ("technical", "signals", "fundamentals")))
    return {"report_md": report, "actions": decided["actions"], "new_ideas": decided["new_ideas"],
            "skipped_ideas": decided["skipped_ideas"],
            "risk": ev["risk"], "as_of": ev["as_of"], "data_session": ev.get("data_session"),
            "price_session": ev.get("price_session"),
            "llm_missing": [t for t in holdings + cands if t not in verdicts], "evidence": evidence_record(ev)}
