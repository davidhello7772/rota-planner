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
from typing import Callable, List, Optional, Tuple

from .model import MINUTES_PER_DAY, PERIODS, Assignment, Person, Task, parse_date, shift_date, weekday_key
from .schedule import Schedule

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

    def others_on(self, iso: str) -> List[Assignment]:
        return [a for a in self.schedule.on_day(self.person.id, iso) if a is not self.ignore]

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
    already = len(p.others_on(p.date))
    if limit is not None and already + 1 > limit:
        return Finding("daily_tasks", BLOCK, f"Already has {already} task(s) that day (limit {limit})")
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
    warnings that matter.
    """
    total = p.hours_this_month() + p.task.hours
    ceiling = p.person.max_hours
    if ceiling is not None and total > ceiling:
        return Finding("monthly_hours", BLOCK, f"Would reach {_hours(total)} (limit {_hours(ceiling)})")
    target = p.schedule.target(p.person.id)
    if p.task.hours > 0 and total > target:
        return Finding(
            "over_target", NOTE, f"Would reach {_hours(total)} (target {_hours(round(target, 1))})"
        )
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
    _daily_hours,
    _monthly_hours,
    _no_reply,
)


def evaluate(placement: Placement) -> List[Finding]:
    return [finding for rule in RULES for finding in (rule(placement),) if finding]


def group_of(findings: List[Finding]) -> str:
    severities = {finding.severity for finding in findings}
    if BLOCK in severities:
        return BLOCKED
    return POSSIBLE if WARN in severities else BEST


def placement_cost(p: Placement, findings: List[Finding]) -> float:
    """Lower is a better choice. Blocked placements are not ranked by this.

    The main term is the growth of the person's squared load (hours over
    target): giving an hour to someone at 20 % of their target costs far less
    than giving it to someone at 90 %, which is what evens loads out.
    """
    weights = p.schedule.settings.weights
    target = max(p.schedule.target(p.person.id), 1.0)
    effort = p.task.hours or NOMINAL_EFFORT_HOURS
    before = p.hours_this_month() / target
    after = before + effort / target

    cost = weights.load * (after**2 - before**2)
    cost += weights.over_target * max(0.0, after - 1.0)
    cost += weights.same_day * len(p.others_on(p.date))
    cost += weights.back_to_back * sum(
        1
        for offset in (-1, 1)
        for other in p.others_on(shift_date(p.date, offset))
        if other.task_id == p.task.id
    )
    # Share weekends and evenings: each one already held makes another less likely.
    mine = p.ignore if p.ignore is not None and p.ignore.person_id == p.person.id else None
    if parse_date(p.date).weekday() >= 5:
        held = p.schedule.weekends(p.person.id) - (1 if mine and parse_date(mine.date).weekday() >= 5 else 0)
        cost += weights.weekend * held
    if "evening" in p.task.periods:
        mine_evening = mine is not None and mine.task_id in p.schedule.tasks and \
            "evening" in p.schedule.task_on(mine.task_id, mine.date).periods
        held = p.schedule.evenings(p.person.id) - (1 if mine_evening else 0)
        cost += weights.evening * held
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
