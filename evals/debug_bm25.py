"""
evals/debug_bm25.py — Verify BM25 behavior for Bai 14 self-answers.

Checks:
1. How underthesea actually tokenizes financial terms
2. BM25 raw vs vn scores for specific queries (q08, q30, q11, q13)
3. Whether "tài chính" split causes IDF drop (compare token scores)

Usage:
    uv run python evals/debug_bm25.py
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

from rag.retrieval_bm25 import BM25Retriever

COLLECTION = "bctc_structural"
_TICKERS = ["HPG"]  # HPG-specific debug script

# ── 1. Tokenization check ─────────────────────────────────────────────────────

TERMS = [
    "tài chính",
    "đầu tư tài chính dài hạn",
    "lợi nhuận sau thuế",
    "doanh thu thuần quý ba năm 2024",
    "kế toán trưởng",
    "mã chứng khoán HPG",
]

def check_tokenize():
    from underthesea import word_tokenize
    print("=" * 60)
    print("1. TOKENIZATION (underthesea vs split)")
    print("=" * 60)
    for term in TERMS:
        raw = term.lower().split()
        vn  = word_tokenize(term.lower(), format="text").split()
        changed = raw != vn
        marker = "  *** CHANGED ***" if changed else ""
        print(f"\n  Term: {term!r}")
        print(f"    raw  ({len(raw):2d} tokens): {raw}")
        print(f"    vn   ({len(vn):2d} tokens): {vn}{marker}")


# ── 2. Per-query BM25 score comparison ───────────────────────────────────────

QUERIES = {
    "q08": "Đầu tư tài chính dài hạn khoản đầu tư vào công ty con của Công ty mẹ HPG tại ngày 31/12/2025 là bao nhiêu",
    "q11": "HPG niêm yết trên sàn giao dịch chứng khoán nào với mã gì và từ ngày nào",
    "q13": "Giấy Chứng nhận Đăng ký Kinh doanh lần đầu của HPG mang số hiệu gì do cơ quan nào cấp và vào ngày nào",
    "q30": "Lợi nhuận sau thuế TNDN của Công ty mẹ HPG năm 2024 là bao nhiêu",
    "q27": "Hoạt động kinh doanh chính của Công ty mẹ HPG trong năm 2024 là gì",
}

GROUND_TRUTH_KEYWORDS = {
    "q08": "97.018",
    "q11": "15 tháng11 năm 2007",
    "q13": "0503000008",
    "q30": "10.247.400.472.100",
    "q27": "mua bán các sản phẩm thép",
}


def hit(contexts: list[str], keyword: str) -> bool:
    return any(keyword in c for c in contexts)


def score_dump(bm25_raw, bm25_vn, query: str, qid: str):
    from rank_bm25 import BM25Okapi
    keyword = GROUND_TRUTH_KEYWORDS[qid]

    raw_results = bm25_raw.search(query, top_k=5)
    vn_results  = bm25_vn.search(query, top_k=5)

    raw_hit = hit(raw_results, keyword)
    vn_hit  = hit(vn_results, keyword)

    print(f"\n  {qid}: raw={'HIT' if raw_hit else 'MISS'}  vn={'HIT' if vn_hit else 'MISS'}")
    print(f"    keyword: {keyword!r}")

    # Show top-1 context snippet for each
    def snippet(text: str) -> str:
        text = text.replace("\n", " ")
        return text[:120] + "..." if len(text) > 120 else text

    print(f"    raw top-1: {snippet(raw_results[0])}")
    print(f"    vn  top-1: {snippet(vn_results[0])}")

    # Check if keyword chunk appears but ranked lower
    for rank, ctx in enumerate(raw_results, 1):
        if keyword in ctx:
            print(f"    raw: keyword found at rank {rank}")
            break
    else:
        print(f"    raw: keyword NOT in top-5")

    for rank, ctx in enumerate(vn_results, 1):
        if keyword in ctx:
            print(f"    vn:  keyword found at rank {rank}")
            break
    else:
        print(f"    vn:  keyword NOT in top-5")


def check_scores():
    print("\n" + "=" * 60)
    print("2. BM25 SCORE COMPARISON (raw vs vn, top-5)")
    print("=" * 60)
    print("Loading BM25 raw index...")
    bm25_raw = BM25Retriever(COLLECTION, use_vn_tokenize=False, tickers=_TICKERS)
    print("Loading BM25 vn index...")
    bm25_vn  = BM25Retriever(COLLECTION, use_vn_tokenize=True, tickers=_TICKERS)

    for qid, query in QUERIES.items():
        score_dump(bm25_raw, bm25_vn, query, qid)


# ── 3. IDF comparison for "tài chính" split ──────────────────────────────────

def check_idf():
    print("\n" + "=" * 60)
    print("3. IDF ANALYSIS — 'tài chính' split effect")
    print("=" * 60)
    print("Loading corpus (raw)...")
    from qdrant_client import QdrantClient
    from rank_bm25 import BM25Okapi

    client = QdrantClient("localhost", port=6333)
    texts: list[str] = []
    offset = None
    while True:
        results, offset = client.scroll(
            collection_name=COLLECTION, limit=256, offset=offset,
            with_payload=True, with_vectors=False,
        )
        for p in results:
            t = p.payload.get("text", "")
            if t:
                texts.append(t)
        if offset is None:
            break

    print(f"  Corpus: {len(texts)} chunks")

    raw_corpus = [t.lower().split() for t in texts]
    bm25_raw = BM25Okapi(raw_corpus)

    from underthesea import word_tokenize
    vn_corpus = [word_tokenize(t.lower(), format="text").split() for t in texts]
    bm25_vn = BM25Okapi(vn_corpus)

    check_terms = [
        ("tài", "chính", "tài chính", "tài_chính"),
    ]
    for raw_a, raw_b, compound_raw, compound_vn in check_terms:
        # Count doc frequency
        df_a   = sum(1 for doc in raw_corpus if raw_a in doc)
        df_b   = sum(1 for doc in raw_corpus if raw_b in doc)
        df_comp_raw = sum(1 for doc in raw_corpus if compound_raw in " ".join(doc))
        df_comp_vn  = sum(1 for doc in vn_corpus  if compound_vn in doc)

        import math
        n = len(texts)
        idf = lambda df: math.log((n - df + 0.5) / (df + 0.5) + 1)

        print(f"\n  Token analysis:")
        print(f"    '{raw_a}'         : df={df_a:4d}/{n}  idf={idf(df_a):.3f}")
        print(f"    '{raw_b}'         : df={df_b:4d}/{n}  idf={idf(df_b):.3f}")
        print(f"    '{compound_raw}'  : df={df_comp_raw:4d}/{n}  idf={idf(df_comp_raw):.3f}  (raw, 2-word phrase count)")
        print(f"    '{compound_vn}'   : df={df_comp_vn:4d}/{n}  idf={idf(df_comp_vn):.3f}  (vn compound token)")
        print(f"\n  → If vn tokenizer joins 'tài_chính': higher IDF = stronger signal")
        print(f"  → If vn tokenizer splits: same IDF as raw = no benefit")

        # Verify actual tokenization of "tài chính"
        sample = word_tokenize("tài chính", format="text")
        print(f"\n  Actual tokenization of 'tài chính': {sample!r}")
        sample2 = word_tokenize("đầu tư tài chính dài hạn", format="text")
        print(f"  Actual tokenization of 'đầu tư tài chính dài hạn': {sample2!r}")


if __name__ == "__main__":
    check_tokenize()
    check_scores()
    check_idf()
    print("\n" + "=" * 60)
    print("DONE")
    print("=" * 60)
