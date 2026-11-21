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

from typing import Any, Dict, List, Optional, Tuple

from .editing import AFTERNOON_FROM, EVENING_FROM
from .model import ANYTIME, MINUTES_PER_DAY, PERIODS, Assignment, parse_date, shift_date
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
        "month_detail": f"{number(hours)} of {number(target)} h in {month}" if target > 0
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
    November to Sunday 6 December", or one day ("Sunday 1 November")."""
    first, last = days[0], days[-1]
    if first == last:   # a week cut to one day: "Sunday 1 November"
        return f"{_day_name(first)} {int(first[8:])} {_day_month(first)}"
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
    cut = len(days) < 7
    title = _span(days) + (f". {_cut_words(schedule, days)}" if cut else "")
    return {"label": label, "short": f"{one}–{two}", "title": title, "cut": cut}


def _cut_words(schedule: Schedule, days: List[str]) -> str:
    """Why a week has fewer than seven days that count: the month next door has no rota yet."""
    monday = schedule.week_of(days[0])
    if days[0] != monday:   # it began in the month before
        return f"The week began in {_day_month(monday)}, which has no rota yet: those days are not counted."
    return (f"The week goes on in {_day_month(shift_date(monday, 6))}, which has no rota yet: "   # in the month after
            "those days are not counted.")


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


# -- the list for a shift: the week and the month, with this shift ---------------

BAR_SCALE = 1.5
"""The bars of the list for a shift run from 0 to this many times the target (the target two thirds along)."""


def _position(hours: float, scale: float) -> float:
    """Where some hours fall on a bar from 0 to ``scale`` hours, in percent, cut at the end."""
    return round(min(100.0, max(0.0, hours) / scale * 100), 2) + 0.0   # never -0


def _bar(now: float, added: float, target: float) -> Dict[str, Any]:
    """A bar from 0 to 1.5 × the target, in ready-made percentages: the hours so
    far (``now``), then what this shift adds (``add``); past the target, the
    ``-over`` kinds. With a target of 0, from 0 to the hours with the shift (at
    least 1 h), every part past the target and no line for it."""
    now, after, target = max(0.0, now), max(0.0, now) + max(0.0, added), max(0.0, target)
    if target > 0:
        scale, target_at = BAR_SCALE * target, round(100 / BAR_SCALE, 2)
    else:
        scale, target_at = max(after, 1.0), None
    pieces = (("now", 0.0, min(now, target)), ("now-over", target, now),
              ("add", now, min(after, target)), ("add-over", max(now, target), after))
    segments = []
    for kind, start, end in pieces:
        left, right = _position(start, scale), _position(end, scale)
        if right > left:
            segments.append([kind, left, right])
    return {"segments": segments, "target_at": target_at}


def _figures(now: float, added: float, target: float, target_text: str, held: bool = False) -> List[List[str]]:
    """"3 → 6 of 4 h", in parts with their kind: the hours so far and the arrow
    (``was``, only when the shift adds hours and is not theirs already), the
    hours with this shift (``after``, or ``after-over`` past the target), then
    "of" the target."""
    after = now + added
    parts = [["was", f"{number(now)} → "]] if added > TOLERANCE and not held else []
    parts.append(["after-over" if after > target + TOLERANCE else "after", number(after)])
    parts.append(["of", f" of {target_text} h" if target > 0 else " h"])
    return parts


def _so_far(now: float, added: float, held: bool = False) -> str:
    """"3 h so far, 6 h with this shift" (the second part only when the shift adds
    hours); for the person on the shift, "6 h, 3 h of them this shift"."""
    if held:
        text = f"{number(now + added)} h"
        return text + (f", {number(added)} h of them this shift" if added > TOLERANCE else "")
    text = f"{number(now)} h so far"
    if added > TOLERANCE:
        text += f", {number(now + added)} h with this shift"
    return text


