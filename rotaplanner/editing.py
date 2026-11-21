"""Changing the permanent set-up: people, skills, areas, tasks, regular week.

Every function validates what it receives (it comes from a form) and raises
``ValueError`` with a message meant for the person using the program.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List, Optional, Union

from . import emails
from .model import (
    ANYTIME,
    DEFAULT_HEADCOUNT,
    EVENT_DAY,
    EVENT_TASK_DAYS,
    EVENT_TASKS_PRINTED,
    FREQUENCIES,
    PERIODS,
    PRINT_SIZES,
    PRIORITIES,
    TIMES_A_MONTH,
    WEEKDAY_KEYS,
    WEEKS_A_MONTH,
    Area,
    Catalog,
    ConfigTemplate,
    EventType,
    Month,
    NeverRule,
    Person,
    PrintTemplate,
    Regular,
    Settings,
    Skill,
    Task,
    new_id,
    parse_date,
    to_minutes,
)

Json = Dict[str, Any]
TaskConfig = Union[Catalog, Month, ConfigTemplate]
"""Anything holding ``areas`` and ``tasks``: a month, the typical month, a template."""
AREA_COLOURS = ("slate", "red", "orange", "amber", "lime", "green", "teal", "sky", "indigo", "violet", "pink")
MAX_TEXT = 200
GREETING_LENGTH, CLOSING_LENGTH = 120, 200  # the words around a schedule sent by message
SUBJECT_LENGTH, DEFAULT_SUBJECT = 120, "Your shifts for {month}"
MAX_HEADCOUNT = 30


def _text(data: Json, key: str, required: bool = False, label: str = "") -> str:
    value = " ".join(str(data.get(key) or "").split())[:MAX_TEXT]
    if required and not value:
        raise ValueError(f"{label or key.capitalize()} is required.")
    return value


def number(data: Json, key: str, low: float, high: float, label: str) -> Optional[float]:
    value = data.get(key)
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number.") from None
    if not low <= number <= high:
        raise ValueError(f"{label} must be between {low:g} and {high:g}.")
    return number


def whole(data: Json, key: str, low: int, high: int, label: str) -> Optional[int]:
    """A count (shifts, tasks): a whole number, never cut silently (1.5 is refused)."""
    value = number(data, key, low, high, label)
    if value is None:
        return None
    if not value.is_integer():
        raise ValueError(f"{label} must be a whole number.")
    return int(value)


OLDER_PAGE = "This page is older than the program: reload it, then save again."


def refuse_older_page(data: Json, old_key: str, new_key: str) -> None:
    """A page loaded before an update may still send a field by its old name
    (a target in hours a month): saving would silently empty the new one, so
    the save is refused with a word to reload the page."""
    if old_key in data and new_key not in data:
        raise ValueError(OLDER_PAGE)


def _date(value: Any, label: str) -> str:
    if not value:
        return ""
    try:
        return parse_date(str(value)).isoformat()
    except ValueError:
        raise ValueError(f"{label} is not a valid date.") from None


def rota_name(first: str, last: str, taken: Iterable[str]) -> str:
    """"First L.", lengthened ("First Le.") when that name is already taken."""
    first, last = " ".join(first.split()), " ".join(last.split())
    used = {name.casefold() for name in taken}
    for length in range(1, max(2, len(last) + 1)):
        name = f"{first} {last[:length]}." if last else first
        if name.casefold() not in used:
            return name
    number = 2
    while f"{first} {last} {number}".casefold() in used:
        number += 1
    return f"{first} {last} {number}"


# --------------------------------------------------------------------------
# People
# --------------------------------------------------------------------------


def save_person(people: List[Person], catalog: Catalog, data: Json, salt: bytes = b"") -> Person:
    """Create (no ``id``) or update a person from form data.

    ``email`` (optional) is turned into its fingerprint and dropped;
    ``forget_email`` removes the one known.
    """
    refuse_older_page(data, "target_hours", "target_hours_per_week")
    existing = next((p for p in people if p.id == data.get("id")), None) if data.get("id") else None
    others = [p for p in people if p is not existing]
    first = _text(data, "first_name")
    last = _text(data, "last_name")
    name = _text(data, "name") or (rota_name(first, last, (p.name for p in others)) if first else "")
    if not name:
        raise ValueError("Give at least a first name.")
    if any(p.name.casefold() == name.casefold() for p in others):
        raise ValueError(f"Someone is already called “{name}” on the rota. Add a letter of the last name.")

    skill_ids = {s.id for s in catalog.skills}
    area_ids = {a.id for a in catalog.areas}
    task_ids = {t.id for t in catalog.tasks}
    never = []
    for rule in data.get("never") or []:
        weekday, period = rule.get("weekday"), rule.get("period", "any")
        if weekday not in WEEKDAY_KEYS or period not in ("any",) + PERIODS:
            raise ValueError("A standing unavailability has an unknown day or period.")
        never.append(NeverRule(weekday, period))
    preferences = {}
    for area_id, value in (data.get("preferences") or {}).items():
        if area_id in area_ids and value not in (None, ""):
            rating = int(number({"v": value}, "v", 1, 5, "A preference"))
            preferences[area_id] = rating

    address = str(data.get("email") or "").strip()
    email_fp = ""
    if address:
        email_fp = emails.fingerprint(address, salt) if salt else ""
        if not email_fp:
            raise ValueError("This e-mail address does not look right.")
        holder = next((p for p in others if p.email_fp == email_fp), None)
        if holder is not None:
            raise ValueError(f"This e-mail address is already known for {holder.name}.")

    person = existing or Person(id=new_id("person"), name=name)
    if email_fp:
        person.email_fp = email_fp
    elif data.get("forget_email"):
        person.email_fp = ""
    person.name = name
    person.first_name, person.last_name = first, last
    person.lay_name = _text(data, "lay_name")
    person.phone = _text(data, "phone")
    person.resident = bool(data.get("resident", True))
    person.active = bool(data.get("active", True))
    person.archived = bool(data.get("archived", False))
    person.skills = [s for s in data.get("skills") or [] if s in skill_ids]
    person.skilled_tasks_only = bool(data.get("skilled_tasks_only", False))
    person.preferences = preferences
    person.target_hours_per_week = number(data, "target_hours_per_week", 0, 80, "Target hours a week")
    person.max_hours = number(data, "max_hours", 0, 300, "Monthly limit")
    person.max_hours_per_day = number(data, "max_hours_per_day", 0, 24, "Hours a day")
    person.max_tasks_per_day = whole(data, "max_tasks_per_day", 1, 20, "Tasks a day")
    person.max_shifts_per_week = whole(data, "max_shifts_per_week", 1, 50, "Shifts a week")
    person.never = never
    person.avoid_task_ids = [t for t in data.get("avoid_task_ids") or [] if t in task_ids]
    person.note = str(data.get("note") or "")[:1000].strip()
    if existing is None:
        people.append(person)
    return person


def set_person_skill(people: List[Person], catalog: Catalog, person_id: str, skill_id: str, on: bool) -> Person:
    person = next((p for p in people if p.id == person_id), None)
    if person is None or skill_id not in {s.id for s in catalog.skills}:
        raise ValueError("Unknown person or skill.")
    if on and skill_id not in person.skills:
        person.skills.append(skill_id)
    elif not on and skill_id in person.skills:
        person.skills.remove(skill_id)
    return person


# --------------------------------------------------------------------------
# Skills and areas
# --------------------------------------------------------------------------


def save_skill(catalog: Catalog, data: Json) -> Skill:
    name = _text(data, "name", required=True, label="Skill name")
    existing = next((s for s in catalog.skills if s.id == data.get("id")), None)
    if any(s.name.casefold() == name.casefold() and s is not existing for s in catalog.skills):
        raise ValueError(f"There is already a skill called “{name}”.")
    if existing:
        existing.name = name
        return existing
    skill = Skill(id=new_id("skill"), name=name)
    catalog.skills.append(skill)
    return skill


def delete_skill(catalog: Catalog, people: List[Person], skill_id: str, elsewhere: Iterable[Any] = ()) -> None:
    """Remove a skill. ``elsewhere``: other configurations (months, templates) whose tasks may need it."""
    configs = [catalog, *elsewhere]
    users = sorted({t.name for c in configs for t in c.tasks if t.skill_id == skill_id and not t.archived}
                   | {t.name for et in catalog.event_types for t in et.tasks if t.skill_id == skill_id})
    if users:
        raise ValueError(f"This skill is needed for {', '.join(users[:3])}{' and more' if len(users) > 3 else ''}. "
                         "Change those tasks first (in every month that has them).")
    catalog.skills = [s for s in catalog.skills if s.id != skill_id]
    for person in people:
        if skill_id in person.skills:
            person.skills.remove(skill_id)


def save_area(config: TaskConfig, data: Json) -> Area:
    """Create or change an area of a configuration (a month, or the typical month)."""
    name = _text(data, "name", required=True, label="Area name")
    colour = data.get("color") or AREA_COLOURS[len(config.areas) % len(AREA_COLOURS)]
    if colour not in AREA_COLOURS:
        raise ValueError("Unknown colour.")
    existing = next((a for a in config.areas if a.id == data.get("id")), None)
    if any(a.name.casefold() == name.casefold() and a is not existing and not a.archived for a in config.areas):
        raise ValueError(f"There is already an area called “{name}”.")
    area = existing or Area(id=new_id("area"), name=name)
    area.name, area.manager, area.color = name, _text(data, "manager"), colour
    area.archived = bool(data.get("archived", False))
    if existing is None:
        config.areas.append(area)
    return area


def reorder(items: list, ids: List[str]) -> None:
    """Put ``items`` in the order of ``ids``; items not listed keep their place at the end."""
    rank = {item_id: index for index, item_id in enumerate(ids)}
    items.sort(key=lambda item: rank.get(item.id, len(rank)))


# --------------------------------------------------------------------------
# Tasks
# --------------------------------------------------------------------------


def clock(value: Any, label: str) -> str:
    """A time typed in a form ("9.30", "09:30") as "09:30"; empty stays empty."""
    text = str(value or "").strip().replace(".", ":")
    if not text:
        return ""
    if len(text) == 4 and text[1] == ":":
        text = "0" + text
    minutes = to_minutes(text)
    if minutes is None or minutes >= 24 * 60 or len(text) != 5:
        raise ValueError(f"{label} must look like 09:30.")
    return text


def duration_hours(start: str, end: str) -> Optional[float]:
    """Length of a start–end range in hours, running past midnight if needed."""
    a, b = to_minutes(start), to_minutes(end)
    if a is None or b is None:
        return None
    if b <= a:
        b += 24 * 60
    return round((b - a) / 60, 2)


def parse_periods(values: Any) -> List[str]:
    """Parts of the day a task covers, in day order; "any time" stands alone."""
    chosen = [v for v in values if v in PERIODS + (ANYTIME,)] if isinstance(values, list) else []
    if not chosen:
        raise ValueError("Choose the part(s) of the day this task covers.")
    if ANYTIME in chosen:
        return [ANYTIME]
    return [p for p in PERIODS if p in chosen]


AFTERNOON_FROM, EVENING_FROM = 14 * 60, 17 * 60
"""The centre's parts of the day: morning before 14:00, evening from 17:00."""
PART_COUNTS_FROM = 30  # minutes inside a part of the day before it counts (14:00–17:15 is not an evening task)


