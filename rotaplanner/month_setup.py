"""Preparing a month: creating it, its events, closures and notes, and how it
compares with the hours people can give.

The common case must be quick: next month is like this month. ``new_month``
copies the previous month's areas and tasks and applies the regular week, so
one click gives a month ready for availability and fine-tuning.
"""

from __future__ import annotations

import copy
import math
from collections import defaultdict
from datetime import timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from . import demand, editing
from .editing import TaskConfig
from .model import (
    EVENT_DAY,
    NORMAL_DAY,
    Catalog,
    Event,
    EventType,
    Marker,
    Month,
    Person,
    Settings,
    Task,
    new_id,
    parse_date,
)
from .months import apply_regulars
from .schedule import Schedule

Json = Dict[str, Any]
MARKER_KINDS = {"closed": "Closed", "offrota": "Off-rota event", "note": "Note"}
NEARLY_ALL = 0.9  # needing more than this share of what people can give is worth a warning
DAY_TYPES = (NORMAL_DAY, EVENT_DAY)


def next_month_after(year: int, month: int) -> tuple:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def new_month(
    catalog: Catalog,
    source: TaskConfig,
    year: int,
    month_number: int,
    people: Iterable[Person],
    settings: Settings,
) -> Month:
    """A new month with the areas and tasks of ``source``, regular week applied.

    Tasks that only existed on particular days of the source month, or for
    one of its events, are left behind: they belonged to that month.
    """
    month = Month(
        id=Month.make_id(year, month_number),
        year=year,
        month=month_number,
        areas=copy.deepcopy(source.areas),
        tasks=[copy.deepcopy(t) for t in source.tasks if not t.only_dates and t.event_id is None],
    )
    apply_regulars(month, catalog, people, settings)
    return month


# --------------------------------------------------------------------------
# Events, closures and notes
# --------------------------------------------------------------------------


MAX_SPAN_DAYS = 62
"""Events and closures may run into the next month, but not on and on."""


def _dates(month: Month, data: Json) -> tuple:
    try:
        start = parse_date(str(data.get("start"))).isoformat()
        end = parse_date(str(data.get("end") or data.get("start"))).isoformat()
    except ValueError:
        raise ValueError("Choose the first and last day.") from None
    if end < start:
        raise ValueError("The last day is before the first day.")
    if (parse_date(end) - parse_date(start)).days > MAX_SPAN_DAYS:
        raise ValueError("An event or a closure can last two months at most.")
    dates = month.dates()
    if end < dates[0] or start > dates[-1]:
        raise ValueError(f"These days are not in {month.title()}.")
    return start, end


def _label(data: Json, key: str, what: str) -> str:
    text = " ".join(str(data.get(key) or "").split())[:120]
    if not text:
        raise ValueError(f"Give the {what} a name.")
    return text


def save_event(month: Month, data: Json) -> Event:
    """Add or change an event. Its days count as event days (more people needed)."""
    name = _label(data, "name", "event")
    start, end = _dates(month, data)
    event = next((e for e in month.events if e.id == data.get("id")), None)
    created = event is None
    if event is None:
        event = Event(id=new_id("event"), name=name, start=start, end=end)
        month.events.append(event)
    event.name, event.start, event.end = name, start, end
    event.big = bool(data.get("big", False))
    if "type_id" in data:
        event.type_id = str(data["type_id"]) if data["type_id"] else None
    if "tasks_on_main_rota" in data:
        event.tasks_on_main_rota = bool(data["tasks_on_main_rota"])
    elif created:
        event.tasks_on_main_rota = not event.big
    month.events.sort(key=lambda e: (e.start, e.end))
    return event


def delete_event(month: Month, event_id: str) -> bool:
    """Remove an event, with its own tasks and whoever was placed on them."""
    before = len(month.events)
    month.events = [e for e in month.events if e.id != event_id]
    drop_tasks(month, {t.id for t in event_tasks(month, event_id)})
    return len(month.events) != before


# --------------------------------------------------------------------------
# Tasks of one event (hot chocolate on the first day of a retreat, a
# festival's registration desk…). They are needed only on the event's days,
# and can stay off the printed monthly rota. An event type (Retreat,
# Festival…) keeps the usual ones, so an event of that type gets them at once.
# --------------------------------------------------------------------------


def event_tasks(month: Month, event_id: str) -> List[Task]:
    return [t for t in month.tasks if t.event_id == event_id]


def drop_tasks(month: Month, task_ids: Set[str]) -> None:
    """Remove tasks from a month, with everything that refers to them."""
    if not task_ids:
        return
    month.tasks = [t for t in month.tasks if t.id not in task_ids]
    month.assignments = [a for a in month.assignments if a.task_id not in task_ids]
    month.wanted = [key for key in month.wanted if key.split("|")[0] not in task_ids]
    month.demand_overrides = {t: days for t, days in month.demand_overrides.items() if t not in task_ids}
    month.time_changes = {key: c for key, c in month.time_changes.items() if key.split("|")[0] not in task_ids}


