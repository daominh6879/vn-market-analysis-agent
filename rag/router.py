"""
rag/router.py — Question classifier for the RAG + SQL hybrid pipeline (Bài 18).

Routes each question to one of 4 labels:
  diễn_giải    → text interpretation from documents → RAG path
  số_liệu      → numerical aggregation/ranking across the entire DB → SQL path
  cả_hai       → needs both DB numbers AND textual context → SQL + RAG
  ngoài_phạm_vi → out of scope / not in any data source → refusal

Key signal for số_liệu: "top N", "highest/lowest X across all records",
"aggregate over time", "calculate ratio from raw data" — vector search
CANNOT answer these in principle because it retrieves document chunks, not
structured rows to aggregate.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from llm.types import Message

LABELS = ("diễn_giải", "số_liệu", "cả_hai", "ngoài_phạm_vi")

_SYSTEM = """\
You are a financial question router for an HPG (Hòa Phát) financial assistant.
Classify the user's question into exactly one of these labels:

  diễn_giải   — qualitative/textual lookup FROM DOCUMENTS: auditor info, business
                  description, personnel (kế toán trưởng, nhân viên), company history,
                  legal registration, accounting standards, subsidiary list AS STATED
                  IN THE REPORT (e.g. "how many subsidiaries?" → the document states this
                  directly, no DB aggregation needed).

  số_liệu     — requires SQL aggregation over the structured DATABASE:
                  • Ranking/top-N across multiple records (e.g. "top 5 mã ROE cao nhất")
                  • Extremal values (e.g. "highest/lowest X in the entire database")
                  • Arithmetic aggregation (SUM, AVG, COUNT across many rows)
                  • Historical stock price queries (stock_prices table EXISTS in DB:
                    ticker, trade_date, close_adj, volume — ANY question about historical
                    price data goes here, NOT ngoài_phạm_vi)
                  • Cross-period calculation using DB rows
                  Vector search CANNOT answer these; they need SQL.

  cả_hai      — needs BOTH: a numerical result from DB AND explanatory text context
                  (e.g. "tổng tài sản HPG 2025 là bao nhiêu và tăng trưởng này phản ánh
                  chiến lược gì?" — number from DB + strategy explanation from document).
                  Also: stock price TREND + business result correlation → cả_hai.

  ngoài_phạm_vi — NOT in any data source (financial reports OR the database):
                  • Commodity/market prices (HRC, steel spot prices)
                  • Direct buy/sell advice with no screenable metric
                    ("có nên mua HPG không?" — subjective, no DB criterion)
                  • Manufacturing/operational details (production volume in tonnes,
                    furnace technology, steel plant operations) — these are NOT in
                    financial reports or financial_facts/stock_prices tables
                  • Competitor data (other companies not in our DB)
                  • Future projections/dividend policy not yet disclosed

  IMPORTANT — time phrases do NOT make a question out of scope:
    "trong thời gian này", "hiện tại", "gần đây", "thời điểm này" are all
    valid references to "use the most recent available data." Treat them as
    implicit filters (latest period) and route to số_liệu or cả_hai.

  IMPORTANT — screening questions ARE answerable via số_liệu:
    "cổ phiếu nào đáng chú ý?" → screen by ROE/profit/revenue in DB → số_liệu
    "mã nào nổi bật gần đây?"  → screen latest financial_facts → số_liệu
    These are NOT investment advice — they are data queries with implicit criteria.

Key distinctions:
  "how many employees/subsidiaries?" → STATED IN DOCUMENT → diễn_giải
  "highest stock price in 2024?"     → QUERY stock_prices DB → số_liệu
  "cổ phiếu nào đáng chú ý?"        → screen financial_facts DB → số_liệu
  "production volume in tonnes?"     → NOT IN ANY DATA → ngoài_phạm_vi
  "furnace technology at Dung Quat?" → NOT IN ANY DATA → ngoài_phạm_vi
  "có nên mua HPG không?"            → subjective advice → ngoài_phạm_vi

Call the route_question tool with the correct label and a one-sentence reason."""

_TOOL = {
    "name": "route_question",
    "description": "Classify a financial question into one routing label.",
    "input_schema": {
        "type": "object",
        "properties": {
            "label": {
                "type": "string",
                "enum": list(LABELS),
                "description": "One of: diễn_giải | số_liệu | cả_hai | ngoài_phạm_vi",
            },
            "reason": {
                "type": "string",
                "description": "One sentence explaining the classification.",
            },
        },
        "required": ["label", "reason"],
    },
}


@dataclass
class RouteResult:
    label: str   # one of LABELS
    reason: str


def classify(question: str, client=None) -> RouteResult:
    """Classify a question. Returns RouteResult with label + reason."""
    if client is None:
        from llm.factory import create_client
        client = create_client()

    resp = client.generate(
        messages=[Message(role="user", content=question)],
        system=_SYSTEM,
        tools=[_TOOL],
        max_tokens=512,
    )

    if resp.tool_calls:
        tc = resp.tool_calls[0]
        label = tc.input.get("label", "ngoài_phạm_vi")
        reason = tc.input.get("reason", "")
        if label not in LABELS:
            label = "ngoài_phạm_vi"
        return RouteResult(label=label, reason=reason)

    # Fallback: scan text for label keyword
    text = resp.text.strip().lower()
    for label in LABELS:
        if label in text:
            return RouteResult(label=label, reason=resp.text)

    return RouteResult(label="ngoài_phạm_vi", reason=resp.text)


def batch_classify(questions: list[str], client=None) -> list[RouteResult]:
    """Classify a list of questions (sequential — avoids rate limits)."""
    if client is None:
        from llm.factory import create_client
        client = create_client()
    return [classify(q, client=client) for q in questions]
