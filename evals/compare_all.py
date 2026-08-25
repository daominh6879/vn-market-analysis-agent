"""
evals/compare_all.py — Full matrix: chunking × meta × embed model × retriever.

Tự chấm điểm bằng context_hit@k — không gọi LLM API.

Configs tested:
  Chunking × meta  (tất cả dùng bge-m3):
    hpg_b7_fixed_nometa      fixed_512    | no meta
    hpg_b7_structural_nometa structural   | no meta  ← winner Bài 7
    hpg_b7_hier_nometa       hierarchical | no meta
    hpg_b7_structural_meta   structural   | meta
    hpg_b7_fixed_meta        fixed_512    | meta
    hpg_b7_hier_meta         hierarchical | meta

  Embedding models (tất cả dùng fixed_512, no meta):
    hpg_emb_nomic-embed-text  nomic-embed-text
    hpg_emb_bge-m3            bge-m3
    hpg_emb_mxbai-embed-large mxbai-embed-large

  Current winner:
    hpg_structural            structural | no meta | bge-m3  (Bài 14 baseline)

Retriever per config:
  - vector  (dùng embed_model của config đó)
  - bm25_raw
  - bm25_vn

Usage:
    uv run python evals/compare_all.py
    uv run python evals/compare_all.py --top-k 3
    uv run python evals/compare_all.py --out evals/compare_all.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

import yaml

# ── Collection configs ────────────────────────────────────────────────────────

CONFIGS = [
    # label                          collection                   embed_model   group
    ("fixed_512|no-meta|bge-m3",    "hpg_b7_fixed_nometa",       "bge-m3",     "chunking"),
    ("structural|no-meta|bge-m3",   "hpg_b7_structural_nometa",  "bge-m3",     "chunking"),
    ("hierarch|no-meta|bge-m3",     "hpg_b7_hier_nometa",        "bge-m3",     "chunking"),
    ("fixed_512|meta|bge-m3",       "hpg_b7_fixed_meta",         "bge-m3",     "meta"),
    ("structural|meta|bge-m3",      "hpg_b7_structural_meta",    "bge-m3",     "meta"),
    ("hierarch|meta|bge-m3",        "hpg_b7_hier_meta",          "bge-m3",     "meta"),
]

# ── Ground-truth keywords ─────────────────────────────────────────────────────

GT_KEYWORDS: dict[str, list[str]] = {
    # 2025 PDF
    "q08": ["97.018.349.440.000"],
    "q09": ["131"],
    "q10": ["tư vấn quản lý"],
    "q11": ["15 tháng 11 năm 2007"],
    "q12": ["Phạm Thị Kim Oanh"],
    "q13": ["0503000008"],
    "q31": ["98.670.778.691.605"],
    "q32": ["14.074.169.615.158"],
    "q33": ["94.430.926.468.210"],
    "q34": ["Deloitte"],
    "q35": ["14.347.362.462.056"],
    "q37": ["2.859.500.000.000"],
    # 2024 PDF
    "q26": ["127 người"],
    "q27": ["mua bán các sản phẩm thép"],
    "q28": ["24 tháng 3 năm 2025"],
    "q29": ["80.585.847.420.000"],
    "q30": ["10.247.400.472.100"],
    "q36": ["81.793.076.515.644"],
    "q38": ["KPMG"],
    "q39": ["80.780.186.578.052"],
    "q40": ["5 công ty con cấp 1"],
}

OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_questions(path: Path) -> list[dict]:
    qs = yaml.safe_load(path.read_text(encoding="utf-8"))["questions"]
    return [q for q in qs
            if q.get("indexed", True)
            and q["group"] not in ("no_answer", "out_of_scope")
            and q["id"] in GT_KEYWORDS]


def hit_at_k(contexts: list[str], keywords: list[str], k: int) -> bool:
    text = " ".join(contexts[:k]).lower()
    return all(kw.lower() in text for kw in keywords)


def get_embedding(text: str, model: str) -> list[float]:
    import httpx
    r = httpx.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": model, "prompt": text},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["embedding"]


def retrieve_vector(question: str, collection: str, embed_model: str, top_k: int) -> list[str]:
    from qdrant_client import QdrantClient
    qvec = get_embedding(question, embed_model)
    qdrant = QdrantClient("localhost", port=6333)
    results = qdrant.query_points(
        collection_name=collection, query=qvec, limit=top_k
    ).points
    return [p.payload.get("text", "") for p in results]


def collection_exists(collection: str) -> bool:
    try:
        from qdrant_client import QdrantClient
        qdrant = QdrantClient("localhost", port=6333)
        cols = [c.name for c in qdrant.get_collections().collections]
        return collection in cols
    except Exception:
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--questions", default="evals/golden_hpg.yaml")
    parser.add_argument("--out", default="evals/compare_all.json")
    parser.add_argument("--retrievers", default="vector,bm25_raw,bm25_vn",
                        help="Comma-separated: vector,bm25_raw,bm25_vn")
    args = parser.parse_args()

    K = args.top_k
    want_retrievers = set(args.retrievers.split(","))
    questions = load_questions(Path(args.questions))
    n_q = len(questions)

    print(f"Questions : {n_q}  (indexed, with GT keywords)")
    print(f"Top-k     : {K}")
    print(f"Retrievers: {', '.join(sorted(want_retrievers))}")
    print()

    from rag.retrieval_bm25 import BM25Retriever

    all_results: list[dict] = []

    for label, collection, embed_model, grp in CONFIGS:
        if not collection_exists(collection):
            print(f"  SKIP {label} — collection '{collection}' not found in Qdrant")
            continue

        print(f"\n{'─'*60}")
        print(f"  {label}")
        print(f"  collection={collection}  embed={embed_model}")
        print(f"{'─'*60}")

        bm25_raw_ret = None
        bm25_vn_ret  = None

        if "bm25_raw" in want_retrievers:
            print("  Loading BM25 raw...", end=" ", flush=True)
            bm25_raw_ret = BM25Retriever(collection, use_vn_tokenize=False)
        if "bm25_vn" in want_retrievers:
            print("  Loading BM25 vn...", end=" ", flush=True)
            bm25_vn_ret = BM25Retriever(collection, use_vn_tokenize=True)

        config_rows: list[dict] = []

        for q in questions:
            qid  = q["id"]
            text = q["question"]
            kws  = GT_KEYWORDS[qid]

            row: dict = {"id": qid, "group": q["group"], "retrievers": {}}

            for rname in ("vector", "bm25_raw", "bm25_vn"):
                if rname not in want_retrievers:
                    continue

                t0 = time.perf_counter()
                try:
                    if rname == "vector":
                        ctxs = retrieve_vector(text, collection, embed_model, K)
                    elif rname == "bm25_raw":
                        ctxs = bm25_raw_ret.search(text, top_k=K)
                    else:
                        ctxs = bm25_vn_ret.search(text, top_k=K)
                except Exception as e:
                    row["retrievers"][rname] = {"error": str(e)}
                    continue

                elapsed = time.perf_counter() - t0
                h1 = hit_at_k(ctxs, kws, 1)
                h3 = hit_at_k(ctxs, kws, min(3, K))
                hK = hit_at_k(ctxs, kws, K)
                row["retrievers"][rname] = {
                    "hit@1": h1, "hit@3": h3, f"hit@{K}": hK,
                    "elapsed": round(elapsed, 2),
                }

            config_rows.append(row)

        # Print per-config summary
        scored = [r for r in config_rows if GT_KEYWORDS.get(r["id"])]
        ns = len(scored)
        print(f"\n  {'Retriever':<12} {'@1':>5} {'@3':>5} {'@'+str(K):>5}")
        print(f"  {'-'*30}")
        for rname in ("vector", "bm25_raw", "bm25_vn"):
            if rname not in want_retrievers:
                continue
            h1 = sum(1 for r in scored if r["retrievers"].get(rname, {}).get("hit@1") is True)
            h3 = sum(1 for r in scored if r["retrievers"].get(rname, {}).get("hit@3") is True)
            hK = sum(1 for r in scored if r["retrievers"].get(rname, {}).get(f"hit@{K}") is True)
            print(f"  {rname:<12} {h1}/{ns}  {h3}/{ns}  {hK}/{ns}")

        all_results.append({
            "label": label,
            "collection": collection,
            "embed_model": embed_model,
            "config_group": grp,
            "questions": config_rows,
            "summary": {
                rname: {
                    "hit@1": sum(1 for r in scored
                                 if r["retrievers"].get(rname, {}).get("hit@1") is True),
                    "hit@3": sum(1 for r in scored
                                 if r["retrievers"].get(rname, {}).get("hit@3") is True),
                    f"hit@{K}": sum(1 for r in scored
                                    if r["retrievers"].get(rname, {}).get(f"hit@{K}") is True),
                    "n": ns,
                }
                for rname in ("vector", "bm25_raw", "bm25_vn")
                if rname in want_retrievers
            },
        })

    # ── Master summary table ──────────────────────────────────────────────────
    if not all_results:
        print("\nNo collections found. Check Qdrant is running.")
        return

    print(f"\n\n{'='*80}")
    print(f"MASTER SUMMARY — context_hit@{K}  [{n_q} questions, {K} contexts]")
    print(f"{'='*80}")

    col_w = 38
    hdr = f"{'Config':<{col_w}}"
    for rname in ("vector", "bm25_raw", "bm25_vn"):
        if rname not in want_retrievers:
            continue
        hdr += f"  {rname:<12}"
    print(hdr)
    print("-" * (col_w + len(want_retrievers) * 14))

    for cfg in all_results:
        ns = next(iter(cfg["summary"].values()))["n"] if cfg["summary"] else n_q
        line = f"{cfg['label']:<{col_w}}"
        for rname in ("vector", "bm25_raw", "bm25_vn"):
            if rname not in want_retrievers:
                continue
            s = cfg["summary"].get(rname, {})
            hK = s.get(f"hit@{K}", "?")
            line += f"  {hK}/{ns:<10}"
        print(line)

    # ── Save JSON ─────────────────────────────────────────────────────────────
    Path(args.out).write_text(
        json.dumps({"top_k": K, "configs": all_results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # ── Save markdown report ──────────────────────────────────────────────────
    md_path = Path(args.out).with_suffix(".md")
    lines: list[str] = [
        f"# compare_all — context_hit@{K}",
        f"",
        f"**{n_q} questions · top-k={K} · retrievers: {', '.join(sorted(want_retrievers))}**",
        f"",
        f"## Master summary",
        f"",
    ]

    ret_cols = [r for r in ("vector", "bm25_raw", "bm25_vn") if r in want_retrievers]
    header_cells = ["Config"] + ret_cols
    lines.append("| " + " | ".join(header_cells) + " |")
    lines.append("| " + " | ".join(["---"] * len(header_cells)) + " |")

    for cfg in all_results:
        ns = next(iter(cfg["summary"].values()))["n"] if cfg["summary"] else n_q
        cells = [cfg["label"]]
        for rname in ret_cols:
            hK = cfg["summary"].get(rname, {}).get(f"hit@{K}", "?")
            cells.append(f"{hK}/{ns}")
        lines.append("| " + " | ".join(cells) + " |")

    # Group breakdown
    for gname in ("current", "chunking", "meta", "embed"):
        grp_cfgs = [c for c in all_results if c["config_group"] == gname]
        if not grp_cfgs:
            continue
        lines += ["", f"## Group: {gname}", ""]
        lines.append("| " + " | ".join(header_cells) + " |")
        lines.append("| " + " | ".join(["---"] * len(header_cells)) + " |")
        for cfg in grp_cfgs:
            ns = next(iter(cfg["summary"].values()))["n"] if cfg["summary"] else n_q
            cells = [cfg["label"]]
            for rname in ret_cols:
                hK = cfg["summary"].get(rname, {}).get(f"hit@{K}", "?")
                cells.append(f"{hK}/{ns}")
            lines.append("| " + " | ".join(cells) + " |")

    # Per-question detail
    lines += ["", "## Per-question detail (hit@" + str(K) + ")", ""]
    q_header = ["q", "group"] + [f"{r}" for r in ret_cols for _ in ["vector"]]
    # simpler: one column per (config × retriever) is too wide; show first config only
    # instead: per question, for each retriever show which configs hit
    lines.append(f"*(showing config '{all_results[0]['label']}' as representative)*")
    lines.append("")
    cfg0 = all_results[0]
    ph = ["id", "group"] + ret_cols + ["keyword"]
    lines.append("| " + " | ".join(ph) + " |")
    lines.append("| " + " | ".join(["---"] * len(ph)) + " |")
    for row in cfg0["questions"]:
        kws = GT_KEYWORDS.get(row["id"], [])
        cells = [row["id"], row["group"]]
        for rname in ret_cols:
            hK = row["retrievers"].get(rname, {}).get(f"hit@{K}")
            cells.append("✓" if hK else ("?" if hK is None else "✗"))
        cells.append(", ".join(kws))
        lines.append("| " + " | ".join(cells) + " |")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Saved JSON → {args.out}")
    print(f"Saved MD   → {md_path}")


if __name__ == "__main__":
    main()
