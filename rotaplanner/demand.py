"""How many people each task needs on each day of a month.

The answer is built in layers, later layers winning:

1. the task's own headcount for that kind of day,
2. closures (a closed centre or area needs nobody),
3. numbers the coordinator typed for one particular day.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from .model import DEFAULT_HEADCOUNT, EVENT_DAY, NORMAL_DAY, WEEKDAY_KEYS, Event, Marker, Month, Task, weekday_key

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


# ---- How many people a task needs, said in words (the rota shows it) -------

WEEKDAY_PLURALS = ("Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays")
"""In the order of ``WEEKDAY_KEYS``: written out here, never taken from the computer's language."""


def people_text(count: int) -> str:
    """ "1 person", "2 people"."""
    return f"{count} person" if count == 1 else f"{count} people"


def _and(parts: List[str]) -> str:
    """ "a", "a and b", "a, b and c"."""
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"


def _weekdays_text(days: List[int]) -> str:
    """Weekday numbers (0 = Monday) as words: three days or more in a row read "Mondays to Fridays"."""
    runs: List[List[int]] = []
    for day in sorted(days):
        if runs and runs[-1][-1] == day - 1:
            runs[-1].append(day)
        else:
            runs.append([day])
    parts: List[str] = []
    for run in runs:
        if len(run) >= 3:
            parts.append(f"{WEEKDAY_PLURALS[run[0]]} to {WEEKDAY_PLURALS[run[-1]]}")
        else:
            parts.extend(WEEKDAY_PLURALS[day] for day in run)
    return _and(parts)


def _weekday_counts(task: Task) -> List[int]:
    """People the task asks for on each weekday of an ordinary week, Monday first."""
    counts = task.headcount
    return [counts.get(key, counts.get(DEFAULT_HEADCOUNT, 0)) for key in WEEKDAY_KEYS]


def _range_text(values: Iterable[int]) -> str:
    """ "2", or "1–2" when the numbers differ."""
    values = sorted(set(values))
    return str(values[0]) if len(values) == 1 else f"{values[0]}–{values[-1]}"


def usual_need(task: Task) -> Dict[str, str]:
    """How many people the task usually needs, shown by its name on the rota.

    ``text`` is the number ("1"), or a range ("1–2") when it varies by
    weekday; ``""`` when the task is never needed on an ordinary day and has
    no number for event days. ``title`` says it in words.
    """
    counts = _weekday_counts(task)
    on_events = task.headcount.get(EVENT_DAY, 0)
    if task.event_id is not None:
        # Needed on its event's days only, whatever their weekday.
        values = {max(count, on_events) for count in counts if max(count, on_events) > 0}
        if not values:
            return {"text": "", "title": ""}
        text = _range_text(values)
        noun = "person" if text == "1" else "people"
        return {"text": text, "title": f"Needed only on the days of its event: {text} {noun}."}

    groups: Dict[int, List[int]] = {}
    for day, count in enumerate(counts):
        if count > 0:
            groups.setdefault(count, []).append(day)
    if not groups:
        if on_events <= 0:
            return {"text": "", "title": ""}
        return {"text": str(on_events), "title": f"Needed only on event days: {people_text(on_events)}."}

    if len(groups) == 1 and all(counts):
        sentence = f"Needs {people_text(counts[0])} a day."
    elif all(counts):
        # Every day: the most usual number first, then the days that differ.
        usual = max(groups, key=lambda count: (len(groups[count]), -count))
        others = [f"{count} on {_weekdays_text(days)}"
                  for count, days in sorted(groups.items(), key=lambda item: item[1][0]) if count != usual]
        sentence = f"Needs {people_text(usual)} a day, {_and(others)}."
    else:
        ordered = sorted(groups.items(), key=lambda item: item[1][0])
        parts = [f"{people_text(count) if i == 0 else count} on {_weekdays_text(days)}"
                 for i, (count, days) in enumerate(ordered)]
        sentence = f"Needs {_and(parts)}."
    if on_events > min(counts):
        sentence += f" At least {on_events} on event days."
    return {"text": _range_text(groups), "title": sentence}


def day_need(month: Month, task: Task, iso: str, required: int) -> Optional[Dict[str, Any]]:
    """The need of one day, when it is not the task's usual number for that weekday.

    ``required`` is the day's need (``required(month)``). A number set by hand
    for the day, or an event day asking for more, gives ``{"count": 3,
    "title": "3 people needed this day (usually 1): Retreat."}``; any other day
    gives ``None`` (a closure shows by itself). For a task of an event, only a
    number set by hand counts: its need already comes from its event.
    """
    by_hand = iso in month.demand_overrides.get(task.id, {})
    if task.event_id is not None:
        if not by_hand:
            return None
        usual = rule_headcount(task, iso, month.day_type(iso), month.events)
    else:
        usual = rule_headcount(task, iso, NORMAL_DAY, month.events)
    if required == usual or (not by_hand and required < usual):
        return None   # a closure or an archived area lowers it, and says so elsewhere
    if by_hand:
        why = "set by hand"
    else:
        day_type = month.day_type(iso)
        event = next((e for e in month.events if e.covers(iso) and e.day_type == day_type), None)
        if event is not None:
            why = event.name.rstrip(". ")
        else:   # the kind of day was chosen by hand for that date
            why = "an event day" if day_type == EVENT_DAY else "a special day"
    needed = "Nobody needed" if required == 0 else f"{people_text(required)} needed"
    return {"count": required, "title": f"{needed} this day (usually {usual or 'none'}): {why}."}
