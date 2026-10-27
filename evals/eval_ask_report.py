#!/usr/bin/env python3
"""
evals/eval_ask_report.py — retrieval + answer quality of the ask_report path (tools/rag_query.py), real pipeline.

No mocks: real Qdrant (bctc_structural), real Ollama embeddings, real LLM via create_client().
Compares plain vector retrieval (hybrid=False) with vector + lexical fusion (hybrid=True) on the same cases.

Every case carries a GOLD fact: folded substrings that must co-occur in ONE indexed chunk of the ticker. The eval first
checks the gold is really in the index (a re-sync or OCR change would otherwise show up as a retrieval failure) and
skips cases whose gold is gone. Then, per mode, it reports two things separately:
  ctx — is the gold chunk in the retrieved context?          (retrieval, deterministic: measured once)
  ans — does the generated answer contain the expected facts? (retrieval + generation: measured --runs times)
A negative control (no gold) checks that a question the documents cannot answer still gets "Không có trong tài liệu".

Expected figures are balance-sheet rows of the separate FY2025 statements (HPG, VCB read from the OCR'd text with
assets = liabilities + equity checked; VHM read off the rendered PDF pages, because its OCR has digit errors and a
dropped "Tổng cộng tài sản" row, which is exactly what the VHM cases measure). Add cases only with values read from the
PDF or the index, never guessed.

Usage:
    python evals/eval_ask_report.py                      # both modes, 3 answer runs per case
    python evals/eval_ask_report.py --runs 5
    python evals/eval_ask_report.py --no-answer          # retrieval only (no LLM calls)
    python evals/eval_ask_report.py --out evals/ask_report_eval.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

YEAR = "2025"


@dataclass
class Case:
    id: str
    ticker: str
    question: str
    kind: str                                           # "figure" | "policy" | "negative"
    gold: list[str] = field(default_factory=list)       # folded substrings that must share one chunk (empty = no gold)
    figures: list[str] = field(default_factory=list)    # numbers the answer must contain (separators ignored)
    facts: list[list[str]] = field(default_factory=list)  # each group: any alternative (folded match) must be in the answer


CASES: list[Case] = [
    # ── figures: balance-sheet rows, 31/12/2025 (HPG in VND, VCB in triệu VND) ──
    Case("hpg_total_assets", "HPG", "Tổng tài sản của HPG cuối năm 2025 là bao nhiêu?", "figure",
         gold=["98670778691605"], figures=["98.670.778.691.605"]),
    Case("hpg_liabilities", "HPG", "Nợ phải trả của HPG cuối năm 2025?", "figure",
         gold=["4239852223395"], figures=["4.239.852.223.395"]),
    Case("hpg_equity_end", "HPG", "Vốn chủ sở hữu của HPG cuối năm 2025?", "figure",
         gold=["94430926468210"], figures=["94.430.926.468.210"]),
    Case("hpg_equity_start", "HPG", "Vốn chủ sở hữu của HPG đầu năm 2025 (cuối năm 2024)?", "figure",
         gold=["80780186578052"], figures=["80.780.186.578.052"]),
    Case("hpg_cash", "HPG", "Tiền và các khoản tương đương tiền của HPG cuối năm 2025?", "figure",
         gold=["481464857088"], figures=["481.464.857.088"]),
    Case("hpg_long_term_borrowings", "HPG", "Vay dài hạn của HPG cuối năm 2025 là bao nhiêu?", "figure",
         gold=["2859500000000"], figures=["2.859.500.000.000"]),
    Case("vcb_total_assets", "VCB", "Tổng tài sản của VCB cuối năm 2025 là bao nhiêu?", "figure",
         gold=["2426649127"], figures=["2.426.649.127"]),
    Case("vcb_liabilities", "VCB", "Tổng nợ phải trả của VCB cuối năm 2025?", "figure",
         gold=["2205948110"], figures=["2.205.948.110"]),
    Case("vcb_equity", "VCB", "Vốn chủ sở hữu của VCB cuối năm 2025?", "figure",
         gold=["220701017"], figures=["220.701.017"]),
    Case("vcb_charter_capital", "VCB", "Vốn điều lệ của VCB cuối năm 2025 là bao nhiêu?", "figure",
         gold=["83556751"], figures=["83.556.751"]),
    # ── VHM (triệu VND): scanned PDF whose OCR drops the "270 Tổng cộng tài sản" row and misreads digits ──
    Case("vhm_total_assets", "VHM", "Tổng tài sản của VHM cuối năm 2025 là bao nhiêu?", "figure",
         gold=["590372684"], figures=["590.372.684"]),
    Case("vhm_liabilities", "VHM", "Nợ phải trả của VHM cuối năm 2025?", "figure",
         gold=["417640988"], figures=["417.640.988"]),
    Case("vhm_equity", "VHM", "Vốn chủ sở hữu của VHM cuối năm 2025?", "figure",
         gold=["172731696"], figures=["172.731.696"]),
    # ── policies: facts read from the notes (VCB chunks 83/84, HPG chunk 76 at the time of writing) ──
    Case("vcb_general_provision_rate", "VCB", "Tỷ lệ dự phòng rủi ro chung của Vietcombank là bao nhiêu?", "policy",
         gold=["ruirochung", "075"], facts=[["0,75"]]),
    Case("vcb_provisioning_policy", "VCB", "Ngân hàng Vietcombank trích lập dự phòng rủi ro tín dụng như thế nào?", "policy",
         gold=["ruirochung", "075"], facts=[["0,75"]]),
    Case("vcb_trading_securities", "VCB", "Chính sách kế toán đối với chứng khoán kinh doanh của Vietcombank?", "policy",
         gold=["trongvongmotnam", "giagoc"], facts=[["một năm"], ["giá gốc"]]),
    Case("hpg_unearned_revenue", "HPG", "Doanh thu chưa thực hiện của Hòa Phát được ghi nhận như thế nào?", "policy",
         gold=["doanhthuchuathuchien", "nghiavu"], facts=[["nghĩa vụ", "điều kiện ghi nhận"]]),
    # ── negative control: the documents cannot answer this ──
    Case("hpg_future_revenue", "HPG", "Doanh thu năm 2030 của Hòa Phát dự kiến là bao nhiêu?", "negative",
         facts=[["không có"]]),
]


def _all_chunks(ticker: str) -> list[str]:
    from rag.filter import BCTC_COLLECTION, build_filter
    from tools.rag_query import _get_qdrant

    qdrant, out, offset = _get_qdrant(), [], None
    while True:
        batch, offset = qdrant.scroll(BCTC_COLLECTION, scroll_filter=build_filter(tickers=[ticker]),
                                      limit=256, offset=offset, with_payload=True)
        out += [p.payload.get("text", "") for p in batch if p.payload]
        if offset is None:
            return out


def gold_in(chunks: list[str], gold: list[str]) -> bool:
    from rag.lexical import fold
    return any(all(g in f for g in gold) for f in map(fold, chunks))


def answer_ok(case: Case, answer: str) -> bool:
    from rag.lexical import fold
    digits_only = re.sub(r"[.,\s]", "", answer)
    folded = fold(answer)
    return (all(re.sub(r"[.,\s]", "", fig) in digits_only for fig in case.figures)
            and all(any(fold(alt) in folded for alt in group) for group in case.facts))


def main() -> None:
    parser = argparse.ArgumentParser(description="ask_report retrieval + answer eval (vector vs hybrid)")
    parser.add_argument("--runs", type=int, default=3, help="LLM answers per case and mode (default 3)")
    parser.add_argument("--no-answer", action="store_true", help="retrieval only: skip LLM calls")
    parser.add_argument("--out", default=str(ROOT / "evals" / "ask_report_eval.json"))
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    from tools.rag_query import HYBRID_TOP_K, TOP_K, _llm_answer, _retrieve

    index = {t: _all_chunks(t) for t in sorted({c.ticker for c in CASES})}
    modes = {"vector": False, "hybrid": True}
    rows: list[dict] = []

    for case in CASES:
        row: dict = {"id": case.id, "ticker": case.ticker, "kind": case.kind, "question": case.question}
        if case.gold and not gold_in(index[case.ticker], case.gold):
            row["skipped"] = "gold fact not in the index (re-synced or OCR changed?)"
            rows.append(row)
            print(f"[skip] {case.id}: {row['skipped']}")
            continue
        for mode, hybrid in modes.items():
            ctx = _retrieve(case.question, tickers=[case.ticker], year=YEAR, hybrid=hybrid)   # each mode's own default top_k
            res: dict = {"ctx": gold_in(ctx, case.gold) if case.gold else None}
            if not args.no_answer:
                answers = [_llm_answer(case.question, ctx) for _ in range(args.runs)]
                res["ans_ok"] = sum(answer_ok(case, a) for a in answers)
                res["empty"] = sum(not a for a in answers)
                res["sample"] = answers[0][:240]
            row[mode] = res
        rows.append(row)
        print(f"  done {case.id}", flush=True)

    scored = [r for r in rows if "skipped" not in r]
    print(f"\n{'case':<28}{'kind':<9}{'ctx vec':<9}{'ctx hyb':<9}" + ("" if args.no_answer else f"{'ans vec':<9}{'ans hyb':<9}"))
    def cell(r: dict, mode: str, key: str) -> str:
        v = r[mode][key]
        if v is None:
            return "-"
        return ("yes" if v else "no") if key == "ctx" else f"{v}/{args.runs}"

    for r in scored:
        line = f"{r['id']:<28}{r['kind']:<9}{cell(r, 'vector', 'ctx'):<9}{cell(r, 'hybrid', 'ctx'):<9}"
        if not args.no_answer:
            line += f"{cell(r, 'vector', 'ans_ok'):<9}{cell(r, 'hybrid', 'ans_ok'):<9}"
        print(line)

    summary: dict = {}
    for mode in modes:
        gold_rows = [r for r in scored if r["kind"] != "negative"]
        summary[mode] = {"ctx_hit": f"{sum(bool(r[mode]['ctx']) for r in gold_rows)}/{len(gold_rows)}"}
        if not args.no_answer:
            for kind in ("figure", "policy", "negative"):
                ks = [r for r in scored if r["kind"] == kind]
                summary[mode][f"ans_{kind}"] = f"{sum(r[mode]['ans_ok'] for r in ks)}/{len(ks) * args.runs}"
            summary[mode]["empty_answers"] = sum(r[mode]["empty"] for r in scored)
    print("\nsummary (top_k vector=%d hybrid=%d, year=%s, runs=%d):" % (TOP_K, HYBRID_TOP_K, YEAR, args.runs))
    for mode, s in summary.items():
        print(f"  {mode:<7}", s)

    Path(args.out).write_text(json.dumps({"top_k": {"vector": TOP_K, "hybrid": HYBRID_TOP_K}, "year": YEAR, "runs": args.runs, "summary": summary,
                                          "cases": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwritten {args.out}")


if __name__ == "__main__":
    main()
