"""The printed documents, as HTML pages styled by ``web/css/print.css``.

Each function returns a ``Document``; ``page()`` turns it into a complete
HTML file, either linking the style sheet (shown by the local server) or
with the styles inside (a stand-alone file, turned into PDF).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from ..model import PrintTemplate, parse_date
from . import versions
from .html import Markup, el, join
from .sheet import GUEST, OPEN, WANTED, Cell, Day, Row, Sheet, Span, long_date

WEB = Path(__file__).resolve().parent.parent / "web"
PRINT_CSS = WEB / "css" / "print.css"
PRINT_JS = WEB / "js" / "print-page.js"  # fits the main rota to its paper; inside files made into PDF too
SPAN_WORDS = {"event": "Event", "big": "Event", "closed": "Closed", "offrota": "Off-rota", "note": "Note"}


@dataclass
class Document:
    title: str
    filename: str   # without extension
    body: Markup
    kind: str       # "month", "event", "weekly", "open": a class on <body>
    stamp: str = ""  # in each header: "Draft · printed …" or "Version 2 · …"


def page(document: Document, standalone: bool) -> str:
    """The whole HTML page, with ``document.stamp`` in every header.
    ``standalone``: styles inside, for a file turned into PDF; otherwise linked, as the server shows it."""
    if standalone:
        style = el("style", {}, Markup(PRINT_CSS.read_text(encoding="utf-8")))
        bar = ""
        script = el("script", {}, Markup(PRINT_JS.read_text(encoding="utf-8")))
    else:
        style = el("link", {"rel": "stylesheet", "href": "/css/print.css"})
        script = el("script", {"src": "/js/print-page.js"})
        bar = el("div", {"class_": "screen-bar"},
                 el("strong", {}, document.title),
                 el("span", {"class_": "hint"}, "This is how it will print. Use Print, or Save as PDF in the program."),
                 el("button", {"type": "button", "id": "print"}, "Print…"))
    body = document.body.replace(STAMP, el("span", {}, document.stamp or f"Draft · {printed_on()}"))
    return ("<!doctype html>\n" + el("html", {"lang": "en"},
            el("head", {}, el("meta", {"charset": "utf-8"}), el("title", {}, document.title), style),
            el("body", {"class_": ["doc", f"doc-{document.kind}"]}, bar, Markup(body), script)))


STAMP = "<!--stamp-->"  # where the date (and later the version) goes in each header


def _stamp() -> Markup:
    return Markup(STAMP)


def head(title: str, subtitle: str = "") -> Markup:
    return el("header", {"class_": "doc-head"},
              el("h1", {}, title),
              el("span", {"class_": "sub"}, subtitle) if subtitle else None,
              el("span", {"class_": "stamp"}, _stamp()))


def _short(iso: str) -> str:
    day = parse_date(iso)
    return f"{day.day} {day.strftime('%b')}"


def _range(start: str, end: str) -> str:
    return _short(start) if start == end else f"{_short(start)} – {_short(end)}"


def _task_time(row: Row, show_times: bool) -> Optional[Markup]:
    """Its time and hours, when times are shown. A task cut into several times always
    shows them: the time is what tells its rows apart."""
    if not show_times and row.task.piece_of is None:
        return None
    task = row.task
    bits = [task.time_text(), f"{task.hours:g} h" if task.hours else ""]
    text = " · ".join(b for b in bits if b)
    return el("small", {}, text) if text else None


def _follows(row: Row, previous: Optional[Row]) -> bool:
    """Is ``row`` one more time of the task printed just before it (a task cut into
    several times)? Then it is printed without the name, under the first."""
    return row.task.piece_of is not None and previous is not None and previous.task.piece_of == row.task.piece_of


def _task_label(row: Row, previous: Optional[Row], show_times: bool) -> List[Optional[Markup]]:
    """A task's name and time in a printed grid; only its time for one more time of the task above."""
    return [None if _follows(row, previous) else el("span", {}, row.task.name), _task_time(row, show_times)]


