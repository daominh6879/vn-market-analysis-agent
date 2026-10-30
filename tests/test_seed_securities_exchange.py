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
