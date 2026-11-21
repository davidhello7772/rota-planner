"""Domain records.

Everything the application stores is one of these plain dataclasses. They hold
data and a few lookups only; the rules that act on them live in ``demand``,
``eligibility``, ``checks`` and ``solver`` so each can be tested on its own.

Dates are ISO strings (``"2026-12-01"``) wherever they are stored or used as
keys, so records serialise to JSON without conversion.
"""

from __future__ import annotations

import calendar
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import date, timedelta
from typing import Any, ClassVar, Dict, List, Optional, Tuple

SCHEMA_VERSION = 4

PERIODS = ("morning", "afternoon", "evening")
ANYTIME = "anytime"
"""Period of a task that can be done at any moment of the day."""
WEEKDAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
PRIORITIES = ("critical", "normal", "optional")

NORMAL_DAY = "normal"
"""Day type of an ordinary day: headcount comes from the weekday."""

EVENT_DAY = "event"
"""Day type of event and retreat days; always available."""

DEFAULT_HEADCOUNT = "default"
"""Headcount key used when a task needs the same number of people every day."""

MINUTES_PER_DAY = 24 * 60


def new_id(prefix: str) -> str:
    """Return a short unique identifier such as ``"task-3f9a1c2e"``."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def parse_date(iso: str) -> date:
    return date.fromisoformat(iso)


def shift_date(iso: str, days: int) -> str:
    return (parse_date(iso) + timedelta(days=days)).isoformat()


def weekday_key(iso: str) -> str:
    return WEEKDAY_KEYS[parse_date(iso).weekday()]


def week_start(iso: str) -> str:
    """The Monday of the week of ``iso``: weeks run Monday to Sunday everywhere in the program."""
    day = parse_date(iso)
    return (day - timedelta(days=day.weekday())).isoformat()


def week_dates(iso: str) -> List[str]:
    """The seven dates, Monday to Sunday, of the week of ``iso``."""
    monday = parse_date(week_start(iso))
    return [(monday + timedelta(days=offset)).isoformat() for offset in range(7)]


def month_dates(year: int, month: int) -> List[str]:
    """ISO dates of every day in the month, in order."""
    last = calendar.monthrange(year, month)[1]
    return [date(year, month, day).isoformat() for day in range(1, last + 1)]


def to_minutes(clock: str) -> Optional[int]:
    """Convert ``"HH:MM"`` to minutes after midnight; ``None`` if not a time."""
    hours, sep, minutes = clock.partition(":")
    if not sep or not hours.isdigit() or not minutes.isdigit() or len(minutes) != 2 or int(minutes) >= 60:
        return None
    return int(hours) * 60 + int(minutes)


def _revive(spec: Any, value: Any) -> Any:
    """Rebuild nested records described by a ``_nested`` entry."""
    if spec is None or value is None:
        return value
    if isinstance(spec, list):
        return [spec[0].from_dict(item) for item in value]
    if isinstance(spec, dict):
        (record,) = spec.values()
        return {key: record.from_dict(item) for key, item in value.items()}
    return spec.from_dict(value)


@dataclass
class Record:
    """Base class giving every record a JSON round trip.

    ``_nested`` names the fields that contain other records: ``[Task]`` for a
    list, ``{"*": Task}`` for a mapping, ``Task`` for a single record.
    Unknown keys are ignored on load so that a file written by a newer version
    of the program still opens.
    """

    _nested: ClassVar[Dict[str, Any]] = {}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]):
        known = {f.name for f in fields(cls)}
        kwargs = {
            key: _revive(cls._nested.get(key), value)
            for key, value in data.items()
            if key in known
        }
        return cls(**kwargs)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------
# Catalog: what the centre needs done
# --------------------------------------------------------------------------


@dataclass
class Skill(Record):
    id: str
    name: str


@dataclass
class Area(Record):
    id: str
    name: str
    manager: str = ""
    color: str = "slate"
    archived: bool = False


@dataclass
class DayType(Record):
    """A special kind of day (event, festival…) with its own headcounts."""

    id: str
    name: str


@dataclass
class Task(Record):
    """One line of the rota: something that needs people on some days.

    ``periods`` lists the parts of the day the task covers (``PERIODS``): a
    shift from 11:00 to 15:00 covers the morning and the afternoon, and needs
    someone available for both. ``[ANYTIME]`` is a task that can be done
    whenever the person is around that day.

    ``headcount`` says how many people are needed: by weekday (``"sun"``),
    else ``"default"``; a missing key means nobody. On a special day the
    day type's own number (``"event"``) applies when it is higher.
    """

    id: str
    area_id: str
    name: str
    periods: List[str] = field(default_factory=lambda: ["morning"])
    skill_id: Optional[str] = None
    start: str = ""
    end: str = ""
    time_label: str = ""
    hours: float = 0.0
    headcount: Dict[str, int] = field(default_factory=dict)
    priority: str = "normal"
    signup: bool = False
    archived: bool = False
    only_dates: List[str] = field(default_factory=list)
    event_id: Optional[str] = None
    """Set for a task of one event only: needed on that event's days (``event_days``)."""
    event_days: str = "all"
    on_month_rota: Optional[bool] = None
    """For a task of an event: ``None`` follows the event's choice
    (``Event.tasks_on_main_rota``); ``True`` or ``False`` is this task's own."""

    @property
    def any_time(self) -> bool:
        return ANYTIME in self.periods

    def interval(self) -> Optional[Tuple[int, int]]:
        """Start and end in minutes after midnight, or ``None`` if untimed.

        A task that ends at or before its start runs past midnight, so its end
        is pushed into the next day.
        """
        start, end = to_minutes(self.start), to_minutes(self.end)
        if start is None or end is None:
            return None
        if end <= start:
            end += MINUTES_PER_DAY
        return start, end

    def time_text(self) -> str:
        """Times for display, e.g. ``"09:00–13:00 · 10:00–15:00 at weekends"``."""
        parts = [f"{self.start}–{self.end}"] if self.start and self.end else []
        if self.time_label:
            parts.append(self.time_label)
        elif not parts and self.any_time:
            parts.append("Any time")
        return " · ".join(parts)


