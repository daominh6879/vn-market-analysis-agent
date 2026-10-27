"""
portfolio/news_scorer.py — one LLM pass that keeps the sector / related articles that actually move each ticker.

news_relevance matches titles on keywords, so its sector / related groups hold anything that mentions the topic
("Chứng khoán Mỹ tăng… giá dầu đi xuống" for DCM's gas-price channel). Here the LLM reads each candidate (title +
snippet) against the company's profile (sector, its impact channels) and scores it:
3 = direct, material impact on this company's earnings / price; 2 = real but indirect or small; 1 = loosely related;
0 = not related. Articles scoring >= SCORE_MIN are kept, best first, KEEP_PER_GROUP per group, each with a one-line
`impact` (channel and direction for this company). Ticker news is never scored or dropped.

On any failure (LLM error, unparsable output, PORTFOLIO_NEWS_SCORER=0) the keyword groups are cut to their usual size
instead, so the advice never loses its news because of this pass. `status` says which happened.
"""
from __future__ import annotations

import os

from portfolio.news_relevance import (RELATED_NEWS_PER_CHANNEL, RELATED_NEWS_PER_TICKER, SECTOR_NEWS_PER_TICKER,
                                      channels_for)

SCORE_MIN = 2
KEEP_PER_GROUP = {"sector": SECTOR_NEWS_PER_TICKER, "related": RELATED_NEWS_PER_TICKER}
MAX_OUTPUT_TOKENS = 6000
IMPACT_CHARS = 200

_SYSTEM = (
    "Bạn là chuyên viên phân tích ngành chứng khoán Việt Nam. Với mỗi cặp (mã, tin) được đưa, chấm mức tin đó tác động "
    "tới CHÍNH doanh nghiệp này (lợi nhuận, giá bán, chi phí, nhu cầu, rủi ro), dựa vào hồ sơ doanh nghiệp và các kênh "
    "tác động đã cho: 3 = tác động trực tiếp, đáng kể; 2 = có tác động thật nhưng gián tiếp hoặc nhỏ; 1 = chỉ liên quan "
    "lỏng (cùng chủ đề, không ảnh hưởng gì tới doanh nghiệp này); 0 = không liên quan. Tin về một doanh nghiệp khác cùng "
    "ngành chỉ được 2-3 khi nó nói lên xu hướng chung của ngành (giá bán, nhu cầu, chính sách) hoặc là đối thủ trực tiếp. "
    "Chỉ dùng tiêu đề và đoạn trích; không bịa số liệu. impact: một câu ngắn tiếng Việt, nêu kênh và chiều tác động lên "
    "doanh nghiệp (tích cực / tiêu cực / chưa rõ). Chỉ trả về MỘT đối tượng JSON: "
    '{"items": [{"t": "MÃ", "id": 123, "score": 0, "impact": "..."}]}, đủ mọi cặp được hỏi, không có văn bản nào khác.'
)


def enabled() -> bool:
    return os.getenv("PORTFOLIO_NEWS_SCORER", "1") != "0"


def _model() -> str | None:
    return os.getenv("PORTFOLIO_NEWS_SCORER_MODEL") or None


def build_prompt(groups: dict[str, dict[str, list[dict]]], meta: dict[str, dict]) -> str:
    lines = []
    for t, g in groups.items():
        items = g["sector"] + g["related"]
        if not items:
            continue
        m = meta.get(t, {})
        chans = "; ".join(channels_for(t, m.get("sector"), m.get("tags") or [])) or "(chưa khai báo)"
        lines += [f"## {t} — {m.get('company_name') or t} (ngành {m.get('sector') or 'chưa rõ'})", f"Kênh tác động: {chans}"]
        lines += [f"- id={a['id']} [{'tin ngành' if a['kind'] == 'sector' else 'kênh: ' + a['group']}] {a['published_at']} "
                  f"{a['title']} — {a['snippet'][:300]}" for a in items]
        lines.append("")
    return "\n".join(lines)


def parse_scores(text: str, groups: dict[str, dict[str, list[dict]]]) -> dict[tuple[str, int], dict]:
    """{(ticker, id): {score, impact}} for pairs that were asked; out-of-range scores and unknown pairs dropped."""
    from portfolio.advisor import _extract_json
    obj = _extract_json(text) or {}
    asked = {(t, a["id"]) for t, g in groups.items() for a in g["sector"] + g["related"]}
    out: dict[tuple[str, int], dict] = {}
    for it in obj.get("items") or []:
        if not isinstance(it, dict):
            continue
        try:
            key, score = (str(it.get("t", "")).upper(), int(it.get("id"))), int(it.get("score"))
        except (TypeError, ValueError):
            continue
        if key in asked and 0 <= score <= 3:
            out[key] = {"score": score, "impact": " ".join(str(it.get("impact") or "").split())[:IMPACT_CHARS]}
    return out


def apply_scores(groups: dict[str, dict[str, list[dict]]], scores: dict[tuple[str, int], dict]) -> dict:
    """Keep scored >= SCORE_MIN, highest score first (newest first within a score), KEEP_PER_GROUP per group."""
    out = {}
    for t, g in groups.items():
        out[t] = {"ticker": g["ticker"]}
        for k in ("sector", "related"):
            kept = [{**a, **scores[(t, a["id"])]} for a in g[k]
                    if (t, a["id"]) in scores and scores[(t, a["id"])]["score"] >= SCORE_MIN]
            out[t][k] = sorted(kept, key=lambda a: -a["score"])[:KEEP_PER_GROUP[k]]       # stable: newest first per score
    return out


def cut_to_size(groups: dict[str, dict[str, list[dict]]]) -> dict:
    """The keyword groups at their usual size (no LLM): newest first, RELATED_NEWS_PER_CHANNEL per channel."""
    out = {}
    for t, g in groups.items():
        related: list[dict] = []
        for a in g["related"]:
            if len(related) < KEEP_PER_GROUP["related"] and \
                    sum(r["group"] == a["group"] for r in related) < RELATED_NEWS_PER_CHANNEL:
                related.append(a)
        out[t] = {"ticker": g["ticker"], "sector": g["sector"][:KEEP_PER_GROUP["sector"]], "related": related}
    return out


def score_news(groups: dict[str, dict[str, list[dict]]], meta: dict[str, dict], client=None) -> tuple[dict, dict]:
    """-> (groups, status). status = {scored: bool, pairs, kept, error}."""
    pairs = sum(len(g["sector"]) + len(g["related"]) for g in groups.values())
    status = {"scored": False, "pairs": pairs, "kept": None, "error": None}
    if not pairs or not enabled():
        return cut_to_size(groups), status
    try:
        from llm import Message, create_client
        client = client or create_client()
        resp = client.generate([Message(role="user", content=build_prompt(groups, meta))], model=_model(),
                               max_tokens=MAX_OUTPUT_TOKENS, temperature=0, system=_SYSTEM)
        scores = parse_scores(resp.text, groups)
    except Exception as exc:                       # never lose the advice's news over this pass
        status["error"] = f"{type(exc).__name__}: {exc}"[:300]
        return cut_to_size(groups), status
    if len(scores) < pairs / 2:
        status["error"] = f"scored {len(scores)}/{pairs} pairs"
        return cut_to_size(groups), status
    out = apply_scores(groups, scores)
    status.update(scored=True, kept=sum(len(g["sector"]) + len(g["related"]) for g in out.values()))
    return out, status

