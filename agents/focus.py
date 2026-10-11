"""
agents/focus.py — Dialogue-state focus: the single source of truth for "what is this
conversation currently about".

Splits the turn into distinct layers, each with one job (mature stock-chat design):

  1. Entity extraction — deterministic (wraps core.tickers.extract_tickers). This is the
     ONLY place that decides "did the user name something new."
  2. Focus object — one explicit `Focus` per conversation: tickers/sector/intent/query.
  3. Carry-forward rule (resolve_focus) — pure, no LLM. Replaces the old marker-only
     continuation heuristic so a message naming a new ticker can never be misread as
     "inherit the old subject".

No LLM calls here — pure, unit-testable functions.
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field

# Follow-ups that name no financial action of their own → the turn inherits the prior
# intent/subject. Everything else classifies fresh.
_CONTINUATION_MARKERS = (
    "sâu hơn", "chi tiết hơn", "cụ thể hơn", "rõ hơn",
    "phân tích thêm", "more detail", "elaborate",
)

# resolve_focus outcome kinds.
FRESH = "fresh"          # message names ≥1 new entity → replace focus (never carry forward)
CONTINUE = "continue"    # no entity + continuation phrase → carry the whole prior focus forward
AMBIGUOUS = "ambiguous"  # no entity, no continuation phrase, no other signal → LLM/clarify decide


@dataclass
class Focus:
    tickers: list[str] = field(default_factory=list)  # all tickers in scope (comparison-safe)
    sector: str = ""                                   # sector/index subject when no specific ticker
    intent: str = ""                                   # last resolved intent
    query: str = ""                                    # last self-contained query (trace/debug only)
    updated_at: str = ""                               # ISO timestamp


def extract_focus_entities(query: str) -> list[str]:
    """Deterministic entity extraction — wraps core.tickers.extract_tickers.

    This is the ONLY place that decides "did the user name something new."
    """
    from core.tickers import extract_tickers
    return extract_tickers(query or "")


# Market-index subjects (not in securities data — checked before sector labels).
_INDEX_SUBJECTS = (
    ("vnindex", "VNINDEX"),
    ("vn-index", "VNINDEX"),
    ("vn30", "VN30"),
    ("hnx", "HNX"),
)


_SECTOR_ALIASES: tuple[tuple[str, str], ...] = (
    ("bank", "ngan hang"), ("thep con", "thep"), ("bds", "bat dong san"),
    ("bat dong san", "bat dong san"), ("dau khi", "dau khi"),
    ("oil", "dau khi"), ("steel", "thep"), ("real estate", "bat dong san"), ("securities", "chung khoan"),
)


def _strip_accents(text: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFD", (text or "").lower().replace("đ", "d"))
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def extract_focus_sector(query: str) -> str:
    """Deterministic sector/index subject for macro_sector / market_brief queries.

    Returns the longest known sector label/tag present in the query — sourced from
    `securities.sector` + `securities.tags` (data-driven, no hardcoded gazetteer) — or
    "" when the query names no known sector/index. Used to carry the subject across
    turns when there is no specific ticker.
    """
    q = (query or "").lower()
    if not q:
        return ""
    for kw, label in _INDEX_SUBJECTS:
        if kw in q:
            return label
    from core.tickers import _load_sector_labels
    labels = _load_sector_labels()
    for label in labels:
        if label in q:
            return label
    # Slang / unaccented fallback: "bank", "thép con", "bds", "ngan hang" -> a known label.
    q_plain = _strip_accents(q)
    for alias, target in _SECTOR_ALIASES:
        if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", q_plain):
            for label in labels:
                if target in _strip_accents(label):
                    return label
    for label in sorted(labels, key=len, reverse=True):
        if _strip_accents(label) in q_plain:
            return label
    return ""


def _is_continuation_phrase(query: str) -> bool:
    q = (query or "").lower()
    return any(m in q for m in _CONTINUATION_MARKERS)


def resolve_focus(query: str, prior: Focus | None) -> tuple[str, list[str]]:
    """Pure carry-forward rule. No LLM.

    Returns (kind, entities):
      - (FRESH, new_tickers)      when the message names ≥1 ticker
      - (CONTINUE, prior.tickers) when no ticker AND a continuation phrase matches AND
                                  the prior focus has something to carry (tickers, sector,
                                  or intent) — e.g. a bare "phân tích sâu hơn"
      - (AMBIGUOUS, [])           otherwise — let the LLM/clarify decide

    A continuation phrase AND a new ticker in the same message ("phân tích thêm HPG")
    is FRESH, not CONTINUE — this is the bug being fixed: the new ticker must NOT be
    swallowed by carry-forward.
    """
    entities = extract_focus_entities(query)
    if entities:
        return FRESH, entities
    if prior and _is_continuation_phrase(query) and (prior.tickers or prior.sector or prior.intent):
        return CONTINUE, list(prior.tickers)
    return AMBIGUOUS, []
