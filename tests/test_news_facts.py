"""
tests/test_news_facts.py — insider-trade parsing and the scheduled news scoring for vn-portfolio's rule engine.

No network, no DB, no LLM (vnmarket client, get_conn and score_pairs are patched).
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from data import insider_trades as IT
from portfolio import news_facts as NF


def _client(df: pd.DataFrame):
    c = MagicMock()
    c.market.events.return_value = df
    return c


class TestInsiderTrades:
    def test_plain_converts_pandas_values(self):
        assert IT._plain(pd.Timestamp("2026-10-28")) == "2026-10-28T00:00:00"
        assert IT._plain(pd.NaT) is None
        assert IT._plain(float("nan")) is None
        assert IT._plain(pd.Series([5]).iloc[0]) == 5

    def test_fetch_keeps_insider_codes_with_dates_and_raw(self):
        df = pd.DataFrame([
            {"ticker": "hpg", "event_code": "DDIND", "event_title_vi": "HPG: Ông A đăng ký bán 1.000.000 cp",
             "public_date": pd.Timestamp("2026-10-20"), "start_date": pd.Timestamp("2026-10-23"),
             "end_date": pd.Timestamp("2026-11-21"), "extra_field": 7},
            {"ticker": "VNM", "event_code": "DIV", "event_title_vi": "cổ tức"},
        ])
        with patch("core.vnmarket_client.get_client", return_value=_client(df)):
            rows = IT.fetch_insider_trades(date(2026, 10, 1), date(2026, 10, 30))
        assert len(rows) == 1
        r = rows[0]
        assert (r["ticker"], r["event_code"], r["public_date"], r["end_date"]) == \
            ("HPG", "DDIND", date(2026, 10, 20), date(2026, 11, 21))
        assert r["raw"]["extra_field"] == 7                      # the full source record is kept

    def test_full_page_is_an_error(self):
        df = pd.DataFrame([{"ticker": "HPG", "event_code": "DDIND"}] * IT.PAGE_LIMIT)
        with patch("core.vnmarket_client.get_client", return_value=_client(df)):
            with pytest.raises(RuntimeError, match="page limit"):
                IT.fetch_insider_trades(date(2026, 1, 1), date(2026, 1, 30))

    def test_ingest_walks_the_window_in_chunks(self):
        calls = []
        with patch.object(IT, "fetch_insider_trades", side_effect=lambda a, b: calls.append((a, b)) or []), \
                patch.object(IT, "upsert_insider_trades", return_value=0):
            out = IT.ingest(days_back=65, chunk_days=30)
        assert len(calls) == 3 and calls[0][1] < calls[1][0]     # contiguous, non-overlapping windows
        assert out["fetched"] == 0


class TestScoreUnscored:
    PAIRS = [("HPG", {"id": i, "title": f"t{i}", "snippet": "", "kind": "ticker"}) for i in range(5)]

    @contextmanager
    def _patched(self, score):
        conn = MagicMock()
        with patch.object(NF, "unscored_pairs", return_value=(self.PAIRS, {"HPG": {}})), \
                patch("portfolio.news_scorer.score_pairs", side_effect=score), \
                patch("portfolio.evidence.save_scores") as save, \
                patch("core.db.get_conn") as gc:
            gc.return_value.__enter__.return_value = conn
            yield save

    def test_stores_every_batch_with_origin(self):
        def score(todo, meta):
            return {(t, a["id"]): {"score": 3, "direction": 1, "magnitude": 2, "impact": ""}
                    for t, items in todo.items() for a in items}
        with self._patched(score) as save:
            out = NF.score_unscored(date(2026, 10, 1), date(2026, 10, 2), workers=1)
        assert out["stored"] == 5 and out["pairs"] == 5
        assert save.call_args.args[3] == NF.SCORE_ORIGIN

    def test_raises_when_most_pairs_fail(self):
        def score(todo, meta):
            raise ValueError("llm down")
        with self._patched(score):
            with pytest.raises(RuntimeError, match="stored 0/5"):
                NF.score_unscored(date(2026, 10, 1), date(2026, 10, 2), workers=1)


# ── CafeF insider deals ────────────────────────────────────────────────────────

from data import cafef_insider as CI


def _cafef(published_ms: int, **kw) -> dict:
    base = {"Stock": "hdc", "ShareHolderCode": "CEO_1", "TransactionMan": "Lê A", "TransactionManPosition": "Phó Tổng GĐ",
            "RelatedMan": "", "RelatedManPosition": "", "VolumeBeforeTransaction": 8727429, "VolumeAfterTransaction": 0,
            "TyLeSoHuu": 0, "PlanBuyVolume": 0, "PlanSellVolume": 500000, "PlanBeginDate": "/Date(1790874000000)/",
            "PlanEndDate": "/Date(1793379600000)/", "RealBuyVolume": 0, "RealSellVolume": 700000,
            "RealEndDate": "/Date(1790614800000)/", "PublishedDate": f"/Date({published_ms})/", "TransactionNote": ""}
    return {**base, **kw}


class TestCafefInsider:
    def test_parse_row_dates_in_vn_time_and_volumes(self):
        p = CI.parse_row(_cafef(1791910800000))
        assert p["ticker"] == "HDC" and p["real_sell"] == 700000 and p["volume_before"] == 8727429
        assert p["plan_begin"] == date(2026, 10, 2)                    # 1790874000000 ms = 2026-10-01 17:00 UTC
        assert p["published_at"].tzinfo is not None

    def test_parse_row_missing_dates(self):
        p = CI.parse_row(_cafef(0, RealEndDate=None, PlanBeginDate=None))
        assert p["real_end"] is None and p["plan_begin"] is None

    def test_fetch_stops_at_since_and_short_page(self):
        new, old = 1791910800000, 1700000000000                         # 2026-10-13, 2023-11-14
        pages = [[_cafef(new)] * CI.PAGE_SIZE, [_cafef(new)] * 10 + [_cafef(old)] * 10]
        client = MagicMock()
        client.get.side_effect = [MagicMock(json=MagicMock(return_value={"Data": {"Data": p}}),
                                            raise_for_status=MagicMock()) for p in pages]
        with patch.object(CI.time, "sleep"):
            rows = CI.fetch_ticker("HDC", date(2026, 1, 1), client)
        assert len(rows) == CI.PAGE_SIZE + 10 and client.get.call_count == 2     # old rows dropped, walk stops