def _event(month: Month, event_id: Any) -> Event:
    event = next((e for e in month.events if e.id == event_id), None)
    if event is None:
        raise ValueError("Unknown event.")
    return event


def save_event_task(month: Month, catalog: Catalog, data: Json) -> Task:
    """Add or change a task of one event (see ``editing.save_event_task``)."""
    event = _event(month, data.get("event_id"))
    existing = next((t for t in month.tasks if t.id == data.get("id")), None)
    if existing is not None and existing.event_id != event.id:
        raise ValueError("Unknown task.")
    task = editing.save_event_task(month, catalog, data)
    task.event_id = event.id
    return task


def delete_event_task(month: Month, task_id: str) -> bool:
    task = next((t for t in month.tasks if t.id == task_id and t.event_id), None)
    if task is None:
        return False
    drop_tasks(month, {task.id})
    return True


def add_type_tasks(month: Month, event_id: str, event_type: EventType) -> int:
    """Give an event the tasks of an event type (those it does not have yet). Returns how many were added."""
    event = _event(month, event_id)
    have = {t.name.lower() for t in event_tasks(month, event.id)}
    areas = {a.id for a in month.areas}
    added = 0
    for model in event_type.tasks:
        if model.name.lower() in have or model.area_id not in areas:
            continue
        task = copy.deepcopy(model)
        task.id, task.event_id = new_id("task"), event.id
        month.tasks.append(task)
        added += 1
    return added


def type_from_event(month: Month, event_id: str, name: str, type_id: Optional[str] = None) -> EventType:
    """An event type whose tasks are those of this event."""
    name = " ".join(str(name or "").split())[:80]
    if not name:
        raise ValueError("Give the type of event a name.")
    event = _event(month, event_id)
    tasks = []
    for original in event_tasks(month, event.id):
        task = copy.deepcopy(original)
        task.id, task.event_id = new_id("task"), None
        tasks.append(task)
    if not tasks:
        raise ValueError("This event has no task of its own yet.")
    return EventType(id=type_id or new_id("type"), name=name, big=event.big,
                     tasks_on_main_rota=event.tasks_on_main_rota, tasks=tasks)


def save_marker(month: Month, data: Json) -> Marker:
    """Add or change a closure, an off-rota event or a note."""
    kind = data.get("kind")
    if kind not in MARKER_KINDS:
        raise ValueError("Choose what kind of day this is.")
    label = _label(data, "label", MARKER_KINDS[kind].lower())
    start, end = _dates(month, data)
    area_ids = {a.id for a in month.areas}
    task_ids = {t.id for t in month.tasks}
    marker = next((m for m in month.markers if m.id == data.get("id")), None)
    if marker is None:
        marker = Marker(id=new_id("marker"), kind=kind, label=label, start=start, end=end)
        month.markers.append(marker)
    marker.kind, marker.label, marker.start, marker.end = kind, label, start, end
    # Ids this month does not know may belong to the other month of a linked
    # closure: keep those the marker already had, so they are not lost.
    area_ids |= set(marker.area_ids)
    task_ids |= set(marker.keep_task_ids)
    marker.area_ids = [a for a in data.get("area_ids") or [] if a in area_ids]
    marker.keep_task_ids = [t for t in data.get("keep_task_ids") or [] if t in task_ids] if kind == "closed" else []
    month.markers.sort(key=lambda m: (m.start, m.end))
    if kind == "closed":
        drop_unneeded_regulars(month)
    return marker


def drop_unneeded_regulars(month: Month) -> int:
    """Take off regular-week placements on shifts no longer needed (a closed day…).

    Placements made by hand stay, shown as "not required", so nothing the
    coordinator decided disappears silently.
    """
    needed = demand.required(month)
    unneeded = [a for a in month.assignments
                if a.source == "regular" and not a.locked and a.position >= needed.get((a.task_id, a.date), 0)]
    for assignment in unneeded:
        month.assignments.remove(assignment)
    return len(unneeded)


def delete_marker(month: Month, marker_id: str) -> bool:
    before = len(month.markers)
    month.markers = [m for m in month.markers if m.id != marker_id]
    return len(month.markers) != before


def set_day_type(month: Month, iso: str, day_type: Optional[str]) -> bool:
    """Treat one day as an event day or a normal day; ``None``: decided by the events."""
    if iso not in month.dates():
        raise ValueError("That date is not in this month.")
    if day_type is None:
        return month.day_types.pop(iso, None) is not None
    if day_type not in DAY_TYPES:
        raise ValueError("Unknown kind of day.")
    if month.day_types.get(iso) == day_type:
        return False
    month.day_types[iso] = day_type
    return True


# --------------------------------------------------------------------------
# Events and closures running over two months
#
# Such an event is one event: each month it touches keeps a copy under the
# same id, so that its days count as event days there. ``sync_item`` keeps the
# copies in line after a change in one of the months.
# --------------------------------------------------------------------------

LINKED_KINDS = ("events", "markers")


