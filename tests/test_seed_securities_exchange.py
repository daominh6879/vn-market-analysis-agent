"""securities.exchange follows the live listing (30/10/2026: HNX / UPCoM stocks of HOSE_SEED were labelled HOSE)."""
from ingest.seed_securities import exchange_updates


def test_relabels_only_what_the_listing_contradicts():
    current = {"FPT": "HOSE", "PVS": "HOSE", "ACV": "HOSE", "ECO": "HOSE", "XYZ": "HOSE"}
    listing = {"FPT": "HSX", "PVS": "HNX", "ACV": "UPCOM", "ECO": "HSX"}          # XYZ not listed: left alone
    assert exchange_updates(current, listing) == [("UPCOM", "ACV"), ("HNX", "PVS")]


def test_unknown_listing_code_is_not_guessed():
    assert exchange_updates({"AAA": "HOSE"}, {"AAA": "BOND"}) == []


def test_delisted_stocks_are_reported():
    from ingest.seed_securities import delisted
    assert delisted({"BCG": "HOSE", "SVI": "HOSE", "FPT": "HOSE"}, {"BCG": "DELISTED", "SVI": "DELISTED",
                                                                    "FPT": "HSX"}) == ["BCG", "SVI"]


def test_pick_shares_from_conflicting_sources():
    from ingest.fetch_shares import pick_shares
    # ECO: FireAnt 129M implausible vs statements (20M, Q4/2023); KBS 30M (room = exactly 50%)
    assert pick_shares({"fireant": 129176390, "kbs": 29999985, "vci": 30000000}, 14999992, 20e6)[0] == 29999985
    # TYA / TCR: VCI = the room figure (dropped), KBS 6.13M implausible vs statements 30.69M → FireAnt
    assert pick_shares({"fireant": 30680582, "kbs": 6125410, "vci": 6134773}, 6134773, 30689945)[0] == 30680582
    assert pick_shares({"fireant": 45425142, "kbs": 10365590, "vci": 10373190}, 10373190, 45432836)[0] == 45425142
    # DQC: FireAnt = KBS (outstanding, treasury shares excluded)
    assert pick_shares({"fireant": 27558066, "kbs": 27558066, "vci": 34359416}, None, 34359416)[0] == 27558066
    # GMD: bonus 50% not listed yet — both plausible; KBS sits exactly on the 49% room → listed count
    assert pick_shares({"fireant": 649337392, "kbs": 432892509}, 212117329, 432892509)[0] == 432892509
    assert pick_shares({"kbs": 100}, None, None) == (100, "first:kbs")
