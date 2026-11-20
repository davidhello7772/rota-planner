"""Creating a month and changing who does what in it."""

from __future__ import annotations

import copy
from typing import Dict, Iterable, List, Optional, Tuple

from . import demand, editing
from . import eligibility as rules
from .model import GUEST_KINDS, Assignment, Catalog, Month, Person, Regular, Settings, TimeChange, slot_key
from .schedule import Schedule

GUEST_NAME_MAX = 60


class RuleViolation(Exception):
    """Placing this person breaks a hard rule and was not forced."""

    def __init__(self, findings: List[rules.Finding]) -> None:
        super().__init__("; ".join(f.message for f in findings))
        self.findings = findings


def create_month(catalog: Catalog, year: int, month: int) -> Month:
    """A new, empty month using a copy of the current areas and tasks."""
    return Month(
        id=Month.make_id(year, month),
        year=year,
        month=month,
        areas=[copy.deepcopy(a) for a in catalog.areas if not a.archived],
        tasks=[copy.deepcopy(t) for t in catalog.tasks if not t.archived and t.event_id is None],
    )


def regulars_on(catalog: Catalog, iso: str) -> Dict[Tuple[str, int], Regular]:
    """Regular commitments that apply on a date, keyed by ``(task_id, position)``."""
    return {(r.task_id, r.position): r for r in catalog.regulars if r.applies(iso)}


def apply_regulars(month: Month, catalog: Catalog, people: Iterable[Person], settings: Settings) -> int:
    """Pre-fill the month with the regular week.

    A regular who cannot do a given day (away, already busy…) is skipped; the
    slot stays open and the grid shows it as "cover needed".
    Returns the number of assignments made.
    """
    schedule = Schedule(month, people, settings)
    placed = 0
    for iso in month.dates():
        for regular in regulars_on(catalog, iso).values():
            task = schedule.tasks.get(regular.task_id)
            person = schedule.people.get(regular.person_id)
            slot = (regular.task_id, iso, regular.position)
            if task is None or person is None or schedule.at(slot) is not None:
                continue
            if regular.position >= schedule.required.get((task.id, iso), 0):
                continue
            findings = rules.evaluate(rules.Placement(schedule, person, schedule.task_on(task.id, iso), iso))
            if rules.group_of(findings) == rules.BLOCKED:
                continue
            assignment = Assignment(task.id, iso, regular.position, person_id=person.id, source="regular")
            month.assignments.append(assignment)
            schedule.add(assignment)
            placed += 1
    return placed


def parse_slot_key(key: str) -> Tuple[str, str, int]:
    """``"task|2026-12-01|0"`` → ``("task", "2026-12-01", 0)``."""
    task_id, iso, position = (key.split("|") + ["", "", ""])[:3]
    if not position.isdigit():
        raise ValueError("Unknown shift.")
    return task_id, iso, int(position)


def _check_slot(schedule: Schedule, task_id: str, iso: str, position: int) -> None:
    if task_id not in schedule.tasks:
        raise ValueError("Unknown task.")
    if iso not in schedule.dates:
        raise ValueError("That date is not in this month.")
    if not 0 <= position <= schedule.positions(task_id, iso):
        raise ValueError("Unknown position.")


def assign(
    month: Month,
    people: Iterable[Person],
    settings: Settings,
    task_id: str,
    iso: str,
    position: int,
    person_id: str = "",
    guest: str = "",
    force: bool = False,
    reason: Optional[str] = None,
    guest_kind: str = "",
) -> Assignment:
    """Put a person, a guest or a volunteer in a slot, replacing whoever was there.

    A guest or volunteer may have no name yet (``guest_kind`` alone).
    Raises ``RuleViolation`` when a hard rule is broken and ``force`` is off.
    """
    schedule = Schedule(month, people, settings)
    _check_slot(schedule, task_id, iso, position)
    guest = " ".join(guest.split())[:GUEST_NAME_MAX]
    if guest_kind and guest_kind not in GUEST_KINDS:
        raise ValueError("Unknown kind of helper.")
    if guest and not guest_kind:
        guest_kind = "guest"
    if bool(person_id) == bool(guest_kind):
        raise ValueError("Choose a person, a guest or a volunteer.")

    previous = schedule.at((task_id, iso, position))
    if previous is not None:
        schedule.remove(previous)

    forced_reason = None
    if person_id:
        person = schedule.people.get(person_id)
        if person is None:
            raise ValueError("Unknown person.")
        placement = rules.Placement(schedule, person, schedule.task_on(task_id, iso), iso)
        blocking = [f for f in rules.evaluate(placement) if f.severity == rules.BLOCK]
        if blocking and not force:
            raise RuleViolation(blocking)
        if blocking:
            forced_reason = (reason or "").strip()[:200]

    assignment = Assignment(
        task_id, iso, position, person_id=person_id, guest=guest, guest_kind=guest_kind, forced_reason=forced_reason
    )
    if previous is not None:
        month.assignments.remove(previous)
    month.assignments.append(assignment)
    return assignment