def months_spanned(start: str, end: str) -> List[str]:
    """Ids of the months from ``start``'s to ``end``'s, in order."""
    year, month_number = parse_date(start).year, parse_date(start).month
    last = (parse_date(end).year, parse_date(end).month)
    found = []
    while (year, month_number) <= last:
        found.append(Month.make_id(year, month_number))
        year, month_number = next_month_after(year, month_number)
    return found


def overlaps(item: Any, month: Month) -> bool:
    dates = month.dates()
    return item.start <= dates[-1] and item.end >= dates[0]


def sync_item(month: Month, kind: str, item_id: str, item: Optional[Any], tasks: Sequence[Task] = (),
              areas: Sequence[Any] = ()) -> bool:
    """Make ``month``'s copy of an event or closure match ``item`` (None: deleted).

    The month keeps a copy only if the item reaches into it; for an event,
    with copies of its own ``tasks`` (and of the ``areas`` they need that this
    month does not have). Returns whether anything changed.
    """
    items = getattr(month, kind)
    index = next((i for i, x in enumerate(items) if x.id == item_id), None)
    if item is None or not overlaps(item, month):
        own = {t.id for t in event_tasks(month, item_id)} if kind == "events" else set()
        drop_tasks(month, own)
        if index is not None:
            del items[index]
        return index is not None or bool(own)
    fresh = copy.deepcopy(item)
    changed = True
    if index is None:
        items.append(fresh)
    elif items[index].to_dict() == fresh.to_dict():
        changed = False
    else:
        items[index] = fresh
    items.sort(key=lambda x: (x.start, x.end))
    if kind == "events":
        changed |= _sync_tasks(month, item_id, tasks, areas)
    if changed and kind == "markers" and fresh.kind == "closed":
        drop_unneeded_regulars(month)
    return changed


def _sync_tasks(month: Month, event_id: str, tasks: Sequence[Task], areas: Sequence[Any] = ()) -> bool:
    have = {a.id for a in month.areas}
    for area in areas:  # an area only the other month has: the event's tasks need it here too
        if area.id not in have and any(t.area_id == area.id for t in tasks):
            month.areas.append(copy.deepcopy(area))
            have.add(area.id)
    wanted = [copy.deepcopy(t) for t in tasks if t.area_id in have]
    current = event_tasks(month, event_id)
    if [t.to_dict() for t in current] == [t.to_dict() for t in wanted]:
        return False
    drop_tasks(month, {t.id for t in current} - {t.id for t in wanted})
    index = {t.id: i for i, t in enumerate(month.tasks)}
    for task in wanted:
        if task.id in index:
            month.tasks[index[task.id]] = task
        else:
            month.tasks.append(task)
    return True


def take_linked_items(month: Month, neighbours: Iterable[Month]) -> bool:
    """Give a new month the events (with their tasks) and closures of nearby months that reach into it."""
    changed = False
    for other in neighbours:
        for kind in LINKED_KINDS:
            for item in getattr(other, kind):
                if overlaps(item, month):
                    tasks = event_tasks(other, item.id) if kind == "events" else ()
                    changed |= sync_item(month, kind, item.id, item, tasks, other.areas)
    return changed


# --------------------------------------------------------------------------
# How the month looks: work to do against hours people can give
# --------------------------------------------------------------------------


def overview(month: Month, people: List[Person], settings: Settings) -> Json:
    """Per day and per week: shifts and hours needed; and the hours people can give."""
    schedule = Schedule(month, people, settings)
    per_day: Dict[str, Json] = {iso: {"shifts": 0, "hours": 0.0, "filled": 0} for iso in schedule.dates}
    for (task_id, iso), count in schedule.required.items():
        hours = schedule.task_on(task_id, iso).hours
        filled = sum(1 for position in schedule.cell(task_id, iso) if position < count)
        per_day[iso]["shifts"] += count
        per_day[iso]["hours"] += hours * count
        per_day[iso]["filled"] += filled

    weeks: Dict[str, Json] = defaultdict(lambda: {"shifts": 0, "hours": 0.0, "days": []})
    for iso in schedule.dates:
        day = parse_date(iso)
        monday = (day - timedelta(days=day.weekday())).isoformat()
        week = weeks[monday]
        week["shifts"] += per_day[iso]["shifts"]
        week["hours"] += per_day[iso]["hours"]
        week["days"].append(iso)

    active = [p for p in people if p.active and not p.archived]
    available = sum(schedule.target(p.id) for p in active)
    needed = sum(d["hours"] for d in per_day.values())
    short = needed - available
    return {
        # Can the people give the hours the month needs? "bad": not enough; "warn": only just.
        "balance": "bad" if short > 0 else "warn" if needed > NEARLY_ALL * available else "ok",
        "hours_short": math.ceil(short) if short > 0 else 0,
        "days": {iso: {**d, "hours": round(d["hours"], 2)} for iso, d in per_day.items()},
        "weeks": [{"start": start, **{**w, "hours": round(w["hours"], 1)}} for start, w in sorted(weeks.items())],
        "hours_needed": round(needed, 1),
        "hours_available": round(available, 1),
        "people": len(active),
        "replied": sum(1 for p in active if schedule.has_replied(p.id)),
    }
