"""Who can do what, and how good a choice they are.

Every constraint is one small function in ``RULES``. The same list drives the
candidate picker (may I put this person here?), the checks (is this
assignment still fine?) and the solver, so a new rule is written once.

A rule returns ``None`` when it has nothing to say, or a ``Finding``:

* ``BLOCK``: breaks a hard constraint; only "Assign anyway" can place them.
* ``WARN``:  allowed, but the coordinator should know.
* ``NOTE``:  information only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Optional, Tuple

from .model import (MINUTES_PER_DAY, PERIODS, Assignment, Person, Task, parse_date, shift_date, week_dates,
                    weekday_key)
from .schedule import Schedule
from .workload import over_target_text

BLOCK, WARN, NOTE = "block", "warn", "note"

BEST, POSSIBLE, BLOCKED = "best", "warn", "blocked"
"""Candidate groups, in the order the picker shows them."""

NOMINAL_EFFORT_HOURS = 0.5
"""Hours used when ranking a task that counts for zero hours (night security),
so that it is still shared around instead of always going to the same person."""

WEEKDAY_NAMES = {
    "mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday",
    "fri": "Friday", "sat": "Saturday", "sun": "Sunday",
}  # fmt: skip


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    message: str


@dataclass(frozen=True)
class Placement:
    """The question being asked: this person, on this task, on this day.

    ``ignore`` is the assignment under review when re-checking one that
    already exists, so that it does not clash with itself.
    """

    schedule: Schedule
    person: Person
    task: Task
    date: str
    ignore: Optional[Assignment] = None

    @property
    def rechecking(self) -> bool:
        """An existing shift of this person is under review (not a new placement).

        The solver also passes someone else's shift as ``ignore`` when it asks
        who could take it: for those other people it is a new placement."""
        return self.ignore is not None and self.ignore.person_id == self.person.id

    def others_on(self, iso: str) -> List[Assignment]:
        return [a for a in self.schedule.on_day(self.person.id, iso) if a is not self.ignore]

    def counted(self, assignments: Iterable[Assignment]) -> List[Assignment]:
        """Shifts that count against a personal limit. When an existing shift is
        re-checked, those assigned anyway do not count against it: the override
        is theirs alone to show, it does not turn the person's other shifts red."""
        if not self.rechecking or self.ignore.forced_reason is not None:
            # A new placement, or a shift itself assigned anyway: everything counts,
            # so the override keeps showing (also when there are two of them).
            return list(assignments)
        return [a for a in assignments if a.forced_reason is None]

    def shifts_that_week(self) -> int:
        """All the person's other shifts in the week (Monday to Sunday) of this
        day, those of the month next door included; quick (a counter)."""
        count = self.schedule.shifts_in_week(self.person.id, self.date)
        mine = self.ignore
        if self.rechecking and mine is not None and self.schedule.knows(mine) \
                and self.schedule.week_of(mine.date) == self.schedule.week_of(self.date):
            count -= 1
        return count

    def hours_that_day(self) -> float:
        return sum(self.schedule.hours_of(a) for a in self.others_on(self.date))

    def hours_this_month(self) -> float:
        hours = self.schedule.hours(self.person.id)
        if self.ignore is not None and self.ignore.person_id == self.person.id:
            hours -= self.schedule.hours_of(self.ignore)
        return hours


Rule = Callable[[Placement], Optional[Finding]]


def _hours(value: float) -> str:
    return f"{value:g} h"


def _inactive(p: Placement) -> Optional[Finding]:
    if p.person.archived or not p.person.active:
        return Finding("inactive", BLOCK, "Not active on the rota")
    return None


def _not_trained(p: Placement) -> Optional[Finding]:
    if p.task.skill_id and p.task.skill_id not in p.person.skills:
        return Finding("not_trained", BLOCK, "Not trained for this task")
    return None


def _excluded(p: Placement) -> Optional[Finding]:
    if p.task.id in p.person.avoid_task_ids:
        return Finding("excluded", BLOCK, "Does not do this task")
    return None


def _free_periods(p: Placement) -> List[str]:
    """Periods of that day the person is around (no absence, no standing rule)."""
    weekday = weekday_key(p.date)
    never = {rule.period for rule in p.person.never if rule.weekday == weekday}
    if "any" in never:
        return []
    return [
        period for period in PERIODS
        if period not in never and not p.schedule.is_unavailable(p.person.id, p.date, period)
    ]


def _unavailable(p: Placement) -> Optional[Finding]:
    if p.task.any_time:
        # Any moment of the day will do: only a full day away blocks.
        free = _free_periods(p)
        if not free:
            return Finding("unavailable", BLOCK, "Away all day")
        if len(free) < len(PERIODS):
            return Finding("partly_away", NOTE, f"Around in the {' and '.join(free)} only")
        return None
    away = [period for period in p.task.periods if p.schedule.is_unavailable(p.person.id, p.date, period)]
    if away:
        return Finding("unavailable", BLOCK, f"Unavailable that {' and '.join(away)}")
    return None


