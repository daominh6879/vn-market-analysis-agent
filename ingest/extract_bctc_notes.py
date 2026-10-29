"""
ingest/extract_bctc_notes.py — read figures that only exist in the notes of the annual BCTC → bctc_note_facts.

For vn-portfolio's rule engine (khung v2.2): 5.5 rf.related_party_lending (receivables / loans to related parties vs
equity), rf.bond_pressure (bonds due within 12 months vs cash), 5.6 / 5.8 bank metrics (loan groups 1–5, demand
deposits = CASA, restructured loans, corporate bonds held).

Only for figures no structured source has (user 29/10: do not lean on the PDFs): bank loan groups / NPL come from
FireAnt financial-data (fireant_financial_data); this module reads CASA, bank corporate-bond holdings, related-party
balances and bonds due — and bank loan groups only on request (--topics bank_quality) to cross-check FireAnt.

Same rule as extract_facts.py: the LLM only reads tables into a typed schema; nothing is computed by it. For each
active indexed report (documents → Qdrant bctc_structural) the chunks whose text names the topic are sent (with the
next chunk, tables often continue there); every returned value carries the row label it was read from. Values are
sanity-checked against each other and the report's own totals before they are stored.

Usage (debug wrapper around run(), Dagster asset bctc_notes_extract):
    python ingest/extract_bctc_notes.py --tickers HPG,VCB,NVL
    python ingest/extract_bctc_notes.py --force
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from core.db import get_conn

COLLECTION = "bctc_structural"
MAX_CHARS = 20000                 # per topic prompt
BANK_SECTORS = ("Ngân hàng",)

TOPICS = {
    "related_party": {
        "keywords": ("bên liên quan",),
        "must_any": ("phải thu", "cho vay", "số dư", "phảithu", "chovay"),
        "metrics": {
            "related_party_receivables": "Tổng số dư phải thu các bên liên quan (ngắn hạn + dài hạn, gồm phải thu khách "
                                         "hàng, trả trước, phải thu khác) tại ngày kết thúc năm",
            "related_party_loans": "Tổng số dư cho vay (phải thu về cho vay) các bên liên quan tại ngày kết thúc năm",
            "related_party_non_subsidiary": "Tổng phải thu + cho vay các bên liên quan KHÔNG phải công ty con (công ty "
                                            "liên kết, cùng chủ sở hữu, cá nhân/doanh nghiệp liên quan lãnh đạo) tại "
                                            "ngày kết thúc năm; cộng các dòng có mối quan hệ khác 'công ty con'",
        },
    },
    "bonds": {
        "keywords": ("trái phiếu",),
        "must_any": ("phát hành", "đáo hạn", "vay", "ngắn hạn"),
        "metrics": {
            "bonds_payable": "Tổng số dư trái phiếu do chính công ty phát hành (nợ phải trả) tại ngày kết thúc năm",
            "bonds_due_12m": "Phần trái phiếu do công ty phát hành đến hạn trả trong 12 tháng tới (trái phiếu ngắn hạn / "
                             "nợ dài hạn đến hạn trả là trái phiếu) tại ngày kết thúc năm",
        },
    },
    "audit_opinion": {
        "keywords": ("ý kiến của kiểm toán viên", "ý kiến kiểm toán", "cơ sở của ý kiến", "kết luận của kiểm toán"),
        "must_any": ("kiểm toán", "ý kiến"),
        "metrics": {
            "audit_opinion": "Loại ý kiến kiểm toán về báo cáo tài chính năm: 0 = chấp nhận toàn phần, 1 = ngoại trừ, "
                             "2 = từ chối đưa ra ý kiến, 3 = trái ngược. value_vnd = mã số; quote = nguyên câu tiêu đề "
                             "hoặc câu kết luận của kiểm toán viên",
        },
    },
    "bank_quality": {
        "keywords": ("nợ cần chú ý", "chất lượng nợ", "nợ đủ tiêu chuẩn"),
        "must_any": ("triệu", "vnd", "đồng"),
        "metrics": {
            "loans_group1": "Dư nợ cho vay khách hàng Nợ đủ tiêu chuẩn (nhóm 1) cuối năm",
            "loans_group2": "Nợ cần chú ý (nhóm 2) cuối năm",
            "loans_group3": "Nợ dưới tiêu chuẩn (nhóm 3) cuối năm",
            "loans_group4": "Nợ nghi ngờ (nhóm 4) cuối năm",
            "loans_group5": "Nợ có khả năng mất vốn (nhóm 5) cuối năm",
            "customer_loans": "Tổng dư nợ cho vay khách hàng cuối năm (trước dự phòng)",
            "restructured_loans": "Dư nợ được cơ cấu lại thời hạn trả nợ / giữ nguyên nhóm nợ cuối năm (nếu thuyết minh có)",
        },
    },
    "bank_deposits": {
        "keywords": ("tiền gửi không kỳ hạn",),
        "must_any": ("triệu", "vnd", "khách hàng"),
        "metrics": {
            "demand_deposits": "Tiền gửi không kỳ hạn của khách hàng cuối năm (bảng 'Tiền gửi của khách hàng' phân "
                               "theo loại tiền gửi; không phải tiền gửi tại NHNN hay tại TCTD khác)",
            "customer_deposits": "Tổng tiền gửi của khách hàng cuối năm",
        },
    },
    "bank_bonds": {
        "keywords": ("tổ chức kinh tế trong nước phát hành", "trái phiếu doanh nghiệp"),
        "must_any": ("triệu", "vnd", "đồng"),
        "metrics": {
            "corporate_bonds_held": "Trái phiếu do các tổ chức kinh tế trong nước phát hành mà ngân hàng nắm giữ (cộng "
                                    "chứng khoán đầu tư sẵn sàng để bán + giữ đến ngày đáo hạn + kinh doanh) cuối năm",
        },
    },
}

_TOOL = {
    "name": "save_note_facts",
    "description": "Save figures read from the notes of a Vietnamese financial statement",
    "parameters": {
        "type": "object",
        "properties": {
            "facts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "metric_code": {"type": "string"},
                        "value_vnd": {"type": "number", "description": "Full VND (convert from triệu/tỷ as the table states)"},
                        "quote": {"type": "string", "description": "Row label and number exactly as written"},
                    },
                    "required": ["metric_code", "value_vnd", "quote"],
                },
            }
        },
        "required": ["facts"],
    },
}


def _docs(tickers: list[str] | None, force: bool) -> list[dict]:
    """Active indexed reports (ticker/year come from the Qdrant payload of their chunks)."""
    from qdrant_client import QdrantClient
    from core.config import settings

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT doc_id, source_uri FROM documents WHERE status = 'active' AND collection = %s", (COLLECTION,))
        active = dict(cur.fetchall())
        cur.execute("SELECT DISTINCT ticker, period FROM bctc_note_facts")
        done = set(cur.fetchall())
        cur.execute("SELECT ticker, sector FROM securities")
        sectors = dict(cur.fetchall())
    q = QdrantClient(settings.QDRANT_HOST, port=settings.QDRANT_PORT, timeout=60)
    out, offset = {}, None
    while True:      # one pass over payloads (no vectors) to map doc_id → ticker/year/report type
        pts, offset = q.scroll(COLLECTION, limit=2000, offset=offset, with_payload=["doc_id", "ticker", "year",
                                                                                     "report_type", "source_key"],
                               with_vectors=False)
        for p in pts:
            pl = p.payload or {}
            d = pl.get("doc_id")
            if d in active and d not in out:
                out[d] = {"doc_id": d, "ticker": pl.get("ticker"), "year": str(pl.get("year")),
                          "report_type": pl.get("report_type"), "source_key": pl.get("source_key") or active[d]}
        if offset is None:
            break
    docs = [x for x in out.values() if x["ticker"] and (not tickers or x["ticker"] in tickers)
            and (force or (x["ticker"], x["year"]) not in done)]
    for x in docs:
        x["bank"] = sectors.get(x["ticker"]) in BANK_SECTORS
    return sorted(docs, key=lambda x: x["ticker"])


def _chunks(doc_id: str) -> list[tuple[int, str]]:
    from qdrant_client import QdrantClient
    from qdrant_client.models import FieldCondition, Filter, MatchValue
    from core.config import settings

    q = QdrantClient(settings.QDRANT_HOST, port=settings.QDRANT_PORT, timeout=60)
    flt = Filter(must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))])
    out, offset = [], None
    while True:
        pts, offset = q.scroll(COLLECTION, scroll_filter=flt, limit=500, offset=offset, with_payload=["idx", "text"],
                               with_vectors=False)
        out += [(int(p.payload.get("idx") or 0), p.payload.get("text") or "") for p in pts]
        if offset is None:
            break
    return sorted(out)


def _squash(text: str) -> str:
    """Lower-case without whitespace: OCR often drops or inserts spaces ('Nợcầnchúý')."""
    return re.sub(r"\s+", "", text.lower())


def _select(chunks: list[tuple[int, str]], topic: dict) -> str:
    """Chunks naming the topic (and the next one), in report order, capped at MAX_CHARS."""
    keep: set[int] = set()
    kws = [_squash(k) for k in topic["keywords"]]
    must = [_squash(m) for m in topic["must_any"]]
    for i, (_, text) in enumerate(chunks):
        t = _squash(text)
        if any(k in t for k in kws) and any(m in t for m in must):
            keep.update((i, i + 1))
    parts, size = [], 0
    for i in sorted(k for k in keep if k < len(chunks)):
        text = re.sub(r"[ \t]+", " ", chunks[i][1])
        if size + len(text) > MAX_CHARS:
            break
        parts.append(f"[đoạn {chunks[i][0]}]\n{text}")
        size += len(text)
    return "\n\n".join(parts)


def _ask(client, doc: dict, topic_name: str, text: str) -> list[dict]:
    from llm.types import Message

    metrics = TOPICS[topic_name]["metrics"]
    spec = "\n".join(f"- {k}: {v}" for k, v in metrics.items())
    prompt = (
        f"Đây là các đoạn thuyết minh trích từ báo cáo tài chính năm {doc['year']} của {doc['ticker']} "
        f"(văn bản OCR, có thể lỗi chính tả). Đọc các bảng và trả về những chỉ tiêu sau nếu báo cáo CÓ ghi rõ số:\n"
        f"{spec}\n\nQuy tắc: chỉ lấy số tại ngày cuối năm {doc['year']} (cột số cuối năm/31/12/{doc['year']}), "
        f"không lấy số đầu năm. Đổi về đồng theo đơn vị bảng ghi (Triệu VND ×1.000.000, tỷ ×1.000.000.000). "
        f"Không tự cộng/ước tính nếu báo cáo không có dòng tổng; chỉ cộng khi chỉ tiêu yêu cầu tổng và các dòng "
        f"thành phần đều có trong bảng. Chỉ tiêu nào không thấy thì bỏ qua. 'quote' ghi nguyên dòng và số đã đọc.\n\n"
        f"{text}"
    )
    resp = client.generate(messages=[Message(role="user", content=prompt)], tools=[_TOOL], max_tokens=2048,
                           temperature=0)
    raw = resp.tool_calls[0].input.get("facts") if resp.tool_calls else None
    if raw is None:
        m = re.search(r"\{.*\}|\[.*\]", resp.text or "", re.S)
        if not m:
            return []
        try:
            j = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
        raw = j.get("facts", []) if isinstance(j, dict) else j
    out = []
    for f in raw or []:
        code = str(f.get("metric_code", "")).strip()
        try:
            v = float(f.get("value_vnd"))
        except (TypeError, ValueError):
            continue
        quote = str(f.get("quote", ""))[:300]
        if code == "audit_opinion" and not _opinion_confirmed(int(v), quote):
            continue                                       # the quoted wording must carry the opinion type
        if code in metrics and v >= 0:
            out.append({"metric_code": code, "value": v, "quote": quote})
    return out


# the opinion type must be named as an opinion, not just the word: HSL 2025 "ngoại trừ ảnh hưởng của vấn đề nêu tại
# đoạn 'Vấn đề cần nhấn mạnh'" is an emphasis of matter, not a qualified opinion. OCR writes "ngoài trừ" too.
OPINION_RX = {1: r"ý kiến (kiểm toán )?ngo[aạà]i trừ|cơ sở (của|đưa ra) ý kiến (kiểm toán )?ngo[aạà]i trừ",
              2: r"từ chối (đưa ra )?ý kiến|ý kiến (kiểm toán )?từ chối",
              3: r"ý kiến (kiểm toán )?trái ngược|cơ sở (của|đưa ra) ý kiến (kiểm toán )?trái ngược"}


def _opinion_confirmed(code: int, quote: str) -> bool:
    """A modified opinion only counts when the quoted sentence names it (the LLM locates, the text decides);
    a clean opinion when the quote names none of them."""
    q = quote.lower()
    if code in OPINION_RX:
        return re.search(OPINION_RX[code], q) is not None
    return code == 0 and not any(re.search(rx, q) for rx in OPINION_RX.values())


def _sane(facts: list[dict], bank: bool) -> list[dict]:
    """Drop values that cannot be right: unit slips (a bank loan book under 1,000 tỷ, groups above the book),
    a part above its whole."""
    by = {f["metric_code"]: f["value"] for f in facts}
    bad = set()
    if bank:
        book = by.get("customer_loans")
        if book is not None and book < 1e12:
            bad.update(k for k in by if k.startswith("loans_group") or k == "customer_loans")
        for k, v in by.items():
            if book and k.startswith("loans_group") and v > book * 1.01:
                bad.add(k)
        if by.get("demand_deposits") and by.get("customer_deposits") and by["demand_deposits"] > by["customer_deposits"]:
            bad.add("demand_deposits")
    if by.get("bonds_due_12m") and by.get("bonds_payable") and by["bonds_due_12m"] > by["bonds_payable"] * 1.01:
        bad.add("bonds_due_12m")
    return [f for f in facts if f["metric_code"] not in bad]


def _store(doc: dict, facts: list[dict]) -> int:
    if not facts:
        return 0
    with get_conn() as conn, conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO bctc_note_facts (ticker, period, metric_code, value, source_file, quote)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticker, period, metric_code) DO UPDATE SET value = EXCLUDED.value,
                source_file = EXCLUDED.source_file, quote = EXCLUDED.quote, extracted_at = NOW()""",
            [(doc["ticker"], doc["year"], f["metric_code"], f["value"], doc["source_key"], f["quote"]) for f in facts])
    return len(facts)