def periods_for_times(start: str, end: str) -> List[str]:
    """Parts of the day a shift from ``start`` to ``end`` covers; no times: any time.

    A part counts when the shift spends at least half an hour in it; a shift
    that does so nowhere (a quarter of an hour, or a few minutes on each side
    of 14:00) belongs to the part that holds most of it. What runs past
    midnight is evening.
    """
    a, b = to_minutes(start or ""), to_minutes(end or "")
    if a is None or b is None:
        return [ANYTIME]
    if b <= a:
        b += 24 * 60
    bounds = ((0, AFTERNOON_FROM), (AFTERNOON_FROM, EVENING_FROM), (EVENING_FROM, 48 * 60))
    inside = [min(b, upto) - max(a, since) for since, upto in bounds]
    covered = [p for p, minutes in zip(PERIODS, inside) if minutes >= PART_COUNTS_FROM]
    return covered or [PERIODS[inside.index(max(inside))]]


def save_task(config: TaskConfig, catalog: Catalog, data: Json) -> Task:
    """Create or change a task of a configuration; skills come from the catalog."""
    name = _text(data, "name", required=True, label="Task name")
    area_ids = {a.id for a in config.areas}
    if data.get("area_id") not in area_ids:
        raise ValueError("Choose the area of this task.")
    skill_id = data.get("skill_id") or None
    if skill_id and skill_id not in {s.id for s in catalog.skills}:
        raise ValueError("Unknown skill.")
    existing = next((t for t in config.tasks if t.id == data.get("id")), None)
    if "periods" in data:
        periods = parse_periods(data["periods"])
    else:  # not part of the form that sent it: keep what the task had
        periods = existing.periods if existing else ["morning"]
    priority = data.get("priority", "normal")
    if priority not in PRIORITIES:
        raise ValueError("Unknown priority.")
    start, end = clock(data.get("start"), "Start time"), clock(data.get("end"), "End time")
    if bool(start) != bool(end):
        raise ValueError("Give both a start and an end time, or neither.")
    hours = number(data, "hours", 0, 24, "Hours")
    if hours is None:
        hours = duration_hours(start, end) or 0.0

    headcount = {}
    allowed_keys = set(WEEKDAY_KEYS) | {DEFAULT_HEADCOUNT, EVENT_DAY} | {d.id for d in catalog.day_types}
    for key, value in (data.get("headcount") or {}).items():
        if key not in allowed_keys:
            raise ValueError(f"Unknown kind of day: {key}.")
        count = number({"v": value}, "v", 0, MAX_HEADCOUNT, "Number of people")
        if count:
            headcount[key] = int(count)

    task = existing or Task(id=new_id("task"), area_id=data["area_id"], name=name)
    task.area_id, task.name, task.skill_id, task.periods = data["area_id"], name, skill_id, periods
    task.start, task.end, task.hours = start, end, round(hours, 2)
    task.time_label = _text(data, "time_label")
    task.headcount, task.priority = headcount, priority
    task.signup = bool(data.get("signup", False))
    task.archived = bool(data.get("archived", False))
    if existing is None:
        config.tasks.append(task)
    return task


