"""The Check step: what still needs attention, and how fairly work is shared."""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterable, List

from . import checks, workload
from .eligibility import WEEKDAY_NAMES as DAY_NAMES
from .model import PERIODS, WEEKDAY_KEYS, Month, Person, Settings, parse_date, weekday_key
from .schedule import Schedule

Json = Dict[str, Any]


WEEKDAY_NAMES = ("Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays")
MOSTLY = 0.6          # share of someone's shifts on one weekday (or in one week) worth pointing out…
MOSTLY_FROM = 4       # …once they have at least this many shifts
MANY_IN_A_DAY = 3     # shifts on the same day
LONG_RUN = 6          # days in a row with a shift
WELL_OVER, WELL_UNDER = 1.15, 0.5  # hours against the target that stand out in the balance


def month_weeks(dates: List[str]) -> List[List[str]]:
    """The month's dates cut into weeks starting on Monday."""
    weeks: List[List[str]] = []
    for iso in dates:
        if not weeks or parse_date(iso).weekday() == 0:
            weeks.append([])
        weeks[-1].append(iso)
    return weeks


def days_pattern(schedule: Schedule, shifts: List[Any], weeks: List[List[str]]) -> Json:
    """How one person's shifts fall over the weekdays and the weeks of the month,
    with what is worth a look: always the same weekday, all in one week, many
    shifts the same day, many days in a row."""
    weekdays = [0] * 7
    per_day: Counter = Counter()
    for a in shifts:
        weekdays[parse_date(a.date).weekday()] += 1
        per_day[a.date] += 1
    per_week = [sum(per_day[iso] for iso in week) for week in weeks]
    total = len(shifts)
    notes = []
    if total >= MOSTLY_FROM:
        top = max(range(7), key=lambda d: weekdays[d])
        if weekdays[top] / total >= MOSTLY:
            regular = sum(1 for a in shifts if a.source == "regular" and parse_date(a.date).weekday() == top)
            notes.append(f"Mostly {WEEKDAY_NAMES[top]} ({weekdays[top]} of {total})"
                         + (", as in the regular week" if regular * 2 >= weekdays[top] else ""))
        busiest_week = max(range(len(weeks)), key=lambda w: per_week[w]) if weeks else 0
        if weeks and len(weeks[busiest_week]) < len(schedule.dates) and per_week[busiest_week] / total >= MOSTLY:
            first = parse_date(weeks[busiest_week][0])
            notes.append(f"{per_week[busiest_week]} of {total} in the week of {first.day} {first.strftime('%b')}")
    busiest, count = max(per_day.items(), key=lambda item: (item[1], item[0]), default=("", 0))
    if count >= MANY_IN_A_DAY:
        day = parse_date(busiest)
        notes.append(f"{count} shifts on {day.strftime('%a')} {day.day}")
    run = longest = 0
    for iso in schedule.dates:
        run = run + 1 if per_day[iso] else 0
        longest = max(longest, run)
    if longest >= LONG_RUN:
        notes.append(f"{longest} days in a row")
    return {"weekdays": weekdays, "weeks": per_week, "most_in_a_day": count, "busiest_day": busiest,
            "days_in_a_row": longest, "days": len(per_day), "notes": notes}


# --------------------------------------------------------------------------
# Days away, and each person's month day by day (Balance › Days and Calendar)
# --------------------------------------------------------------------------

SHORT_DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")  # not strftime: it follows the computer's language
AWAY_RULES = ("unavailable", "never")  # eligibility rules saying the person is away at that time


def day_label(iso: str) -> str:
    day = parse_date(iso)
    return f"{SHORT_DAY_NAMES[day.weekday()]} {day.day}"


def away_parts(schedule: Schedule, person: Person, iso: str) -> List[str]:
    """Parts of the day the person is away: from their reply, or a standing rule on their card."""
    weekday = weekday_key(iso)
    never = {rule.period for rule in person.never if rule.weekday == weekday}
    if "any" in never:
        return list(PERIODS)
    return [part for part in PERIODS if part in never or schedule.is_unavailable(person.id, iso, part)]


def away_words(parts: List[str]) -> str:
    return "all day" if len(parts) == len(PERIODS) else " and ".join(parts)