def set_wanted(
    month: Month,
    people: Iterable[Person],
    settings: Settings,
    task_id: str,
    iso: str,
    position: int,
    on: bool,
) -> bool:
    """Highlight (or stop highlighting) a slot that should be filled when possible.

    Returns ``False`` when nothing changed.
    """
    _check_slot(Schedule(month, people, settings), task_id, iso, position)
    key = slot_key(task_id, iso, position)
    if on == (key in month.wanted):
        return False
    if on:
        month.wanted.append(key)
    else:
        month.wanted.remove(key)
    return True


MAX_HEADCOUNT = 30


def set_headcount(month: Month, task_id: str, iso: str, count: Optional[int]) -> bool:
    """Say by hand how many people a task needs on one day.

    ``0`` switches the task off for that day; ``None`` goes back to the
    usual rule. People already placed are moved down into the positions that
    remain, so reducing the number never hides someone. Returns ``False``
    when nothing changed.
    """
    if not any(task.id == task_id for task in month.tasks):
        raise ValueError("Unknown task.")
    if iso not in month.dates():
        raise ValueError("That date is not in this month.")
    if count is not None and not 0 <= count <= MAX_HEADCOUNT:
        raise ValueError(f"Choose between 0 and {MAX_HEADCOUNT} people.")

    overrides = month.demand_overrides.get(task_id, {})
    if count is None:
        if iso not in overrides:
            return False
        del overrides[iso]
        if not overrides:
            del month.demand_overrides[task_id]
    else:
        if overrides.get(iso) == count:
            return False
        month.demand_overrides.setdefault(task_id, {})[iso] = count
    _compact(month, task_id, iso)
    return True


def _compact(month: Month, task_id: str, iso: str) -> None:
    """Move people sitting beyond the required positions into free ones below."""
    required = demand.required(month).get((task_id, iso), 0)
    in_cell = sorted(
        (a for a in month.assignments if a.task_id == task_id and a.date == iso),
        key=lambda a: a.position,
    )
    taken = {a.position for a in in_cell}
    free = [p for p in range(required) if p not in taken]
    for assignment in in_cell:
        if assignment.position >= required and free:
            assignment.position = free.pop(0)


def set_time(month: Month, task_id: str, iso: str, data: Optional[Dict[str, object]]) -> bool:
    """Other times for a task on one day; ``None`` goes back to the usual times."""
    task = next((t for t in month.tasks if t.id == task_id), None)
    if task is None or iso not in month.dates():
        raise ValueError("Unknown task or date.")
    key = f"{task_id}|{iso}"
    if data is None:
        return month.time_changes.pop(key, None) is not None
    start = editing.clock(data.get("start"), "Start time")
    end = editing.clock(data.get("end"), "End time")
    if not (start and end):
        raise ValueError("Give the start and end times for that day.")
    hours = editing.number(data, "hours", 0, 24, "Hours")
    month.time_changes[key] = TimeChange(
        start=start, end=end, label=" ".join(str(data.get("label") or "").split())[:120],
        hours=round(hours if hours is not None else editing.duration_hours(start, end), 2),
    )
    return True


NOTE_MAX = 60


def set_note(month: Month, task_id: str, iso: str, position: int, note: str) -> bool:
    """A short practical note on someone's place ("arrives at 10", "with a trainee").

    It is shown and printed with the name, so it must stay practical: nothing
    about health or private matters (the screen says so). It goes when the
    place is given to someone else.
    """
    existing = month.find_assignment(task_id, iso, position)
    if existing is None:
        raise ValueError("Nobody is on this shift yet.")
    note = " ".join(str(note or "").split())[:NOTE_MAX]
    if existing.note == note:
        return False
    existing.note = note
    return True


def clear(month: Month, task_id: str, iso: str, position: int) -> bool:
    """Empty a slot. Returns ``False`` if it was already empty."""
    existing = month.find_assignment(task_id, iso, position)
    if existing is None:
        return False
    month.assignments.remove(existing)
    return True
