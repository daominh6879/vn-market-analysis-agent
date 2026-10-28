"""
core/balance_check.py — index-time sanity check for OCR'd balance sheets.

A scanned BCTC is OCR'd, and OCR misreads digits (an 8 for a 5, a dropped row). The balance sheet gives a free
cross-check: "Tổng cộng tài sản" must equal "Tổng cộng nguồn vốn" (banks: "Tổng tài sản" = "Tổng nợ phải trả và vốn chủ
sở hữu"). When the two disagree, one of them was misread, so the document's other figures deserve less trust.

The check only flags: it never blocks indexing. Statuses:
  ok          a total-assets figure equals a total-capital figure
  mismatch    both found, none equal  (detail says how many digits differ when the lengths match)
  unverified  a total row is missing (OCR dropped it) or has no readable number
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from rag.lexical import fold

# 2.426.649.127; an OCR-flattened 2426649127; a dropped separator "45.814.910047405"
# Bump when the matching rules change: ingest/sync_bctc.ensure_quality re-runs the check on documents stored with an older one.
CHECK_VERSION = 3
_NUMBER = re.compile(r"\d{1,3}(?:[.,]\d{3})+(?:\d{3})*|\d{7,}")
_CODE_CELL = re.compile(r"\d{1,4}")                          # the "mã số" cell: 270, 440
_ASSET_KEYS = ("tongcongtaisan", "tongtaisan")
_CAPITAL_KEYS = ("tongcongnguonvon", "tongnguonvon", "tongnophaitravavonchusohuu", "tongcongnophaitravavonchusohuu")
# after the key only a form code "(270=100+200)" (with a few letters of OCR noise around it), a footnote, the bank suffix
# "có" or up to 3 letters alone may follow; a real word means a different row ("Tổng tài sản dài hạn", "... thuế ...")
_REMAINDER_OK = re.compile(r"(?:co)?(?:[a-z]{0,4}\d+[a-z]{0,3}|[a-z]{0,3})")   # noise around a code, or up to 3 letters alone
_KEY_SIMILARITY = 0.9            # one misread letter in "tongcongnguonvom" still counts as the key
_MAX_PREFIX = 6                  # OCR noise before the label ("Ps F", "R", a row marker)
_MAX_NUMBERS_IN_ROW = 3          # a balance-sheet total has 2 (this year, last year); segment tables have many


@dataclass
class BalanceCheck:
    status: str
    assets: list[str] = field(default_factory=list)      # current-year digit strings found on total-assets rows
    capital: list[str] = field(default_factory=list)
    detail: str = ""

    def as_dict(self) -> dict:
        return {"version": CHECK_VERSION, "status": self.status, "assets": self.assets[:3], "capital": self.capital[:3],
                "detail": self.detail}


def _label_matches(label: str, keys: tuple[str, ...]) -> bool:
    """The label is one of `keys` (allowing OCR noise before it, one misread letter in it, and a code/footnote after)."""
    for k in keys:
        for at in range(_MAX_PREFIX + 1):
            seg = label[at:at + len(k)]
            if len(seg) == len(k) and SequenceMatcher(None, seg, k).ratio() >= _KEY_SIMILARITY \
                    and _REMAINDER_OK.fullmatch(label[at + len(k):]):
                return True
    return False


def _virtual_rows(line: str) -> list[list[str]]:
    """Cell lists of a markdown row; a row whose cells hold several `<br>` lines (OCR merged neighbouring table rows into one)
    is also split into one virtual row per line, aligned from the top and from the bottom of each cell."""
    cells = [c.replace("**", "").strip(" *_") for c in line.strip().strip("|").split("|")]
    rows = [[c.replace("<br>", " ") for c in cells]]
    parts = [[p.strip() for p in c.split("<br>") if p.strip()] or [""] for c in cells]
    depth = max(map(len, parts))
    if depth > 1:
        for j in range(depth):
            rows.append([p[j] if j < len(p) else "" for p in parts])                 # top-aligned
            rows.append([p[len(p) - depth + j] if len(p) - depth + j >= 0 else "" for p in parts])   # bottom-aligned
    return rows


def _row_total(line: str, keys: tuple[str, ...]) -> str | None:
    """Current-year number of a table row whose label is one of `keys`, else None.

    The label is what precedes the first number (the "mã số" cell excluded), even when OCR put label and number in the
    same cell. OCR also leaves junk cells after the numbers and noise inside the label, so neither may veto the row.
    """
    if not line.lstrip().startswith("|"):
        return None
    for cells in _virtual_rows(line):
        if (total := _cells_total(cells, keys)) is not None:
            return total
    return None


def _cells_total(cells: list[str], keys: tuple[str, ...]) -> str | None:
    first = next((i for i, c in enumerate(cells) if _NUMBER.search(c)), None)
    if first is None:
        return None
    label = fold("".join(c for c in cells[:first] if not _CODE_CELL.fullmatch(c)))
    if not any(ch.isalpha() for ch in label):          # label and number share a cell: use the text before the number
        label = fold(cells[first][:_NUMBER.search(cells[first]).start()])
    if not _label_matches(label, keys):
        return None
    numbers = [m.group(0) for c in cells[first:] for m in _NUMBER.finditer(c)]
    if not 1 <= len(numbers) <= _MAX_NUMBERS_IN_ROW:
        return None
    return re.sub(r"\D", "", numbers[0])


def balance_check(markdown: str) -> BalanceCheck:
    """Compare total assets with total capital found in the parsed markdown of a BCTC."""
    assets: list[str] = []
    capital: list[str] = []
    for line in markdown.splitlines():
        if (a := _row_total(line, _ASSET_KEYS)) is not None:
            assets.append(a)
        elif (c := _row_total(line, _CAPITAL_KEYS)) is not None:
            capital.append(c)

    if not assets or not capital:
        missing = "total assets" if not assets else "total capital"
        return BalanceCheck("unverified", assets, capital, f"no readable {missing} row")
    if set(assets) & set(capital):
        return BalanceCheck("ok", assets, capital)
    a, c = assets[0], capital[0]
    diff = sum(x != y for x, y in zip(a, c)) if len(a) == len(c) else None
    detail = f"assets {a} vs capital {c}" + (f" ({diff} digit(s) differ: likely OCR)" if diff is not None and diff <= 3 else "")
    return BalanceCheck("mismatch", assets, capital, detail)
