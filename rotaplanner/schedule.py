"""A month's assignments, indexed for fast questions.

Rules, checks and the solver all ask the same things many times: what is this
person doing that day, how many hours do they have, who is in that slot.
``Schedule`` answers from indexes that ``add`` and ``remove`` keep current, so
the solver can try and undo placements cheaply. It never modifies the
``Month`` it was built from.

Rules also look a few days past the month: a night shift on the 31st, the
shifts already in a week that started in the month before. ``around`` gives
the months next door; their shifts on the nearby days are seen by
``on_day``, ``shifts_in_week``, ``task_on``, ``hours_of`` and ``knows``,
and never count in this month's own totals, slots or statuses.
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Set, Tuple

from . import demand
from .model import PERIODS, Assignment, Month, Person, Settings, Task, availability_key, parse_date, shift_date, week_start

Slot = Tuple[str, str, int]
"""One position of a task on a day: ``(task_id, iso_date, position)``."""


class Schedule:
    def __init__(
        self, month: Month, people: Iterable[Person], settings: Settings, around: Iterable[Month] = ()
    ) -> None:
        self.month = month
        self.settings = settings
        self.people: Dict[str, Person] = {person.id: person for person in people}
        self.tasks: Dict[str, Task] = {task.id: task for task in month.tasks}
        self.dates: List[str] = month.dates()
        self._in_month: Set[str] = set(self.dates)
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
        self._week_of: Dict[str, str] = {}                  # date -> its Monday
        self._weeks: Dict[Tuple[str, str], int] = defaultdict(int)  # shifts per (person, Monday)
        # The months next door, by id ("2026-11"), with their tasks; and their
        # shifts on the nearby days, which belong to them and not to this month.
        self._next_door: Dict[str, Month] = {}
        self._next_door_tasks: Dict[str, Dict[str, Task]] = {}
        self._nearby: Dict[Tuple[str, str], List[Assignment]] = defaultdict(list)
        for assignment in month.assignments:
            self.add(assignment)
        self._add_nearby(around)

    def _add_nearby(self, around: Iterable[Month]) -> None:
        """Index the shifts of the months next door that fall on the nearby days:
        the rest of the first and last weeks, and at least the day before and after."""
        first, last = self.dates[0], self.dates[-1]
        low = min(week_start(first), shift_date(first, -1))
        high = max(shift_date(week_start(last), 6), shift_date(last, 1))
        for other in around:
            if other.id == self.month.id or other.id in self._next_door:
                continue
            self._next_door[other.id] = other
            self._next_door_tasks[other.id] = {task.id: task for task in other.tasks}
            for assignment in other.assignments:
                iso = assignment.date
                if not assignment.person_id or not low <= iso <= high or iso in self._in_month:
                    continue
                if iso[:7] != other.id:
                    continue  # a shift belongs to the month of its date
                self._nearby[(assignment.person_id, iso)].append(assignment)
                if self.knows(assignment):
                    self._weeks[(assignment.person_id, self.week_of(iso))] += 1

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
        self._weeks[(assignment.person_id, self.week_of(assignment.date))] += step
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

    def _owner(self, iso: str) -> Tuple[Month, Dict[str, Task]]:
        """The month a date belongs to, with its tasks: this one, or the one next door
        (a month owns its tasks; the same id can have other times there)."""
        if iso not in self._in_month:
            other = self._next_door.get(iso[:7])
            if other is not None:
                return other, self._next_door_tasks[other.id]
        return self.month, self.tasks

    def in_month(self, iso: str) -> bool:
        """Is this day one of the month's own (not a nearby day of the month next door)?"""
        return iso in self._in_month

    def knows(self, assignment: Assignment) -> bool:
        """Is the task of this shift still in the month of its date?"""
        return assignment.task_id in self._owner(assignment.date)[1]

    def task_on(self, task_id: str, iso: str) -> Task:
        """The task as it is on that day: its usual times, or the day's own."""
        key = (task_id, iso)
        if key not in self._on_day:
            owner, tasks = self._owner(iso)
            task = tasks[task_id]
            change = owner.time_changes.get(f"{task_id}|{iso}")
            if change is not None:
                task = dataclasses.replace(
                    task, start=change.start, end=change.end, hours=change.hours, time_label=change.label
                )
            self._on_day[key] = task
        return self._on_day[key]

    def hours_of(self, assignment: Assignment) -> float:
        if not self.knows(assignment):
            return 0.0
        return self.task_on(assignment.task_id, assignment.date).hours

    def on_day(self, person_id: str, iso: str) -> List[Assignment]:
        """The person's shifts that day: in this month, or on a nearby day of the month next door."""
        if iso in self._in_month:
            return self._days.get((person_id, iso), [])
        return self._nearby.get((person_id, iso), [])

    def week_of(self, iso: str) -> str:
        """The Monday of the week of ``iso`` (remembered: it is asked thousands of times)."""
        monday = self._week_of.get(iso)
        if monday is None:
            monday = self._week_of[iso] = week_start(iso)
        return monday

    def shifts_in_week(self, person_id: str, iso: str) -> int:
        """Shifts the person has in the week (Monday to Sunday) of ``iso``, counting
        those of the month next door on the nearby days."""
        return self._weeks.get((person_id, self.week_of(iso)), 0)

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
