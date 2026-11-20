"""A month as it is printed: for every task and day, the names to show.

Every printed document (monthly grid, event rota, weekly sheets, open
shifts) is drawn from this one structure, so they always agree with each
other and with the screen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .. import demand
from ..model import Area, Month, Person, Settings, Task, Version, parse_date, slot_key
from ..schedule import Schedule
from ..view import person_labels
from . import versions

PERSON, GUEST, OPEN, WANTED = "person", "guest", "open", "wanted"


@dataclass
class Place:
    """One position on one day: who, or an open place to sign up for."""

    kind: str       # PERSON, GUEST, OPEN or WANTED (open and highlighted)
    text: str = ""  # name on the rota, or the guest's name
    changed: bool = False  # not as in the last version handed out
    note: str = ""         # a short practical note printed with the name

    @property
    def is_open(self) -> bool:
        return self.kind in (OPEN, WANTED)


@dataclass
class Cell:
    places: List[Place] = field(default_factory=list)
    closed: str = ""  # label of the closure cancelling it
    time: str = ""    # other times that day ("12:00–15:00 · Christmas meal")


@dataclass
class Row:
    task: Task
    cells: Dict[str, Cell]  # by ISO date; a day missing here needs nobody
    on_main_rota: bool = True  # False: a task of an event shown on that event's rota only

    def needed(self, iso: str) -> bool:
        return bool(self.cells.get(iso) and self.cells[iso].places)


@dataclass
class AreaRows:
    area: Area
    rows: List[Row]


@dataclass
class Day:
    iso: str
    day: int
    weekday: str     # "Mon"
    long: str        # "Monday 7 December"
    weekend: bool
    items: List["Span"] = field(default_factory=list)  # events and markers covering it


@dataclass
class Span:
    """An event or marker as printed: a label over a run of days."""

    kind: str   # "event", "big", "closed", "offrota", "note"
    label: str
    start: str
    end: str

    def covers(self, iso: str) -> bool:
        return self.start <= iso <= self.end


@dataclass
class Sheet:
    month: Month
    days: List[Day]
    areas: List[AreaRows]
    spans: List[Span]
    names: Dict[str, str]  # person id -> name on the rota
    changes: int = 0       # places changed since the last version

    def day(self, iso: str) -> Optional[Day]:
        return next((d for d in self.days if d.iso == iso), None)


def long_date(iso: str) -> str:
    day = parse_date(iso)
    return f"{day.strftime('%A')} {day.day} {day.strftime('%B')}"


def build_sheet(month: Month, people: List[Person], settings: Settings) -> Sheet:
    schedule = Schedule(month, people, settings)
    names = {pid: label["short"] for pid, label in person_labels(people).items()}
    wanted = set(month.wanted)
    last = versions.reference(month)

    spans = [Span("big" if e.big else "event", e.name, e.start, e.end) for e in month.events]
    spans += [Span(m.kind, m.label, m.start, m.end) for m in month.markers]
    days = []
    for iso in schedule.dates:
        date = parse_date(iso)
        days.append(Day(iso, date.day, date.strftime("%a"), long_date(iso), date.weekday() >= 5,
                        [s for s in spans if s.covers(iso)]))

    areas = []
    for area in month.areas:
        if area.archived:
            continue
        rows = []
        for task in month.tasks:
            if task.area_id != area.id or task.archived:
                continue
            cells = {}
            for iso in schedule.dates:
                cell = _cell(schedule, month, task, iso, names, wanted, last)
                if cell is not None:
                    cells[iso] = cell
            if any(c.places for c in cells.values()):
                rows.append(Row(task, cells, month.on_main_rota(task)))
        if rows:
            areas.append(AreaRows(area, rows))
    changes = sum(1 for a in areas for r in a.rows for c in r.cells.values() for p in c.places if p.changed)
    return Sheet(month, days, areas, spans, names, changes)


def _cell(schedule: Schedule, month: Month, task: Task, iso: str, names: Dict[str, str], wanted: set,
          last: Optional[Version]) -> Optional[Cell]:
    closed = demand.closure(month, task, iso)
    count = schedule.positions(task.id, iso)
    if count == 0 and closed is None:
        return None
    required = schedule.required.get((task.id, iso), 0)
    places = []
    for position in range(count):
        key = slot_key(task.id, iso, position)
        assignment = schedule.at((task.id, iso, position))
        if assignment is not None and assignment.person_id:
            place = Place(PERSON, names.get(assignment.person_id, "?"))
        elif assignment is not None:
            place = Place(GUEST, assignment.guest_label)
        elif position < required:
            place = Place(WANTED if key in wanted else OPEN)
        else:
            continue
        place.note = assignment.note if assignment is not None else ""
        place.changed = versions.changed(last, key, versions.place_label(place.text, place.note))
        places.append(place)
    change = month.time_changes.get(f"{task.id}|{iso}")
    return Cell(places, closed.label if closed else "", schedule.task_on(task.id, iso).time_text() if change else "")
