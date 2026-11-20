"""Import the centre's current rota workbook (Google Sheets, downloaded as .xlsx).

What is read, and from where:

* people and their skills ........ "Residents" (one YES/NO column per skill)
* areas and tasks ................ the latest monthly sheet ("December 2026"):
  area in column A, task in B, times in AJ (or C), hours in D, skill in AL,
  period in AM; several rows for the same task are its positions
* how many people a day needs .... grey cells in every monthly sheet mean
  "nobody needed"; the usual numbers per weekday and on event days are the
  most common ones, and any day that differs is kept as a one-day exception
* events ......................... row 3 of each monthly sheet (merged cells = span)
* who does what .................. the names in each monthly sheet
* the regular week ............... "Weekly Assignments"
* availability ................... "Resident unavailability from the form"
* preferences .................... "Info Residents" (ratings 1 to 5)

With ``anonymous`` on, every person gets an invented name, and phone
numbers, lay names, managers, comments and guests' names are dropped. The
link between real and invented names exists only while importing.
"""

from __future__ import annotations

import calendar
import copy
import difflib
import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import IO, Dict, List, Optional, Tuple, Union

from .. import demand
from ..availability import parse_days
from ..checks import all_statuses, summarize
from ..demo import invented_names
from ..editing import rota_name
from ..model import (
    ANYTIME,
    EVENT_DAY,
    PERIODS,
    WEEKDAY_KEYS,
    Area,
    Assignment,
    Catalog,
    DayType,
    Event,
    Month,
    Person,
    PersonAvailability,
    Regular,
    Settings,
    Skill,
    Task,
    availability_key,
)
from ..schedule import Schedule
from ..xlsx_read import Sheet, Workbook, XlsxError, column_index, serial_to_date

