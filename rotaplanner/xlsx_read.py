"""Read .xlsx workbooks with the standard library only.

Only what the importers need: cell values (text, numbers, cached results of
formulas), whether a cell holds a formula, cell fill colours and merged
ranges. Workbooks come from people, so the reader is defensive: it never
evaluates anything and refuses archives that are suspiciously large.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import IO, Dict, Iterator, List, Optional, Tuple, Union
from xml.etree import ElementTree
from xml.parsers import expat as pyexpat

MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
MAX_UNPACKED_BYTES = 40_000_000
# Cells further out than this are ignored: no rota sheet is that large, and a
# single stray cell far away would otherwise make every loop over the sheet huge.
MAX_ROW, MAX_COL = 20_000, 400
SAFE_EXPAT = (2, 4, 1)  # first version that stops "billion laughs" XML
EXCEL_EPOCH = date(1899, 12, 30)

Value = Union[str, float, None]


class XlsxError(Exception):
    """The file is not a workbook this program can read."""


def column_index(letters: str) -> int:
    """``"A"`` → 1, ``"AJ"`` → 36."""
    number = 0
    for char in letters:
        number = number * 26 + ord(char) - 64
    return number


def column_letters(index: int) -> str:
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def split_ref(ref: str) -> Tuple[int, int]:
    """``"AJ12"`` → ``(12, 36)`` as (row, column)."""
    match = re.fullmatch(r"([A-Z]{1,3})([0-9]{1,7})", ref)
    if not match:
        raise XlsxError(f"Bad cell reference {ref!r}")
    return int(match.group(2)), column_index(match.group(1))


def _children(parent: ElementTree.Element, tag: str) -> List[ElementTree.Element]:
    """Children of the first ``tag`` element under ``parent`` (none if absent)."""
    element = parent.find(f"{MAIN}{tag}")
    return list(element) if element is not None else []


def serial_to_date(value: Value) -> Optional[date]:
    """Excel day number (as text or number) to a date, or None."""
    try:
        return EXCEL_EPOCH + timedelta(days=int(float(value)))
    except (TypeError, ValueError, OverflowError):
        return None


@dataclass
class Cell:
    value: Value = None
    formula: bool = False
    fill: Optional[str] = None  # "FFB7B7B7"-style colour, None when not filled


@dataclass
class Sheet:
    name: str
    cells: Dict[Tuple[int, int], Cell] = field(default_factory=dict)
    merges: List[Tuple[int, int, int, int]] = field(default_factory=list)

    def cell(self, row: int, col: int) -> Cell:
        return self.cells.get((row, col)) or Cell()

    def value(self, row: int, col: int) -> Value:
        return self.cell(row, col).value

    def text(self, row: int, col: int) -> str:
        """Value as tidy text: spaces collapsed, line breaks kept as spaces."""
        value = self.value(row, col)
        if value is None:
            return ""
        if isinstance(value, float):
            return str(int(value)) if value.is_integer() else repr(value)
        return " ".join(str(value).split())

    def number(self, row: int, col: int) -> Optional[float]:
        value = self.value(row, col)
        try:
            return float(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    def merged_value(self, row: int, col: int) -> Value:
        """Value of the merged block a cell belongs to (its top-left cell)."""
        for top, left, bottom, right in self.merges:
            if top <= row <= bottom and left <= col <= right:
                return self.value(top, left)
        return self.value(row, col)

    def merge_span(self, row: int, col: int) -> int:
        """Number of columns a merged block starting at this cell covers."""
        for top, left, bottom, right in self.merges:
            if top == row and left == col:
                return right - left + 1
        return 1

    def rows(self) -> Iterator[int]:
        return iter(sorted({r for r, _ in self.cells}))

    def max_row(self) -> int:
        return max((r for r, _ in self.cells), default=0)

    def table(self, max_rows: int = 5000, max_cols: int = 200) -> List[List[str]]:
        """The sheet as rows of text, for the rows that hold something (bounded)."""
        width = min(max((col for _, col in self.cells), default=0), max_cols)
        rows = sorted({row for row, _ in self.cells})[:max_rows]
        return [[self.text(row, col) for col in range(1, width + 1)] for row in rows]


class Workbook:
    def __init__(self, source: Union[str, IO[bytes]]) -> None:
        if pyexpat.version_info < SAFE_EXPAT:
            raise XlsxError("This Python is too old to read workbooks safely. Please install a recent Python 3.")
        try:
            self._zip = zipfile.ZipFile(source)
        except (zipfile.BadZipFile, OSError) as error:
            raise XlsxError("This is not an Excel (.xlsx) file.") from error
        if sum(info.file_size for info in self._zip.infolist()) > MAX_UNPACKED_BYTES:
            raise XlsxError("This workbook is too large to read safely.")
        try:
            self._strings = self._shared_strings()
            self._fills = self._fill_colours()
            self._targets = self._sheet_targets()
        except (KeyError, ElementTree.ParseError) as error:
            raise XlsxError("This workbook is damaged or not in Excel format.") from error
        self._cache: Dict[str, Sheet] = {}

    @property
    def sheet_names(self) -> List[str]:
        return list(self._targets)

    def sheet(self, name: str) -> Sheet:
        if name not in self._targets:
            raise XlsxError(f"The workbook has no sheet named {name!r}.")
        if name not in self._cache:
            try:
                self._cache[name] = self._read_sheet(name, self._targets[name])
            except (KeyError, ElementTree.ParseError, zipfile.BadZipFile, ValueError) as error:
                raise XlsxError(f"The sheet {name!r} of this workbook cannot be read ({error}).") from None
        return self._cache[name]

    def find_sheet(self, *prefixes: str) -> Optional[str]:
        """First sheet whose name starts with one of the prefixes (case-insensitive)."""
        for prefix in prefixes:
            for name in self._targets:
                if name.lower().startswith(prefix.lower()):
                    return name
        return None

    # -- parts -------------------------------------------------------------

    def _xml(self, part: str) -> ElementTree.Element:
        return ElementTree.fromstring(self._zip.read(part))

    def _shared_strings(self) -> List[str]:
        if "xl/sharedStrings.xml" not in self._zip.namelist():
            return []
        return ["".join(t.text or "" for t in item.iter(f"{MAIN}t")) for item in self._xml("xl/sharedStrings.xml")]

    def _fill_colours(self) -> List[Optional[str]]:
        """Fill colour of each cell style, indexed like the ``s`` attribute."""
        if "xl/styles.xml" not in self._zip.namelist():
            return []
        styles = self._xml("xl/styles.xml")
        fills = []
        for fill in _children(styles, "fills"):
            pattern = fill.find(f"{MAIN}patternFill")
            colour = pattern.find(f"{MAIN}fgColor") if pattern is not None else None
            solid = pattern is not None and pattern.get("patternType") not in (None, "none")
            fills.append(colour.get("rgb") if solid and colour is not None else None)
        formats = _children(styles, "cellXfs")
        return [fills[int(xf.get("fillId", 0))] if int(xf.get("fillId", 0)) < len(fills) else None for xf in formats]

    def _sheet_targets(self) -> Dict[str, str]:
        rels = {
            rel.get("Id"): rel.get("Target")
            for rel in self._xml("xl/_rels/workbook.xml.rels")
            if rel.tag == f"{PKG_REL}Relationship"
        }
        targets = {}
        for sheet in self._xml("xl/workbook.xml").iter(f"{MAIN}sheet"):
            target = rels[sheet.get(f"{REL}id")]
            targets[sheet.get("name")] = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
        return targets

    def _read_sheet(self, name: str, part: str) -> Sheet:
        sheet = Sheet(name)
        root = self._xml(part)
        for row in root.iter(f"{MAIN}row"):
            for element in row.findall(f"{MAIN}c"):
                ref = element.get("r")
                if not ref:
                    continue
                position = split_ref(ref)
                if position[0] > MAX_ROW or position[1] > MAX_COL:
                    continue
                cell = Cell(
                    value=self._cell_value(element),
                    formula=element.find(f"{MAIN}f") is not None,
                    fill=self._fills[int(element.get("s", 0))] if int(element.get("s", 0)) < len(self._fills) else None,
                )
                if cell.value is not None or cell.formula or cell.fill:
                    sheet.cells[position] = cell
        for merge in root.iter(f"{MAIN}mergeCell"):
            first, _, last = merge.get("ref", "").partition(":")
            if first and last:
                (top, left), (bottom, right) = split_ref(first), split_ref(last)
                sheet.merges.append((top, left, bottom, right))
        return sheet

    def _cell_value(self, element: ElementTree.Element) -> Value:
        kind = element.get("t")
        if kind == "inlineStr":
            return "".join(t.text or "" for t in element.iter(f"{MAIN}t")) or None
        raw = element.find(f"{MAIN}v")
        if raw is None or raw.text is None:
            return None
        if kind == "s":
            index = int(raw.text)
            return self._strings[index] if index < len(self._strings) else None
        if kind in ("str", "e"):
            return raw.text
        if kind == "b":
            return "TRUE" if raw.text == "1" else "FALSE"
        try:
            return float(raw.text)
        except ValueError:
            return raw.text
