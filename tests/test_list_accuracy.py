"""Is the list for a shift right? Every figure it shows, worked out again here.

The oracle below reads the records themselves (the months' assignments, tasks,
time changes and availability replies, the people and the settings) and never
asks ``Schedule``, ``workload`` or ``eligibility``: a mistake there cannot hide
itself. ``check_slot`` compares one slot's list (``view.candidates_view``) with
it, field by field; the tests run it on crafted cases of the tiny set and on
the demonstration month with the months next door. All names are invented.
"""

import copy
import datetime
import math
import re
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from rotaplanner import demand, view
from rotaplanner.demo import build_demo
from rotaplanner.model import (
    Assignment, Month, Person, PersonAvailability, Settings, Task, TimeChange, month_dates, week_dates,
)
from rotaplanner.month_setup import new_month
from rotaplanner.months import assign, create_month
from rotaplanner.store import Store

from . import helpers as h

MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
               "November", "December")
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
PARTS = ("morning", "afternoon", "evening")
EXACT = 1e-9   # the month's figure comes out of a division: hours that reach it are not past it


# -- the oracle: from the records alone -----------------------------------------

class Facts:
    """What a shift's figures may read: its month, and the months next door that exist."""

    def __init__(self, month: Month, people: List[Person], settings: Settings, around=()):
        self.month, self.settings = month, settings
        self.people = {person.id: person for person in people}
        self.months = {other.id: other for other in around}
        self.months[month.id] = month
        self.dates = month_dates(month.year, month.month)
        # Each person's shifts by day, in the month of their date (read once: the records are not changed).
        self.shifts: Dict[Tuple[str, str], List[Assignment]] = defaultdict(list)
        for owner in self.months.values():
            for a in owner.assignments:
                if a.person_id and a.date[:7] == owner.id:
                    self.shifts[(a.person_id, a.date)].append(a)

    def task_hours(self, task_id: str, iso: str) -> float:
        """A task's hours on a day, as the month of that date has it (0 when it no longer has it)."""
        owner = self.months[iso[:7]]
        task = next((t for t in owner.tasks if t.id == task_id), None)
        if task is None:
            return 0.0
        change = owner.time_changes.get(f"{task_id}|{iso}")
        return float(change.hours if change is not None else task.hours)

    def task_on(self, task_id: str, iso: str) -> Optional[Tuple[str, str, str, List[str]]]:
        """A task on a day, as the month of that date has it: (name, start, end, parts of the day),
        with that day's own times; None when that month does not have it (or is not there)."""
        owner = self.months.get(iso[:7])
        task = next((t for t in owner.tasks if t.id == task_id), None) if owner else None
        if task is None:
            return None
        change = owner.time_changes.get(f"{task_id}|{iso}")
        start, end = (change.start, change.end) if change is not None else (task.start, task.end)
        return task.name, start, end, list(task.periods)

    def week_days(self, iso: str) -> List[str]:
        """The days of the week (Monday to Sunday) of ``iso`` that have a rota."""
        return [day for day in week_dates(iso) if day[:7] in self.months]

    def week_hours(self, person_id: str, iso: str) -> float:
        return sum(self.task_hours(a.task_id, day) for day in self.week_days(iso)
                   for a in self.shifts[(person_id, day)])

    def month_hours(self, person_id: str) -> float:
        return sum(self.task_hours(a.task_id, iso) for iso in self.dates for a in self.shifts[(person_id, iso)])

    def weekly_target(self, person_id: str) -> float:
        own = self.people[person_id].target_hours_per_week
        return float(own if own is not None else self.settings.default_target_hours_per_week)

    def away(self, person_id: str) -> Tuple[List[int], Dict[int, List[str]]]:
        """Whole days away, and parts of days away {day: parts}, from the reply only."""
        reply = self.month.availability.get(person_id)
        keys = set(reply.unavailable) if reply else set()
        whole, part = [], {}
        for iso in self.dates:
            parts = [p for p in PARTS if f"{iso}/{p}" in keys]
            if len(parts) == 3:
                whole.append(int(iso[8:]))
            elif parts:
                part[int(iso[8:])] = parts
        return whole, part

    def month_target(self, person_id: str) -> float:
        share = 1.0
        if self.settings.prorate_target:
            whole, part = self.away(person_id)
            share = 1 - (3 * len(whole) + sum(len(p) for p in part.values())) / (3 * len(self.dates))
        return self.weekly_target(person_id) * len(self.dates) / 7 * share


def expected_segments(now: float, added: float, target: float) -> List[list]:
    """The bar from 0 to 1.5 × the target (with no target: 0 to the hours with the shift, at least 1)."""
    after = now + added
    scale = 1.5 * target if target > 0 else max(after, 1.0)

    def at(hours):
        return round(min(100.0, hours / scale * 100), 2)

    segments = []

    def put(kind, start, end):
        if at(end) > at(start):
            segments.append([kind, at(start), at(end)])

    if now > 0:
        put("now", 0, min(now, target))
    if now > target:
        put("now-over", target, now)
    if added > 0 and now < target:
        put("add", now, min(after, target))
    if after > target:
        put("add-over", max(now, target), after)
    return segments