def _never(p: Placement) -> Optional[Finding]:
    if p.task.any_time:
        return None  # covered by _unavailable, which sees the whole day
    weekday = weekday_key(p.date)
    for rule in p.person.never:
        if rule.weekday == weekday and (rule.period == "any" or rule.period in p.task.periods):
            when = "" if rule.period == "any" else f" {rule.period}s"
            return Finding("never", BLOCK, f"Never available on {WEEKDAY_NAMES[weekday]}{when}")
    return None


def _double_booked(p: Placement) -> Optional[Finding]:
    if any(a.task_id == p.task.id for a in p.others_on(p.date)):
        return Finding("double_booked", BLOCK, "Already on this task that day")
    return None


def _span(task: Task, day_offset: int) -> Optional[Tuple[int, int]]:
    interval = task.interval()
    if interval is None:
        return None
    shift = day_offset * MINUTES_PER_DAY
    return interval[0] + shift, interval[1] + shift


def _overlap(p: Placement) -> Optional[Finding]:
    """Clash with another timed task, including one running past midnight."""
    mine = _span(p.task, 0)
    if mine is None:
        return None
    for offset in (-1, 0, 1):
        day = shift_date(p.date, offset)
        for other in p.others_on(day):
            if not p.schedule.knows(other):
                continue
            other_task = p.schedule.task_on(other.task_id, day)
            theirs = _span(other_task, offset)
            if theirs and mine[0] < theirs[1] and theirs[0] < mine[1]:
                return Finding(
                    "overlap", BLOCK, f"Overlaps {other_task.name} ({other_task.time_text()})"
                )
    return None


def _daily_tasks(p: Placement) -> Optional[Finding]:
    limit = p.person.max_tasks_per_day
    if limit is None:
        return None
    already = len(p.counted(p.others_on(p.date)))
    if already + 1 > limit:
        return Finding("daily_tasks", BLOCK, f"Already has {already} task(s) that day (limit {limit})")
    return None


def _weekly_shifts(p: Placement) -> Optional[Finding]:
    """At most so many shifts from Monday to Sunday, across two months too.

    A number of shifts, whatever their length: never hours. A new placement
    reads the counter; a re-check walks the week, so that the shifts assigned
    anyway can be left out (``Placement.counted``). Both count the same
    shifts: those whose task the month of their date still has."""
    limit = p.person.max_shifts_per_week
    if limit is None:
        return None
    if p.rechecking:
        already = len(p.counted(
            other for day in week_dates(p.date) for other in p.others_on(day) if p.schedule.knows(other)
        ))
    else:
        already = p.shifts_that_week()
    if already + 1 > limit:
        plural = "" if already == 1 else "s"
        return Finding("weekly_shifts", BLOCK, f"Already has {already} shift{plural} that week (limit {limit})")
    return None


def _daily_hours(p: Placement) -> Optional[Finding]:
    """A personal daily limit is hard; the centre-wide default only warns."""
    total = p.hours_that_day() + p.task.hours
    personal = p.person.max_hours_per_day
    if personal is not None:
        if total > personal:
            return Finding(
                "daily_hours", BLOCK, f"Would reach {_hours(total)} that day (limit {_hours(personal)})"
            )
        return None
    usual = p.schedule.settings.default_max_hours_per_day
    if total > usual:
        return Finding("daily_hours", WARN, f"Would reach {_hours(total)} that day (usual max {_hours(usual)})")
    return None


def _monthly_hours(p: Placement) -> Optional[Finding]:
    """A personal monthly ceiling is hard.

    Going over the target is only a note: the ranking already prefers people
    with fewer hours, and a warning on every busy person would drown the
    warnings that matter. The target is hours a week; it is compared here
    through the month's figure worked out from it (``Schedule.target``), with
    a hair of tolerance, since that figure comes out of a division. The note
    says the average a week the person would reach (``workload``).
    """
    total = p.hours_this_month() + p.task.hours
    ceiling = p.person.max_hours
    if ceiling is not None and total > ceiling:
        return Finding("monthly_hours", BLOCK, f"Would reach {_hours(total)} (limit {_hours(ceiling)})")
    target = p.schedule.target(p.person.id)
    if p.task.hours > 0 and total > target + 1e-9:
        return Finding("over_target", NOTE, over_target_text(p.schedule, p.person.id, total))
    return None


def _no_reply(p: Placement) -> Optional[Finding]:
    if not p.schedule.has_replied(p.person.id):
        return Finding("no_reply", NOTE, "Has not replied to the availability form")
    return None


RULES: Tuple[Rule, ...] = (
    _inactive,
    _not_trained,
    _excluded,
    _unavailable,
    _never,
    _double_booked,
    _overlap,
    _daily_tasks,
    _weekly_shifts,
    _daily_hours,
    _monthly_hours,
    _no_reply,
)


def _count(number: int, word: str) -> str:
    return f"{number} {word}{'' if number == 1 else 's'}"