FREQUENCIES = {
    "weekly": "Every week",
    "odd": "Odd weeks",
    "even": "Even weeks",
    "1st": "1st of the month",
    "2nd": "2nd of the month",
    "3rd": "3rd of the month",
    "4th": "4th of the month",
    "last": "Last of the month",
}
# About how many times a month each comes round (a year has 52 weeks in 12 months).
TIMES_A_MONTH = {"weekly": 52 / 12, "odd": 26 / 12, "even": 26 / 12, "1st": 1.0, "2nd": 1.0, "3rd": 1.0, "4th": 1.0,
                 "last": 1.0}


@dataclass
class Regular(Record):
    """Someone who does the same task on the same weekday, week after week.

    ``frequency`` narrows which weeks (see ``FREQUENCIES``); odd and even use
    ISO week numbers. ``start`` and ``end`` (ISO dates, optional) bound it.
    """

    id: str
    person_id: str
    task_id: str
    weekday: str
    position: int = 0
    frequency: str = "weekly"
    start: str = ""
    end: str = ""

    def applies(self, iso: str) -> bool:
        if weekday_key(iso) != self.weekday:
            return False
        if (self.start and iso < self.start) or (self.end and iso > self.end):
            return False
        day = parse_date(iso)
        if self.frequency in ("odd", "even"):
            return (day.isocalendar()[1] % 2 == 1) == (self.frequency == "odd")
        if self.frequency == "last":
            return day.day + 7 > calendar.monthrange(day.year, day.month)[1]
        if self.frequency in ("1st", "2nd", "3rd", "4th"):
            return (day.day - 1) // 7 + 1 == int(self.frequency[0])
        return True


@dataclass
class EventType(Record):
    """A kind of event (Retreat, Festival…) and the tasks such events need on
    top of the usual ones (hot chocolate on the first day…). An event of this
    type gets copies of them, which can then be changed for that event only."""

    _nested: ClassVar[Dict[str, Any]] = {"tasks": [Task]}

    id: str
    name: str
    big: bool = False
    tasks_on_main_rota: bool = True  # what events of this type start with
    tasks: List[Task] = field(default_factory=list)