def _places(cell: Cell) -> List[Markup]:
    """The names in a cell, one per line; open places as lines to sign up on.
    A place that changed since the last version is marked with a dot."""
    out = []
    for place in cell.places:
        mark = ["p", place.changed and "changed"]
        if place.is_open:  # nothing printed: room to write a name (a highlighted one colours the cell)
            out.append(el("div", {"class_": mark + ["blank", place.kind == WANTED and "wanted"]}, Markup("&nbsp;")))
        else:
            out.append(el("div", {"class_": mark + [place.kind == GUEST and "guest"]}, place.text,
                          el("small", {"class_": "note"}, place.note) if place.note else None))
    return out


# ---- Monthly rota ------------------------------------------------------------


def month_rota_rows(rows: List[Row], event_tasks: str = "events") -> List[Row]:
    """Rows for the monthly rota and weekly sheets: tasks of events as
    ``event_tasks`` says ("events": as each event decides, "all", "none"), and
    the same event task for several events (hot chocolate on the first day of
    each retreat) sharing one row."""
    out: List[Row] = []
    shared = {}
    for row in rows:
        task = row.task
        if task.event_id is not None and (event_tasks == "none" or (event_tasks == "events" and not row.on_main_rota)):
            continue
        if task.event_id is None:
            out.append(row)
            continue
        key = (task.name.lower(), task.time_text())
        first = shared.get(key)
        needed = {iso for iso, cell in row.cells.items() if cell.places}
        if first is not None and not needed & {iso for iso, cell in first.cells.items() if cell.places}:
            first.cells = {**first.cells, **{iso: row.cells[iso] for iso in needed}}
            continue
        row = Row(task, dict(row.cells), row.on_main_rota)
        shared.setdefault(key, row)
        out.append(row)
    return out


def _lanes(spans: Sequence[Span], dates: List[str]) -> List[List[Tuple[Span, int, int]]]:
    """Spans over the days shown, packed in lanes: (span, first column, last column)."""
    first, last = dates[0], dates[-1]
    index = {iso: i for i, iso in enumerate(dates)}
    placed = sorted(
        ((s, index[max(s.start, first)], index[min(s.end, last)]) for s in spans if s.end >= first and s.start <= last),
        key=lambda item: item[1])
    lanes: List[List[Tuple[Span, int, int]]] = []
    for item in placed:
        lane = next((lane for lane in lanes if lane[-1][2] < item[1]), None)
        if lane is None:
            lanes.append([item])
        else:
            lane.append(item)
    return lanes


def _band(spans: Sequence[Span], dates: List[str]) -> List[Markup]:
    rows = []
    for number, lane in enumerate(_lanes(spans, dates)):
        cells = [el("th", {"class_": "lane"}, "Events & notes" if number == 0 else "")]
        column = 0
        for span, start, end in lane:
            if start > column:
                cells.append(el("td", {"colspan": start - column}))
            before = "‹ " if span.start < dates[0] else ""
            after = " ›" if span.end > dates[-1] else ""
            cells.append(el("td", {"colspan": end - start + 1},
                            el("span", {"class_": ["span", f"span-{span.kind}"], "title": SPAN_WORDS.get(span.kind, "")},
                               f"{before}{span.label}{after}")))
            column = end + 1
        if column < len(dates):
            cells.append(el("td", {"colspan": len(dates) - column}))
        rows.append(el("tr", {"class_": "band"}, cells))
    return rows


def _grid_cell(cell: Optional[Cell], closed_label: bool = False) -> Markup:
    """One task on one day, in a printed grid: the names centred; nothing when the
    place is open (room to write); the whole cell coloured when a place is highlighted."""
    if cell is None:
        return el("td", {"class_": "off"})
    if cell.closed and not cell.places:
        return el("td", {"class_": "closed", "title": cell.closed}, el("small", {}, cell.closed) if closed_label else None)
    wanted = any(place.kind == WANTED for place in cell.places)
    return el("td", {"class_": ["cell", not cell.places and "off", wanted and "signup"]},
              el("div", {"class_": "retimed"}, f"◷ {cell.time}") if cell.time else None,
              _places(cell))


def _day_cells(days: List[Day], tag: str = "th") -> List[Markup]:
    return [el(tag, {"class_": ["day", d.weekend and "weekend"]}, el("span", {}, d.weekday), el("b", {}, str(d.day)))
            for d in days]


