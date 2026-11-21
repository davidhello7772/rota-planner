"""A person's hours against their target: every figure and sentence about them.

The target is hours a week, on average (``Schedule.week_target``: their own,
else the usual one in Settings). What is judged is the month's average: the
hours of the month divided by the weeks that count (the month's days ÷ 7,
less the time the person is away when Settings says so,
``Schedule.weeks_counted``). That average against the weekly target is the
same as the month's hours against the month's figure worked out from it
(``Schedule.target``), which the ranking and the suggestions use.

A week is Monday to Sunday (``Schedule.calendar_weeks``). One shared with the
month next door counts whole when that month has a rota, as the limit of
shifts a week does; when it has none yet, only this month's days count and the
week says so. A week is pointed out only when it has more than twice the
weekly target in two shifts or more (``FAR_ABOVE``): never a rule, since the
target is an average and one busy week is fine.

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

from typing import Any, Dict, List, Optional

from .model import parse_date, shift_date
from .schedule import Schedule

BAR_OVER, BAR_UNDER = 1.0, 0.5
"""The load bar on the rota: past the target (amber), below half of it (grey)."""

FAR_ABOVE = 2.0
"""A week over this many times the weekly target, in two shifts or more, is pointed out (never a rule)."""

TOLERANCE = 1e-9
"""The month's figure comes out of a division: hours that reach it exactly are not over it."""

MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
               "October", "November", "December")
"""In English whatever the computer's language (``strftime`` can follow it)."""

MONTH_SHORT = tuple(name[:3] for name in MONTH_NAMES)
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


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
    """A suggestion's own line, before its warnings: "3.2 of 3.5 h a week · 2 h
    that week · 14 h in December". ``iso``: the day of the suggested shift."""
    hours = schedule.hours(person_id)
    lead = _week_text(schedule, person_id, hours, " of ") if _counted(schedule, person_id) else "Away all month"
    return f"{lead} · {that_week(schedule, person_id, iso)['that_week_text']} · {number(hours)} h in {_month_name(schedule)}"


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


# -- the weeks: Monday to Sunday ------------------------------------------------

def _day_month(iso: str) -> str:
    return MONTH_NAMES[int(iso[5:7]) - 1]


def _day_name(iso: str) -> str:
    return DAY_NAMES[parse_date(iso).weekday()]


def _span(days: List[str]) -> str:
    """The days of a week in words: "Monday 7 to Sunday 13 December", "Monday 30
    November to Sunday 6 December"."""
    first, last = days[0], days[-1]
    start = f"{_day_name(first)} {int(first[8:])}"
    if first[:7] != last[:7]:
        start += f" {_day_month(first)}"
    return f"{start} to {_day_name(last)} {int(last[8:])} {_day_month(last)}"


def _week_words(schedule: Schedule, days: List[str]) -> Dict[str, Any]:
    """One week as the screens name it, from the days of it that count."""
    first, last = days[0], days[-1]
    one, two = int(first[8:]), int(last[8:])
    if first[:7] == last[:7]:
        label = f"{one}–{two}"
    else:
        label = f"{one} {MONTH_SHORT[int(first[5:7]) - 1]}–{two} {MONTH_SHORT[int(last[5:7]) - 1]}"
    title = _span(days)
    cut = len(days) < 7
    if cut:
        monday = schedule.week_of(first)
        if first != monday:   # it began in the month before, which has no rota yet
            title += f". The week began in {_day_month(monday)}, which has no rota yet: those days are not counted."
        else:                 # it goes on in the month after
            title += (f". The week goes on in {_day_month(shift_date(monday, 6))}, which has no rota yet: "
                      "those days are not counted.")
    return {"label": label, "short": f"{one}–{two}", "title": title, "cut": cut}


def weeks(schedule: Schedule) -> List[Dict[str, Any]]:
    """The weeks the month touches (Monday to Sunday), as the screens name them:
    ``{"start": "2026-11-30", "label": "30 Nov–6 Dec", "short": "30–6", "title":
    "Monday 30 November to Sunday 6 December", "cut": False}``. ``start`` is the
    Monday. A week cut because the month next door has no rota yet shows only
    the days that count ("28–31") and its title says so."""
    return [{"start": schedule.week_of(days[0]), **_week_words(schedule, days)} for days in schedule.calendar_weeks()]


def that_week(schedule: Schedule, person_id: str, iso: str) -> Dict[str, Any]:
    """A person's hours in the week of ``iso``, as the schedule stands: "2 h that
    week", or "Free that week" (``free_week``) with no shift at all that week."""
    hours = schedule.hours_in_week(person_id, iso)
    free = schedule.shifts_in_week(person_id, iso) == 0
    return {
        "that_week": round(hours, 2) + 0.0,   # never -0
        "that_week_text": "Free that week" if free else f"{number(hours)} h that week",
        "free_week": free,
    }


def _other_part(schedule: Schedule, person_id: str, days: List[str]) -> float:
    """The hours of these days that belong to the month next door."""
    return sum(schedule.hours_of(a) for iso in days if not schedule.in_month(iso)
               for a in schedule.on_day(person_id, iso))


def by_week(schedule: Schedule, person_id: str, detailed: bool = False) -> List[Dict[str, Any]]:
    """A person's hours in each week of ``weeks(schedule)`` (aligned with it):
    ``{"hours": 4.0, "level": 1, "far": False, "that_week_text": "4 h that week"}``.

    ``far``: more than twice the weekly target (``FAR_ABOVE``), in two shifts or
    more, never with a target of 0. ``level`` (the tint of the week): 0 no
    hours, 1 up to the weekly target (any hours with a target of 0), 2 above
    it, 3 far. ``text``, the week in words, on a far week always, and on every
    week when ``detailed``: "Week of Monday 14 to Sunday 20 December: 8 h, more
    than twice their target (3.5 h a week)"."""
    target = schedule.week_target(person_id)
    result = []
    for days in schedule.calendar_weeks():
        monday = schedule.week_of(days[0])
        hours = schedule.hours_in_week(person_id, monday)
        far = (target > 0 and hours > FAR_ABOVE * target + TOLERANCE
               and schedule.shifts_in_week(person_id, monday) >= 2)
        if far:
            level = 3
        elif hours <= TOLERANCE:
            level = 0
        elif target <= 0 or hours <= target + TOLERANCE:
            level = 1
        else:
            level = 2
        week = {"hours": round(hours, 2) + 0.0, "level": level, "far": far,
                "that_week_text": that_week(schedule, person_id, monday)["that_week_text"]}
        if far or detailed:
            text = f"Week of {_span(days)}: {number(hours)} h"   # a cut week: its days that count
            other = _other_part(schedule, person_id, days)
            if other > TOLERANCE:
                outside = next(iso for iso in days if not schedule.in_month(iso))
                text += f", {number(other)} h of them in {_day_month(outside)}"
            if far:
                text += f", more than twice their target ({number(target)} h a week)"
            week["text"] = text
        result.append(week)
    return result


def far_note(schedule: Schedule, person_id: str, weeks_of_theirs: Optional[List[Dict[str, Any]]] = None) -> str:
    """One line under a person's weeks when some are pointed out, saying what the
    mark means: "Marked: more than twice their target (3.5 h a week)."; else "".
    ``weeks_of_theirs``: their ``by_week`` when it is already worked out."""
    if weeks_of_theirs is None:
        weeks_of_theirs = by_week(schedule, person_id)
    if not any(week["far"] for week in weeks_of_theirs):
        return ""
    return f"Marked: more than twice their target ({number(schedule.week_target(person_id))} h a week)."