@dataclass
class Catalog(Record):
    _nested: ClassVar[Dict[str, Any]] = {
        "skills": [Skill],
        "areas": [Area],
        "tasks": [Task],
        "day_types": [DayType],
        "regulars": [Regular],
        "event_types": [EventType],
    }

    skills: List[Skill] = field(default_factory=list)
    areas: List[Area] = field(default_factory=list)
    tasks: List[Task] = field(default_factory=list)
    day_types: List[DayType] = field(default_factory=list)
    regulars: List[Regular] = field(default_factory=list)
    event_types: List[EventType] = field(default_factory=list)


@dataclass
class ConfigTemplate(Record):
    """A saved set of areas and tasks, to give a month later on."""

    _nested: ClassVar[Dict[str, Any]] = {"areas": [Area], "tasks": [Task]}

    id: str
    name: str
    areas: List[Area] = field(default_factory=list)
    tasks: List[Task] = field(default_factory=list)
    created: str = ""


# --------------------------------------------------------------------------
# People
# --------------------------------------------------------------------------


@dataclass
class NeverRule(Record):
    """A standing unavailability, e.g. never on Monday morning.

    ``period`` is one of ``PERIODS`` or ``"any"`` for the whole day.
    """

    weekday: str
    period: str = "any"


@dataclass
class Person(Record):
    _nested: ClassVar[Dict[str, Any]] = {"never": [NeverRule]}

    id: str
    name: str
    """Name on the rota, e.g. "Quinn J."."""
    first_name: str = ""
    last_name: str = ""
    lay_name: str = ""
    phone: str = ""
    resident: bool = True
    active: bool = True
    archived: bool = False
    skills: List[str] = field(default_factory=list)
    preferences: Dict[str, int] = field(default_factory=dict)
    target_hours: Optional[float] = None
    max_hours: Optional[float] = None
    max_hours_per_day: Optional[float] = None
    max_tasks_per_day: Optional[int] = None
    never: List[NeverRule] = field(default_factory=list)
    avoid_task_ids: List[str] = field(default_factory=list)
    note: str = ""
    email_fp: str = ""
    """One-way fingerprint of their e-mail address (``emails.py``); never the address."""


@dataclass
class Weights(Record):
    """How strongly each wish pulls when ranking who to suggest.

    Each weight multiplies a quantity of roughly unit size, so the numbers
    can be compared with one another directly.
    """

    load: float = 10.0
    over_target: float = 6.0
    same_day: float = 2.5
    back_to_back: float = 1.0
    preference: float = 0.6
    warning: float = 4.0
    weekend: float = 0.8
    evening: float = 0.5


PRINT_SIZES = {
    "a3": "A3, one sheet",
    "a2": "A2 poster",
    "a1": "A1 poster",
    "a4x2": "A4, two halves of the month",
}
"""Sizes of the printed main rota. A2 and A1 come on A3 sheets to join
(``PrintTemplate.output``), or as one big page for a print shop."""

EVENT_TASKS_PRINTED = {
    "events": "As set on each event",
    "all": "All of them",
    "none": "None of them",
}


@dataclass
class PrintTemplate(Record):
    """A way to print the main rota: size, areas, options. Kept for every month."""

    id: str
    name: str
    size: str = "a3"                 # PRINT_SIZES
    output: str = "a3_sheets"        # for A2/A1: "a3_sheets" (to join) or "one_page"
    area_ids: List[str] = field(default_factory=list)  # empty: every area
    show_times: bool = True
    event_tasks: str = "events"      # EVENT_TASKS_PRINTED
    tasks_at_end: bool = False       # the task and its time again after the last day
    repeat_days: bool = False        # day numbers again on each area's line


