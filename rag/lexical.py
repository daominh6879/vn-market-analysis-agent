"""
rag/lexical.py — OCR-tolerant lexical scoring for scanned BCTC chunks.

Scanned tables come out of OCR with dropped spaces ("Tiềnvàcáckhoảntươngđươngtiền") and wrong diacritics
("TONGCỘNGTÀISAN"), so vector search ranks the row we need far down. Folding both sides to bare [a-z0-9]
makes "tổng tài sản" match "TONGCỘNGTÀISAN" and "Tổng tài sản" alike. Pure functions, no I/O.
"""
from __future__ import annotations

import re
import unicodedata

# Words that carry no lookup value in a finance question. Matched on the accented form: folded, "tại" (at) collides
# with "tài" (asset) and "có" with "cổ" (cổ phiếu). Queries typed without accents only use the unambiguous ASCII set.
_STOPWORDS = frozenset(
    "của là bao nhiêu cuối năm hiện tại các những cho và với trong ở được có không nào gì thế ra sao như "
    "sánh hãy biết tôi về đến từ một hai này đó thì nên".split()
)
_ASCII_STOPWORDS = frozenset("cua la bao nhieu cuoi nam hien cac nhung voi trong duoc khong nao gi".split())
_RRF_K = 60


def fold(text: str) -> str:
    """Lowercase, strip accents (đ→d) and every non-alphanumeric char, including spaces."""
    s = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in s if unicodedata.category(c) != "Mn"))


def query_ngrams(question: str, drop: set[str] | None = None) -> dict[str, int]:
    """Folded n-grams (1-4 adjacent content words, joined without spaces) of the question → weight (words²).

    `drop` holds folded tokens to ignore (tickers; the payload filter already handles them). Bare years are ignored too.
    """
    drop = drop or set()
    words: list[str | None] = []
    for raw in re.findall(r"\w+", question.lower()):
        w = fold(raw)
        if (not w or raw in _STOPWORDS or (raw.isascii() and raw in _ASCII_STOPWORDS) or w in drop
                or re.fullmatch(r"(19|20)\d\d", w)):
            words.append(None)      # keeps n-grams from spanning a removed word
        else:
            words.append(w)
    grams: dict[str, int] = {}
    for n in range(1, 5):
        for i in range(len(words) - n + 1):
            window = words[i:i + n]
            if None in window:
                continue
            g = "".join(window)    # type: ignore[arg-type]
            if len(g) >= 4:         # 1-3 char fragments match everywhere
                grams[g] = max(grams.get(g, 0), n * n)
    # BCTC rows say "Tổng cộng X" where a question says "Tổng X"
    for g, w in list(grams.items()):
        if g.startswith("tong") and not g.startswith("tongcong") and len(g) > 4:
            grams.setdefault("tongcong" + g[4:], w)
    return grams


def lexical_score(folded_chunk: str, grams: dict[str, int]) -> int:
    return sum(w for g, w in grams.items() if g in folded_chunk)


_PRIMARY_STATEMENT_KEYS = ("bangcandoiketoan", "baocaotinhhinhtaichinh", "baocaoketquahoatdong", "baocaoluuchuyentien")


def is_primary_statement(chunk: str) -> bool:
    """True when the chunk belongs to a primary statement (balance sheet, income, cash flow), not to the notes.

    Reads the "[Ngữ cảnh: <statement> ...]" line rag.chunking.add_table_context puts on mid-table chunks, or the
    statement title the chunk starts with. Headline figures (total assets, equity...) live there; notes only break them down.
    """
    return any(key in fold(chunk[:160]) for key in _PRIMARY_STATEMENT_KEYS)


def hybrid_rank(vector_ids: list, lexical_scores: dict, boost_ids: frozenset | set = frozenset(),
                lexical_weight: float = 2.0, k: int = _RRF_K, anchors: int = 0) -> list:
    """Order ids by vector rank (RRF term 1/(k+rank)) plus a lexical term that uses the match *strength*.

    Rank-based fusion hides how much stronger a text match is: on OCR'd tables the right row matches with score 18
    while the vector favourites score 4-5, yet both sit a few ranks apart. The lexical term is
    lexical_weight/(k+1) * (score/best_score)², so the best text match is worth `lexical_weight` top vector ranks and a
    weak one almost nothing. `boost_ids` (primary statements) get a 5% score edge, which only decides near-ties.
    Ties keep vector order.

    The first `anchors` vector hits always lead the result. A broad question ("trích lập dự phòng rủi ro tín dụng")
    matches dozens of chunks lexically about equally, which would evict the vector favourites; anchors let the lexical
    side add chunks without ever removing the best semantic ones.
    """
    fused: dict = {pid: 1.0 / (k + rank) for rank, pid in enumerate(vector_ids, 1)}
    best = max(lexical_scores.values(), default=0)
    if best > 0:
        for pid, score in lexical_scores.items():
            if score > 0:
                norm = score / best * (1.05 if pid in boost_ids else 1.0)
                fused[pid] = fused.get(pid, 0.0) + lexical_weight / (k + 1) * norm ** 2
    order = {pid: i for i, pid in enumerate(vector_ids)}
    head = list(vector_ids[:anchors])
    rest = sorted((pid for pid in fused if pid not in head), key=lambda pid: (-fused[pid], order.get(pid, len(order))))
    return head + rest