def away_summary(person: Person, dates: List[str], away: Dict[str, List[str]], weekday: str = "") -> Json:
    """How much of a group of days (one weekday, one week) the person is away, said in words.

    ``level``: "" (never away), "some" (away on some of the days, or only part
    of the day) or "all" (away the whole day, every one of them).
    """
    days = [iso for iso in dates if away.get(iso)]
    if not days:
        return {"level": "", "text": ""}
    rules = [rule for rule in person.never if rule.weekday == weekday] if weekday else []
    by_rule = set(PERIODS) if any(r.period == "any" for r in rules) else {r.period for r in rules}
    lines = []
    listed = [iso for iso in days if set(away[iso]) - by_rule]  # days the card's rule does not already explain
    if listed:
        lines.append(f"Away: {', '.join(f'{day_label(iso)} ({away_words(away[iso])})' for iso in listed)}")
    for rule in rules:
        when = "" if rule.period == "any" else f" {rule.period}s"
        lines.append(f"Never on {DAY_NAMES[rule.weekday]}{when} (on their card)")
    whole = len(days) == len(dates) and all(len(away[iso]) == len(PERIODS) for iso in days)
    return {"level": "all" if whole else "some", "text": "\n".join(lines)}


def calendar_row(schedule: Schedule, shifts: List[Any], away: Dict[str, List[str]], clashes: set) -> List[Json]:
    """One entry per date: how many tasks, which (for the tooltip), and whether they are away.
    ``clashes``: the dates where the checks found a task while the person is away."""
    by_day: Dict[str, List[Any]] = {}
    for a in shifts:
        by_day.setdefault(a.date, []).append(a)
    row = []
    for iso in schedule.dates:
        tasks = sorted((schedule.task_on(a.task_id, iso) for a in by_day.get(iso, [])), key=lambda t: t.start or "99")
        parts = away.get(iso, [])
        clash = iso in clashes
        lines = [day_label(iso) + (f": {len(tasks)} task{'s' if len(tasks) != 1 else ''}" if tasks else ": no task")]
        lines += [f"• {t.name}" + (f" {t.start}–{t.end}" if t.start and t.end else "") for t in tasks]
        if parts:
            lines.append(f"Away {away_words(parts)}" + (" — but has a task then" if clash else ""))
        row.append({"date": iso, "count": len(tasks), "away": ("all" if len(parts) == len(PERIODS) else "part") if parts else "",
                    "clash": clash, "text": "\n".join(lines)})
    return row


def balance_text(schedule: Schedule, figures: List[Any], balance: List[Json], settings: Settings) -> str:
    """The sentence above Balance › Hours: the means over the rows of the table
    ("On average 3.1 h a week for a target of 3.4 h · 13.5 h each in December ·
    0.7 weekend and 0.3 evening shifts each."). ``figures``: each row's average
    a week, weekly target and hours in the month, unrounded."""
    month = workload.MONTH_NAMES[schedule.month.month - 1]
    if not balance:
        return f"Nobody to compare in {month}."
    count = len(balance)
    week, target, hours = (sum(values) / count for values in zip(*figures))
    weekends = sum(p["weekends"] for p in balance) / count
    evenings = sum(p["evenings"] for p in balance) / count
    text = (f"On average {workload.number(week, 1)} h a week for a target of {workload.number(target)} h · "
            f"{workload.number(hours, 1)} h each in {month} · "
            f"{workload.number(weekends, 1)} weekend and {workload.number(evenings, 1)} evening shifts each.")
    if settings.prorate_target:
        text += " The time someone is away is not counted in their average."
    return text