def choice(schedule: Schedule, person_id: str, iso: str, added: float, held: bool = False) -> Dict[str, Any]:
    """Two lines of the list for a shift on ``iso``, for one person: their hours in
    the shift's week (Monday to Sunday, whole across two months when the month
    next door has a rota) against their weekly target, and their hours this month
    against the month's figure; each with what this shift would add (``added``:
    its hours that day).

    ``held``: the person is on this shift already (``added`` is then its hours
    that day). "So far" leaves it out, the bar draws it lighter after the rest,
    and the figures give the total only, without the arrow ("7.5 of 3.5 h").

    Each line: ``label``, the bar's ``segments`` (``[kind, from %, to %]``),
    ``target_at`` (% of the target's line, ``None`` with a target of 0) and the
    ``figures`` in parts; ``title`` says all of it in words."""
    added = max(0.0, added)
    monday = schedule.week_of(iso)
    days = next((days for days in schedule.calendar_weeks() if schedule.week_of(days[0]) == monday), [iso])
    week_now, week_target = schedule.hours_in_week(person_id, iso), schedule.week_target(person_id)
    month_now, month_target = schedule.hours(person_id), schedule.target(person_id)
    if held:   # their hours as they stand count this shift: without it, then it
        week_now, month_now = max(0.0, week_now - added), max(0.0, month_now - added)
    month = _month_name(schedule)

    week_title = (f"Week of {_span(days)}: {_so_far(week_now, added, held)}, "
                  f"for a target of {number(week_target)} h a week.")
    if len(days) < 7:
        week_title += f" {_cut_words(schedule, days)}"
    month_title = f"{month}: {_so_far(month_now, added, held)}"
    if month_target > 0:
        month_title += f", of {number(month_target)} h."
    elif week_target > 0:   # away all month, when Settings does not count the time away
        month_title += "; away all month, so no target."
    else:
        month_title += ", for a target of 0 h."
    return {
        "week": {"label": "Week", **_bar(week_now, added, week_target),
                 "figures": _figures(week_now, added, week_target, number(week_target), held)},
        "month": {"label": MONTH_SHORT[schedule.month.month - 1], **_bar(month_now, added, month_target),
                  "figures": _figures(month_now, added, month_target, number(month_target), held)},
        "title": f"{week_title} {month_title}",
    }


def _runs(days: List[int]) -> List[List[int]]:
    """Days of the month in runs of consecutive days: [1, 2, 3, 9] → [[1, 3], [9, 9]]."""
    runs: List[List[int]] = []
    for day in days:
        if runs and runs[-1][1] == day - 1:
            runs[-1][1] = day
        else:
            runs.append([day, day])
    return runs


def _run_text(run: List[int]) -> str:
    return f"{run[0]}–{run[1]}" if run[1] != run[0] else str(run[0])


def _listed(texts: List[str]) -> str:
    """"9", "9 and 12", "1–4, 9 and 12"."""
    return texts[0] if len(texts) == 1 else f"{', '.join(texts[:-1])} and {texts[-1]}"


def _days(count: int) -> str:
    return f"{count} day" if count == 1 else f"{count} days"


