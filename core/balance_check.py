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

from rag.lexical import fold

_NUMBER = re.compile(r"\d{1,3}(?:[.,]\d{3})+|\d{7,}")     # 2.426.649.127 or an OCR-flattened 2426649127
_CODE_CELL = re.compile(r"\d{1,4}")                          # the "mã số" cell: 270, 440
_ASSET_KEYS = ("tongcongtaisan", "tongtaisan")
_CAPITAL_KEYS = ("tongcongnguonvon", "tongnguonvon", "tongnophaitravavonchusohuu")
# after the key only a form code "(270=100+200)", a footnote, the bank suffix "có" or up to 3 letters of OCR noise may
# follow; a real word means a different row ("Tổng tài sản thuế thu nhập hoãn lại")
_REMAINDER_OK = re.compile(r"(?:co)?\d*[a-z]{0,3}")
_MAX_PREFIX = 6                  # OCR noise before the label ("Ps F", "R", a row marker)
_MAX_NUMBERS_IN_ROW = 3          # a balance-sheet total has 2 (this year, last year); segment tables have many


@dataclass
class BalanceCheck:
    status: str
    assets: list[str] = field(default_factory=list)      # current-year digit strings found on total-assets rows
    capital: list[str] = field(default_factory=list)
    detail: str = ""

    def as_dict(self) -> dict:
        return {"status": self.status, "assets": self.assets[:3], "capital": self.capital[:3], "detail": self.detail}


def _row_total(line: str, keys: tuple[str, ...]) -> str | None:
    """Current-year number of a table row whose label is one of `keys`, else None.

    The label is what precedes the first number cell, minus the "mã số" cell. OCR leaves junk cells after the numbers
    and noise around the label, so neither is allowed to veto the row.
    """
    if not line.lstrip().startswith("|"):
        return None
    cells = [c.strip(" *_") for c in line.replace("<br>", " ").strip().strip("|").split("|")]
    first_number = next((i for i, c in enumerate(cells) if _NUMBER.search(c)), None)
    if first_number is None:
        return None
    label = fold("".join(c for c in cells[:first_number] if not _CODE_CELL.fullmatch(c)))
    if not any((at := label.find(k)) in range(_MAX_PREFIX + 1) and _REMAINDER_OK.fullmatch(label[at + len(k):]) for k in keys):
        return None
    numbers = [m for c in cells[first_number:] for m in _NUMBER.findall(c)]
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
