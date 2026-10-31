"""Unit tests for api/advisor.py: internal-key guard, stateless pass-through, hidden from OpenAPI (no DB, no LLM)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import advisor as api_advisor

KEY = "secret-key"
POS = {"ticker": "HPG", "quantity": 1000, "sellable_quantity": 1000, "avg_cost": 25_000.0, "last_price": 27_000.0,
       "market_value": 27_000_000.0, "unrealized_pnl": 2_000_000.0, "unrealized_pct": 8.0, "sector": "Steel",
       "weight": 1.0}
CASH = {"cash": 73_000_000.0, "nav": 100_000_000.0, "as_of": "2026-10-20"}
BODY = {"positions": [POS], "cash": CASH}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ADVISOR_INTERNAL_KEY", KEY)
    app = FastAPI()
    app.include_router(api_advisor.router)
    return TestClient(app)


def test_missing_or_wrong_key_is_401(client):
    assert client.post("/advisor/risk", json=BODY).status_code == 401
    assert client.post("/advisor/risk", json=BODY, headers={"X-Internal-Key": "nope"}).status_code == 401


def test_unconfigured_key_fails_closed(client, monkeypatch):
    monkeypatch.delenv("ADVISOR_INTERNAL_KEY")
    assert client.post("/advisor/risk", json=BODY, headers={"X-Internal-Key": ""}).status_code == 503


def test_advise_passes_positions_and_cash_through(client, monkeypatch):
    seen = {}

    def fake(positions, cash, user_id, watchlist):
        seen.update(positions=positions, cash=cash, user_id=user_id, watchlist=watchlist)
        return {"report_md": "ok", "actions": [], "new_ideas": [], "risk": {}, "as_of": cash["as_of"], "llm_missing": []}

    monkeypatch.setattr(api_advisor.advisor, "advise_for", fake)
    r = client.post("/advisor/advise", json={**BODY, "user_id": "t1", "watchlist": ["FPT"]}, headers={"X-Internal-Key": KEY})
    assert r.status_code == 200 and r.json()["as_of"] == "2026-10-20" and seen["user_id"] == "t1"
    assert seen["watchlist"] == ["FPT"]
    assert seen["positions"][0]["ticker"] == "HPG" and seen["positions"][0]["realized_pnl"] == 0.0
    assert seen["cash"]["nav"] == 100_000_000.0


def test_advise_maps_errors(client, monkeypatch):
    def empty(*_):
        raise ValueError("empty")

    def no_json(*_):
        raise RuntimeError("no json")

    h = {"X-Internal-Key": KEY}
    monkeypatch.setattr(api_advisor.advisor, "advise_for", empty)
    assert client.post("/advisor/advise", json=BODY, headers=h).status_code == 400
    monkeypatch.setattr(api_advisor.advisor, "advise_for", no_json)
    assert client.post("/advisor/advise", json=BODY, headers=h).status_code == 503


def test_reference_and_risk_delegate(client, monkeypatch):
    monkeypatch.setattr(api_advisor.ledger, "reference", lambda tickers, as_of: {"known": tickers, "as_of": str(as_of)})
    monkeypatch.setattr(api_advisor.evidence, "risk_for", lambda positions, cash: {"nav": cash["nav"]})
    h = {"X-Internal-Key": KEY}
    r = client.post("/advisor/reference", json={"tickers": ["HPG"], "as_of": "2026-10-20"}, headers=h)
    assert r.json() == {"known": ["HPG"], "as_of": "2026-10-20"}
    assert client.post("/advisor/risk", json=BODY, headers=h).json() == {"nav": 100_000_000.0}


def test_bad_body_is_422(client):
    r = client.post("/advisor/advise", json={"positions": [{"ticker": "HPG"}], "cash": CASH}, headers={"X-Internal-Key": KEY})
    assert r.status_code == 422


def test_hidden_from_openapi(client):
    assert not any(p.startswith("/advisor") for p in client.app.openapi()["paths"])


def test_ask_creates_then_continues_own_conversation(client, monkeypatch):
    import memory.conversation as mc
    import memory.turn_handler as th
    convs, turns = {}, []

    def create(user_id, tenant_id):
        convs["c1"] = {"user_id": user_id, "tenant_id": tenant_id}
        return "c1"

    monkeypatch.setattr(mc, "create_conversation", create)
    monkeypatch.setattr(mc, "get_conversation", lambda cid: convs.get(cid))
    monkeypatch.setattr(th, "run_turn", lambda **kw: turns.append(kw) or "trả lời")
    h = {"X-Internal-Key": KEY}
    r = client.post("/advisor/ask", json={"user_id": "t1", "message": "HPG thế nào?"}, headers=h)
    assert r.json() == {"conversation_id": "c1", "reply": "trả lời"}
    assert convs["c1"]["tenant_id"] == api_advisor.ASK_TENANT and turns[0]["is_first_turn"] is True
    r = client.post("/advisor/ask", json={"user_id": "t1", "message": "còn VCB?", "conversation_id": "c1"}, headers=h)
    assert r.status_code == 200 and turns[1]["is_first_turn"] is False
    r = client.post("/advisor/ask", json={"user_id": "other", "message": "x", "conversation_id": "c1"}, headers=h)
    assert r.status_code == 404 and len(turns) == 2      # someone else's conversation is not continued


def test_advise_stale_data_is_503_with_code_and_freshness_endpoint(client, monkeypatch):
    from portfolio import freshness
    status = {"expected_session": "2026-10-21", "fresh": False, "datasets": {},
              "problems": ["ohlcv_daily phiên 2026-10-21 chỉ có 270/391 dòng (cần ≥ 90%)"]}

    def stale(*_):
        raise freshness.StaleDataError(status)

    h = {"X-Internal-Key": KEY}
    monkeypatch.setattr(api_advisor.advisor, "advise_for", stale)
    r = client.post("/advisor/advise", json=BODY, headers=h)
    assert r.status_code == 503 and r.json()["detail"].startswith("stale_data: dữ liệu thị trường chưa cập nhật: ohlcv_daily")
    monkeypatch.setattr(api_advisor.freshness, "check", lambda: status)
    assert client.post("/advisor/freshness", headers=h).json() == status
    assert client.post("/advisor/freshness").status_code == 401


def test_market_stats_delegates(client, monkeypatch):
    monkeypatch.setattr(api_advisor.evidence, "market_stats", lambda tickers: {"tickers": {t: {} for t in tickers}})
    r = client.post("/advisor/market-stats", json={"tickers": ["HPG"]}, headers={"X-Internal-Key": KEY})
    assert r.status_code == 200 and r.json() == {"tickers": {"HPG": {}}}
    assert client.post("/advisor/market-stats", json={"tickers": ["HPG"]}).status_code == 401


def test_refetch_runs_each_ticker_dataset_and_reports_what_changed(client, monkeypatch):
    from portfolio import refetch as rf
    calls = []
    held = {("HPG", "financials_annual"): {"count": 3, "latest": "2024"},
            ("FPT", "financials_annual"): {"count": 6, "latest": "2025"},
            ("HPG", "prices"): {"count": 500, "latest": "2026-10-30"},
            ("FPT", "prices"): {"count": 500, "latest": "2026-10-30"}}

    def annual(t):
        calls.append(("a", t))
        if t == "HPG":                                       # source had 2 more years for HPG
            held[(t, "financials_annual")] = {"count": 5, "latest": "2025"}
        return 412                                           # FPT: same 6 years rewritten

    def boom(t):
        raise SystemExit("source down")

    monkeypatch.setitem(rf.FETCHERS, "financials_annual", annual)
    monkeypatch.setitem(rf.FETCHERS, "financials_quarterly", boom)
    monkeypatch.setitem(rf.FETCHERS, "prices", lambda t: 0)
    monkeypatch.setattr(rf, "state", lambda t, d: dict(held.get((t, d), {"count": 0, "latest": None})))
    h = {"X-Internal-Key": KEY}
    r = client.post("/advisor/refetch", json={"tickers": ["hpg", "FPT", "HPG"],
                                              "datasets": ["financials_annual", "financials_quarterly", "prices"]},
                    headers=h)
    assert r.status_code == 200
    j = r.json()
    assert calls == [("a", "HPG"), ("a", "FPT")]                 # normalized + de-duplicated
    by = {(x["ticker"], x["dataset"]): x for x in j["results"]}
    assert by[("HPG", "financials_annual")]["status"] == "new"
    assert by[("HPG", "financials_annual")]["before"]["count"] == 3 and by[("HPG", "financials_annual")]["after"]["count"] == 5
    assert by[("FPT", "financials_annual")]["status"] == "unchanged"     # 412 rows rewritten, no new year
    assert by[("HPG", "prices")]["status"] == "empty"
    assert by[("HPG", "financials_quarterly")]["status"] == "error"
    assert (j["new"], j["unchanged"], j["empty"], j["error"]) == (1, 1, 2, 2)


def test_refetch_rejects_bad_requests(client):
    h = {"X-Internal-Key": KEY}
    assert client.post("/advisor/refetch", json={"tickers": [], "datasets": ["prices"]}, headers=h).status_code == 400
    assert client.post("/advisor/refetch", json={"tickers": ["HPG"], "datasets": ["x"]}, headers=h).status_code == 400
    assert client.post("/advisor/refetch", json={"tickers": ["A;DROP"], "datasets": ["prices"]},
                       headers=h).status_code == 400
    many = [f"T{i:02d}X" for i in range(11)]
    assert client.post("/advisor/refetch", json={"tickers": many, "datasets": ["prices"]}, headers=h).status_code == 400