def limits_text(person: Person) -> List[str]:
    """The limits on a person's card, in short sentences for the screen."""
    texts = []
    if person.max_shifts_per_week is not None:
        texts.append(f"At most {_count(person.max_shifts_per_week, 'shift')} a week")
    if person.max_tasks_per_day is not None:
        texts.append(f"At most {_count(person.max_tasks_per_day, 'task')} a day")
    if person.max_hours is not None:
        texts.append(f"At most {_hours(person.max_hours)} a month")
    if person.max_hours_per_day is not None:
        texts.append(f"At most {_hours(person.max_hours_per_day)} a day")
    return texts


def evaluate(placement: Placement) -> List[Finding]:
    return [finding for rule in RULES for finding in (rule(placement),) if finding]


def group_of(findings: List[Finding]) -> str:
    severities = {finding.severity for finding in findings}
    if BLOCK in severities:
        return BLOCKED
    return POSSIBLE if WARN in severities else BEST


OVER_TARGET_CURVE = 4.0
"""How much faster a load grows past the target than below it (with ``Weights.over_target``)."""


def _load(ratio: float, weights) -> float:
    """What a load (hours over target) costs: it grows with the square, and faster past the target."""
    return weights.load * ratio**2 + weights.over_target * OVER_TARGET_CURVE * max(0.0, ratio - 1.0) ** 2


def allowed(placement: Placement) -> Optional[List[Finding]]:
    """The findings of a placement, or ``None`` as soon as a rule blocks it.

    Quicker than ``evaluate`` for the solver: ``RULES`` is ordered cheapest
    first, and most people are blocked by one of the first rules."""
    findings = []
    for rule in RULES:
        finding = rule(placement)
        if finding:
            if finding.severity == BLOCK:
                return None
            findings.append(finding)
    return findings


def placement_cost(p: Placement, findings: List[Finding]) -> float:
    """Lower is a better choice. Blocked placements are not ranked by this.

    The main term is the growth of the person's load (hours over their own
    target), weighed by their share (their target against the usual one):
    minimising it brings everyone to the same percentage of their own target,
    whatever its size. Weekends, evenings and other shifts that week count
    divided by that share, so they are shared in proportion too.

    The target is hours a week, on average; it is compared through the
    month's figure worked out from it (``Schedule.target``, and
    ``Schedule.usual_target`` for the usual one), which comes to the same:
    hours over the month's figure is the average a week over the weekly target.
    """
    weights = p.schedule.settings.weights
    raw = p.schedule.target(p.person.id)
    target = max(raw, 1.0)
    # 1 for the usual target, 0.5 for half of it. A target of zero keeps 1: such a
    # person stays the last choice.
    share = target / max(p.schedule.usual_target(), 1.0) if raw > 0 else 1.0
    effort = p.task.hours or NOMINAL_EFFORT_HOURS
    before = p.hours_this_month() / target
    after = before + effort / target

    cost = share * (_load(after, weights) - _load(before, weights))
    cost += weights.same_week * p.shifts_that_week() / share   # spread over the weeks
    cost += weights.same_day * len(p.others_on(p.date))
    cost += weights.back_to_back * sum(
        1
        for offset in (-1, 1)
        for other in p.others_on(shift_date(p.date, offset))
        if other.task_id == p.task.id
    )
    # Share weekends and evenings: each one already held makes another less likely.
    mine = p.ignore if p.rechecking else None
    if parse_date(p.date).weekday() >= 5:
        held = p.schedule.weekends(p.person.id) - (1 if mine and parse_date(mine.date).weekday() >= 5 else 0)
        cost += weights.weekend * held / share
    if "evening" in p.task.periods:
        mine_evening = mine is not None and mine.task_id in p.schedule.tasks and \
            "evening" in p.schedule.task_on(mine.task_id, mine.date).periods
        held = p.schedule.evenings(p.person.id) - (1 if mine_evening else 0)
        cost += weights.evening * held / share
    preference = p.person.preferences.get(p.task.area_id)
    if preference:
        cost -= weights.preference * (preference - 3)
    cost += weights.warning * sum(1 for finding in findings if finding.severity == WARN)
    return cost


@dataclass
class Candidate:
    person: Person
    group: str
    cost: float
    findings: List[Finding] = field(default_factory=list)


def candidates(
    schedule: Schedule, task: Task, iso: str, ignore: Optional[Assignment] = None
) -> List[Candidate]:
    """Everyone, best choice first: clean matches, then warnings, then blocked."""
    task = schedule.task_on(task.id, iso)
    order = {BEST: 0, POSSIBLE: 1, BLOCKED: 2}
    ranked = []
    for person in schedule.people.values():
        if person.archived:
            continue
        placement = Placement(schedule, person, task, iso, ignore)
        findings = evaluate(placement)
        ranked.append(Candidate(person, group_of(findings), placement_cost(placement, findings), findings))
    # Among the blocked, the closest to possible first: active people, fewest reasons.
    ranked.sort(key=lambda c: (order[c.group], not c.person.active,
                               sum(1 for f in c.findings if f.severity == BLOCK), c.cost, c.person.name))
    return ranked