def default_print_templates() -> List[PrintTemplate]:
    return [
        PrintTemplate(id="print-a3", name="Whole rota", size="a3"),
        PrintTemplate(id="print-a2", name="Wall poster A2", size="a2", tasks_at_end=True, repeat_days=True),
        PrintTemplate(id="print-a1", name="Wall poster A1", size="a1", tasks_at_end=True, repeat_days=True),
    ]


@dataclass
class Settings(Record):
    _nested: ClassVar[Dict[str, Any]] = {"weights": Weights, "print_templates": [PrintTemplate]}

    centre_name: str = ""
    default_target_hours: float = 12.0
    default_max_hours_per_day: float = 4.0
    prorate_target: bool = True
    weights: Weights = field(default_factory=Weights)
    print_templates: List[PrintTemplate] = field(default_factory=default_print_templates)
    message_greeting: str = "Hello {name},"
    """First line of the message sent with each person's schedule; {name} is their first name."""
    message_closing: str = "Thank you!"
    message_subject: str = "Your shifts for {month}"
    """Subject of the e-mail sent with each schedule; {month} is the month's name."""


# --------------------------------------------------------------------------
# A month of rota
# --------------------------------------------------------------------------


@dataclass
class Assignment(Record):
    """Who fills one position of a task on one day.

    Either ``person_id`` is set, or the place goes to someone not on the
    list: a guest or a volunteer (``guest_kind``), whose name (``guest``)
    may not be known yet. ``forced_reason`` is ``None`` for a normal
    assignment; any string (even empty) means the coordinator knowingly
    overrode a rule, so checks stop reporting it.
    """

    task_id: str
    date: str
    position: int
    person_id: str = ""
    guest: str = ""
    guest_kind: str = ""  # GUEST_KINDS; empty with a name: a guest
    source: str = "manual"
    locked: bool = False
    forced_reason: Optional[str] = None
    note: str = ""

    @property
    def slot(self) -> Tuple[str, str, int]:
        return self.task_id, self.date, self.position

    @property
    def outsider(self) -> bool:
        """Someone not on the list of people: a guest or a volunteer."""
        return not self.person_id

    @property
    def guest_label(self) -> str:
        """How a guest or volunteer is shown: their name, else "Volunteer" or "Guest"."""
        return self.guest or GUEST_KINDS.get(self.guest_kind or "guest", "Guest")


GUEST_KINDS = {"guest": "Guest", "volunteer": "Volunteer"}
"""Kinds of people not on the list who can take a shift, named or not yet."""

EVENT_TASK_DAYS = {
    "all": "Every day",
    "first": "First day",
    "last": "Last day",
    "not_first": "All but the first day",
    "not_last": "All but the last day",
}
"""Which days of its event an event task is needed (``Task.event_days``)."""


@dataclass
class Event(Record):
    id: str
    name: str
    start: str
    end: str
    day_type: str = EVENT_DAY
    big: bool = False
    type_id: Optional[str] = None  # its EventType, if any
    tasks_on_main_rota: bool = True
    """Do this event's own tasks show on the main rota (screen and print)?
    Its own rota (the Event view and its printout) always shows them."""

    def covers(self, iso: str) -> bool:
        return self.start <= iso <= self.end

    def dates(self) -> List[str]:
        first, last = parse_date(self.start), parse_date(self.end)
        return [(first + timedelta(days=n)).isoformat() for n in range((last - first).days + 1)]

    def task_dates(self, which: str) -> List[str]:
        """The event's days an event task is needed on (see ``EVENT_TASK_DAYS``)."""
        days = self.dates()
        return {"first": days[:1], "last": days[-1:], "not_first": days[1:], "not_last": days[:-1]}.get(which, days)


@dataclass
class Marker(Record):
    """Something to show on the rota that is not a task.

    ``kind`` is ``"closed"`` (no rota for the areas concerned), ``"offrota"``
    (a special event outside the rota) or ``"note"``. An empty ``area_ids``
    means the whole centre.
    """

    id: str
    kind: str
    label: str
    start: str
    end: str
    area_ids: List[str] = field(default_factory=list)
    keep_task_ids: List[str] = field(default_factory=list)

    def covers(self, iso: str) -> bool:
        return self.start <= iso <= self.end


