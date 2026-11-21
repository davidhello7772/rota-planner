"""Health of a month's rota: the state of each assignment, and the totals."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from . import eligibility as rules
from .model import Assignment
from .schedule import Schedule, Slot

OK, WARN, CONFLICT, FORCED, GUEST = "ok", "warn", "conflict", "forced", "guest"


@dataclass
class Status:
    state: str
    findings: List[rules.Finding] = field(default_factory=list)


def assignment_status(schedule: Schedule, assignment: Assignment) -> Status:
    """Re-check one assignment against every rule, ignoring itself."""
    findings: List[rules.Finding] = []
    required = schedule.required.get((assignment.task_id, assignment.date), 0)
    if assignment.position >= required:
        words = f"Beyond the {required} needed that day" if required else "Not needed that day"
        findings.append(rules.Finding("not_required", rules.WARN, words))

    if assignment.outsider:
        return Status(WARN if findings else GUEST, findings)

    person = schedule.people.get(assignment.person_id)
    task = schedule.tasks.get(assignment.task_id)
    if person is None or task is None:
        missing = rules.Finding("missing", rules.BLOCK, "No longer in the list of people or tasks")
        return Status(CONFLICT, findings + [missing])

    task = schedule.task_on(task.id, assignment.date)
    placement = rules.Placement(schedule, person, task, assignment.date, ignore=assignment)
    findings += [f for f in rules.evaluate(placement) if f.severity != rules.NOTE]
    group = rules.group_of(findings)
    if group == rules.BLOCKED:
        return Status(FORCED if assignment.forced_reason is not None else CONFLICT, findings)
    return Status(WARN if group == rules.POSSIBLE else OK, findings)


def all_statuses(schedule: Schedule) -> Dict[Slot, Status]:
    return {a.slot: assignment_status(schedule, a) for a in schedule.all_assignments()}


@dataclass
class Summary:
    required: int = 0
    filled: int = 0
    conflicts: int = 0
    warnings: int = 0
    forced: int = 0
    guests: int = 0

    @property
    def open(self) -> int:
        return self.required - self.filled

    @property
    def coverage(self) -> float:
        return self.filled / self.required if self.required else 1.0

    def to_dict(self) -> Dict[str, float]:
        return {
            "required": self.required,
            "filled": self.filled,
            "open": self.open,
            "coverage": round(self.coverage, 3),
            "conflicts": self.conflicts,
            "warnings": self.warnings,
            "forced": self.forced,
            "guests": self.guests,
        }


def summarize(schedule: Schedule, statuses: Dict[Slot, Status], dates=None) -> Summary:
    """Totals for the whole month, or only for ``dates`` when given."""
    wanted = set(dates) if dates is not None else None
    summary = Summary()
    for (task_id, iso), count in schedule.required.items():
        if wanted is None or iso in wanted:
            summary.required += count
            summary.filled += sum(1 for position in schedule.cell(task_id, iso) if position < count)
    for (task_id, iso, _), status in statuses.items():
        if wanted is not None and iso not in wanted:
            continue
        if status.state == CONFLICT:
            summary.conflicts += 1
        elif status.state == WARN:
            summary.warnings += 1
        elif status.state == FORCED:
            summary.forced += 1
        elif status.state == GUEST:
            summary.guests += 1
    return summary