def _month_table(sheet: Sheet, days: List[Day], show_times: bool, areas: Optional[List] = None,
                 event_tasks: str = "events", tasks_at_end: bool = False, repeat_days: bool = False) -> Markup:
    """``tasks_at_end``: the task and its time again after the last day (a wide
    sheet is read from both sides). ``repeat_days``: the day numbers again on
    each area's line, so that every sheet of a poster, and every part of a
    long table, shows them."""
    dates = [d.iso for d in days]
    end_head = [el("th", {"class_": "corner end"}, "Task")] if tasks_at_end else []
    head = el("thead", {},
              _band(sheet.spans, dates),
              el("tr", {"class_": "days"}, el("th", {"class_": "corner"}, "Task"), _day_cells(days), end_head))
    width = len(days) + 1 + (1 if tasks_at_end else 0)
    bodies = []
    for block in sheet.areas if areas is None else areas:
        rows = [row for row in month_rota_rows(block.rows, event_tasks) if any(row.cells.get(iso) for iso in dates)]
        if not rows:
            continue
        name = [el("span", {"class_": "area-name"}, block.area.name),
                el("span", {"class_": "manager"}, block.area.manager) if block.area.manager else None]
        if repeat_days:
            lines = [el("tr", {"class_": "area-row with-days"}, el("th", {}, name), _day_cells(days),
                        el("th", {"class_": "end"}, block.area.name) if tasks_at_end else None)]
        else:
            lines = [el("tr", {"class_": "area-row"}, el("th", {"colspan": width}, name))]
        for previous, row in zip([None] + rows, rows):
            label = _task_label(row, previous, show_times)
            piece = _follows(row, previous) and "piece"
            lines.append(el("tr", {"class_": piece},
                            el("th", {"class_": ["task", piece]}, label),
                            [_grid_cell(row.cells.get(iso)) for iso in dates],
                            el("th", {"class_": ["task end", piece]}, label) if tasks_at_end else None))
        bodies.append(el("tbody", {"class_": ["area", f"area-{block.area.color}"]}, lines))
    columns = el("colgroup", {}, el("col", {"class_": "c-task"}), [el("col", {"class_": "c-day"}) for _ in days],
                 el("col", {"class_": "c-task"}) if tasks_at_end else None)
    return el("table", {"class_": ["rota", f"days-{len(days)}", tasks_at_end and "tasks-at-end"]}, columns, head, bodies)


def _legend(sheet: Sheet) -> Markup:
    return el("footer", {"class_": "legend"},
              el("span", {}, el("b", {"class_": "dot"}, "●"), f"changed since version {versions.reference(sheet.month).number}")
              if sheet.changes else None,
              el("span", {}, el("i", {"class_": "key off"}), "Not needed"),
              el("span", {}, el("i", {"class_": "key closed"}), "Closed"),
              el("span", {}, el("i", {"class_": "key blank"}), "Empty: free, add your name"),
              el("span", {}, el("i", {"class_": "key signup"}), "Coloured: please sign up"),
              el("span", {}, "◷ other time that day"))


# Paper for the main rota, in mm. A "poster" is the whole printout; the print
# page (web/js/print-page.js) scales the grid so that it fills it exactly, and
# cuts A2 and A1 posters into A3 sheets to join: `cols` x `rows` tiles that
# overlap by OVERLAP_MM, each with a band at the bottom for its label.
PAGE_MARGIN_MM, SHEET_MARGIN_MM, OVERLAP_MM, LABEL_MM = 8, 5, 10, 6
PAGES = {"a4l": (297, 210), "a3l": (420, 297), "a3p": (297, 420), "a2l": (594, 420), "a1l": (841, 594)}
TILED = {"a2": ("a3p", 2, 1), "a1": ("a3l", 2, 2)}  # poster size -> A3 sheet, columns, rows
SINGLE = {"a3": "a3l", "a4x2": "a4l", "a2": "a2l", "a1": "a1l"}


