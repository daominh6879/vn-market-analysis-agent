"""Shared helpers for the intent model (separate from the live agent path)."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
ARTIFACTS = ROOT / "artifacts"
EMBED_MODEL = "BAAI/bge-m3"  # same family as OLLAMA_EMBED_MODEL=bge-m3


MARKET_TOPICS = ("index", "movers", "foreign", "liquidity", "marketcap", "list")
MACRO_TOPICS = ("fx", "commodities", "gold", "sector")
TIME_UNITS = ("session", "day", "week", "month", "quarter", "year")
FILTER_OPS = ("<", ">", "<=", ">=")
FILTER_INDICATORS = (
    "pe", "pb", "roe", "roa", "eps", "ev_ebitda", "de", "gross_margin", "net_margin", "revenue_growth",
    "earnings_growth", "revenue", "profit", "market_cap", "rsi", "macd", "volume", "adx", "ma20", "ma50",
    "ma200", "pct_52w_high", "pct_52w_low",
)
# Router convention (agents/conversation_router.py time_context): hôm qua=1d, tuần trước=7d,
# tháng trước=30d, quý trước=90d, năm ngoái=365d, N unit = N x unit. A trading "session" is
# approximated as one calendar day back (the router prompt defines no session mapping).
_UNIT_DAYS = {"session": 1, "day": 1, "week": 7, "month": 30, "quarter": 90, "year": 365}


def resolve_time(time: dict, today) -> dict:
    """Anchor-independent time label -> router-style {start_date, end_date, explicit} for `today` (a date)."""
    from datetime import date, timedelta
    kind = (time or {}).get("kind", "none")
    if kind == "relative":
        start = today - timedelta(days=time["n"] * _UNIT_DAYS[time["unit"]])
        return {"start_date": start.isoformat(), "end_date": today.isoformat(), "explicit": True}
    if kind == "absolute":
        y, q = time["year"], time.get("quarter")
        if q:
            s, e = date(y, 3 * q - 2, 1), (date(y + (q == 4), (3 * q) % 12 + 1, 1) - timedelta(days=1))
        else:
            s, e = date(y, 1, 1), date(y, 12, 31)
        return {"start_date": s.isoformat(), "end_date": e.isoformat(), "explicit": True}
    return {"start_date": None, "end_date": today.isoformat(), "explicit": False}


def build_time(query: str, kind: str, unit: str | None) -> dict:
    """Model predicts kind (+ unit for relative); numbers are read from the text deterministically."""
    import re
    if kind == "relative" and unit:
        # number must sit next to a time unit, so "VN30 tuần tới" / "HPG 5 phiên" don't read the 30 of VN30
        m = re.search(r"(?<![A-Za-z\d])(\d+)\s*(?:phiên|phien|ngày|ngay|tuần|tuan|tháng|thang|quý|quy|năm|nam|"
                      r"session|day|week|month|quarter|year)", query, re.I)
        return {"kind": "relative", "n": int(m.group(1)) if m else 1, "unit": unit}
    if kind == "absolute":
        y = re.search(r"\b(19|20)\d{2}\b", query)
        q = re.search(r"(?:quý|quy|q)\s*([1-4])\b", query, re.I)
        if y:
            return {"kind": "absolute", "year": int(y.group(0)), "quarter": int(q.group(1)) if q else None}
    return {"kind": "none"}


_EXPLICIT_TIME = None


def _explicit_time(query: str) -> bool:
    """True when the query holds an unmistakable time range (N units, yesterday/last week, a year)."""
    import re
    global _EXPLICIT_TIME
    if _EXPLICIT_TIME is None:
        _EXPLICIT_TIME = re.compile(
            r"\d+\s*(?:phiên|phien|ngày|ngay|tuần|tuan|tháng|thang|năm|nam|quý|quy|sessions?|days?|weeks?|months?|years?)"
            r"|hôm qua|hom qua|(?:tuần|tuan|tháng|thang|quý|quy)\s*(?:trước|truoc|qua)|(?:năm|nam)\s*(?:ngoái|ngoai|trước|truoc|qua)"
            r"|\b20[12]\d\b|yesterday|last\s+(?:week|month|year|quarter)", re.I)
    return bool(_EXPLICIT_TIME.search(query))


TEACHER_FILE = ARTIFACTS / "teacher_synth.json"


def apply_teacher(rows: list[dict]) -> list[dict]:
    """Keep rows whose generator label equals the live router's intent; take topics/flags from the router.

    Rows the router has not labelled (file missing or incomplete) are kept unchanged.
    """
    from collections import Counter
    if not TEACHER_FILE.exists():
        return rows
    teacher = json.loads(TEACHER_FILE.read_text(encoding="utf-8"))
    out, drop, seen = [], Counter(), Counter()
    for r in rows:
        a = teacher.get(r["query"])
        if a is None:
            out.append(r)
            continue
        seen[r["label"]] += 1
        if a.get("intent") != r["label"]:
            drop[r["label"]] += 1
            continue
        agent = a.get("type") in ("agent", "mixed")
        out.append({**r,
                    "market_topics": (a.get("market_topics") or []) if agent else [],
                    "macro_topics": (a.get("macro_topics") or []) if agent else [],
                    "rag_topics": (a.get("rag_topics") or []) if agent else [],
                    "risk_flags": (a.get("risk_flags") or []) if agent else []})
    print(f"teacher: dropped {sum(drop.values())}/{sum(seen.values())} rows where llm_route disagrees with the generator label")
    for k, v in drop.most_common():
        print(f"   teacher-dropped {v:4d}/{seen[k]:4d} {k}")
    return out


def load_synthetic(exclude_queries=(), teacher=True) -> list[dict]:
    """Load data/synthetic.jsonl and drop rows whose label contradicts the router's own rules.

    Rules come from ROUTING_RULES in agents/classifier.py: market_data/closing_commentary/breakout_scan/
    market_brief are market-wide, a single stock's indicator is technical_analysis not screening, a
    single stock's price is price_action. Rows that name a stock under those intents, or market_data
    with no market topic, are label noise from the generating LLM. Also drops rows labelled "no time"
    that contain an explicit time range.
    """
    import re
    import unicodedata
    from collections import Counter, defaultdict
    rows = read_jsonl(DATA / "synthetic.jsonl")
    ex = {q.lower() for q in exclude_queries}

    def norm(s):
        s = unicodedata.normalize("NFD", s.lower().replace("đ", "d"))
        return re.sub(r"[^a-z0-9]+", " ", "".join(c for c in s if unicodedata.category(c) != "Mn")).strip()

    by_text = defaultdict(set)
    for r in rows:
        by_text[norm(r["query"])].add(r["label"])
    unit_words = {"session": "phiên|phien|session", "day": "ngày|ngay|day|hôm qua|hom qua|yesterday",
                  "week": "tuần|tuan|week", "month": "tháng|thang|month", "quarter": "quý|quy|quarter|\\bq\\d",
                  "year": "năm|nam|year"}
    in_scope_macro = re.compile(r"vàng|vang|SJC|dầu|dau tho|brent|usd/?vnd|tỷ giá|ty gia|lãi suất|lai suat|thép|thep"
                              r"|\bgold\b|\boil\b|crude|brent|commodit|\bsteel\b|natural gas|\bsilver\b|vnd", re.I)
    dropped, kept = Counter(), []
    for r in rows:
        why = None
        if r["label"] == "out_of_scope" and r["time"]["kind"] != "none":
            r = {**r, "time": {"kind": "none"}}  # time is meaningless for a refused query
        t = r["time"]
        if len(by_text[norm(r["query"])]) > 1:
            why = "same text under conflicting labels"
        elif r["label"] == "out_of_scope" and (r["tickers"] or in_scope_macro.search(r["query"])):
            # stock/gold/oil/FX/rates questions are in scope per ROUTING_RULES; generator mislabels them
            why = "out_of_scope that names a stock or a macro topic"
        elif r["label"] == "price_action" and not r["tickers"]:
            why = "price_action with no stock (pronoun-only, needs conversation context)"
        elif r["label"] == "closing_commentary" and (t["kind"] == "absolute" or t.get("unit") in ("month", "quarter", "year")):
            why = "closing_commentary over a long period (it is a session wrap-up)"
        elif r["label"] in ("rag_qa", "technical_analysis", "investment_case") and not r["tickers"]:
            why = "single-stock intent with no stock (pronoun/follow-up, needs conversation context)"
        elif r["label"] == "valuation" and (t["kind"] == "absolute" or re.search(r"quý \d|\bq\d", r["query"], re.I)):
            why = "valuation with a specific report period (rag_qa)"
        elif r["label"] == "news_sentiment" and not r["tickers"] and re.search(r"khối ngoại|khoi ngoai|top |thanh khoản|vốn hóa lớn", r["query"], re.I):
            why = "news_sentiment asking market data"
        elif r["label"] == "market_brief" and re.search(r"tổng kết phiên|tong ket phien|market recap|cuối phiên|cuoi phien", r["query"], re.I):
            why = "market_brief asking for a session wrap-up (closing_commentary)"
        elif r["label"] == "market_data" and not r["market_topics"]:
            why = "market_data with no market topic"
        elif r["label"] == "closing_commentary" and not re.search(r"hôm nay|hom nay|hôm qua|phiên|phien|today|day|session|cuối|cuoi|đóng cửa", r["query"], re.I):
            why = "closing_commentary with no session/day cue"
        elif r["label"] == "news_sentiment" and re.search(r"foreign|net buy", r["query"], re.I) and not r["tickers"]:
            why = "news_sentiment asking market data"
        elif r["label"] == "screening" and not r["filters"]:
            why = "screening without filters"
        elif r["label"] == "breakout_scan" and re.search(r"RSI|MACD|P/E|ROE", r["query"], re.I):
            why = "breakout_scan naming an indicator (screening)"
        elif t["kind"] == "relative" and not re.search(unit_words[t["unit"]], r["query"], re.I):
            why = "relative time whose unit is not in the query"
        elif r["query"].lower() in ex:
            why = "duplicate of a gold seed query"
        elif r["label"] == "market_data" and r["tickers"] and not r["market_topics"]:
            why = "market_data naming a stock with no market topic"
        elif r["label"] in ("closing_commentary", "breakout_scan", "market_brief", "screening") and r["tickers"]:
            why = f"{r['label']} naming a stock"
        elif r["label"] == "conversation" and (r["tickers"] or r["time"]["kind"] != "none"):
            # "…VCB, FPT từ đầu năm 2024… mà thật ra mình chỉ hỏi T+2": a stock question abandoned
            # mid-sentence. The ticker/time labels describe the dropped question, and the real
            # router would answer the rule question or decompose — not a plain conversation turn.
            why = "conversation mixing a stock question with a trading-rule question"
        elif r["label"] != "out_of_scope" and r["time"]["kind"] == "none" and _explicit_time(r["query"]):
            why = "labelled no-time but has an explicit time range"
        if why:
            dropped[why] += 1
        else:
            kept.append(r)
    print(f"synthetic: kept {len(kept)}/{len(rows)}")
    for k, v in dropped.most_common():
        print(f"   dropped {v:5d}  {k}")
    return apply_teacher(kept) if teacher else kept


def read_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def embed(texts: list[str]):
    """bge-m3 embeddings, cached on disk by content hash (re-runs on unchanged data are instant)."""
    import hashlib

    import numpy as np
    key = hashlib.md5(("\n".join(texts) + EMBED_MODEL).encode("utf-8")).hexdigest()
    cache = ARTIFACTS / f"emb_{key}.npy"
    if cache.exists():
        return np.load(cache)
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(EMBED_MODEL)
    X = model.encode(texts, normalize_embeddings=True, batch_size=32, show_progress_bar=True)
    ARTIFACTS.mkdir(exist_ok=True)
    np.save(cache, X)
    return X