def _mark_read(doc: dict) -> None:
    """A report with none of the figures still counts as read (sentinel row), so the weekly run moves on."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO bctc_note_facts (ticker, period, metric_code, value, source_file, quote)
                       VALUES (%s, %s, '_read', 0, %s, %s) ON CONFLICT (ticker, period, metric_code) DO UPDATE
                       SET extracted_at = NOW()""", (doc["ticker"], doc["year"], doc["source_key"],
                                                    doc.get("report_type") or ""))


def run(tickers: list[str] | None = None, force: bool = False, limit: int | None = None,
        topics: tuple[str, ...] | None = None) -> dict:
    from llm import create_client

    docs = _docs(tickers, force)[:limit] if limit else _docs(tickers, force)
    client = create_client()
    stored, read, failed = 0, 0, []
    for doc in docs:
        try:
            chunks = _chunks(doc["doc_id"])
            facts = []
            # bank loan quality comes from FireAnt (fireant_financial_data); bank_quality stays for cross-checks (--topics)
            for name in (topics or (("bank_deposits", "bank_bonds", "audit_opinion") if doc["bank"]
                                    else ("related_party", "bonds", "audit_opinion"))):
                text = _select(chunks, TOPICS[name])
                if text:
                    facts += _ask(client, doc, name, text)
            stored += _store(doc, _sane(facts, doc["bank"]))
            _mark_read(doc)
            read += 1
        except Exception as exc:                  # one report failing must not stop the run
            failed.append((doc["ticker"], repr(exc)[:160]))
    if docs and read == 0:
        raise RuntimeError(f"bctc_notes: no report could be read; failed={failed[:5]}")
    return {"reports": len(docs), "read": read, "facts": stored, "failed": len(failed), "failed_sample": failed[:5]}


def main() -> None:
    ap = argparse.ArgumentParser(description="BCTC notes → bctc_note_facts")
    ap.add_argument("--tickers", default="")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--topics", default="", help=f"comma list of {sorted(TOPICS)} (default by company type)")
    a = ap.parse_args()
    tickers = [t.strip().upper() for t in a.tickers.split(",") if t.strip()] or None
    topics = tuple(x.strip() for x in a.topics.split(",") if x.strip()) or None
    print(run(tickers, a.force, a.limit, topics))


if __name__ == "__main__":
    main()
