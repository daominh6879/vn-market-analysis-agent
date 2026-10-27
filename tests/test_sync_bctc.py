"""ingest/sync_bctc.py — candidate ranking, mirror-host download and fallback (CafeF / MinIO / Qdrant faked)."""
import httpx
import pytest

from ingest import sync_bctc as sb


def _item(year, kind, host="cafefnew.mediacdn.vn", audited=True):
    word = "hợp nhất" if kind == "consolidated" else "công ty mẹ"
    name = f"Báo cáo tài chính {word} năm {year}" + (" (đã kiểm toán)" if audited else "")
    return {"Year": year, "Quarter": 5, "Name": name, "Link": f"https://{host}/Images/Uploaded/BCTC/{kind}_{year}.pdf"}


class _Resp:
    def __init__(self, status=200, content=b"%PDF-1.7 x", payload=None):
        self.status_code, self.content, self._payload = status, content, payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=httpx.Request("GET", "http://x"), response=httpx.Response(self.status_code))

    def json(self):
        return self._payload


def _cafef(monkeypatch, items):
    monkeypatch.setattr(sb.httpx, "get", lambda *a, **k: _Resp(payload={"Data": items}))


def test_newer_year_of_the_other_type_beats_an_older_year_of_the_requested_type(monkeypatch):
    # MBB: FY2025 exists only consolidated
    _cafef(monkeypatch, [_item(2025, "consolidated"), _item(2024, "standalone"), _item(2024, "consolidated")])
    got = sb.annual_candidates("MBB", "standalone")
    assert [(c["year"], c["report_type"]) for c in got] == [(2025, "consolidated"), (2024, "standalone"), (2024, "consolidated")]


def test_requested_type_wins_within_the_same_year_and_audited_wins_a_tie(monkeypatch):
    _cafef(monkeypatch, [_item(2025, "consolidated"), _item(2025, "standalone", audited=False), _item(2025, "standalone")])
    got = sb.annual_candidates("HPG", "standalone")
    assert [(c["report_type"], "kiểm toán" in c["name"]) for c in got] == [("standalone", True), ("standalone", False), ("consolidated", True)]
    assert sb.annual_candidates("HPG", "consolidated")[0]["report_type"] == "consolidated"


def test_non_annual_and_linkless_rows_are_ignored(monkeypatch):
    quarterly = {**_item(2025, "standalone"), "Quarter": 2}
    no_link = {**_item(2025, "standalone"), "Link": ""}
    _cafef(monkeypatch, [quarterly, no_link])
    assert sb.annual_candidates("X", "standalone") == []


def test_mirror_urls_keep_the_path_and_put_the_listed_url_first():
    urls = sb.mirror_urls("https://cafefnew.mediacdn.vn/Images/Uploaded/BCTC/a%20b.pdf")
    assert urls[0].startswith("https://cafefnew.mediacdn.vn/")
    assert urls[1:] == ["https://cafef1.mediacdn.vn/Images/Uploaded/BCTC/a%20b.pdf", "https://s.cafef.vn/Images/Uploaded/BCTC/a%20b.pdf"]


def test_download_falls_through_to_the_mirror_that_has_the_file(monkeypatch):
    seen = []

    def fake_get(url, **kw):
        seen.append(url)
        return _Resp(404) if "cafefnew" in url else _Resp(200, b"%PDF-1.7 ok")

    monkeypatch.setattr(sb.httpx, "get", fake_get)
    assert sb.download_pdf("https://cafefnew.mediacdn.vn/Images/a.pdf") == b"%PDF-1.7 ok"
    assert [u.split("/")[2] for u in seen] == ["cafefnew.mediacdn.vn", "cafef1.mediacdn.vn"]


def test_download_skips_an_html_error_page_served_with_200(monkeypatch):
    monkeypatch.setattr(sb.httpx, "get", lambda url, **kw: _Resp(200, b"<html>") if "cafefnew" in url else _Resp(200, b"%PDF-1.4"))
    assert sb.download_pdf("https://cafefnew.mediacdn.vn/Images/a.pdf").startswith(b"%PDF")