def poster_layout(size: str, output: str) -> dict:
    """Where the grid goes: one page, or A3 sheets to join (sizes in mm)."""
    if output == "a3_sheets" and size in TILED:
        page, cols, rows = TILED[size]
        width, height = PAGES[page]
        tile_w, tile_h = width - 2 * SHEET_MARGIN_MM, height - 2 * SHEET_MARGIN_MM - LABEL_MM
        return {"page": f"{page}t", "cols": cols, "rows": rows, "tile_w": tile_w, "tile_h": tile_h,
                "w": cols * tile_w - (cols - 1) * OVERLAP_MM, "h": rows * tile_h - (rows - 1) * OVERLAP_MM}
    page = SINGLE.get(size, "a3l")
    width, height = PAGES[page]
    inner_w, inner_h = width - 2 * PAGE_MARGIN_MM, height - 2 * PAGE_MARGIN_MM
    return {"page": page, "cols": 1, "rows": 1, "tile_w": inner_w, "tile_h": inner_h, "w": inner_w, "h": inner_h}


def monthly(sheet: Sheet, template: PrintTemplate) -> Document:
    """The main rota as a grid (tasks down, days across), as ``template`` says:
    which areas, which paper, times or not, which tasks of events."""
    month = sheet.month
    areas = [a for a in sheet.areas if not template.area_ids or a.area.id in template.area_ids]
    title = f"Rota — {month.title()}"
    chosen = " · ".join(a.area.name for a in areas) if template.area_ids else ""
    parts = [sheet.days[:16], sheet.days[16:]] if template.size == "a4x2" else [sheet.days]
    layout = poster_layout(template.size, template.output)
    posters = []
    for number, days in enumerate(parts):
        half = f"{_short(days[0].iso)} – {_short(days[-1].iso)}, part {number + 1} of 2" if len(parts) > 1 else ""
        content = el("div", {"class_": "poster-content"},
                     head(title, " · ".join(x for x in (chosen, half) if x)),
                     _month_table(sheet, days, template.show_times, areas, template.event_tasks,
                                  template.tasks_at_end, template.repeat_days),
                     _legend(sheet))
        data = {f"data_{key}": value for key, value in layout.items()}
        posters.append(el("section", {"class_": "poster", **data, "data_overlap": OVERLAP_MM}, content))
    name = f"Rota {month.title()}" if not template.area_ids and template.size == "a3" else f"Rota {month.title()} - {template.name}"
    return Document(title, name, join(posters), "month")


# ---- Event rota: day by day, by area -----------------------------------------


def _day_block(sheet: Sheet, day: Day, show_times: bool, skip: Iterable[str] = ()) -> Markup:
    areas = []
    for block in sheet.areas:
        rows = []
        previous = None
        for row in block.rows:
            cell = row.cells.get(day.iso)
            if cell is None or not cell.places:
                continue
            # A task cut into several times: the name once, then each further time alone, always with its time.
            follows = _follows(row, previous)
            previous = row
            time = cell.time or (row.task.time_text() if show_times or row.task.piece_of else "")
            rows.append(el("tr", {"class_": follows and "piece"},
                           el("th", {}, None if follows else el("span", {}, row.task.name),
                              el("small", {"class_": cell.time and "retimed"}, f"◷ {time}" if cell.time else time) if time else None),
                           el("td", {}, _places(cell))))
        if rows:
            areas.append(el("section", {"class_": ["area-box", f"area-{block.area.color}"]},
                            el("h3", {}, block.area.name), el("table", {}, rows)))
    items = [s for s in day.items if s.label not in skip]
    return el("article", {"class_": "day"},
              el("h2", {}, day.long, [el("span", {"class_": ["span", f"span-{s.kind}"]}, s.label) for s in items]),
              el("div", {"class_": "area-boxes"}, areas) if areas else el("p", {"class_": "empty"}, "Nothing to staff this day."))


def event_rota(name: str, start: str, end: str, parts: List[Tuple[Sheet, List[str]]],
               missing: Sequence[str] = (), one_day_per_page: bool = True, show_times: bool = True) -> Document:
    """``parts``: each month's sheet with the event's days in it (an event can run into the next month).
    ``missing``: titles of months the event reaches that are not created yet."""
    title = f"{name} — rota"
    first, last = parse_date(start), parse_date(end)
    dates = f"{long_date(start)} – {long_date(end)} {last.year}" if start != end else f"{long_date(start)} {first.year}"
    days = [_day_block(sheet, sheet.day(iso), show_times, skip=[name]) for sheet, isos in parts for iso in isos if sheet.day(iso)]
    notice = el("p", {"class_": "notice"},
                f"Not planned yet: the days in {' and '.join(missing)}.") if missing else None
    body = el("section", {"class_": ["sheet", "paper-a4p", one_day_per_page and "page-per-day"]},
              head(title, dates), notice, days)
    return Document(title, f"{name} rota", body, "event")