GREY = "FFB7B7B7"
MONTH_SHEET = re.compile(r"^(%s) (\d{4})$" % "|".join(calendar.month_name[1:]))
FIRST_DAY_COL = column_index("E")
EVENTS_ROW, DATES_ROW, FIRST_TASK_ROW = 3, 2, 4
COL_AREA, COL_TASK, COL_TIME, COL_HOURS = 1, 2, 3, 4
COL_TIME_TEXT, COL_SKILL, COL_PERIOD = column_index("AJ"), column_index("AL"), column_index("AM")
TIME_RANGE = re.compile(r"(\d{1,2})[:.](\d{2})\s*-\s*(\d{1,2})[:.](\d{2})")
DAY_TOKEN = re.compile(r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\.?\s*(\d{1,2})\b", re.IGNORECASE)
NAME_SEPARATORS = re.compile(r"\s*(?:/|,|&|\+|\band\b)\s*", re.IGNORECASE)
ANYTIME_WORDS = ("any time", "anytime", "during the day")
CRITICAL_FILL_RATE, OPTIONAL_FILL_RATE = 0.95, 0.35
AREA_COLOURS = ("orange", "amber", "pink", "teal", "violet", "indigo", "sky", "green", "slate", "lime", "red")
PREFERENCE_ALIASES = (("steward", "reception"),)  # a word in an "Info Residents" header -> a word of the area it means


def area_for_header(header: str, areas: List[Area]) -> Optional[Area]:
    """The area a preference column is about: the one whose name is in the
    column's header (every word of it), or the one an alias points to."""
    for area in areas:
        words = [w for w in area.name.lower().split() if len(w) >= 3]
        if words and all(w in header for w in words):
            return area
    for word, area_word in PREFERENCE_ALIASES:
        if word in header:
            return next((a for a in areas if area_word in a.name.lower()), None)
    return None


class WorkbookImportError(Exception):
    """The workbook does not look like the centre's rota workbook."""


@dataclass
class ImportResult:
    catalog: Catalog
    people: List[Person]
    months: List[Month]
    summary: Dict[str, object] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Small text helpers
# --------------------------------------------------------------------------


def norm(text: str) -> str:
    """Comparable form of a name: lower case, punctuation and spaces folded."""
    return " ".join(re.sub(r"[^\w]+", " ", text.casefold()).split())


def tidy(text: str) -> str:
    return " ".join(text.split()).strip(" :;,-")


def base_task_name(name: str) -> Tuple[str, Optional[int]]:
    """``"Cafe Morning shift - 3"`` → ``("Cafe Morning shift", 3)``."""
    match = re.search(r"\s*-?\s*(\d+)$", name)
    if match and not re.search(r"\d\s*(am|pm)$", name, re.IGNORECASE):
        return tidy(name[: match.start()]), int(match.group(1))
    return tidy(name), None


def nice_skill(raw: str) -> str:
    """``"RECEPTION - GP"`` → ``"Reception - GP"``."""
    words = [w if (w.isupper() and len(w) <= 3 and w.isalpha()) else w.lower() for w in tidy(raw).split()]
    text = " ".join(words)
    return text[:1].upper() + text[1:]


def slug(*parts: str) -> str:
    return "-".join(norm(" ".join(parts)).split()) or "x"


def parse_times(text: str) -> Tuple[str, str, str]:
    """First "HH:MM - HH:MM" in the text as start and end; the rest as a label."""
    match = TIME_RANGE.search(text)
    if match:
        h1, m1, h2, m2 = (int(g) for g in match.groups())
        if h1 < 24 and h2 < 24 and m1 < 60 and m2 < 60:
            rest = text[: match.start()] + " " + text[match.end():]
            rest = tidy(re.sub(r"\(\s*d\s*\+\s*1\s*\)", "", rest))
            return f"{h1:02d}:{m1:02d}", f"{h2:02d}:{m2:02d}", rest
    return "", "", tidy(text)


def mode(values: List[int]) -> int:
    """Most common value; on a tie, the larger one."""
    counts = Counter(values)
    return max(counts, key=lambda v: (counts[v], v)) if counts else 0


# --------------------------------------------------------------------------
# Monthly sheets
# --------------------------------------------------------------------------


@dataclass
class TaskRows:
    """One task as it appears in a monthly sheet: its rows are its positions."""

    key: Tuple
    area: str
    manager: str
    name: str
    base: str
    time_text: str
    hours: float
    skill: str
    periods: List[str]
    rows: List[int] = field(default_factory=list)
    formula_rows: Dict[Tuple[int, int], bool] = field(default_factory=dict)


@dataclass
class MonthSheet:
    sheet: Sheet
    year: int
    month: int
    columns: Dict[int, str]          # column -> ISO date
    events: List[Event]
    groups: List[TaskRows]

    def required_rows(self, group: TaskRows, col: int) -> List[int]:
        return [r for r in group.rows if self.sheet.cell(r, col).fill != GREY]


def read_month_sheet(sheet: Sheet) -> Optional[MonthSheet]:
    first = serial_to_date(sheet.value(DATES_ROW, FIRST_DAY_COL))
    if first is None:
        return None
    columns = {}
    for col in range(FIRST_DAY_COL, FIRST_DAY_COL + 31):
        day = serial_to_date(sheet.value(DATES_ROW, col))
        if day and (day.year, day.month) == (first.year, first.month):
            columns[col] = day.isoformat()

    events = []
    for col in sorted(columns):
        name = sheet.text(EVENTS_ROW, col)
        if not name:
            continue
        span = [columns[c] for c in range(col, col + sheet.merge_span(EVENTS_ROW, col)) if c in columns]
        events.append(Event(id=f"event-{first:%Y%m}-{len(events) + 1}", name=name, start=span[0], end=span[-1]))

    groups: Dict[Tuple, TaskRows] = {}
    area, manager = None, ""
    for row in range(FIRST_TASK_ROW, sheet.max_row() + 1):
        label = sheet.text(row, COL_AREA)
        if label.startswith("("):
            manager = tidy(re.sub(r"(?i)^\(\s*manager\s*:?|\)$", "", label))
        elif label and label not in ("Area", "Events") and serial_to_date(sheet.value(row, COL_AREA)) is None:
            area, manager = tidy(label), ""
        name = sheet.text(row, COL_TASK)
        if not name or area is None or sheet.text(row, COL_HOURS) in ("Duration", "in hour"):
            continue
        base, _ = base_task_name(name)
        time_text = sheet.text(row, COL_TIME_TEXT) or " ".join(str(sheet.merged_value(row, COL_TIME) or "").split())
        hours = sheet.number(row, COL_HOURS) or 0.0
        skill = sheet.text(row, COL_SKILL)
        periods = [p for p in PERIODS if p in sheet.text(row, COL_PERIOD).lower()]
        key = (norm(area), norm(base), norm(time_text), hours, norm(skill))
        group = groups.get(key)
        if group is None:
            group = groups[key] = TaskRows(key, area, manager, name, base, time_text, hours, skill, periods)
        group.rows.append(row)
    return MonthSheet(sheet, first.year, first.month, columns, events, list(groups.values()))


# --------------------------------------------------------------------------
# The importer
# --------------------------------------------------------------------------


class _Importer:
    def __init__(self, workbook: Workbook, anonymous: bool, rng: random.Random) -> None:
        self.wb = workbook
        self.anonymous = anonymous
        self.rng = rng
        self.warnings: List[str] = []
        self.skill_ids: Dict[str, str] = {}      # norm(raw skill) -> skill id
        self.by_rota: Dict[str, str] = {}        # norm(real name on rota) -> person id
        self.by_full: Dict[str, str] = {}        # norm(real first + last) -> person id
        self.by_first: Dict[str, List[str]] = defaultdict(list)
        self.guests: Dict[str, str] = {}         # norm(real guest name) -> name shown
        self.unknown_names: Counter = Counter()

    # -- people --------------------------------------------------------------

    def read_people(self) -> Tuple[List[Skill], List[Person]]:
        if "Residents" not in self.wb.sheet_names:
            raise WorkbookImportError("No sheet named “Residents”: is this the rota workbook?")
        sheet = self.wb.sheet("Residents")
        header = {sheet.text(1, c): c for c in range(1, 80) if sheet.text(1, c)}
        try:
            col = {key: header[key] for key in ("Name", "Last name", "Name on Rota", "ACTIVE ON ROTA", "Resident")}
        except KeyError as missing:
            raise WorkbookImportError(f"The Residents sheet has no column {missing}.") from None
        skill_cols = {c: h for h, c in header.items() if c > col["Name on Rota"]}
        skills = []
        for c in sorted(skill_cols):
            skill = Skill(id=f"skill-{slug(skill_cols[c])}", name=nice_skill(skill_cols[c]))
            self.skill_ids[norm(skill_cols[c])] = skill.id
            skills.append(skill)

        people, used, seen = [], [], set()
        for row in range(2, sheet.max_row() + 1):
            first, last = sheet.text(row, col["Name"]), sheet.text(row, col["Last name"])
            on_rota = sheet.text(row, col["Name on Rota"])
            if not (first or on_rota):
                continue
            on_rota = on_rota or rota_name(first, last, used)
            if norm(on_rota) in seen:
                self.warnings.append(f"Two people share the rota name of row {row} in Residents; kept both.")
            seen.add(norm(on_rota))
            used.append(on_rota)
            person = Person(
                id=f"person-{len(people) + 1:03d}",
                name=on_rota,
                first_name=first,
                last_name=last,
                lay_name=sheet.text(row, header["Lay name"]) if "Lay name" in header else "",
                phone=sheet.text(row, header["Phone number"]) if "Phone number" in header else "",
                resident=sheet.text(row, col["Resident"]).upper() != "NO",
                active=sheet.text(row, col["ACTIVE ON ROTA"]).upper() == "YES",
                skills=[f"skill-{slug(skill_cols[c])}" for c in sorted(skill_cols) if sheet.text(row, c).upper() == "YES"],
            )
            self.by_rota.setdefault(norm(on_rota), person.id)
            self.by_full.setdefault(norm(f"{first} {last}"), person.id)
            self.by_first[norm(first)].append(person.id)
            people.append(person)
        return skills, people

    def person_for(self, text: str) -> Optional[str]:
        key = norm(text)
        if key in self.by_rota:
            return self.by_rota[key]
        if key in self.by_full:
            return self.by_full[key]
        candidates = self.by_first.get(key, [])
        return candidates[0] if len(candidates) == 1 else None

    def person_for_full_name(self, text: str) -> Optional[str]:
        """A full name typed in a form: exact, nearly exact, or first name + initial."""
        key = norm(text)
        if key in self.by_full:
            return self.by_full[key]
        close = difflib.get_close_matches(key, list(self.by_full), n=1, cutoff=0.8)
        if close:
            return self.by_full[close[0]]
        words = key.split()
        if len(words) > 1:
            initial = norm(f"{words[0]} {words[-1][:1]}")
            if initial in self.by_rota:
                return self.by_rota[initial]
        return self.person_for(text)

    def guest_name(self, text: str) -> str:
        key = norm(text)
        if key not in self.guests:
            self.guests[key] = f"Guest {len(self.guests) + 1}" if self.anonymous else tidy(text)
        return self.guests[key]

    def anonymise(self, people: List[Person], areas: List[Area]) -> None:
        used: List[str] = []
        for person, (first, last) in zip(people, invented_names(self.rng, len(people))):
            person.first_name, person.last_name = first, last
            person.name = rota_name(first, last, used)
            used.append(person.name)
            person.phone = person.lay_name = person.note = ""
        for area in areas:
            area.manager = ""

    # -- tasks ---------------------------------------------------------------

    def skill_named_after(self, task_name: str, sheet_skill: str) -> Optional[str]:
        """A skill named after the task, when the sheet's skill has nothing to do with it.

        The sheet links "Stewards" to RECEPTION although there is a RECEPTION
        STEWARD column. The sheet's skill is kept whenever it shares a word with
        the task ("Cafe Lunch shift" / CAFE SHIFT); otherwise the one skill named
        by a distinctive word of the task wins ("Garden …" alone names three).
        """
        words = {w.rstrip("s") for w in norm(task_name).split() if len(w) >= 5}
        if any(w in norm(sheet_skill).split() or f"{w}s" in norm(sheet_skill).split() for w in words):
            return None
        found = set()
        for word in words:
            skills = {skill_id for skill_name, skill_id in self.skill_ids.items()
                      if word in skill_name.split() or f"{word}s" in skill_name.split()}
            if len(skills) == 1:  # only a word naming a single skill tells anything
                found |= skills
        return found.pop() if len(found) == 1 else None

    def build_tasks(self, sheets: List[MonthSheet]) -> Tuple[List[Area], List[Task], Dict[Tuple, str]]:
        """Areas and tasks from the latest sheet, then any extra from older ones."""
        areas: Dict[str, Area] = {}
        tasks: List[Task] = []
        task_of_key: Dict[Tuple, str] = {}
        by_area_base: Dict[Tuple[str, str], str] = {}
        for month_sheet in reversed(sheets):  # latest first
            for group in month_sheet.groups:
                if group.key in task_of_key:
                    continue
                known = by_area_base.get((norm(group.area), norm(group.base)))
                if known and month_sheet is not sheets[-1]:
                    task_of_key[group.key] = known
                    continue
                area = areas.get(norm(group.area))
                if area is None:
                    area = areas[norm(group.area)] = Area(
                        id=f"area-{slug(group.area)}", name=group.area, manager=group.manager,
                        color=AREA_COLOURS[len(areas) % len(AREA_COLOURS)],
                    )
                start, end, label = parse_times(group.time_text)
                periods = group.periods or ["morning"]
                if any(word in group.time_text.lower() for word in ANYTIME_WORDS):
                    periods = [ANYTIME]
                sheet_skill = self.skill_ids.get(norm(group.skill))
                skill_id = self.skill_named_after(group.base, group.skill) or sheet_skill
                if sheet_skill and skill_id != sheet_skill:
                    self.warnings.append(
                        f"“{group.base}” now needs the skill “{nice_skill(next(k for k, v in self.skill_ids.items() if v == skill_id))}”"
                        f" (the sheet said “{group.skill}”). Change it in Tasks & Areas if that is wrong."
                    )
                if group.skill and skill_id is None:
                    self.warnings.append(f"The skill “{group.skill}” of “{group.base}” is not a column in Residents.")
                task = Task(
                    id=f"task-{slug(group.area, group.base, start)}", area_id=area.id, name=group.base,
                    periods=periods, skill_id=skill_id, start=start, end=end, time_label=label, hours=group.hours,
                )
                if any(t.id == task.id for t in tasks):
                    task.id += f"-{len(tasks)}"
                tasks.append(task)
                task_of_key[group.key] = task.id
                by_area_base.setdefault((norm(group.area), norm(group.base)), task.id)

        # Same name twice in an area (e.g. three "Stewards" shifts): add the start time.
        names = Counter((t.area_id, norm(t.name)) for t in tasks)
        for task in tasks:
            if names[(task.area_id, norm(task.name))] > 1 and task.start:
                task.name = f"{task.name} – {task.start}"
        return list(areas.values()), tasks, task_of_key

    def derive_headcounts(self, sheets: List[MonthSheet], tasks: List[Task], task_of_key: Dict[Tuple, str]) -> None:
        """Usual numbers per weekday and on event days, and a priority guess."""
        normal: Dict[str, Dict[str, List[int]]] = defaultdict(lambda: defaultdict(list))
        events: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
        filled: Counter = Counter()
        needed: Counter = Counter()
        for month_sheet in sheets:
            event_days = {iso for e in month_sheet.events for iso in month_sheet.columns.values() if e.start <= iso <= e.end}
            for group in month_sheet.groups:
                task_id = task_of_key[group.key]
                for col, iso in month_sheet.columns.items():
                    rows = month_sheet.required_rows(group, col)
                    weekday = WEEKDAY_KEYS[date.fromisoformat(iso).weekday()]
                    if iso in event_days:
                        events[task_id].append((weekday, len(rows)))
                    else:
                        normal[task_id][weekday].append(len(rows))
                    needed[task_id] += len(rows)
                    filled[task_id] += sum(1 for r in rows if month_sheet.sheet.text(r, col))

        for task in tasks:
            samples = normal[task.id]
            everything = [n for values in samples.values() for n in values]
            usual = {wd: mode(samples[wd]) if samples[wd] else mode(everything) for wd in WEEKDAY_KEYS}
            if len(set(usual.values())) == 1:
                task.headcount = {"default": usual["mon"]} if usual["mon"] else {}
            else:
                task.headcount = {wd: n for wd, n in usual.items() if n}
            higher = [n for weekday, n in events[task.id] if n > usual[weekday]]
            if higher:
                task.headcount[EVENT_DAY] = mode(higher)
            # A first guess, to be corrected in Tasks & Areas: daily tasks that
            # are always staffed look essential; rarely staffed ones optional.
            if needed[task.id]:
                rate = filled[task.id] / needed[task.id]
                if rate >= CRITICAL_FILL_RATE and "default" in task.headcount:
                    task.priority = "critical"
                elif rate <= OPTIONAL_FILL_RATE:
                    task.priority = "optional"

    # -- months --------------------------------------------------------------

    def build_month(self, month_sheet: MonthSheet, catalog: Catalog, task_of_key: Dict[Tuple, str]) -> Month:
        month = Month(
            id=Month.make_id(month_sheet.year, month_sheet.month), year=month_sheet.year, month=month_sheet.month,
            areas=copy.deepcopy(catalog.areas), tasks=copy.deepcopy(catalog.tasks), events=month_sheet.events,
        )
        tasks = {t.id: t for t in month.tasks}
        sheet = month_sheet.sheet
        for group in month_sheet.groups:
            task = tasks[task_of_key[group.key]]
            for col, iso in month_sheet.columns.items():
                required = month_sheet.required_rows(group, col)
                if len(required) != demand.rule_headcount(task, iso, month.day_type(iso)):
                    month.demand_overrides.setdefault(task.id, {})[iso] = len(required)
                # Required rows take the first positions, in sheet order.
                ordered = required + [r for r in group.rows if r not in required]
                taken: set = set()
                for row in ordered:
                    text = sheet.text(row, col)
                    if not text or text.lower() == "none":
                        continue
                    source = "regular" if sheet.cell(row, col).formula else "manual"
                    for piece in (p for p in NAME_SEPARATORS.split(text) if p.strip()):
                        position = ordered.index(row)
                        while position in taken:
                            position += 1
                        taken.add(position)
                        month.assignments.append(self.assignment(task.id, iso, position, piece, source))
        return month

    def assignment(self, task_id: str, iso: str, position: int, text: str, source: str) -> Assignment:
        person_id = self.person_for(text)
        if person_id:
            return Assignment(task_id, iso, position, person_id=person_id, source=source)
        self.unknown_names[norm(text)] += 1
        return Assignment(task_id, iso, position, guest=self.guest_name(text), source="manual")

    # -- regular week --------------------------------------------------------

    def read_regulars(self, tasks: List[Task]) -> List[Regular]:
        if "Weekly Assignments" not in self.wb.sheet_names:
            self.warnings.append("No “Weekly Assignments” sheet: no regular week imported.")
            return []
        sheet = self.wb.sheet("Weekly Assignments")
        header_row = next((r for r in range(1, 10) if sheet.text(r, 3).lower() == "monday"), None)
        if header_row is None:
            self.warnings.append("The regular week sheet has no Monday…Sunday header.")
            return []
        days = {c: WEEKDAY_KEYS[i] for i, c in enumerate(range(3, 10))}
        bases = {t.id: norm(base_task_name(t.name.split(" – ")[0])[0]) for t in tasks}
        regulars: List[Regular] = []
        seen: Counter = Counter()
        unmatched_tasks, unmatched_people = set(), 0
        for row in range(header_row + 1, sheet.max_row() + 1):
            raw = sheet.text(row, 2)
            if not raw or raw.lower() == "none":
                continue
            base, number = base_task_name(raw)
            task_id = self.match_task(norm(base), bases)
            if task_id is None:
                if any(sheet.text(row, c) not in ("", "None") for c in days):
                    unmatched_tasks.add(base)
                continue
            position = number - 1 if number else seen[task_id]
            seen[task_id] += 1
            for col, weekday in days.items():
                text = sheet.text(row, col)
                if not text or text.lower() == "none":
                    continue
                person_id = self.person_for(text)
                if person_id is None:
                    unmatched_people += 1
                    continue
                regulars.append(Regular(id=f"regular-{len(regulars) + 1:03d}", person_id=person_id,
                                        task_id=task_id, weekday=weekday, position=position))
        for base in sorted(unmatched_tasks):
            self.warnings.append(f"Regular week: no task matches “{base}”.")
        if unmatched_people:
            self.warnings.append(f"Regular week: {unmatched_people} name(s) not found in Residents were left out.")
        return regulars

    @staticmethod
    def match_task(base: str, bases: Dict[str, str]) -> Optional[str]:
        exact = [tid for tid, b in bases.items() if b == base]
        if exact:
            return exact[0]
        contained = [tid for tid, b in bases.items() if base and (base in b or b in base)]
        if len(contained) == 1:
            return contained[0]
        best = difflib.get_close_matches(base, list(bases.values()), n=1, cutoff=0.75)
        return next((tid for tid, b in bases.items() if best and b == best[0]), None)

    # -- availability and preferences ----------------------------------------

    def read_availability(self, months: List[Month]) -> Optional[str]:
        name = self.wb.find_sheet("Resident unavailability")
        if name is None or not months:
            return None
        sheet = self.wb.sheet(name)
        header = {sheet.text(1, c).lower(): c for c in range(1, 12) if sheet.text(1, c)}
        cols = {p: header.get(p) for p in PERIODS}
        rows = [r for r in range(2, sheet.max_row() + 1) if sheet.text(r, header.get("name", 1))]
        tokens = [t for r in rows for p in PERIODS if cols[p] for t in DAY_TOKEN.findall(sheet.text(r, cols[p]))]

        def fits(month: Month) -> int:
            last = calendar.monthrange(month.year, month.month)[1]
            return sum(1 for wd, d in tokens if 1 <= int(d) <= last
                       and date(month.year, month.month, int(d)).strftime("%a").lower() == wd[:3].lower())

        target = max(months, key=fits)
        if not tokens or fits(target) < 0.8 * len(tokens):
            self.warnings.append("Availability replies: could not tell which month they are for; not imported.")
            return None

        unknown = 0
        for row in rows:  # a later reply replaces an earlier one
            person_id = self.person_for(sheet.text(row, header.get("name", 1)))
            if person_id is None:
                unknown += 1
                continue
            away = []
            for period in PERIODS:
                days, _ = parse_days(sheet.text(row, cols[period]) if cols[period] else "", target.year, target.month)
                away += [availability_key(date(target.year, target.month, d).isoformat(), period) for d in days]
            comment = "" if self.anonymous else sheet.text(row, header.get("comment", 5))
            target.availability[person_id] = PersonAvailability(unavailable=sorted(set(away)), comment=comment)
        if unknown:
            self.warnings.append(f"Availability: {unknown} reply(ies) from names not in Residents were left out.")
        return target.id

    def read_preferences(self, people: Dict[str, Person], areas: List[Area]) -> int:
        name = self.wb.find_sheet("Info Residents")
        if name is None:
            return 0
        sheet = self.wb.sheet(name)
        header = {c: sheet.text(1, c).lower() for c in range(1, 40) if sheet.text(1, c)}
        name_col = next((c for c, h in header.items() if h == "full name"), None)
        if name_col is None:
            return 0
        area_for_col = {}
        for c, h in header.items():
            area = area_for_header(h, areas)
            if area is not None:
                area_for_col[c] = area.id
        count = 0
        for row in range(2, sheet.max_row() + 1):
            if not sheet.text(row, name_col):
                continue
            person_id = self.person_for_full_name(sheet.text(row, name_col))
            if person_id is None:
                continue
            ratings: Dict[str, int] = {}
            for c, area_id in area_for_col.items():
                value = sheet.number(row, c)
                if value and 1 <= value <= 5:
                    ratings[area_id] = max(ratings.get(area_id, 0), int(value))
            if ratings:
                people[person_id].preferences = ratings
                count += 1
        return count

    # -- all together --------------------------------------------------------

    def run(self) -> ImportResult:
        skills, people = self.read_people()
        sheets = []
        for name in self.wb.sheet_names:
            if MONTH_SHEET.match(name):
                month_sheet = read_month_sheet(self.wb.sheet(name))
                if month_sheet and month_sheet.groups:
                    sheets.append(month_sheet)
        if not sheets:
            raise WorkbookImportError("No monthly sheet (such as “December 2026”) with tasks was found.")
        sheets.sort(key=lambda s: (s.year, s.month))

        areas, tasks, task_of_key = self.build_tasks(sheets)
        self.derive_headcounts(sheets, tasks, task_of_key)
        catalog = Catalog(skills=skills, areas=areas, tasks=tasks, day_types=[DayType(EVENT_DAY, "Event day")])
        months = [self.build_month(s, catalog, task_of_key) for s in sheets]
        catalog.regulars = self.read_regulars(tasks)
        availability_month = self.read_availability(months)
        preferences = self.read_preferences({p.id: p for p in people}, areas)
        if self.unknown_names:
            self.warnings.append(
                f"{len(self.unknown_names)} name(s) in the monthly sheets are not in Residents; "
                "they were kept as people not on the list."
            )
        if self.anonymous:
            self.anonymise(people, areas)
            for month in months:
                for area in month.areas:
                    area.manager = ""

        summary = {
            "anonymous": self.anonymous,
            "people": len(people),
            "active": sum(p.active for p in people),
            "skills": len(skills),
            "areas": len(areas),
            "tasks": len(tasks),
            "regulars": len(catalog.regulars),
            "preferences": preferences,
            "availability_month": availability_month,
            "availability_replies": len(next((m.availability for m in months if m.id == availability_month), {})),
            "months": [_month_summary(m, people) for m in months],
        }
        return ImportResult(catalog, people, months, summary, list(dict.fromkeys(self.warnings)))


def _month_summary(month: Month, people: List[Person]) -> Dict[str, object]:
    schedule = Schedule(month, people, Settings())
    summary = summarize(schedule, all_statuses(schedule))
    hours = sum(schedule.hours(p.id) for p in people)
    return {
        "id": month.id, "title": month.title(), "required": summary.required, "filled": summary.filled,
        "hours": round(hours, 1), "events": len(month.events), "assignments": len(month.assignments),
    }


def import_workbook(source: Union[str, IO[bytes]], anonymous: bool = True, seed: Optional[int] = None) -> ImportResult:
    """Read the workbook. Nothing is saved: the caller decides what to keep."""
    try:
        return _Importer(Workbook(source), anonymous, random.Random(seed)).run()
    except XlsxError as error:
        raise WorkbookImportError(str(error)) from error