def save_event_task(config: TaskConfig, catalog: Catalog, data: Json) -> Task:
    """A task of an event or of an event type: the fields of any task, with one
    number of people, which days of the event, and (only as an exception to the
    event's choice) whether it shows on the main rota."""
    which = data.get("event_days") or "all"
    if which not in EVENT_TASK_DAYS:
        raise ValueError("Choose which days of the event.")
    people = number(data, "people", 1, MAX_HEADCOUNT, "Number of people") or 1
    task = save_task(config, catalog, {**data, "headcount": {"default": int(people)}})
    if not data.get("periods"):  # worked out from the times
        task.periods = periods_for_times(task.start, task.end)
    task.event_days = which
    own = data.get("on_month_rota")
    task.on_month_rota = None if own is None else bool(own)
    return task


# --------------------------------------------------------------------------
# Event types: kinds of event (Retreat, Festival…) and the tasks they need
# --------------------------------------------------------------------------


class _TypeTasks:
    """An event type seen as a configuration: the catalog's areas, the type's tasks."""

    def __init__(self, catalog: Catalog, event_type: EventType) -> None:
        self.areas, self.tasks = catalog.areas, event_type.tasks


def _event_type(catalog: Catalog, type_id: Any) -> EventType:
    found = next((t for t in catalog.event_types if t.id == type_id), None)
    if found is None:
        raise ValueError("Unknown type of event.")
    return found