def away(schedule: Schedule, person_id: str) -> Dict[str, str]:
    """A person's time away this month, as their reply to the availability form
    gives it (not the "never" rules of their card), for the list for a shift:
    ``{"level": "all", "text": "Away 1–4 Dec, part of 9 Dec", "title": "Away all
    day 1–4 December; away the evening of 9 December"}``.

    ``level``: "all" with at least one whole day away, "some" with only parts of
    days, "" when never away (then no words). The words stay short (they share a
    line with the reasons): one or two runs of whole days by their dates, more
    counted ("Away 9 days"); parts of days by their dates when they are one run,
    else counted ("part of 2 days"; after counted whole days, "part of 3 more").
    The ``title`` says every day."""
    whole: List[int] = []
    part: Dict[int, List[str]] = {}
    for iso in schedule.dates:
        parts = [period for period in PERIODS if schedule.is_unavailable(person_id, iso, period)]
        if len(parts) == len(PERIODS):
            whole.append(int(iso[8:]))
        elif parts:
            part[int(iso[8:])] = parts
    if not whole and not part:
        return {"level": "", "text": "", "title": ""}
    short, month = MONTH_SHORT[schedule.month.month - 1], _month_name(schedule)
    whole_runs, part_runs = _runs(whole), _runs(sorted(part))
    pieces = []
    if len(whole_runs) > 2:
        pieces.append(_days(len(whole)))
    elif whole_runs:
        pieces.append(f"{_listed([_run_text(run) for run in whole_runs])} {short}")
    if len(part_runs) == 1:
        pieces.append(f"part of {_run_text(part_runs[0])} {short}")
    elif part_runs:
        pieces.append(f"part of {len(part)} more" if len(whole_runs) > 2 else f"part of {_days(len(part))}")
    text = "Away " + ", ".join(pieces)
    # The title, in date order: whole days in runs, parts of days in runs of the same parts.
    said = [(run[0], f"away all day {_run_text(run)} {month}") for run in whole_runs]
    same: List[List[Any]] = []   # [first, last, parts]
    for day in sorted(part):
        if same and same[-1][1] == day - 1 and same[-1][2] == part[day]:
            same[-1][1] = day
        else:
            same.append([day, day, part[day]])
    said += [(first, f"away the {' and '.join(parts)} of {_run_text([first, last])} {month}")
             for first, last, parts in same]
    title = "; ".join(words for _, words in sorted(said))
    return {"level": "all" if whole else "some", "text": text, "title": title[0].upper() + title[1:]}


# -- the list for a shift: the person's day ----------------------------------------

DAY_WINDOW = (6 * 60, 22 * 60)
"""The hours of the strips of a person's day when the month has no task with times: 06:00 to 22:00."""