def _clock_minutes(clock: str) -> Optional[int]:
    if not re.fullmatch(r"\d{1,2}:\d\d", clock or ""):
        return None
    hours, minutes = clock.split(":")
    return int(hours) * 60 + int(minutes)


def _times(start: str, end: str) -> Optional[Tuple[int, int]]:
    """Minutes after midnight; an end at or before the start is the next day."""
    a, b = _clock_minutes(start), _clock_minutes(end)
    if a is None or b is None:
        return None
    return (a, b + 1440) if b <= a else (a, b)


def expected_window(facts: Facts) -> Tuple[int, int]:
    """The month's tasks with times, and its days' own times: the hour before the first start to the
    hour after the last end, or midnight when one runs past it; 06:00–22:00 with none."""
    month = facts.month
    ids = {t.id for t in month.tasks}
    spans = [_times(t.start, t.end) for t in month.tasks]
    spans += [_times(change.start, change.end) for key, change in month.time_changes.items()
              if key.split("|")[0] in ids and key.split("|")[1] in facts.dates]
    spans = [span for span in spans if span]
    if not spans:
        return 360, 1320
    low = max(0, min(a for a, _ in spans) // 60 * 60)
    high = min(1440, math.ceil(max(b for _, b in spans) / 60) * 60)
    return (low, high) if high > low else (360, 1320)


def _hhmm(minute: int) -> str:
    return "midnight" if minute in (0, 1440) else f"{minute // 60:02d}:{minute % 60:02d}"


def _parts_runs(parts: List[str], low: int, high: int) -> List[Tuple[int, int]]:
    """Parts of the day as minutes (morning from the window's start, evening to its end), neighbours joined."""
    if "anytime" in parts or not any(p in PARTS for p in parts):
        return [(low, high)]
    edges = {"morning": (low, 14 * 60), "afternoon": (14 * 60, 17 * 60), "evening": (17 * 60, high)}
    runs: List[List[int]] = []
    for i, part in enumerate(PARTS):
        if part not in parts:
            continue
        if runs and i > 0 and PARTS[i - 1] in parts:
            runs[-1][1] = edges[part][1]
        else:
            runs.append(list(edges[part]))
    return [tuple(run) for run in runs]


def _part_words(parts: List[str]) -> str:
    chosen = [p for p in PARTS if p in parts]
    if "anytime" in parts or not chosen:
        return "any time"
    return "in the " + (chosen[0] if len(chosen) == 1 else f"{', '.join(chosen[:-1])} and {chosen[-1]}")


DRAWN = ("away", "loose", "busy", "clash")


def check_day(audit: Audit, where: str, shown: dict, facts: Facts, task_id: str, iso: str, person_id: str,
              current: Optional[Assignment]) -> None:
    """The strip of a person's day: its slot, every piece and its kind, the borders, and its words."""
    low, high = expected_window(facts)

    def at(minute):
        return round(min(100.0, max(0.0, (minute - low) / (high - low) * 100)), 2)

    name, start, end, parts = facts.task_on(task_id, iso)
    mine = _times(start, end)
    if mine:
        slot, this = mine, f"{start}–{end}"
    else:
        runs = _parts_runs(parts, low, high)
        slot, this = (runs[0][0], runs[-1][1]), _part_words(parts)
    audit.same(where, "day slot", [at(slot[0]), at(slot[1])], shown["slot"])
    audit.same(where, "day marks", [at(m) for m in (840, 1020) if low < m < high], shown["marks"])

    def clash(a, b):
        return mine is not None and mine[0] < b and a < mine[1]

    others = []   # (order, words, kind, pieces)
    before = (datetime.date.fromisoformat(iso) - datetime.timedelta(days=1)).isoformat()
    for a in facts.shifts[(person_id, before)]:
        task = facts.task_on(a.task_id, before)
        span = _times(task[1], task[2]) if task else None
        if span and span[1] > 1440:
            a_, b_ = span[0] - 1440, span[1] - 1440
            weekday = DAY_NAMES[datetime.date.fromisoformat(before).weekday()]
            others.append(((0, a_, task[0]), f"{task[0]} from {weekday} {task[1]} to {task[2]}",
                           "clash" if clash(a_, b_) else "busy", [(a_, b_)]))
    for a in facts.shifts[(person_id, iso)]:
        task = facts.task_on(a.task_id, iso)
        if a is current or task is None:
            continue
        span = _times(task[1], task[2])
        if span:
            others.append(((1, span[0], task[0]), f"{task[0]} {task[1]}–{task[2]}",
                           "clash" if clash(*span) else "busy", [span]))
        else:
            others.append(((2, 0, task[0]), f"{task[0]} ({_part_words(task[3])})", "loose",
                           _parts_runs(task[3], low, high)))
    others.sort(key=lambda item: item[0])
    reply = facts.month.availability.get(person_id)
    away = [p for p in PARTS if reply and f"{iso}/{p}" in reply.unavailable]

    expected = [["away", at(a), at(b)] for a, b in (_parts_runs(away, low, high) if away else [])]
    expected += [[kind, at(a), at(b)] for _, _, kind, pieces in others for a, b in pieces]
    expected = [piece for piece in expected if piece[2] > piece[1]]
    audit.same(where, "day pieces", sorted(expected), sorted(shown["segments"]))
    order = [DRAWN.index(kind) for kind, _, _ in shown["segments"]]
    audit.same(where, "day pieces drawn away, loose, busy, clash", sorted(order), order)

    date = datetime.date.fromisoformat(iso)
    title = (f"{DAY_NAMES[date.weekday()]} {date.day} {MONTH_NAMES[date.month - 1]}, from {_hhmm(low)} to "
             f"{_hhmm(high)}. This shift: {this}. ")
    title += ("Already: " + ", ".join(words + (" (at the same time)" if kind == "clash" else "")
                                      for _, words, kind, _ in others) + "."
              if others else "Nothing else that day.")
    if len(away) == 3:
        title += " Away all day."
    elif away:
        title += " Away in the " + " and ".join(away) + "."
    audit.same(where, "day words", title, shown["title"])


# -- comparing ---------------------------------------------------------------------

class Audit:
    """Comparisons made, mismatches with the oracle, and figures whose colour the rounded numbers contradict."""

    def __init__(self):
        self.comparisons = 0
        self.mismatches: List[str] = []
        self.rounding: List[str] = []

    def same(self, where: str, what: str, expected, shown) -> None:
        self.comparisons += 1
        if isinstance(expected, float) or isinstance(shown, float):
            ok = expected is not None and shown is not None and abs(expected - shown) < 1e-6
        else:
            ok = expected == shown
        if not ok:
            self.mismatches.append(f"{where}: {what}: expected {expected!r}, shown {shown!r}")


NUMBER = r"(\d+(?:\.\d+)?)"


def _number(text: str) -> Optional[float]:
    found = re.search(NUMBER, text)
    return float(found.group(1)) if found else None


def check_line(audit: Audit, where: str, line: dict, now: float, added: float, target: float, digits: int,
               held: bool) -> None:
    """One bar line (week or month): figures, colour, bar, target line."""
    after = now + added
    over = after > target + EXACT
    figures = line["figures"]
    kinds = [kind for kind, _ in figures]
    expected_kinds = (["was"] if added > 0 and not held else []) + ["after-over" if over else "after", "of"]
    audit.same(where, "figure kinds", expected_kinds, kinds)
    texts = dict(figures)
    if "was" in texts and "was" in expected_kinds:
        audit.same(where, "hours so far", round(now, 2), _number(texts["was"]))
    bold = texts.get("after-over", texts.get("after", ""))
    audit.same(where, "hours with the shift", round(after, 2), _number(bold))
    of = texts.get("of", "")
    audit.same(where, "target", round(target, digits) if target > 0 else None, _number(of))
    audit.same(where, "segments", expected_segments(now, added, target), line["segments"])
    audit.same(where, "target line", 66.67 if target > 0 else None, line["target_at"])
    # On screen: an amber figure shows a bold number above the target shown, and only then.
    bar_over = any(kind.endswith("-over") for kind, _, _ in line["segments"])
    figure_over = "after-over" in kinds
    if bar_over != figure_over:
        audit.rounding.append(f"{where}: bar over {bar_over}, figure over {figure_over} ({bold} / {of.strip()})")
    shown_target = _number(of) if target > 0 else 0.0
    if _number(bold) is not None and (figure_over != (_number(bold) > shown_target)):
        audit.rounding.append(f"{where}: {'amber' if figure_over else 'plain'} '{bold}{of}' "
                              f"(hours {after!r}, target {target!r})")


def _span_words(days: List[str]) -> Tuple[str, str]:
    first, last = days[0], days[-1]
    start = f"{DAY_NAMES[week_dates(first).index(first)]} {int(first[8:])}"
    end = f"{DAY_NAMES[week_dates(last).index(last)]} {int(last[8:])} {MONTH_NAMES[int(last[5:7]) - 1]}"
    return start, end


def _title_pattern(month_name: str, held: bool) -> "re.Pattern":
    def so_far(p):
        if held:
            return rf"(?P<{p}total>{NUMBER[1:-1]}) h(?:, (?P<{p}part>{NUMBER[1:-1]}) h of them this shift)?"
        return rf"(?P<{p}now>{NUMBER[1:-1]}) h so far(?:, (?P<{p}after>{NUMBER[1:-1]}) h with this shift)?"
    return re.compile(
        rf"^Week of (?P<span>.+?): {so_far('w')}, for a target of (?P<wt>{NUMBER[1:-1]}) h a week\."
        rf"(?P<cut> The week (?:began|goes on) in \w+, which has no rota yet: those days are not counted\.)? "
        rf"{month_name}: {so_far('m')}"
        rf"(?:, of (?P<mt>{NUMBER[1:-1]}) h\.|(?P<awayall>; away all month, so no target\.)"
        rf"|(?P<zero>, for a target of 0 h\.))$")


def check_title(audit: Audit, where: str, title: str, facts: Facts, iso: str, week_now: float, month_now: float,
                added: float, week_target: float, month_target: float, held: bool) -> None:
    found = _title_pattern(MONTH_NAMES[facts.month.month - 1], held).match(title)
    audit.same(where, "title readable", True, found is not None)
    if found is None:
        audit.mismatches.append(f"{where}: title {title!r}")
        return
    days = facts.week_days(iso)
    start, end = _span_words(days)
    audit.same(where, "title week", True, found["span"].startswith(start) and found["span"].endswith(end))
    audit.same(where, "title cut week", len(days) < 7, found["cut"] is not None)
    for p, now in (("w", week_now), ("m", month_now)):
        if held:
            audit.same(where, f"title {p} total", round(now + added, 2), float(found[f"{p}total"]))
            audit.same(where, f"title {p} this shift", round(added, 2) if added > 0 else None,
                       float(found[f"{p}part"]) if found[f"{p}part"] else None)
        else:
            audit.same(where, f"title {p} so far", round(now, 2), float(found[f"{p}now"]))
            audit.same(where, f"title {p} with the shift", round(now + added, 2) if added > 0 else None,
                       float(found[f"{p}after"]) if found[f"{p}after"] else None)
    audit.same(where, "title week target", round(week_target, 2), float(found["wt"]))
    if month_target > 0:
        audit.same(where, "title month target", round(month_target, 2), float(found["mt"]) if found["mt"] else None)
    else:
        audit.same(where, "title no month target", "away" if week_target > 0 else "zero",
                   "away" if found["awayall"] else "zero" if found["zero"] else found["mt"])


def _runs(days: List[int]) -> List[List[int]]:
    runs: List[List[int]] = []
    for day in days:
        if runs and runs[-1][1] == day - 1:
            runs[-1][1] = day
        else:
            runs.append([day, day])
    return runs


def _run(run: List[int]) -> str:
    return f"{run[0]}–{run[1]}" if run[0] != run[1] else str(run[0])


def check_away(audit: Audit, where: str, away: dict, facts: Facts, person_id: str) -> None:
    """The level, and every day the title names, against the reply; the short words name or count them."""
    whole, part = facts.away(person_id)
    audit.same(where, "away level", "all" if whole else "some" if part else "", away["level"])
    named_whole, named_part = [], {}
    month_name = MONTH_NAMES[facts.month.month - 1]
    for piece in filter(None, away["title"].split("; ")):
        found = re.match(rf"^[Aa]way all day (\d+)(?:–(\d+))? {month_name}$", piece)
        if found:
            named_whole += range(int(found[1]), int(found[2] or found[1]) + 1)
            continue
        found = re.match(rf"^[Aa]way the ((?:morning|afternoon|evening)(?: and (?:afternoon|evening))*) of "
                         rf"(\d+)(?:–(\d+))? {month_name}$", piece)
        if found is None:
            audit.mismatches.append(f"{where}: away title piece {piece!r}")
            continue
        for day in range(int(found[2]), int(found[3] or found[2]) + 1):
            named_part[day] = found[1].split(" and ")
    audit.same(where, "away whole days", whole, sorted(named_whole))
    audit.same(where, "away parts of days", part, named_part)
    if not whole and not part:
        audit.same(where, "away words", "", away["text"])
        return
    text, short = away["text"], month_name[:3]
    whole_runs, part_runs = _runs(whole), _runs(sorted(part))
    if len(whole_runs) > 2:
        words = [f"{len(whole)} days"]
    else:
        words = [_run(run) for run in whole_runs]
    if len(part_runs) == 1:
        words.append(f"part of {_run(part_runs[0])} {short}")
    elif part_runs:
        words.append(f"part of {len(part)} more" if len(whole_runs) > 2 else f"part of {len(part)} days")
    audit.same(where, "away words", True, text.startswith("Away ") and all(w in text for w in words))


def check_slot(audit: Audit, facts: Facts, task_id: str, iso: str, position: int, catalog=None) -> dict:
    """One slot's list against the oracle. Returns what the program showed."""
    month = facts.month
    shown = view.candidates_view(month, list(facts.people.values()), facts.settings, task_id, iso, position,
                                 catalog=catalog, around=[m for k, m in facts.months.items() if k != month.id])
    slot = f"{task_id}|{iso}|{position}"
    current = next((a for a in month.assignments if (a.task_id, a.date, a.position) == (task_id, iso, position)), None)
    holder_id = current.person_id if current is not None else ""
    listed = facts.people.get(holder_id) if holder_id else None
    if listed is not None and listed.archived:
        listed = None

    # The list: everyone not archived but the person on the shift, who is shown above it.
    expected_ids = sorted(p.id for p in facts.people.values() if not p.archived and p.id != holder_id)
    audit.same(slot, "people in the list", expected_ids, sorted(c["id"] for c in shown["candidates"]))
    audit.same(slot, "person on the shift", listed.id if listed else None,
               shown["current_person"]["id"] if shown["current_person"] else None)
    if current is None:
        name = None
    elif not current.person_id:
        name = current.guest or ("Volunteer" if current.guest_kind == "volunteer" else "Guest")
    elif current.person_id in facts.people:
        name = facts.people[current.person_id].name
    else:
        name = "Someone no longer on the list"
    audit.same(slot, "name of whoever holds it", name, shown["current_name"])

    added = facts.task_hours(task_id, iso)
    audit.same(slot, "the shift's hours", added, float(shown["task"]["hours"]))
    entries = list(shown["candidates"]) + ([shown["current_person"]] if shown["current_person"] else [])
    for entry in entries:
        person_id = entry["id"]
        where = f"{slot} {person_id}"
        held = person_id == holder_id
        week_now, month_now = facts.week_hours(person_id, iso), facts.month_hours(person_id)
        if held:   # their hours without this shift
            week_now, month_now = week_now - added, month_now - added
        week_target, month_target = facts.weekly_target(person_id), facts.month_target(person_id)
        load = entry["load"]
        check_line(audit, f"{where} week", load["week"], week_now, added, week_target, 2, held)
        check_line(audit, f"{where} month", load["month"], month_now, added, month_target, 2, held)
        check_title(audit, where, load["title"], facts, iso, week_now, month_now, added, week_target, month_target,
                    held)
        check_away(audit, where, entry["away"], facts, person_id)
        audit.same(where, "over-target note left out", [],
                   [f["message"] for f in entry["findings"] if f["message"].startswith("Over target")])
        check_day(audit, f"{where} day", entry["day"], facts, task_id, iso, person_id, current)
    return shown


def every_slot(month: Month) -> List[Tuple[str, str, int]]:
    """Every place of the month's rota to fill: those required, and any filled beyond them."""
    count = dict(demand.required(month))
    for a in month.assignments:
        key = (a.task_id, a.date)
        count[key] = max(count.get(key, 0), a.position + 1)
    tasks = {t.id for t in month.tasks}
    return sorted((task_id, iso, position) for (task_id, iso), n in count.items() if task_id in tasks
                  for position in range(n))


# -- the demonstration month, with the months next door ----------------------------

def demo_with_neighbours(folder: str):
    """The demo December, with a November and a January filled a little around it, and one of each
    special case. Returns (december, [november, january], people, settings, catalog)."""
    store = Store(Path(folder))
    build_demo(store)
    catalog, people, settings = store.load_catalog(), store.load_people(), store.load_settings()
    december = store.load_month("2026-12")
    november = new_month(catalog, catalog, 2026, 11, people, settings, around=[december])
    january = new_month(catalog, catalog, 2027, 1, people, settings, around=[december])
    active = [p for p in people if p.active]

    def place(month, task_id, iso, person_id="", **more):
        position = next(i for i in range(9) if month.find_assignment(task_id, iso, i) is None)
        around = [m for m in (november, december, january) if m is not month]
        return assign(month, people, settings, task_id, iso, position, person_id=person_id, force=True,
                      around=around, **more)

    # Shifts on the nearby days, by people who also work in December's first and last weeks.
    for i, iso in enumerate(("2026-11-30", "2026-11-30", "2026-11-29", "2026-11-28")):
        place(november, "task-shop-morning-shift", iso, active[i].id)
    for i, iso in enumerate(("2027-01-01", "2027-01-02", "2027-01-03", "2027-01-03", "2027-01-04")):
        place(january, "task-visitor-hosting-morning", iso, active[10 + i].id)
    place(november, "task-shop-afternoon-shift", "2026-11-30", guest_kind="volunteer")
    # Times changed: in December, and on nearby days in the months next door (their own).
    december.time_changes["task-lunch-cook|2026-12-08"] = TimeChange("09:00", "15:00", 6.0)
    december.time_changes["task-supper-cook|2026-12-31"] = TimeChange("15:00", "19:30", 4.5)
    november.time_changes["task-shop-morning-shift|2026-11-30"] = TimeChange("09:00", "14:30", 5.5)
    january.time_changes["task-visitor-hosting-morning|2027-01-02"] = TimeChange("10:00", "11:00", 1.0)
    # A shift whose task its month no longer has.
    place(january, "task-front-desk-morning", "2027-01-03", active[12].id)
    january.tasks = [t for t in january.tasks if t.id != "task-front-desk-morning"]
    # The New Year retreat's tasks, in both months (January's copy of one with other hours: a month owns its tasks).
    retreat = [t for t in december.tasks if t.event_id == "event-new-year"]
    january.events.append(copy.deepcopy(next(e for e in december.events if e.id == "event-new-year")))
    january.tasks += [copy.deepcopy(t) for t in retreat]
    january.tasks[-1].hours = 2.0
    place(december, retreat[-1].id, "2026-12-30", active[40].id)
    january.assignments.append(Assignment(retreat[-1].id, "2027-01-02", 0, person_id=active[40].id))
    # A night task of no hours.
    december.tasks.append(Task("task-night-watch", "area-hosting", "Night watch", periods=["evening"],
                               start="22:00", end="07:00", hours=0, headcount={"default": 1}))
    for i, iso in enumerate(("2026-12-01", "2026-12-15", "2026-12-31")):
        december.assignments.append(Assignment("task-night-watch", iso, 0, person_id=active[20 + i].id))
    # Away all month, and assigned anyway; a target of 0; parts of days only.
    gone, nothing, parts = active[30], active[31], active[32]
    december.availability[gone.id] = PersonAvailability(
        [f"{iso}/{p}" for iso in month_dates(2026, 12) for p in PARTS])
    place(december, "task-shop-afternoon-shift", "2026-12-09", gone.id, reason="Back for the day")
    nothing.target_hours_per_week = 0
    place(december, "task-shop-afternoon-shift", "2026-12-10", nothing.id)
    december.availability[parts.id] = PersonAvailability(
        ["2026-12-03/evening", "2026-12-04/evening", "2026-12-09/morning", "2026-12-09/afternoon"])
    # Someone archived who still holds shifts; someone no longer on the list; a volunteer with no name.
    holder = next(p for p in active if any(a.person_id == p.id for a in december.assignments))
    holder.archived = True
    december.assignments.append(Assignment("task-shop-morning-shift", "2026-12-12", 5, person_id="person-gone"))
    place(december, "task-shop-morning-shift", "2026-12-13", guest_kind="volunteer")
    return december, [november, january], people, settings, catalog


# -- the tests ---------------------------------------------------------------------

def _settings(**changes) -> Settings:
    return Settings(default_target_hours_per_week=changes.pop("week", 2.75), **changes)


class ListAccuracyTest(unittest.TestCase):
    """Crafted cases of the tiny set: each checks the whole list against the oracle, and one figure by hand."""

    def setUp(self):
        self.catalog = h.catalog()
        self.people = h.people()
        self.december = h.month(self.catalog)

    def put(self, month, task_id, iso, person_id="", position=0, **more):
        month.assignments.append(Assignment(task_id, iso, position, person_id=person_id, **more))

    def audit(self, task_id, iso, position=0, settings=None, around=()):
        audit = Audit()
        facts = Facts(self.december, self.people, settings or _settings(), around)
        shown = check_slot(audit, facts, task_id, iso, position)
        self.assertEqual(audit.mismatches, [])
        self.assertEqual(audit.rounding, [])   # an amber figure never shows a number at or under its target
        self.assertGreater(audit.comparisons, 50)
        return shown, audit

    @staticmethod
    def entry(shown, person_id):
        return next(c for c in shown["candidates"] + [shown["current_person"]] if c and c["id"] == person_id)

    @staticmethod
    def figures(line):
        return "".join(text for _, text in line["figures"])

    def test_the_week_counts_the_days_of_the_month_before(self):
        november = create_month(self.catalog, 2026, 11)
        self.put(november, "lunch", "2026-11-30", "ana")
        november.time_changes["lunch|2026-11-30"] = TimeChange("09:00", "15:00", 6.0)   # November's own
        self.put(self.december, "lunch", "2026-12-01", "ana")
        shown, _ = self.audit("lunch", "2026-12-02", around=[november])
        self.assertEqual(self.figures(self.entry(shown, "ana")["load"]["week"]), "10 → 14 of 2.75 h")
        self.assertEqual(self.figures(self.entry(shown, "ana")["load"]["month"]), "4 → 8 of 12.18 h")

    def test_without_the_month_before_the_week_is_cut(self):
        self.put(self.december, "lunch", "2026-12-01", "ana")
        shown, _ = self.audit("lunch", "2026-12-02")
        self.assertIn("which has no rota yet", self.entry(shown, "ana")["load"]["title"])
        self.assertEqual(self.figures(self.entry(shown, "ana")["load"]["week"]), "4 → 8 of 2.75 h")

    def test_the_week_counts_the_days_of_the_month_after_with_its_own_tasks(self):
        january = create_month(self.catalog, 2027, 1)
        next(t for t in january.tasks if t.id == "breakfast").hours = 5   # January owns its tasks
        self.put(january, "breakfast", "2027-01-02", "ben")
        self.put(january, "helper", "2027-01-03", "", guest="Pat (guest)")        # counts for nobody
        self.put(self.december, "helper", "2026-12-28", "", guest_kind="volunteer")
        shown, _ = self.audit("lunch", "2026-12-31", around=[january])
        self.assertEqual(self.figures(self.entry(shown, "ben")["load"]["week"]), "5 → 9 of 2.75 h")

    def test_a_task_the_month_no_longer_has_counts_nothing(self):
        january = create_month(self.catalog, 2027, 1)
        self.put(january, "breakfast", "2027-01-02", "ben")
        january.tasks = [t for t in january.tasks if t.id != "breakfast"]
        self.put(self.december, "breakfast", "2026-12-29", "ben")
        self.december.tasks = [t for t in self.december.tasks if t.id != "breakfast"]
        shown, _ = self.audit("lunch", "2026-12-31", around=[january])
        self.assertEqual(self.figures(self.entry(shown, "ben")["load"]["week"]), "0 → 4 of 2.75 h")

    def test_a_time_change_on_the_shifts_day_is_what_it_adds(self):
        self.december.time_changes["lunch|2026-12-10"] = TimeChange("09:00", "15:30", 6.5)
        self.december.time_changes["lunch|2026-12-09"] = TimeChange("09:00", "10:00", 1.0)
        self.put(self.december, "lunch", "2026-12-09", "ana")
        shown, _ = self.audit("lunch", "2026-12-10")
        self.assertEqual(self.figures(self.entry(shown, "ana")["load"]["week"]), "1 → 7.5 of 2.75 h")

    def test_a_shift_of_no_hours_adds_nothing(self):
        self.put(self.december, "night", "2026-12-08", "cleo")
        shown, _ = self.audit("night", "2026-12-09")
        load = self.entry(shown, "cleo")["load"]
        self.assertEqual(self.figures(load["week"]), "0 of 2.75 h")
        self.assertEqual(load["week"]["segments"], [])

    def test_a_target_of_zero(self):
        self.people[0].target_hours_per_week = 0
        self.put(self.december, "lunch", "2026-12-01", "ana")
        shown, _ = self.audit("lunch", "2026-12-02")
        load = self.entry(shown, "ana")["load"]
        self.assertEqual(self.figures(load["month"]), "4 → 8 h")
        self.assertEqual([kind for kind, *_ in load["month"]["segments"]], ["now-over", "add-over"])
        self.assertIsNone(load["month"]["target_at"])
        self.assertIn("for a target of 0 h.", load["title"])

    def test_the_months_figure_leaves_out_the_time_away_only_when_set_to(self):
        h.away(self.december, "ana", *[f"2026-12-{d:02d}/{p}" for d in range(1, 8) for p in PARTS],
               "2026-12-20/evening")
        shown, _ = self.audit("lunch", "2026-12-10", settings=_settings(week=3.5))
        load = self.entry(shown, "ana")["load"]
        self.assertEqual(self.figures(load["month"]), "0 → 4 of 11.83 h")   # 15.5 × (93 − 22) / 93
        self.assertEqual(self.figures(load["week"]), "0 → 4 of 3.5 h")      # never lowered
        shown, _ = self.audit("lunch", "2026-12-10", settings=_settings(week=3.5, prorate_target=False))
        self.assertEqual(self.figures(self.entry(shown, "ana")["load"]["month"]), "0 → 4 of 15.5 h")

    def test_away_all_month_and_assigned_anyway(self):
        h.away(self.december, "ben", *[f"{iso}/{p}" for iso in month_dates(2026, 12) for p in PARTS])
        self.put(self.december, "lunch", "2026-12-01", "ben", forced_reason="Back for the day")
        shown, _ = self.audit("lunch", "2026-12-02")
        ben = self.entry(shown, "ben")
        self.assertEqual(self.figures(ben["load"]["month"]), "4 → 8 h")
        self.assertIn("away all month, so no target", ben["load"]["title"])
        self.assertEqual((ben["away"]["level"], ben["away"]["title"]), ("all", "Away all day 1–31 December"))

    def test_time_away_in_words(self):
        h.away(self.december, "ana", *[f"2026-12-0{d}/{p}" for d in (1, 2) for p in PARTS],
               "2026-12-09/evening", "2026-12-10/evening", "2026-12-20/morning")
        h.away(self.december, "cleo", "2026-12-05/morning", "2026-12-05/afternoon")
        shown, _ = self.audit("helper", "2026-12-12")
        self.assertEqual(self.entry(shown, "ana")["away"]["level"], "all")
        self.assertEqual(self.entry(shown, "cleo")["away"]["text"], "Away part of 5 Dec")
        self.assertEqual(self.entry(shown, "ben")["away"]["level"], "")

    def test_the_person_on_the_shift_is_above_the_list_with_hours_without_it(self):
        self.put(self.december, "lunch", "2026-12-01", "ana")
        self.put(self.december, "lunch", "2026-12-02", "ana")
        shown, _ = self.audit("lunch", "2026-12-02")
        self.assertNotIn("ana", [c["id"] for c in shown["candidates"]])
        self.assertEqual((shown["current_person"]["id"], shown["current_name"]), ("ana", "Ana B."))
        load = shown["current_person"]["load"]
        self.assertEqual(self.figures(load["week"]), "8 of 2.75 h")
        self.assertIn("8 h, 4 h of them this shift", load["title"])

    def test_whoever_holds_it_is_named_never_by_an_id(self):
        self.put(self.december, "lunch", "2026-12-02", "", guest_kind="volunteer")
        self.put(self.december, "lunch", "2026-12-03", "", guest="Pat (guest)", guest_kind="guest")
        self.put(self.december, "lunch", "2026-12-04", "gone")
        self.put(self.december, "lunch", "2026-12-05", "ben")
        self.people[1].archived = True
        for iso, name in (("2026-12-02", "Volunteer"), ("2026-12-03", "Pat (guest)"),
                          ("2026-12-04", "Someone no longer on the list"), ("2026-12-05", "Ben C."),
                          ("2026-12-06", None)):
            shown, _ = self.audit("lunch", iso)
            self.assertEqual((shown["current_name"], shown["current_person"]), (name, None))

    def test_the_over_target_note_is_left_to_the_bars(self):
        for day in range(1, 8):
            self.put(self.december, "lunch", f"2026-12-0{day}", "ana")
        shown, _ = self.audit("supper", "2026-12-09")
        self.assertEqual(self.entry(shown, "ana")["load"]["month"]["figures"][1][0], "after-over")

    def test_other_shifts_that_day(self):
        self.put(self.december, "breakfast", "2026-12-09", "ben")
        self.put(self.december, "lunch", "2026-12-09", "ben")
        shown, _ = self.audit("lunch", "2026-12-09")
        day = shown["current_person"]["day"]   # their own shift is the outline; the breakfast, another shift
        self.assertEqual([kind for kind, *_ in day["segments"]], ["busy"])
        self.assertTrue(day["title"].endswith("This shift: 09:00–13:00. Already: Breakfast 07:00–09:00."))

    def test_reaching_the_target_exactly_is_not_past_it(self):
        settings = _settings(week=h.a_week(12))   # 12 h in December
        for day in (1, 2):
            self.put(self.december, "lunch", f"2026-12-0{day}", "ana")
        shown, _ = self.audit("lunch", "2026-12-03", settings=settings)
        self.assertEqual(self.figures(self.entry(shown, "ana")["load"]["month"]), "8 → 12 of 12 h")
        self.assertEqual(self.entry(shown, "ana")["load"]["month"]["figures"][1][0], "after")
        self.put(self.december, "breakfast", "2026-12-05", "ana")
        shown, _ = self.audit("lunch", "2026-12-06", settings=settings)
        self.assertEqual(self.entry(shown, "ana")["load"]["month"]["figures"][1][0], "after-over")

    def test_far_past_the_target_the_bar_stops_at_its_end(self):
        for day in range(1, 8):
            self.put(self.december, "lunch", f"2026-12-0{day}", "ana")
        shown, _ = self.audit("supper", "2026-12-07")
        self.assertEqual(self.entry(shown, "ana")["load"]["week"]["segments"][-1][2], 100.0)


class DemoListAccuracyTest(unittest.TestCase):
    """The demonstration month with the months next door: a sample of its slots, prorating on and off."""

    def test_a_sample_of_the_demo_months_slots(self):
        with tempfile.TemporaryDirectory() as folder:
            december, around, people, settings, catalog = demo_with_neighbours(folder)
        slots = every_slot(december)
        # Weeks across the months' ends, every special case, and an even spread of the rest.
        special = [s for s in slots if s[1] in ("2026-12-01", "2026-12-31") or s[0].startswith(("task-night", "task-shop"))
                   and s[1] in ("2026-12-09", "2026-12-10", "2026-12-12", "2026-12-13", "2026-12-15")
                   or s[1] == "2026-12-30" and s[0] not in {t.id for t in december.tasks if not t.event_id}]
        sample = sorted(set(slots[::80] + special))
        for prorate in (True, False):
            audit = Audit()
            facts = Facts(december, people, _with(settings, prorate_target=prorate), around)
            for slot in sample:
                check_slot(audit, facts, *slot)
            self.assertEqual(audit.mismatches, [])
            self.assertEqual(audit.rounding, [])
            self.assertGreater(audit.comparisons, 10000)


def _with(settings: Settings, **changes) -> Settings:
    changed = copy.deepcopy(settings)
    for key, value in changes.items():
        setattr(changed, key, value)
    return changed


if __name__ == "__main__":
    unittest.main()