def save_event_type(catalog: Catalog, data: Json) -> EventType:
    name = _text(data, "name", required=True, label="Name of the type")
    existing = next((t for t in catalog.event_types if t.id == data.get("id")), None)
    if any(t.name.lower() == name.lower() and t is not existing for t in catalog.event_types):
        raise ValueError(f"There is already a type called {name}.")
    event_type = existing or EventType(id=new_id("type"), name=name)
    event_type.name, event_type.big = name, bool(data.get("big", False))
    event_type.tasks_on_main_rota = bool(data.get("tasks_on_main_rota", not event_type.big))
    if existing is None:
        catalog.event_types.append(event_type)
    catalog.event_types.sort(key=lambda t: t.name.lower())
    return event_type


def delete_event_type(catalog: Catalog, type_id: str) -> bool:
    before = len(catalog.event_types)
    catalog.event_types = [t for t in catalog.event_types if t.id != type_id]
    return len(catalog.event_types) != before


def save_type_task(catalog: Catalog, data: Json) -> Task:
    event_type = _event_type(catalog, data.get("type_id"))
    return save_event_task(_TypeTasks(catalog, event_type), catalog, data)


def delete_type_task(catalog: Catalog, type_id: str, task_id: str) -> bool:
    event_type = _event_type(catalog, type_id)
    before = len(event_type.tasks)
    event_type.tasks = [t for t in event_type.tasks if t.id != task_id]
    return len(event_type.tasks) != before