@dataclass
class TimeChange(Record):
    """A task at other times than usual on one day (a Christmas meal…)."""

    start: str = ""
    end: str = ""
    hours: float = 0.0
    label: str = ""


@dataclass
class PersonAvailability(Record):
    """One person's reply for the month.

    ``unavailable`` holds ``"<date>/<period>"`` keys.
    """

    unavailable: List[str] = field(default_factory=list)
    comment: str = ""
    responded: bool = True
    comment_done: bool = False


def availability_key(iso: str, period: str) -> str:
    return f"{iso}/{period}"


def slot_key(task_id: str, iso: str, position: int) -> str:
    """Text form of a slot, as used in JSON and by the interface."""
    return f"{task_id}|{iso}|{position}"


@dataclass
class Version(Record):
    """A state of the rota that was given to someone: who was on each shift then.

    ``audience`` says to whom: the manager, to check it (``"manager"``), or
    everyone, the rota being published and printed (``"residents"``).
    """

    number: int
    published: str  # ISO date
    places: Dict[str, str] = field(default_factory=dict)
    """Slot key -> name shown ("" for an open shift)."""
    audience: str = "residents"  # versions made before audiences existed were for everyone


@dataclass
class Month(Record):
    """Everything about one month's rota.

    Areas and tasks are copied from the catalog when the month is created, so
    later changes to the catalog never rewrite a month already prepared.
    """

    _nested: ClassVar[Dict[str, Any]] = {
        "areas": [Area],
        "tasks": [Task],
        "events": [Event],
        "markers": [Marker],
        "assignments": [Assignment],
        "availability": {"*": PersonAvailability},
        "time_changes": {"*": TimeChange},
        "versions": [Version],
    }

    id: str
    year: int
    month: int
    areas: List[Area] = field(default_factory=list)
    tasks: List[Task] = field(default_factory=list)
    day_types: Dict[str, str] = field(default_factory=dict)
    events: List[Event] = field(default_factory=list)
    markers: List[Marker] = field(default_factory=list)
    demand_overrides: Dict[str, Dict[str, int]] = field(default_factory=dict)
    assignments: List[Assignment] = field(default_factory=list)
    availability: Dict[str, PersonAvailability] = field(default_factory=dict)
    wanted: List[str] = field(default_factory=list)
    """Slot keys the coordinator highlighted: still open, to fill when possible.
    A key stays here once filled, so the highlight returns if the slot empties."""
    time_changes: Dict[str, TimeChange] = field(default_factory=dict)
    """Other times for a task on one day, keyed by ``"<task_id>|<date>"``."""
    versions: List[Version] = field(default_factory=list)
    """Versions handed out, oldest first."""
    mailed: Dict[str, int] = field(default_factory=dict)
    """Person id -> number of the version last e-mailed to them (0: a draft). No address is kept."""

    @staticmethod
    def make_id(year: int, month: int) -> str:
        return f"{year:04d}-{month:02d}"

    def dates(self) -> List[str]:
        return month_dates(self.year, self.month)

    def on_main_rota(self, task: Task) -> bool:
        """Is this task part of the main rota (the month grid and its printouts)?

        Usual tasks always are. A task of an event follows what its event
        says, unless the task has its own answer. The event's own rota shows
        them all.
        """
        if task.event_id is None:
            return True
        if task.on_month_rota is not None:
            return task.on_month_rota
        event = next((e for e in self.events if e.id == task.event_id), None)
        return event.tasks_on_main_rota if event is not None else True

    def title(self) -> str:
        return f"{calendar.month_name[self.month]} {self.year}"

    def day_type(self, iso: str) -> str:
        """Special type of a day: set by hand, else by the first event covering it."""
        chosen = self.day_types.get(iso)
        if chosen is not None:
            return chosen
        for event in self.events:
            if event.covers(iso):
                return event.day_type
        return NORMAL_DAY

    def find_assignment(self, task_id: str, iso: str, position: int) -> Optional[Assignment]:
        for assignment in self.assignments:
            if assignment.slot == (task_id, iso, position):
                return assignment
        return None