# ---- Weekly sheets by area -----------------------------------------------------


def weeks(days: List[Day]) -> List[List[Day]]:
    """The month's days cut into weeks starting on Monday."""
    out: List[List[Day]] = []
    for day in days:
        if not out or parse_date(day.iso).weekday() == 0:
            out.append([])
        out[-1].append(day)
    return out


def weekly(sheet: Sheet, area_ids: Optional[Sequence[str]] = None, show_times: bool = True) -> Document:
    """One page per area and week, in large print, to pin up in each area."""
    sections = []
    for block in sheet.areas:
        if area_ids and block.area.id not in area_ids:
            continue
        for week in weeks(sheet.days):
            dates = [d.iso for d in week]
            rows = [row for row in month_rota_rows(block.rows) if any(row.cells.get(iso) for iso in dates)]
            if not rows:
                continue
            table = el("table", {"class_": "week"},
                       el("thead", {}, el("tr", {}, el("th", {}, block.area.name),
                                         [el("th", {"class_": d.weekend and "weekend"}, el("span", {}, d.weekday), el("b", {}, str(d.day))) for d in week])),
                       el("tbody", {}, [el("tr", {"class_": _follows(row, previous) and "piece"},
                                           el("th", {"class_": ["task", _follows(row, previous) and "piece"]},
                                              _task_label(row, previous, show_times)),
                                           [_grid_cell(row.cells.get(iso), closed_label=True) for iso in dates])
                                        for previous, row in zip([None] + rows, rows)]))
            notes = [s for s in sheet.spans if s.end >= dates[0] and s.start <= dates[-1]]
            sections.append(el("section", {"class_": ["sheet", "paper-a4l", "area-sheet", f"area-{block.area.color}"]},
                               head(f"{block.area.name} — week of {week[0].long}", block.area.manager),
                               el("p", {"class_": "spans"}, [el("span", {"class_": ["span", f"span-{s.kind}"]}, f"{s.label} · {_range(s.start, s.end)}") for s in notes]) if notes else None,
                               table))
    title = f"Weekly sheets — {sheet.month.title()}"
    return Document(title, f"Weekly sheets {sheet.month.title()}",
                    join(sections) or el("p", {"class_": "empty"}, "Nothing to print."), "weekly")




# ---- Open shifts -----------------------------------------------------------------


def open_shifts(sheet: Sheet, only_wanted: bool = False) -> Document:
    """Shifts still open, day by day, with lines to write a name on."""
    title = f"Shifts to sign up for — {sheet.month.title()}"
    days = []
    for day in sheet.days:
        rows = []
        for block in sheet.areas:
            for row in block.rows:
                cell = row.cells.get(day.iso)
                places = [p for p in (cell.places if cell else []) if p.kind == WANTED or (p.kind == OPEN and not only_wanted)]
                if not places:
                    continue
                time = cell.time or row.task.time_text()
                rows.append(el("tr", {"class_": any(p.kind == WANTED for p in places) and "wanted"},
                               el("td", {"class_": ["area-tag", f"area-{block.area.color}"]}, block.area.name),
                               el("td", {}, el("strong", {}, row.task.name), el("small", {}, time) if time else None),
                               el("td", {"class_": "lines"}, [el("div", {"class_": "line"}) for _ in places])))
        if rows:
            days.append(el("article", {"class_": "open-day"}, el("h2", {}, day.long), el("table", {"class_": "open"}, rows)))
    intro = el("p", {"class_": "intro"},
               "Write your name on a line to take the shift. Highlighted shifts are needed most.")
    body = el("section", {"class_": ["sheet", "paper-a4p"]}, head(title), intro,
              days or el("p", {"class_": "empty"}, "Every shift is filled. Thank you!"))
    return Document(title, f"Open shifts {sheet.month.title()}", body, "open")


def printed_on(today: Optional[date] = None) -> str:
    today = today or date.today()
    return f"printed {today.day} {today.strftime('%b %Y')}"