def day_window(schedule: Schedule) -> Tuple[int, int]:
    """The hours the strips of the list for a shift show, the same for every row,
    in minutes after midnight: from the earliest start of the month's tasks with
    times (their usual times and those of a day of the month) rounded down to the
    hour, to the latest end rounded up, or to midnight when one runs past it.
    Never wider than the day; 06:00 to 22:00 when no task has times."""
    intervals = [task.interval() for task in schedule.month.tasks]
    for key in schedule.month.time_changes:
        task_id, _, iso = key.partition("|")
        if task_id in schedule.tasks and schedule.in_month(iso):
            intervals.append(schedule.task_on(task_id, iso).interval())
    timed = [interval for interval in intervals if interval is not None]
    if not timed:
        return DAY_WINDOW
    start = max(0, min(first for first, _ in timed) // 60 * 60)
    end = min(MINUTES_PER_DAY, -(-max(last for _, last in timed) // 60) * 60)
    return (start, end) if end > start else DAY_WINDOW


def _clock(minute: int) -> str:
    """"06:00", "17:30"; "midnight" at either end of the day."""
    if minute % MINUTES_PER_DAY == 0:
        return "midnight"
    return f"{minute // 60 % 24:02d}:{minute % 60:02d}"


def _parts_words(periods: List[str]) -> str:
    """"any time", "in the morning", "in the morning and afternoon"."""
    chosen = [period for period in PERIODS if period in periods]
    return f"in the {_listed(chosen)}" if chosen and ANYTIME not in periods else "any time"


def day(schedule: Schedule, person_id: str, task_id: str, iso: str, window: Tuple[int, int],
        ignore: Optional[Assignment] = None) -> Dict[str, Any]:
    """A small strip of a person's day ``iso``, for the list for a shift of
    ``task_id`` that day: ``{"slot": [from %, to %], "segments": [[kind, from %,
    to %], …], "marks": [%, %], "title": "…"}``, in percentages of ``window``
    (``day_window``), rounded to two decimals, cut to 0–100.

    ``slot`` is this shift (a task without times: the parts of the day it is for,
    "any time" the whole window); ``ignore`` is the place itself, so that the
    person on it does not see it twice. ``segments``, in the order they are drawn:
    ``away`` (the parts of the day of their reply), ``loose`` (a task of theirs
    without times, over its parts of the day), ``busy`` (another shift of theirs)
    and ``clash`` (one at the same time as this shift, as the rule of overlaps
    decides, the night before running into the morning included). Their other
    shifts are those of the day, and the part after midnight of those of the day
    before (in the month next door too), whose task the month of their date still
    has, at that day's times. ``marks``: 14:00 and 17:00, the borders of the
    parts of the day. ``title`` says it all in words."""
    low, high = window if window[1] > window[0] else DAY_WINDOW
    width = high - low

    def at(minute: float) -> float:
        return round(min(100.0, max(0.0, (minute - low) / width * 100)), 2) + 0.0   # never -0

    def spans(periods: List[str]) -> List[List[int]]:
        """The parts of the day, those next to each other together, in minutes; "any time": the window."""
        if ANYTIME in periods or not any(period in PERIODS for period in periods):
            return [[low, high]]
        bounds = (low, AFTERNOON_FROM, EVENING_FROM, high)
        runs: List[List[int]] = []
        previous = False
        for i, period in enumerate(PERIODS):
            chosen = period in periods
            if chosen and previous:
                runs[-1][1] = bounds[i + 1]
            elif chosen:
                runs.append([bounds[i], bounds[i + 1]])
            previous = chosen
        return runs

    task = schedule.task_on(task_id, iso)
    mine = task.interval()
    if mine is not None:
        slot, this_words = (mine[0], mine[1]), f"{task.start}–{task.end}"
    else:
        runs = spans(task.periods)
        slot, this_words = (runs[0][0], runs[-1][1]), _parts_words(task.periods)

    def clashes(start: int, end: int) -> bool:
        return mine is not None and mine[0] < end and start < mine[1]

    # Their other shifts: (order, words, kind, pieces in minutes of this day).
    others: List[Tuple[Tuple[int, int, str], str, str, List[List[int]]]] = []
    before = shift_date(iso, -1)
    for assignment in schedule.on_day(person_id, before):
        if assignment is ignore or not schedule.knows(assignment):
            continue
        other = schedule.task_on(assignment.task_id, before)
        span = other.interval()
        if span is None or span[1] <= MINUTES_PER_DAY:
            continue   # over before midnight
        start, end = span[0] - MINUTES_PER_DAY, span[1] - MINUTES_PER_DAY
        kind = "clash" if clashes(start, end) else "busy"
        words = f"{other.name} from {_day_name(before)} {other.start} to {other.end}"
        others.append(((0, start, other.name), words, kind, [[start, end]]))
    for assignment in schedule.on_day(person_id, iso):
        if assignment is ignore or not schedule.knows(assignment):
            continue
        other = schedule.task_on(assignment.task_id, iso)
        span = other.interval()
        if span is None:
            others.append(((2, 0, other.name), f"{other.name} ({_parts_words(other.periods)})", "loose",
                           spans(other.periods)))
            continue
        kind = "clash" if clashes(*span) else "busy"
        others.append(((1, span[0], other.name), f"{other.name} {other.start}–{other.end}", kind, [list(span)]))
    others.sort(key=lambda item: item[0])

    away = [period for period in PERIODS if schedule.is_unavailable(person_id, iso, period)]
    segments: List[List[Any]] = []

    def piece(kind: str, start: float, end: float) -> None:
        left, right = at(start), at(end)
        if right > left:
            segments.append([kind, left, right])

    for start, end in spans(away) if away else []:
        piece("away", start, end)
    for drawn in ("loose", "busy", "clash"):
        for _, _, kind, pieces in others:
            if kind == drawn:
                for start, end in pieces:
                    piece(kind, start, end)

    title = (f"{_day_name(iso)} {int(iso[8:])} {_day_month(iso)}, from {_clock(low)} to {_clock(high)}. "
             f"This shift: {this_words}. ")
    if others:
        title += "Already: " + ", ".join(
            words + (" (at the same time)" if kind == "clash" else "") for _, words, kind, _ in others) + "."
    else:
        title += "Nothing else that day."
    if len(away) == len(PERIODS):
        title += " Away all day."
    elif away:
        title += f" Away in the {_listed(away)}."
    return {
        "slot": [at(slot[0]), at(slot[1])],
        "segments": segments,
        "marks": [at(minute) for minute in (AFTERNOON_FROM, EVENING_FROM) if low < minute < high],
        "title": title,
    }