def check_report(month: Month, people: List[Person], settings: Settings, previous: List[Month],
                 around: Iterable[Month] = ()) -> Json:
    """``previous``: earlier months, most recent first, for the hours history.
    ``around``: the months next door, which the rules look into (see ``Schedule``)."""
    schedule = Schedule(month, people, settings, around)
    statuses = checks.all_statuses(schedule)
    names = {p.id: p.name for p in people}

    def describe(slot, status=None) -> Json:
        task_id, iso, position = slot
        task = schedule.task_on(task_id, iso)
        assignment = schedule.at(slot)
        return {
            "key": f"{task_id}|{iso}|{position}", "date": iso, "task": task.name, "time": task.time_text(),
            # a task kept on its event's own rota: shown there, not in the month grid
            "event_id": task.event_id if not month.on_main_rota(task) else None,
            "priority": task.priority,
            "person": ((names.get(assignment.person_id, "?") if assignment.person_id else assignment.guest_label)
                       if assignment else None),
            "findings": [f.message for f in status.findings] if status else [],
            "reason": assignment.forced_reason if assignment else None,
        }

    ordered = sorted(((slot, s) for slot, s in statuses.items() if slot[0] in schedule.tasks),
                     key=lambda item: (item[0][1], item[0][0], item[0][2]))
    open_slots = schedule.open_slots()
    previous_schedules = [Schedule(m, people, settings) for m in previous]

    weeks = month_weeks(schedule.dates)
    by_weekday = [[iso for iso in schedule.dates if parse_date(iso).weekday() == i] for i in range(7)]
    balance = []
    figures = []   # each row's average a week, weekly target and hours, unrounded, for the sentence above the table
    for person in people:
        if not person.active or person.archived:
            continue
        shifts = [a for a in month.assignments if a.person_id == person.id and a.task_id in schedule.tasks]
        away = {iso: parts for iso in schedule.dates if (parts := away_parts(schedule, person, iso))}
        # Where the checks (the same rules as the picker) found a task while they are away.
        clashes = {a.date for a in shifts if any(f.rule in AWAY_RULES for f in
                   getattr(statuses.get((a.task_id, a.date, a.position)), "findings", []))}
        pattern = days_pattern(schedule, shifts, weeks)
        hours, target = schedule.hours(person.id), schedule.target(person.id)
        ratio = hours / target if target > 0 else 0.0
        balance.append({
            **pattern,
            # Hours a week against the target, the month's hours and figure, in figures and
            # words (Balance › Hours); "weeks" above stays the Days view's own.
            **workload.summary(schedule, person.id),
            # Each whole week, Monday to Sunday, with the nearby days of the months next door.
            "by_week": workload.by_week(schedule, person.id, detailed=True),
            # Hours against the target (1 = exactly the target), and whether it stands out.
            "ratio": round(ratio, 4),
            "load": "over" if ratio > WELL_OVER else "under" if ratio < WELL_UNDER else "",
            # How unevenly the shifts fall, to put first those worth a look (Balance › Days).
            "uneven": round((max(pattern["weekdays"]) / len(shifts) if len(shifts) >= MOSTLY_FROM else 0)
                            + len(pattern["notes"]), 4),
            "weekday_away": [away_summary(person, by_weekday[i], away, weekday=WEEKDAY_KEYS[i]) for i in range(7)],
            "week_away": [away_summary(person, week, away) for week in weeks],
            "calendar": calendar_row(schedule, shifts, away, clashes),
            "id": person.id,
            "name": person.name,
            "hours": round(hours, 2),
            "target": round(target, 1),
            "shifts": len(shifts),
            "weekends": schedule.weekends(person.id),
            "evenings": schedule.evenings(person.id),
            "previous": [round(s.hours(person.id), 1) for s in previous_schedules],
            "previous_week": [round(s.week_average(person.id), 1) for s in previous_schedules],
            "replied": schedule.has_replied(person.id),
        })
        figures.append((schedule.week_average(person.id), schedule.week_target(person.id), hours))

    by_day = Counter(iso for _, iso, _ in open_slots)
    return {
        "title": month.title(),
        "previous_titles": [m.title() for m in previous],
        "previous_short": [workload.MONTH_SHORT[m.month - 1] for m in previous],
        "balance_text": balance_text(schedule, figures, balance, settings),
        "calendar_weeks": workload.weeks(schedule),
        "to_fix": [describe(slot, s) for slot, s in ordered if s.state == checks.CONFLICT],
        "warnings": [describe(slot, s) for slot, s in ordered if s.state == checks.WARN],
        "overrides": [describe(slot, s) for slot, s in ordered if s.state == checks.FORCED],
        # Volunteers and guests placed before their name was known.
        "names_to_come": [describe(slot) for slot, _ in ordered if schedule.at(slot).outsider and not schedule.at(slot).guest],
        "open": sorted((describe(slot) for slot in open_slots),
                       key=lambda item: ({"critical": 0, "normal": 1, "optional": 2}.get(item["priority"], 1), item["date"])),
        "open_by_day": [{"date": iso, "label": parse_date(iso).strftime("%a %d"), "open": n} for iso, n in sorted(by_day.items())],
        "balance": balance,
        "averages": {key: round(sum(p[key] for p in balance) / max(1, len(balance)), digits)
                     for key, digits in (("hours", 2), ("target", 2), ("weekends", 1), ("evenings", 1))},
        "weeks": [{"start": week[0], "label": f"{parse_date(week[0]).day}–{parse_date(week[-1]).day}"} for week in weeks],
        "dates": [{"date": iso, "day": parse_date(iso).day, "weekday": "MTWTFSS"[parse_date(iso).weekday()],
                   "weekend": parse_date(iso).weekday() >= 5,
                   "week_start": parse_date(iso).weekday() == 0 and iso != schedule.dates[0]} for iso in schedule.dates],
    }
