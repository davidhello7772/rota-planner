"""A person's hours against their target: every figure and sentence about them.

The target is hours a week, on average (``Schedule.week_target``: their own,
else the usual one in Settings). What is judged is the month's average: the
hours of the month divided by the weeks that count (the month's days ÷ 7,
less the time the person is away when Settings says so,
``Schedule.weeks_counted``). That average against the weekly target is the
same as the month's hours against the month's figure worked out from it
(``Schedule.target``), which the ranking and the suggestions use.

Every figure and sentence about a person's hours against their target comes
from here, so that no screen divides hours by a target and ``3.3 / 3.5 h a
week`` is written the same way everywhere. Nothing here may raise or divide
by zero (a target of 0, someone away the whole month): ``transfer.checked``
runs ``view.month_view`` on every month that is imported or taken from
another computer.

(Not named ``hours``: ``solver``, ``report`` and ``eligibility`` have local
variables of that name, which would hide the module.)
"""

from __future__ import annotations

from typing import Any, Dict

from .schedule import Schedule

BAR_OVER, BAR_UNDER = 1.0, 0.5
"""The load bar on the rota: past the target (amber), below half of it (grey)."""

TOLERANCE = 1e-9
"""The month's figure comes out of a division: hours that reach it exactly are not over it."""

MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
               "October", "November", "December")
"""In English whatever the computer's language (``strftime`` can follow it)."""


def number(value: float, digits: int = 2) -> str:
    """A figure as people write it: "3.3", "14", "2.75" (never "-0")."""
    return f"{round(value, digits) + 0.0:g}"


def _month_name(schedule: Schedule) -> str:
    return MONTH_NAMES[schedule.month.month - 1]


def _counted(schedule: Schedule, person_id: str) -> bool:
    """Does any of the month count for this person's average (not away all month)?"""
    return schedule.weeks_counted(person_id) > 0


def _week_text(schedule: Schedule, person_id: str, hours: float, between: str) -> str:
    """"3.2 / 3.5 h a week" (or "3.2 of 3.5 h a week"): the average a week with
    ``hours`` in the month, against the weekly target."""
    average = schedule.week_average(person_id, hours=hours)
    return f"{number(average, 1)}{between}{number(schedule.week_target(person_id))} h a week"


def summary(schedule: Schedule, person_id: str) -> Dict[str, Any]:
    """The figures and words of a person's hours against their target, as every
    screen shows them (a person of the rota, a candidate for a shift)."""
    hours = schedule.hours(person_id)
    target = schedule.target(person_id)
    ratio = hours / target if target > 0 else 0.0
    if ratio > BAR_OVER + TOLERANCE:
        bar = "over"
    elif ratio < BAR_UNDER:
        bar = "under"   # also a target of 0, as there is nothing to be over
    else:
        bar = ""
    month = _month_name(schedule)
    return {
        "hours": round(hours, 2),                                     # this month
        "target": round(target, 1),                                   # this month, worked out
        "week_hours": round(schedule.week_average(person_id, hours=hours), 1),
        "week_target": round(schedule.week_target(person_id), 2),
        "ratio": round(ratio, 4),
        "bar": bar,
        "week_text": _week_text(schedule, person_id, hours, " / ") if _counted(schedule, person_id)
        else "Away all month",
        "month_text": f"{number(hours)} h in {month}",
        # Against the month's figure; without one (a target of 0, away all month), the hours alone.
        "month_detail": f"{number(hours)} of {number(target, 1)} h in {month}" if target > 0
        else f"{number(hours)} h in {month}",
    }


def reason(schedule: Schedule, person_id: str, iso: str) -> str:
    """A suggestion's own line, before its warnings: "3.2 of 3.5 h a week · 14 h
    in December". ``iso``: the day of the suggested shift."""
    hours = schedule.hours(person_id)
    lead = _week_text(schedule, person_id, hours, " of ") if _counted(schedule, person_id) else "Away all month"
    return f"{lead} · {number(hours)} h in {_month_name(schedule)}"


def over_target_text(schedule: Schedule, person_id: str, total: float) -> str:
    """The note of a placement that goes over the target: the average a week
    with ``total`` hours in the month ("Over target: would reach 4.2 h a week")."""
    if not _counted(schedule, person_id):
        return "Over target: away all month"
    return f"Over target: would reach {number(schedule.week_average(person_id, hours=total), 1)} h a week"


def week_change(schedule: Schedule, person_id: str, before: float, after: float) -> str:
    """A suggested move, for one of the two people: their average a week with
    ``before`` and with ``after`` hours in the month ("3.6 → 2.7 of 2.75 h a week")."""
    if not _counted(schedule, person_id):
        return "away all month"
    return (f"{number(schedule.week_average(person_id, hours=before), 1)} → "
            f"{number(schedule.week_average(person_id, hours=after), 1)} of "
            f"{number(schedule.week_target(person_id))} h a week")
