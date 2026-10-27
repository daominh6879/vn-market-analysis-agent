"""tools/rag_query — OCR caution for answers quoting figures from a balance-sheet `mismatch` document."""
from tools import rag_query as rq

MISMATCH = {"ticker": "PLX", "quality": "mismatch", "text": "t"}
OK = {"ticker": "HPG", "quality": "ok", "text": "t"}
UNVERIFIED = {"ticker": "VHM", "quality": "unverified", "text": "t"}
FIGURE = "Tổng tài sản cuối năm 2025 là 53.336.709.742.044 VND."


def test_caution_names_the_flagged_tickers_when_figures_are_quoted():
    out = rq.quality_caution([MISMATCH, OK, {**MISMATCH}], FIGURE)
    assert out.startswith("\n\n⚠") and "PLX" in out and "HPG" not in out


def test_no_caution_for_ok_unverified_or_unflagged_chunks():
    assert rq.quality_caution([OK, UNVERIFIED, {"ticker": "X"}], FIGURE) == ""
    assert rq.quality_caution([], FIGURE) == ""


def test_no_caution_for_an_answer_without_large_figures():
    assert rq.quality_caution([MISMATCH], "Doanh thu chưa thực hiện được ghi nhận khi hoàn thành nghĩa vụ.") == ""
    assert rq.quality_caution([MISMATCH], "Lãi suất 7,5% một năm, 2025.") == ""      # small numbers are not figures


def _ask(monkeypatch, hits, answer):
    class _Col:
        name = rq.BCTC_COLLECTION

    class _Q:
        def get_collections(self):
            return type("C", (), {"collections": [_Col()]})()

    monkeypatch.setattr(rq, "_get_qdrant", lambda: _Q())
    monkeypatch.setattr(rq, "_retrieve_hits", lambda *a, **k: hits)
    monkeypatch.setattr(rq, "_llm_answer", lambda q, ctx: answer)
    return rq.ask_report("Tổng tài sản?", tickers=["PLX"])


def test_ask_report_appends_the_caution_and_says_so_in_the_message(monkeypatch):
    r = _ask(monkeypatch, [("ctx", MISMATCH)], FIGURE)
    assert r.status == "ok" and r.data.startswith(FIGURE) and "⚠" in r.data and "cảnh báo OCR" in r.message


def test_ask_report_is_unchanged_for_a_clean_document(monkeypatch):
    r = _ask(monkeypatch, [("ctx", OK)], FIGURE)
    assert r.data == FIGURE and "cảnh báo" not in r.message


def test_retrieve_keeps_returning_plain_texts(monkeypatch):
    monkeypatch.setattr(rq, "_retrieve_hits", lambda *a, **k: [("a", {"x": 1}), ("b", {})])
    assert rq._retrieve("q") == ["a", "b"]


def test_label_hits_heads_each_chunk_with_its_source():
    hits = [("tổng tài sản 98", {"ticker": "HPG", "year": "2025", "report_type": "standalone"}),
            ("tổng tài sản 27", {"ticker": "MWG", "year": "2025"}), ("no meta", {})]
    assert rq._label_hits(hits) == ["[HPG | 2025 | standalone]\ntổng tài sản 98", "[MWG | 2025]\ntổng tài sản 27", "no meta"]


def test_ask_report_sends_the_labelled_contexts_to_the_llm(monkeypatch):
    seen = {}

    class _Col:
        name = rq.BCTC_COLLECTION

    class _Q:
        def get_collections(self):
            return type("C", (), {"collections": [_Col()]})()

    monkeypatch.setattr(rq, "_get_qdrant", lambda: _Q())
    monkeypatch.setattr(rq, "_retrieve_hits", lambda *a, **k: [("body", {"ticker": "HPG", "year": "2025"})])
    monkeypatch.setattr(rq, "_llm_answer", lambda q, ctx: seen.setdefault("ctx", ctx) and "ok")
    rq.ask_report("q", tickers=["HPG"])
    assert seen["ctx"] == ["[HPG | 2025]\nbody"]
