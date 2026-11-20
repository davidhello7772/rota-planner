"""How many people each task needs on each day of a month.

The answer is built in layers, later layers winning:

1. the task's own headcount for that kind of day,
2. closures (a closed centre or area needs nobody),
3. numbers the coordinator typed for one particular day.
"""

from __future__ import annotations

from typing import Dict, Iterable, Optional, Tuple

from .model import DEFAULT_HEADCOUNT, NORMAL_DAY, Event, Marker, Month, Task, weekday_key

SlotDay = Tuple[str, str]
"""A task on a day: ``(task_id, iso_date)``."""


def rule_headcount(task: Task, iso: str, day_type: str, events: Iterable[Event] = ()) -> int:
    """People the task asks for on this day, before closures and overrides.

    A special day (an event…) asks for *at least* its own number: a cafe
    needing 7 people every Sunday still needs 7 on an event Sunday. A task
    of one event is needed only on the days of that event it was set for.
    """
    if task.archived or (task.only_dates and iso not in task.only_dates):
        return 0
    if task.event_id is not None:
        event = next((e for e in events if e.id == task.event_id), None)
        if event is None or iso not in event.task_dates(task.event_days):
            return 0
    counts = task.headcount
    usual = counts.get(weekday_key(iso), counts.get(DEFAULT_HEADCOUNT, 0))
    if day_type != NORMAL_DAY:
        return max(usual, counts.get(day_type, 0))
    return usual


def closure(month: Month, task: Task, iso: str) -> Optional[Marker]:
    """The closure that cancels this task on this day, if any."""
    for marker in month.markers:
        if marker.kind != "closed" or not marker.covers(iso):
            continue
        if task.id in marker.keep_task_ids:
            continue
        if not marker.area_ids or task.area_id in marker.area_ids:
            return marker
    return None


def required(month: Month) -> Dict[SlotDay, int]:
    """Headcount for every task and day that needs at least one person."""
    needs: Dict[SlotDay, int] = {}
    archived_areas = {a.id for a in month.areas if a.archived}
    for task in month.tasks:
        if task.area_id in archived_areas:
            continue
        overrides = month.demand_overrides.get(task.id, {})
        for iso in month.dates():
            if iso in overrides:
                count = overrides[iso]
            elif closure(month, task, iso) is not None:
                count = 0
            else:
                count = rule_headcount(task, iso, month.day_type(iso), month.events)
            if count > 0:
                needs[(task.id, iso)] = count
    return needs
