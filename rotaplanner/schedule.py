"""A month's assignments, indexed for fast questions.

Rules, checks and the solver all ask the same things many times: what is this
person doing that day, how many hours do they have, who is in that slot.
``Schedule`` answers from indexes that ``add`` and ``remove`` keep current, so
the solver can try and undo placements cheaply. It never modifies the
``Month`` it was built from.
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Set, Tuple

from . import demand
from .model import PERIODS, Assignment, Month, Person, Settings, Task, availability_key, parse_date

Slot = Tuple[str, str, int]
"""One position of a task on a day: ``(task_id, iso_date, position)``."""


class Schedule:
    def __init__(self, month: Month, people: Iterable[Person], settings: Settings) -> None:
        self.month = month
        self.settings = settings
        self.people: Dict[str, Person] = {person.id: person for person in people}
        self.tasks: Dict[str, Task] = {task.id: task for task in month.tasks}
        self.dates: List[str] = month.dates()
        self.required: Dict[demand.SlotDay, int] = demand.required(month)

        self._cells: Dict[demand.SlotDay, Dict[int, Assignment]] = defaultdict(dict)
        self._days: Dict[Tuple[str, str], List[Assignment]] = defaultdict(list)
        self._hours: Dict[str, float] = defaultdict(float)
        self._weekends: Dict[str, int] = defaultdict(int)   # weekend shifts per person
        self._evenings: Dict[str, int] = defaultdict(int)   # evening shifts per person
        self._unavailable: Dict[str, Set[str]] = {
            person_id: set(reply.unavailable) for person_id, reply in month.availability.items()
        }
        self._presence: Dict[str, float] = {}
        self._on_day: Dict[Tuple[str, str], Task] = {}
        for assignment in month.assignments:
            self.add(assignment)

    # -- changing ----------------------------------------------------------

    def add(self, assignment: Assignment) -> None:
        self._cells[(assignment.task_id, assignment.date)][assignment.position] = assignment
        if assignment.person_id:
            self._days[(assignment.person_id, assignment.date)].append(assignment)
            self._hours[assignment.person_id] += self.hours_of(assignment)
            self._count_kinds(assignment, 1)

    def remove(self, assignment: Assignment) -> None:
        del self._cells[(assignment.task_id, assignment.date)][assignment.position]
        if assignment.person_id:
            self._days[(assignment.person_id, assignment.date)].remove(assignment)
            self._hours[assignment.person_id] -= self.hours_of(assignment)
            self._count_kinds(assignment, -1)

    def _count_kinds(self, assignment: Assignment, step: int) -> None:
        if assignment.task_id not in self.tasks:
            return
        if parse_date(assignment.date).weekday() >= 5:
            self._weekends[assignment.person_id] += step
        if "evening" in self.task_on(assignment.task_id, assignment.date).periods:
            self._evenings[assignment.person_id] += step

    # -- slots -------------------------------------------------------------

    def at(self, slot: Slot) -> Optional[Assignment]:
        task_id, iso, position = slot
        return self._cells.get((task_id, iso), {}).get(position)

    def cell(self, task_id: str, iso: str) -> Dict[int, Assignment]:
        return self._cells.get((task_id, iso), {})

    def positions(self, task_id: str, iso: str) -> int:
        """Positions to show: those required, plus any filled beyond them."""
        filled = self.cell(task_id, iso)
        highest_filled = max(filled) + 1 if filled else 0
        return max(self.required.get((task_id, iso), 0), highest_filled)

    def open_slots(self) -> List[Slot]:
        """Required positions nobody fills yet, in date order."""
        slots = [
            (task_id, iso, position)
            for (task_id, iso), count in self.required.items()
            for position in range(count)
            if position not in self.cell(task_id, iso)
        ]
        slots.sort(key=lambda slot: (slot[1], slot[0], slot[2]))
        return slots

    def all_assignments(self) -> List[Assignment]:
        return [a for cell in self._cells.values() for a in cell.values()]

    # -- people ------------------------------------------------------------

    def task_on(self, task_id: str, iso: str) -> Task:
        """The task as it is on that day: its usual times, or the day's own."""
        key = (task_id, iso)
        if key not in self._on_day:
            task = self.tasks[task_id]
            change = self.month.time_changes.get(f"{task_id}|{iso}")
            if change is not None:
                task = dataclasses.replace(
                    task, start=change.start, end=change.end, hours=change.hours, time_label=change.label
                )
            self._on_day[key] = task
        return self._on_day[key]

    def hours_of(self, assignment: Assignment) -> float:
        if assignment.task_id not in self.tasks:
            return 0.0
        return self.task_on(assignment.task_id, assignment.date).hours

    def on_day(self, person_id: str, iso: str) -> List[Assignment]:
        return self._days.get((person_id, iso), [])

    def hours(self, person_id: str) -> float:
        return self._hours.get(person_id, 0.0)

    def weekends(self, person_id: str) -> int:
        return self._weekends.get(person_id, 0)

    def evenings(self, person_id: str) -> int:
        return self._evenings.get(person_id, 0)

    def is_unavailable(self, person_id: str, iso: str, period: str) -> bool:
        return availability_key(iso, period) in self._unavailable.get(person_id, ())

    def has_replied(self, person_id: str) -> bool:
        reply = self.month.availability.get(person_id)
        return reply is not None and reply.responded

    def presence(self, person_id: str) -> float:
        """Share of the month's day-periods on which the person is available."""
        if person_id not in self._presence:
            away = self._unavailable.get(person_id, set())
            total = len(self.dates) * len(PERIODS)
            missed = sum(
                1 for iso in self.dates for period in PERIODS if availability_key(iso, period) in away
            )
            self._presence[person_id] = 1.0 - missed / total
        return self._presence[person_id]

    def target(self, person_id: str) -> float:
        """Hours this person is expected to give this month.

        Scaled by presence when the setting is on, so someone away half the
        month is not expected to catch up in the other half.
        """
        person = self.people[person_id]
        target = person.target_hours
        if target is None:
            target = self.settings.default_target_hours
        if self.settings.prorate_target:
            target *= self.presence(person_id)
        return target
