"""The month as an Excel file (.xlsx), laid out like the monthly sheet used
so far: tasks down, days across, grey where nobody is needed. It opens in
Google Sheets, Excel and LibreOffice/OnlyOffice. Written with the standard
library only.
"""

from __future__ import annotations

import io
import zipfile
from typing import List, Optional, Tuple
from xml.sax.saxutils import escape

from ..xlsx_read import column_letters
from .documents import month_rota_rows
from .sheet import WANTED, Cell, Sheet

# Cell styles, numbered as in STYLES below.
PLAIN, TITLE, HEAD, HEAD_WEEKEND, AREA, TASK, NAMES, OFF, SIGN_UP, NOTE, CLOSED = range(11)

STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="4">
 <font><sz val="10"/><name val="Arial"/></font>
 <font><b/><sz val="10"/><name val="Arial"/></font>
 <font><b/><sz val="14"/><name val="Arial"/></font>
 <font><i/><sz val="9"/><color rgb="FF555B66"/><name val="Arial"/></font>
</fonts>
<fills count="7">
 <fill><patternFill patternType="none"/></fill>
 <fill><patternFill patternType="gray125"/></fill>
 <fill><patternFill patternType="solid"><fgColor rgb="FFD7DAE1"/><bgColor indexed="64"/></patternFill></fill>
 <fill><patternFill patternType="solid"><fgColor rgb="FFFFE98A"/><bgColor indexed="64"/></patternFill></fill>
 <fill><patternFill patternType="solid"><fgColor rgb="FFEEF0F5"/><bgColor indexed="64"/></patternFill></fill>
 <fill><patternFill patternType="solid"><fgColor rgb="FFE3E8F4"/><bgColor indexed="64"/></patternFill></fill>
 <fill><patternFill patternType="lightUp"><fgColor rgb="FFB8BDC7"/><bgColor rgb="FFECEEF2"/></patternFill></fill>
</fills>
<borders count="2">
 <border><left/><right/><top/><bottom/><diagonal/></border>
 <border><left style="thin"><color rgb="FFC9CED8"/></left><right style="thin"><color rgb="FFC9CED8"/></right><top style="thin"><color rgb="FFC9CED8"/></top><bottom style="thin"><color rgb="FFC9CED8"/></bottom><diagonal/></border>
</borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="11">
 <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
 <xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>
 <xf numFmtId="0" fontId="1" fillId="4" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
 <xf numFmtId="0" fontId="1" fillId="5" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
 <xf numFmtId="0" fontId="1" fillId="4" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>
 <xf numFmtId="0" fontId="1" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
 <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
 <xf numFmtId="0" fontId="0" fillId="2" borderId="1" xfId="0" applyFill="1" applyBorder="1"/>
 <xf numFmtId="0" fontId="1" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="top" wrapText="1"/></xf>
 <xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
 <xf numFmtId="0" fontId="3" fillId="6" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
</cellXfs>
</styleSheet>"""

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""

ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

FIRST_DAY_COLUMN = 5  # A: area, B: task, C: time, D: hours
Row = List[Tuple[object, int]]  # (value, style) per column, from column A


def _cell_xml(row: int, col: int, value: object, style: int) -> str:
    ref = f"{column_letters(col)}{row}"
    if value is None or value == "":
        return f'<c r="{ref}" s="{style}"/>' if style else ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}" s="{style}"><v>{value:g}</v></c>'
    text = escape(str(value))
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _names(cell: Optional[Cell]) -> Tuple[str, int]:
    """What a day cell shows, and its style."""
    if cell is None:
        return "", OFF
    if cell.closed and not cell.places:
        return cell.closed, CLOSED
    if not cell.places:
        return "", OFF
    lines = []
    for place in cell.places:
        if place.is_open:  # nothing written; a highlighted one colours the cell
            lines.append("")
        else:
            lines.append(place.text + (f" ({place.note})" if place.note else "") + (" ●" if place.changed else ""))
    if cell.time:
        lines.insert(0, f"◷ {cell.time}")
    style = SIGN_UP if any(p.kind == WANTED for p in cell.places) else NAMES
    return "\n".join(lines).rstrip("\n") if any(lines) else "", style


def workbook(sheet: Sheet, stamp: str = "") -> bytes:
    """The .xlsx file's bytes."""
    days = sheet.days
    rows: List[Tuple[Row, Optional[float]]] = []  # cells, height in points
    rows.append(([(f"Rota — {sheet.month.title()}", TITLE), (None, 0), (stamp, PLAIN)], 22))
    notes = [" / ".join(s.label for s in day.items) for day in days]
    rows.append(([(None, 0)] * (FIRST_DAY_COLUMN - 1) + [(n, NOTE) for n in notes], 30 if any(notes) else None))
    rows.append(([("Area", HEAD), ("Task", HEAD), ("Time", HEAD), ("Hours", HEAD)]
                 + [(f"{d.weekday} {d.day}", HEAD_WEEKEND if d.weekend else HEAD) for d in days], 28))
    for block in sheet.areas:
        task_rows = month_rota_rows(block.rows)
        if not task_rows:
            continue
        rows.append(([(block.area.name, AREA)] + [(None, AREA)] * (FIRST_DAY_COLUMN - 2 + len(days)), None))
        for row in task_rows:
            cells: Row = [(block.area.name, NAMES), (row.task.name, TASK), (row.task.time_text(), NAMES),
                          (row.task.hours or "", NAMES)]
            lines = 1
            for day in days:
                text, style = _names(row.cells.get(day.iso))
                cells.append((text, style))
                lines = max(lines, text.count("\n") + 1 if text else 1)
            rows.append((cells, 13.5 * lines + 2 if lines > 1 else None))

    xml_rows = []
    for number, (cells, height) in enumerate(rows, start=1):
        size = f' ht="{height:g}" customHeight="1"' if height else ""
        body = "".join(_cell_xml(number, col, value, style) for col, (value, style) in enumerate(cells, start=1))
        xml_rows.append(f'<row r="{number}"{size}>{body}</row>')
    widths = [16, 30, 20, 7] + [11] * len(days)
    cols = "".join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths, start=1))
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView workbookViewId="0">'
        f'<pane xSplit="{FIRST_DAY_COLUMN - 1}" ySplit="3" topLeftCell="{column_letters(FIRST_DAY_COLUMN)}4" activePane="bottomRight" state="frozen"/>'
        '</sheetView></sheetViews>'
        f'<cols>{cols}</cols><sheetData>{"".join(xml_rows)}</sheetData></worksheet>'
    )
    name = escape(sheet.month.title()[:31], {'"': "&quot;"})
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{name}" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("_rels/.rels", ROOT_RELS)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", WORKBOOK_RELS)
        archive.writestr("xl/styles.xml", STYLES)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return buffer.getvalue()
