"""Each person's own rota: as a printout, a message to send, a calendar file."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from ..model import Month, Person, Settings, parse_date
from ..schedule import Schedule
from . import versions
from .documents import Document, head
from .html import el, join


@dataclass
class Shift:
    key: str
    date: str
    task: str
    area: str
    time: str     # "09:00–13:00", or "Any time"
    start: str
    end: str
    hours: float
    event: str    # the event it belongs to, if a task of one event
    note: str     # a short practical note on this place
    changed: bool  # not as in the last version handed out


def shifts_of(month: Month, people: List[Person], settings: Settings) -> Dict[str, List[Shift]]:
    """Every person's shifts in the month, in date and time order."""
    schedule = Schedule(month, people, settings)
    areas = {a.id: a.name for a in month.areas}
    events = {e.id: e.name for e in month.events}
    names = {p.id: p.name for p in people}
    last = versions.reference(month)
    found: Dict[str, List[Shift]] = {}
    for assignment in schedule.all_assignments():
        if not assignment.person_id or assignment.task_id not in schedule.tasks:
            continue
        task = schedule.task_on(assignment.task_id, assignment.date)
        key = f"{assignment.task_id}|{assignment.date}|{assignment.position}"
        found.setdefault(assignment.person_id, []).append(Shift(
            key, assignment.date, task.name, areas.get(task.area_id, ""),
            f"{task.start}–{task.end}" if task.start and task.end else (task.time_label or "Any time"),
            task.start, task.end, schedule.hours_of(assignment), events.get(task.event_id or "", ""), assignment.note,
            versions.changed(last, key, versions.place_label(names.get(assignment.person_id, "?"), assignment.note))
            if last else False))
    for shifts in found.values():
        shifts.sort(key=lambda s: (s.date, s.start or "99", s.task))
    return found


def _when(iso: str) -> str:
    day = parse_date(iso)
    return f"{day.strftime('%a')} {day.day} {day.strftime('%b')}"


def _hours(value: float) -> str:
    return f"{value:g} h"


# ---- Printout -------------------------------------------------------------------


def personal_rotas(month: Month, people: List[Person], shifts: Dict[str, List[Shift]],
                   person_ids: Optional[List[str]] = None, page_each: bool = False) -> Document:
    """Each chosen person's rota (everyone with shifts by default), several to a page."""
    chosen = [p for p in people if (p.id in person_ids if person_ids else p.id in shifts)]
    chosen.sort(key=lambda p: p.name.lower())
    blocks = []
    for person in chosen:
        mine = shifts.get(person.id, [])
        rows = [el("tr", {"class_": s.changed and "changed"},
                   el("td", {"class_": "when"}, _when(s.date)),
                   el("td", {}, el("strong", {}, s.task), el("small", {}, " · ".join(x for x in (s.area, s.event, s.note) if x))),
                   el("td", {"class_": "time"}, s.time),
                   el("td", {"class_": "hours"}, _hours(s.hours) if s.hours else ""))
                for s in mine]
        total = sum(s.hours for s in mine)
        blocks.append(el("article", {"class_": "person-rota"},
                         el("h2", {}, person.name, el("span", {}, f"{len(mine)} shift{'s' if len(mine) != 1 else ''} · {_hours(round(total, 2))}")),
                         el("table", {}, rows) if rows else el("p", {"class_": "empty"}, "No shift this month.")))
    title = f"My rota — {month.title()}"
    body = el("section", {"class_": ["sheet", "paper-a4p", page_each and "page-per-person"]},
              head(title), join(blocks) if blocks else el("p", {"class_": "empty"}, "Nobody has a shift yet."))
    name = chosen[0].name if len(chosen) == 1 else "everyone"
    return Document(title, f"My rota {month.title()} - {name}", body, "people")


# ---- Message --------------------------------------------------------------------


def message(month: Month, person: Person, shifts: List[Shift], centre: str = "",
            greeting: str = "Hello {name},", closing: str = "Thank you!") -> str:
    """A plain text to paste in an e-mail or a chat message. ``greeting`` may
    contain {name} (the person's first name)."""
    first = person.first_name or person.name.split()[0]
    hello = greeting.replace("{name}", first).strip()
    lines = [hello, ""] if hello else []
    if not shifts:
        lines.append(f"You have no shift on the rota for {month.title()}.")
    else:
        lines.append(f"Here are your shifts for {month.title()}" + (f" at {centre}" if centre else "") + ":")
    for s in shifts:
        extra = f" ({s.event})" if s.event else ""
        mark = " (changed)" if s.changed else ""
        note = f" — {s.note}" if s.note else ""
        lines.append(f"• {_when(s.date)} · {s.time} · {s.task}{extra}{note}{mark}")
    if closing.strip():  # no total of hours: the message only lists the shifts
        lines += ["", closing.strip()]
    return "\n".join(lines)


# ---- Calendar file (.ics) -----------------------------------------------------------


CONTROL = re.compile("[\x00-\x09\x0b-\x1f\x7f\x85\u2028\u2029]")


def _escape(text: str) -> str:
    text = CONTROL.sub(" ", text)
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Lines longer than 75 bytes are folded, as the calendar format requires."""
    data = line.encode("utf-8")
    if len(data) <= 75:
        return line
    parts, current = [], b""
    for char in line:
        encoded = char.encode("utf-8")
        if len(current) + len(encoded) > (75 if not parts else 74):
            parts.append(current.decode("utf-8"))
            current = b""
        current += encoded
    parts.append(current.decode("utf-8"))
    return "\r\n ".join(parts)


def calendar(month: Month, person: Person, shifts: List[Shift], centre: str = "") -> str:
    """The shifts as calendar events, in local time (no time zone: what the rota says)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # METHOD:PUBLISH: "here are events", so a mail program offers to add them (not an invitation to answer).
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Rota Planner//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             f"X-WR-CALNAME:{_escape(f'Rota {month.title()}')}"]
    for s in shifts:
        day = parse_date(s.date)
        lines += ["BEGIN:VEVENT", f"UID:{re.sub('[^A-Za-z0-9_.-]', '-', s.key)}@rota-planner", f"DTSTAMP:{stamp}"]
        if s.start and s.end:
            begin = datetime.combine(day, datetime.strptime(s.start, "%H:%M").time())
            finish = datetime.combine(day, datetime.strptime(s.end, "%H:%M").time())
            if finish <= begin:
                finish += timedelta(days=1)  # runs past midnight
            lines += [f"DTSTART:{begin:%Y%m%dT%H%M%S}", f"DTEND:{finish:%Y%m%dT%H%M%S}"]
        else:
            lines += [f"DTSTART;VALUE=DATE:{day:%Y%m%d}", f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}"]
        details = " · ".join(x for x in (s.area, s.event, s.note, s.time if not (s.start and s.end) else "") if x)
        lines += [f"SUMMARY:{_escape(s.task)}", f"DESCRIPTION:{_escape(details)}"]
        if centre:
            lines.append(f"LOCATION:{_escape(centre)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
