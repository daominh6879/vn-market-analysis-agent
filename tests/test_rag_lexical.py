"""rag/lexical.py — OCR-tolerant matching used by tools.rag_query._retrieve (pure functions, no services)."""
from rag.lexical import fold, hybrid_rank, lexical_score, query_ngrams


def test_fold_strips_accents_spaces_and_d_bar():
    assert fold("Tổng cộng tài sản") == "tongcongtaisan"
    assert fold("TONGCỘNGTÀISAN (270=100+200)") == "tongcongtaisan270100200"
    assert fold("Đầu tư") == "dautu"


def test_ngrams_drop_stopwords_tickers_years():
    grams = query_ngrams("Tổng tài sản của HPG cuối năm 2025 là bao nhiêu?", drop={"hpg"})
    assert "tongtaisan" in grams and grams["tongtaisan"] == 9
    assert not any("hpg" in g or "2025" in g or "cua" in g for g in grams)


def test_ngrams_do_not_span_removed_words():
    grams = query_ngrams("vốn của chủ")           # "cua" removed between the two content words
    assert "voncua" not in grams and "vonchu" not in grams


def test_ocr_garbled_row_matches_question():
    grams = query_ngrams("Tổng tài sản cuối năm 2025?")
    garbled = fold("||TONGCỘNGTÀISAN(270=100+200)|270||98.670.778.691.605|")
    unrelated = fold("Phải thu ngắn hạn của khách hàng")
    assert lexical_score(garbled, grams) > lexical_score(unrelated, grams) == 0


def test_hybrid_rank_is_vector_order_without_lexical_signal():
    assert hybrid_rank(["a", "b", "c"], {}) == ["a", "b", "c"]
    assert hybrid_rank(["a", "b"], {"b": 0}) == ["a", "b"]


def test_strong_text_match_outranks_vector_favourites_even_far_down():
    vec = [f"v{i}" for i in range(1, 41)]
    scores = {"v1": 5, "v2": 4, "v3": 5, "target": 18}       # target is not even in the vector pool
    assert hybrid_rank(vec, scores)[0] == "target"


def test_weak_text_match_does_not_displace_the_vector_top():
    vec = ["v1", "v2", "v3"]
    assert hybrid_rank(vec, {"v3": 2, "other": 2, "best": 18})[:2] == ["best", "v1"]


def test_primary_statement_wins_a_tie_but_not_a_stronger_match():
    assert hybrid_rank([], {"note": 18, "primary": 18}, boost_ids={"primary"})[0] == "primary"
    assert hybrid_rank([], {"note": 20, "primary": 18}, boost_ids={"primary"})[0] == "note"


def test_tong_also_matches_tong_cong():
    grams = query_ngrams("Tổng tài sản?")
    assert lexical_score(fold("TONGCỘNGTÀISAN (270)"), grams) >= grams["tongtaisan"]


def test_primary_statement_detection():
    from rag.lexical import is_primary_statement
    assert is_primary_statement("[Ngữ cảnh: BÁO CÁO TÌNH HÌNH TÀI CHÍNH | Cột: STT | Chỉ tiêu]\n|x|")
    assert is_primary_statement("BẢNG CÂN ĐỐI KẾ TOÁN\nTại ngày 31 tháng 12")
    assert not is_primary_statement("[Ngữ cảnh: THUYẾT MINH BÁO CÁO TÀI CHÍNH | Cột: a]\n|x|")


def test_anchors_keep_vector_favourites_against_a_strong_lexical_crowd():
    vec = ["v1", "v2", "v3", "v4"]
    crowd = {f"c{i}": 170 for i in range(10)} | {"v3": 59}     # broad query: many chunks match lexically, v3 barely
    assert "v3" not in hybrid_rank(vec, crowd)[:5]
    assert hybrid_rank(vec, crowd, anchors=3)[:3] == ["v1", "v2", "v3"]
    assert hybrid_rank(vec, crowd, anchors=3)[3].startswith("c")  # the rest still comes from the fused order


def test_anchors_do_not_duplicate_ids():
    out = hybrid_rank(["a", "b", "c"], {"a": 9, "x": 18}, anchors=2)
    assert out[:2] == ["a", "b"] and len(out) == len(set(out))