def order_type_tasks(catalog: Catalog, type_id: str, ids: List[str]) -> None:
    reorder(_event_type(catalog, type_id).tasks, ids)


def duplicate_task(config: TaskConfig, task_id: str) -> Task:
    original = next((t for t in config.tasks if t.id == task_id), None)
    if original is None:
        raise ValueError("Unknown task.")
    clone = copy.deepcopy(original)
    clone.id, clone.name = new_id("task"), f"{original.name} (copy)"
    config.tasks.insert(config.tasks.index(original) + 1, clone)
    return clone


def use_configuration(month: Month, source: TaskConfig) -> int:
    """Give a month the areas and tasks of another configuration.

    Tasks keep their identity across months, so people placed on a task that
    exists in both stay where they are. Placements, highlights and one-day
    numbers for tasks that are not in the new configuration are removed;
    their count is returned so the user can be told.
    """
    own_event_tasks = [t for t in month.tasks if t.event_id]  # they belong to the month's events
    needed = {t.area_id for t in own_event_tasks}
    month.areas = copy.deepcopy(source.areas) + [
        a for a in month.areas if a.id in needed and a.id not in {x.id for x in source.areas}]
    month.tasks = [copy.deepcopy(t) for t in source.tasks if not t.event_id] + own_event_tasks
    kept = {t.id for t in month.tasks}
    month.time_changes = {key: c for key, c in month.time_changes.items() if key.split("|")[0] in kept}
    before = len(month.assignments)
    month.assignments = [a for a in month.assignments if a.task_id in kept]
    month.wanted = [key for key in month.wanted if key.split("|")[0] in kept]
    month.demand_overrides = {t: days for t, days in month.demand_overrides.items() if t in kept}
    return before - len(month.assignments)


# --------------------------------------------------------------------------
# Regular week
# --------------------------------------------------------------------------


def set_regular(catalog: Catalog, people: List[Person], data: Json) -> Optional[Regular]:
    """Set who regularly does a task on a weekday (no ``person_id``: nobody)."""
    task_id, weekday = data.get("task_id"), data.get("weekday")
    if task_id not in {t.id for t in catalog.tasks} or weekday not in WEEKDAY_KEYS:
        raise ValueError("Unknown task or day.")
    position = int(number(data, "position", 0, MAX_HEADCOUNT, "Position") or 0)
    frequency = data.get("frequency") or "weekly"
    if frequency not in FREQUENCIES:
        raise ValueError("Unknown frequency.")
    start, end = _date(data.get("start"), "Start date"), _date(data.get("end"), "End date")
    if start and end and end < start:
        raise ValueError("The end date is before the start date.")
    person_id = data.get("person_id") or ""
    if person_id and person_id not in {p.id for p in people}:
        raise ValueError("Unknown person.")

    existing = next((r for r in catalog.regulars
                     if (r.task_id, r.weekday, r.position) == (task_id, weekday, position)), None)
    if not person_id:
        if existing:
            catalog.regulars.remove(existing)
        return None
    regular = existing or Regular(id=new_id("regular"), person_id=person_id, task_id=task_id,
                                  weekday=weekday, position=position)
    regular.person_id, regular.frequency, regular.start, regular.end = person_id, frequency, start, end
    if existing is None:
        catalog.regulars.append(regular)
    return regular


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


