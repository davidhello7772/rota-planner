"""Build small .xlsx files in memory for tests (invented content only)."""

import io
import zipfile
from xml.sax.saxutils import escape

from rotaplanner.xlsx_read import column_letters

GREY = "FFB7B7B7"


def build_xlsx(sheets: dict) -> bytes:
    """``sheets``: name -> {"cells": {(row, col): value | (value, fill, formula)}, "merges": ["A1:B1"]}.

    Text goes to the shared strings, numbers stay numbers; ``fill`` is an
    ARGB colour; ``formula`` marks the cell as computed.
    """
    strings: list = []
    fills = [None, "gray125"]  # the two fills every workbook starts with
    sheet_xml = []
    for name, content in sheets.items():
        rows: dict = {}
        for (row, col), spec in content.get("cells", {}).items():
            value, fill, formula = spec if isinstance(spec, tuple) else (spec, None, False)
            if fill and fill not in fills:
                fills.append(fill)
            style = fills.index(fill) if fill else 0
            ref = f"{column_letters(col)}{row}"
            f_xml = "<f>X()</f>" if formula else ""
            if value is None:
                xml = f'<c r="{ref}" s="{style}">{f_xml}</c>'
            elif isinstance(value, (int, float)):
                xml = f'<c r="{ref}" s="{style}">{f_xml}<v>{value}</v></c>'
            else:
                strings.append(str(value))
                xml = f'<c r="{ref}" s="{style}" t="s">{f_xml}<v>{len(strings) - 1}</v></c>'
            rows.setdefault(row, []).append((col, xml))
        body = "".join(
            f'<row r="{r}">' + "".join(x for _, x in sorted(cells)) + "</row>" for r, cells in sorted(rows.items())
        )
        merges = content.get("merges", [])
        merge_xml = (f'<mergeCells count="{len(merges)}">' + "".join(f'<mergeCell ref="{m}"/>' for m in merges)
                     + "</mergeCells>") if merges else ""
        sheet_xml.append(
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"<sheetData>{body}</sheetData>{merge_xml}</worksheet>"
        )

    fill_xml = "".join(
        '<fill><patternFill patternType="none"/></fill>' if f is None
        else '<fill><patternFill patternType="gray125"/></fill>' if f == "gray125"
        else f'<fill><patternFill patternType="solid"><fgColor rgb="{f}"/></patternFill></fill>'
        for f in fills
    )
    xf_xml = "".join(f'<xf fillId="{i}"/>' for i in range(len(fills)))
    main = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rel_ns = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/workbook.xml", f"<workbook {main} {rel_ns}><sheets>" + "".join(
            f'<sheet name="{escape(n, {chr(34): "&quot;"})}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
            for i, n in enumerate(sheets)) + "</sheets></workbook>")
        archive.writestr("xl/_rels/workbook.xml.rels",
                         '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                         + "".join(f'<Relationship Id="rId{i + 1}" Target="worksheets/sheet{i + 1}.xml" Type="ws"/>'
                                   for i in range(len(sheets))) + "</Relationships>")
        for i, xml in enumerate(sheet_xml):
            archive.writestr(f"xl/worksheets/sheet{i + 1}.xml", xml)
        archive.writestr("xl/sharedStrings.xml", f"<sst {main}>" + "".join(
            f"<si><t>{escape(s)}</t></si>" for s in strings) + "</sst>")
        archive.writestr("xl/styles.xml", f"<styleSheet {main}><fills>{fill_xml}</fills>"
                                          f"<cellXfs>{xf_xml}</cellXfs></styleSheet>")
    return buffer.getvalue()