def test_download_raises_when_every_host_fails(monkeypatch):
    monkeypatch.setattr(sb.httpx, "get", lambda url, **kw: _Resp(404))
    with pytest.raises(httpx.HTTPError):
        sb.download_pdf("https://cafefnew.mediacdn.vn/Images/a.pdf")


class _Minio:
    def __init__(self):
        self.objects = []

    def bucket_exists(self, b):
        return True

    def put_object(self, bucket, key, *a, **k):
        self.objects.append((bucket, key))


def _wire(monkeypatch, cands, downloads, have_minio=(), have_qdrant=()):
    monkeypatch.setattr(sb, "annual_candidates", lambda t, rt: cands)
    monkeypatch.setattr(sb, "in_minio", lambda c, b, key: key in have_minio)
    monkeypatch.setattr(sb, "indexed_doc_id", lambda q, key: "doc" if key in have_qdrant else None)
    embedded = []
    monkeypatch.setattr(sb, "parse_and_embed", lambda c, q, t, b, key, year, rt: embedded.append((key, year, rt)) or 5)

    def fake_download(url):
        r = downloads[url]
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(sb, "download_pdf", fake_download)
    return embedded


def _cand(year, kind):
    return {"year": year, "name": f"n{year}", "url": f"u{year}{kind}", "filename": f"{kind}{year}.pdf", "report_type": kind}


def test_sync_uses_the_candidate_report_type_for_the_payload(monkeypatch):
    emb = _wire(monkeypatch, [_cand(2025, "consolidated")], {"u2025consolidated": b"%PDF"})
    status, note = sb.sync_ticker(_Minio(), None, "MBB", "standalone", dry=False)
    assert status == "synced" and emb == [("2025/consolidated2025.pdf", 2025, "consolidated")]
    assert "no standalone report that year" in note


def test_sync_falls_back_to_the_older_report_when_the_newer_one_cannot_be_downloaded(monkeypatch):
    emb = _wire(monkeypatch, [_cand(2025, "standalone"), _cand(2024, "standalone")],
                {"u2025standalone": httpx.ConnectError("down"), "u2024standalone": b"%PDF"})
    status, note = sb.sync_ticker(_Minio(), None, "ACB", "standalone", dry=False)
    assert status == "synced" and emb[0][1] == 2024
    assert "FY2024" in note and "newer file unavailable" in note


def test_sync_retries_the_newest_file_each_run_before_accepting_the_older_one(monkeypatch):
    # FY2024 is already indexed; FY2025 still fails -> up-to-date, but the newest was tried first
    tried = []
    _wire(monkeypatch, [_cand(2025, "standalone"), _cand(2024, "standalone")],
          {"u2025standalone": httpx.ConnectError("down")},
          have_minio={"2024/standalone2024.pdf"}, have_qdrant={"2024/standalone2024.pdf"})
    orig = sb.download_pdf
    monkeypatch.setattr(sb, "download_pdf", lambda url: tried.append(url) or orig(url))
    status, note = sb.sync_ticker(_Minio(), None, "ACB", "standalone", dry=False)
    assert status == "up-to-date" and tried == ["u2025standalone"] and "FY2024" in note


def test_sync_raises_when_nothing_can_be_downloaded(monkeypatch):
    _wire(monkeypatch, [_cand(2025, "standalone")], {"u2025standalone": httpx.ConnectError("down")})
    with pytest.raises(RuntimeError, match="no downloadable annual report"):
        sb.sync_ticker(_Minio(), None, "ACB", "standalone", dry=False)


def test_dry_run_downloads_nothing(monkeypatch):
    emb = _wire(monkeypatch, [_cand(2025, "standalone")], {})
    assert sb.sync_ticker(_Minio(), None, "ACB", "standalone", dry=True) == ("would sync", "")
    assert emb == []
