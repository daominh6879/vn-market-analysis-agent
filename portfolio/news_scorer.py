"""
portfolio/news_scorer.py — one LLM pass that judges what each article means for each ticker.

news_relevance matches titles on keywords, so its sector / related pools hold anything that mentions the topic
("Chứng khoán Mỹ tăng… giá dầu đi xuống" for DCM's gas-price channel). Here the LLM reads each (ticker, article) pair
(title + snippet) against the company's profile (sector, its impact channels) and returns
- score: 3 = direct, material impact on this company; 2 = real but indirect or small; 1 = loosely related; 0 = none;
- direction: 1 positive / -1 negative / 0 neutral or unclear, for this company;
- magnitude: 1 small / 2 medium / 3 large;
- impact: one line naming the channel and the direction;
- labels: the events the article reports about this company, from LABELS (vn-portfolio's rule engine reads them,
  news.yaml llm_labels; anything else is dropped).
Sector / related articles scoring >= SCORE_MIN are kept, best first, KEEP_PER_GROUP per group. Ticker news is scored
too (direction, for the advisor and the event study) but never dropped.

Scores already stored (news_impact_scores, `cached`) are reused, so a pair is judged once — at the time it was first
seen — and repeated advice runs see the same judgement. On any failure (LLM error, unparsable output,
PORTFOLIO_NEWS_SCORER=0) the keyword pools are cut to their usual size instead; `status` says which happened.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

from portfolio.news_relevance import (RELATED_NEWS_PER_CHANNEL, RELATED_NEWS_PER_TICKER, SECTOR_NEWS_PER_TICKER,
                                      channels_for)

SCORE_MIN = 2
KEEP_PER_GROUP = {"sector": SECTOR_NEWS_PER_TICKER, "related": RELATED_NEWS_PER_TICKER}
MAX_OUTPUT_TOKENS = 8000
BATCH_PAIRS = 40                 # pairs per LLM call: ~120 pairs in one call overran the output and the JSON was cut
WORKERS = 4
IMPACT_CHARS = 200
SCORE_FIELDS = ("score", "direction", "magnitude", "impact", "labels")
LABELS = {                       # code → what it means; keys must match vn-portfolio config/news.yaml llm_labels.map
    "legal_case": "lãnh đạo / cổ đông lớn / chính doanh nghiệp bị khởi tố, bắt, truy tố, thu hồi giấy phép",
    "forced_sale_named": "cổ phiếu của một người / tổ chức cụ thể bị bán giải chấp",
    "bond_default": "doanh nghiệp chậm / không trả lãi hoặc gốc trái phiếu của chính mình",
    "leadership_exit_sudden": "chủ tịch / tổng giám đốc rời đi đột ngột (không phải phó TGĐ, không phải theo kế hoạch)",
    "leadership_exit_planned": "chủ tịch / TGĐ thay theo kế hoạch: hết nhiệm kỳ, nghỉ hưu, bàn giao đã công bố",
    "late_disclosure": "doanh nghiệp bị nhắc nhở / xử phạt vì chậm hoặc không công bố thông tin",
    "rumor": "tin đồn chưa xác thực, \"room kín\", hô hào mua",
    "material_event": "sự kiện đủ lớn để giải thích biến động giá: KQKD, M&A, cổ tức lớn, hợp đồng lớn",
    "financial_report_filing": "chính bản công bố BCTC (không phải giải trình, không phải chọn kiểm toán)",
    "auditor_change": "thay đơn vị kiểm toán",
    "audit_gap": "chênh lệch lợi nhuận trước – sau kiểm toán / soát xét",
    "audit_gap_loss": "chênh lệch kiểm toán làm lãi thành lỗ",
}

_SYSTEM = (
    "Bạn là chuyên viên phân tích ngành chứng khoán Việt Nam. Với mỗi cặp (mã, tin) được đưa, đánh giá tin đó có ý nghĩa "
    "gì với CHÍNH doanh nghiệp này (lợi nhuận, giá bán, chi phí, nhu cầu, rủi ro), dựa vào hồ sơ doanh nghiệp và các kênh "
    "tác động đã cho.\n"
    "score: 3 = tác động trực tiếp, đáng kể; 2 = có tác động thật nhưng gián tiếp hoặc nhỏ; 1 = chỉ liên quan lỏng (cùng "
    "chủ đề, không ảnh hưởng gì tới doanh nghiệp này); 0 = không liên quan. Tin về một doanh nghiệp khác cùng ngành chỉ "
    "được 2-3 khi nó nói lên xu hướng chung của ngành (giá bán, nhu cầu, chính sách) hoặc là đối thủ trực tiếp. Thông báo "
    "hành chính thường lệ (họp, chốt quyền, giải trình nhỏ) là 0-1.\n"
    "direction: 1 = tích cực cho doanh nghiệp, -1 = tiêu cực, 0 = trung tính hoặc chưa rõ chiều. Đánh giá theo nội dung "
    "tin so với tình trạng bình thường của doanh nghiệp, không theo giọng văn của bài; không rõ thì để 0.\n"
    "magnitude: 1 = nhỏ, 2 = vừa, 3 = lớn (so với quy mô lợi nhuận của doanh nghiệp).\n"
    "impact: một câu ngắn tiếng Việt, nêu kênh và chiều tác động lên doanh nghiệp.\n"
    "labels: các sự kiện mà tin nói về CHÍNH doanh nghiệp này, chỉ chọn mã trong danh sách; tin về doanh nghiệp khác, tin "
    "ngành hay không có sự kiện nào thì []:\n"
    + "".join(f"- {k}: {v}\n" for k, v in LABELS.items()) +
    "Chỉ dùng tiêu đề và đoạn trích; không bịa số liệu. Chỉ trả về MỘT đối tượng JSON: "
    '{"items": [{"t": "MÃ", "id": 123, "score": 0, "direction": 0, "magnitude": 1, "impact": "...", "labels": []}]}, '
    "đủ mọi cặp được "
    "hỏi, không có văn bản nào khác."
)


def enabled() -> bool:
    return os.getenv("PORTFOLIO_NEWS_SCORER", "1") != "0"


def model_name() -> str | None:
    return os.getenv("PORTFOLIO_NEWS_SCORER_MODEL") or None


def _items(g: dict[str, list[dict]]) -> list[dict]:
    return g["ticker"] + g["sector"] + g["related"]


def _label(a: dict) -> str:
    return {"ticker": "tin của mã", "sector": "tin ngành"}.get(a.get("kind", "ticker"), f"kênh: {a.get('group')}")


def build_prompt(pairs: dict[str, list[dict]], meta: dict[str, dict]) -> str:
    """pairs = {ticker: [article...]} still to score."""
    lines = []
    for t, items in pairs.items():
        if not items:
            continue
        m = meta.get(t, {})
        chans = "; ".join(channels_for(t, m.get("sector"), m.get("tags") or [])) or "(chưa khai báo)"
        lines += [f"## {t} — {m.get('company_name') or t} (ngành {m.get('sector') or 'chưa rõ'})", f"Kênh tác động: {chans}"]
        lines += [f"- id={a['id']} [{_label(a)}] {a['published_at']} {a['title']} — {(a.get('snippet') or '')[:300]}"
                  for a in items]
        lines.append("")
    return "\n".join(lines)


def _int(v, lo: int, hi: int) -> int | None:
    try:
        i = int(v)
    except (TypeError, ValueError):
        return None
    return i if lo <= i <= hi else None


def parse_scores(text: str, asked: set[tuple[str, int]]) -> dict[tuple[str, int], dict]:
    """{(ticker, id): {score, direction, magnitude, impact, labels}} for asked pairs; a pair needs a valid score.
    labels keeps known LABELS codes only."""
    from portfolio.advisor import _extract_json
    out: dict[tuple[str, int], dict] = {}
    for it in (_extract_json(text) or {}).get("items") or []:
        if not isinstance(it, dict):
            continue
        key = (str(it.get("t", "")).upper(), _int(it.get("id"), 0, 2**31))
        score = _int(it.get("score"), 0, 3)
        if key in asked and score is not None:
            out[key] = {"score": score, "direction": _int(it.get("direction"), -1, 1) or 0,
                        "magnitude": _int(it.get("magnitude"), 1, 3),
                        "impact": " ".join(str(it.get("impact") or "").split())[:IMPACT_CHARS],
                        "labels": sorted({x for x in (it.get("labels") or []) if isinstance(x, str) and x in LABELS})
                        if isinstance(it.get("labels"), list) else []}
    return out


def apply_scores(groups: dict[str, dict[str, list[dict]]], scores: dict[tuple[str, int], dict]) -> dict:
    """Ticker news: all kept, scores attached when known. Sector / related: scored >= SCORE_MIN, highest score first
    (newest first within a score), KEEP_PER_GROUP per group."""
    def with_score(t: str, a: dict) -> dict:
        s = scores.get((t, a["id"]))
        return {**a, **{k: s[k] for k in SCORE_FIELDS if k in s}} if s else a
    out = {}
    for t, g in groups.items():
        out[t] = {"ticker": [with_score(t, a) for a in g["ticker"]]}
        for k in ("sector", "related"):
            kept = [with_score(t, a) for a in g[k] if scores.get((t, a["id"]), {}).get("score", -1) >= SCORE_MIN]
            out[t][k] = sorted(kept, key=lambda a: -a["score"])[:KEEP_PER_GROUP[k]]       # stable: newest first per score
    return out


def cut_to_size(groups: dict[str, dict[str, list[dict]]]) -> dict:
    """The keyword pools at their usual size (no LLM): newest first, RELATED_NEWS_PER_CHANNEL per channel."""
    out = {}
    for t, g in groups.items():
        related: list[dict] = []
        for a in g["related"]:
            if len(related) < KEEP_PER_GROUP["related"] and \
                    sum(r["group"] == a["group"] for r in related) < RELATED_NEWS_PER_CHANNEL:
                related.append(a)
        out[t] = {"ticker": g["ticker"], "sector": g["sector"][:KEEP_PER_GROUP["sector"]], "related": related}
    return out


def score_pairs(pairs: dict[str, list[dict]], meta: dict[str, dict], client=None) -> dict[tuple[str, int], dict]:
    """One LLM call over {ticker: [article...]} -> parsed scores (raises on LLM error)."""
    from llm import Message, create_client
    client = client or create_client()
    resp = client.generate([Message(role="user", content=build_prompt(pairs, meta))], model=model_name(),
                           max_tokens=MAX_OUTPUT_TOKENS, temperature=0, system=_SYSTEM)
    return parse_scores(resp.text, {(t, a["id"]) for t, items in pairs.items() for a in items})


def batches(todo: dict[str, list[dict]], size: int = BATCH_PAIRS) -> list[dict[str, list[dict]]]:
    out: list[dict[str, list[dict]]] = [{}]
    for t, items in todo.items():
        for a in items:
            if sum(len(v) for v in out[-1].values()) >= size:
                out.append({})
            out[-1].setdefault(t, []).append(a)
    return [b for b in out if b]


def score_news(groups: dict[str, dict[str, list[dict]]], meta: dict[str, dict], client=None,
               cached: dict[tuple[str, int], dict] | None = None) -> tuple[dict, dict, dict]:
    """-> (groups, status, new scores to store). status = {scored, pairs, cached, kept, error}. Unscored pairs go out
    in BATCH_PAIRS-sized LLM calls in parallel; a failed batch leaves its pairs unscored (an error when fewer than
    half of the new pairs come back)."""
    cached = cached or {}
    todo = {t: [a for a in _items(g) if (t, a["id"]) not in cached] for t, g in groups.items()}
    n_pairs, n_todo = sum(len(_items(g)) for g in groups.values()), sum(len(v) for v in todo.values())
    status = {"scored": False, "pairs": n_pairs, "cached": n_pairs - n_todo, "kept": None, "error": None}
    if not n_pairs or not enabled():
        return cut_to_size(groups), status, {}
    new: dict[tuple[str, int], dict] = {}
    if n_todo:
        errors: list[str] = []

        def run(batch: dict[str, list[dict]]) -> dict:
            try:
                return score_pairs(batch, meta, client)
            except Exception as exc:               # never lose the advice's news over this pass
                errors.append(f"{type(exc).__name__}: {exc}"[:200])
                return {}
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for part in pool.map(run, batches(todo)):
                new.update(part)
        if len(new) < n_todo / 2:
            status["error"] = (errors[0] if errors else "") + f" (scored {len(new)}/{n_todo} pairs)"
            return cut_to_size(groups), status, {}
        if errors:
            status["error"] = f"{len(errors)} batch(es) failed: {errors[0]}"
    out = apply_scores(groups, {**cached, **new})
    status.update(scored=True, kept=sum(len(g["sector"]) + len(g["related"]) for g in out.values()))
    return out, status, new
