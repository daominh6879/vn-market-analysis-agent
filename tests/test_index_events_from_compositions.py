"""Index reviews derived from compositions (ETF baskets, SSI membership): adds / removes, glitches dropped."""
from datetime import date

from ingest.fetch_rule_data import composition_events


def test_review_adds_and_removes_on_the_new_composition_date():
    snaps = [(date(2025, 1, 31), {"A", "POW"}), (date(2025, 2, 7), {"A", "LPB"}), (date(2025, 2, 14), {"A", "LPB"})]
    assert composition_events(snaps) == [("LPB", "add", date(2025, 2, 7)), ("POW", "remove", date(2025, 2, 7))]


def test_change_undone_within_two_weeks_is_a_glitch():
    snaps = [(date(2025, 7, 11), {"A"}), (date(2025, 7, 18), {"A", "BID"}), (date(2025, 7, 25), {"A"})]
    assert composition_events(snaps) == []