def save_print_template(settings: Settings, catalog: Catalog, data: Json) -> PrintTemplate:
    """Add or change a way of printing the main rota."""
    name = _text(data, "name", required=True, label="Name of the template")
    size, output, event_tasks = data.get("size", "a3"), data.get("output", "a3_sheets"), data.get("event_tasks", "events")
    if size not in PRINT_SIZES or output not in ("a3_sheets", "one_page") or event_tasks not in EVENT_TASKS_PRINTED:
        raise ValueError("Choose the size and options of the printout.")
    known = {a.id for a in catalog.areas}
    areas = [a for a in data.get("area_ids") or [] if a in known]
    template = next((t for t in settings.print_templates if t.id == data.get("id")), None)
    if template is None:
        template = PrintTemplate(id=new_id("print"), name=name)
        settings.print_templates.append(template)
    template.name, template.size, template.output, template.event_tasks = name, size, output, event_tasks
    template.area_ids = [] if len(areas) == len(known) else areas  # every area: say none, so new areas are in
    template.show_times = bool(data.get("show_times", True))
    template.tasks_at_end = bool(data.get("tasks_at_end", False))
    template.repeat_days = bool(data.get("repeat_days", False))
    return template


def delete_print_template(settings: Settings, template_id: str) -> bool:
    before = len(settings.print_templates)
    settings.print_templates = [t for t in settings.print_templates if t.id != template_id]
    return len(settings.print_templates) != before


def duplicate_print_template(settings: Settings, template_id: str) -> PrintTemplate:
    original = next((t for t in settings.print_templates if t.id == template_id), None)
    if original is None:
        raise ValueError("Unknown template.")
    copy_ = copy.deepcopy(original)
    copy_.id, copy_.name = new_id("print"), f"{original.name} (copy)"
    settings.print_templates.insert(settings.print_templates.index(original) + 1, copy_)
    return copy_


def regular_load(catalog: Catalog) -> List[Json]:
    """About how many hours a week the regular week gives each person, before
    anything else is added: [{"person_id", "shifts", "hours"}], the most hours
    first. A shift every other week or once a month counts for its share: the
    hours of an average month, divided by its weeks (``WEEKS_A_MONTH``)."""
    tasks = {t.id: t for t in catalog.tasks}
    load: Dict[str, Json] = {}
    for regular in catalog.regulars:
        task = tasks.get(regular.task_id)
        if task is None:
            continue
        entry = load.setdefault(regular.person_id, {"person_id": regular.person_id, "shifts": 0, "hours": 0.0})
        entry["shifts"] += 1
        entry["hours"] += task.hours * TIMES_A_MONTH.get(regular.frequency, TIMES_A_MONTH["weekly"])
    ordered = sorted(load.values(), key=lambda entry: -entry["hours"])
    return [dict(entry, hours=round(entry["hours"] / WEEKS_A_MONTH, 1)) for entry in ordered]


def message_words(text: Any, limit: int) -> str:
    """The greeting or the closing of the schedule message, as it is kept."""
    return " ".join(str(text or "").split())[:limit]


def message_subject(text: Any) -> str:
    """The subject of a schedule sent by e-mail, as it is kept (never empty)."""
    return message_words(text, SUBJECT_LENGTH) or DEFAULT_SUBJECT


def save_settings(settings: Settings, data: Json) -> Settings:
    refuse_older_page(data, "default_target_hours", "default_target_hours_per_week")
    if "centre_name" in data:  # each part of Settings saves only its own fields
        settings.centre_name = _text(data, "centre_name")
    target = number(data, "default_target_hours_per_week", 0, 80, "Usual target hours a week")
    daily = number(data, "default_max_hours_per_day", 0, 24, "Usual hours a day")
    if target is not None:
        settings.default_target_hours_per_week = target
    if daily is not None:
        settings.default_max_hours_per_day = daily
    settings.prorate_target = bool(data.get("prorate_target", settings.prorate_target))
    if "message_greeting" in data:
        settings.message_greeting = message_words(data["message_greeting"], GREETING_LENGTH)
    if "message_subject" in data:
        settings.message_subject = message_subject(data["message_subject"])
    if "message_closing" in data:
        settings.message_closing = message_words(data["message_closing"], CLOSING_LENGTH)
    return settings
