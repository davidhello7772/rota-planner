"""Replies to the monthly availability form.

People say on which days they are away, per part of the day ("Tue 1, Wed 2"
in the Morning column…), and may add a comment. Replies arrive as the form's
spreadsheet (CSV or Excel) or pasted from it. ``preview`` reads them and
matches each reply to a person, flagging doubtful matches; ``apply`` saves
the replies the coordinator confirmed.

A reply is matched first by its e-mail address, compared through its
fingerprint (``emails.py``), then by the name typed. The address itself is
never kept: only its fingerprint goes into the preview.
"""

from __future__ import annotations

import calendar
import csv
import difflib
import io
import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from . import eligibility as rules
from . import emails
from .model import PERIODS, Assignment, Month, Person, PersonAvailability, availability_key
from .schedule import Schedule
from .xlsx_read import Workbook, XlsxError

Json = Dict[str, Any]
DAY = re.compile(r"\b(?:(mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?\s*)?(\d{1,2})(?:st|nd|rd|th)?\b", re.IGNORECASE)
CLOSE_MATCH = 0.75
MAX_ROWS = 2000


def norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", str(text).casefold()).split())


def parse_days(text: str, year: int, month: int) -> Tuple[List[int], int]:
    """Day numbers in "Tue 1, Wed 2, 15"; also how many named weekdays did not fit."""
    last = calendar.monthrange(year, month)[1]
    days, mismatches = [], 0
    for weekday, number in DAY.findall(text or ""):
        day = int(number)
        if not 1 <= day <= last:
            continue
        if weekday and date(year, month, day).strftime("%a").lower() != weekday[:3].lower():
            mismatches += 1
        days.append(day)
    return sorted(set(days)), mismatches


# --------------------------------------------------------------------------
# Reading the table
# --------------------------------------------------------------------------


def read_table(text: str = "", file_bytes: bytes = b"", filename: str = "") -> List[List[str]]:
    if file_bytes and filename.lower().endswith(".xlsx"):
        try:
            book = Workbook(io.BytesIO(file_bytes))
        except XlsxError as error:
            raise ValueError(str(error)) from error
        if not book.sheet_names:
            raise ValueError("This workbook has no sheet.")
        try:
            return book.sheet(book.sheet_names[0]).table()
        except XlsxError as error:
            raise ValueError(str(error)) from error
    if file_bytes:
        text = file_bytes.decode("utf-8-sig", errors="replace")
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    try:
        dialect = csv.Sniffer().sniff("\n".join(lines[:10]), delimiters="\t;,")
    except csv.Error:
        dialect = csv.excel_tab
    return list(csv.reader(io.StringIO("\n".join(lines)), dialect))


def _columns(header: List[str]) -> Dict[str, int]:
    """Which column holds the name, each part of the day, the comment, the time sent."""
    found: Dict[str, int] = {}
    for index, cell in enumerate(header):
        key = norm(cell)
        for field_name, words in (
            ("timestamp", ("timestamp", "horodateur", "date")),
            ("morning", ("morning", "matin")),
            ("afternoon", ("afternoon", "après midi", "apres midi")),
            ("evening", ("evening", "soir")),
            ("comment", ("comment", "remark", "suggestion", "commentaire")),
            # Before "name": Google Forms calls the address column "Username" or "Email address".
            ("email", ("email", "e mail", "username", "courriel", "adresse mail")),
            ("name", ("name", "nom", "who")),
        ):
            if field_name not in found and any(word in key for word in words):
                found[field_name] = index
                break
    if not ("name" in found or "email" in found) or not any(p in found for p in PERIODS):
        raise ValueError("The first row must name the columns: a name column and Morning / Afternoon / Evening.")
    return found


# --------------------------------------------------------------------------
# Matching names
# --------------------------------------------------------------------------


def match_name(text: str, people: List[Person]) -> Tuple[Optional[str], str, List[str]]:
    """(person id, "exact" | "close" | "none", suggested ids)."""
    key = norm(text)
    names = {}
    for person in people:
        for variant in (person.name, f"{person.first_name} {person.last_name}"):
            if norm(variant):
                names.setdefault(norm(variant), person.id)
    if key in names:
        return names[key], "exact", []
    first = [p.id for p in people if norm(p.first_name) == key]
    if len(first) == 1:
        return first[0], "exact", []
    close = difflib.get_close_matches(key, list(names), n=3, cutoff=CLOSE_MATCH)
    suggestions = list(dict.fromkeys(names[c] for c in close))
    if suggestions:
        return suggestions[0], "close", suggestions
    return None, "none", []


def _match_reply(name: str, email_fp: str, candidates: List[Person], by_email: Dict[str, str]
                 ) -> Tuple[Optional[str], str, List[str]]:
    """By the address first, then the name. Both known but pointing at two people: to check."""
    named, how, suggestions = match_name(name, candidates) if name else (None, "none", [])
    owner = by_email.get(email_fp) if email_fp else None
    if owner is None:
        return named, how, suggestions
    if how == "exact" and named != owner:
        return owner, "close", [owner, named]
    return owner, "exact", []


def preview(month: Month, people: List[Person], text: str = "", file_bytes: bytes = b"", filename: str = "",
            salt: bytes = b"") -> Json:
    table = read_table(text, file_bytes, filename)
    if len(table) < 2:
        raise ValueError("No replies found. Paste the whole table, with its first row.")
    cols = _columns(table[0])
    candidates = [p for p in people if not p.archived]
    by_email = {fp: pid for fp, pid in emails.owners(people).items() if pid in {p.id for p in candidates}}
    by_person: Dict[str, int] = {}
    rows: List[Json] = []
    for raw in table[1:MAX_ROWS]:
        def cell(column: str) -> str:
            index = cols.get(column)
            return raw[index].strip() if index is not None and index < len(raw) else ""

        name = cell("name")
        email_fp = emails.fingerprint(cell("email"), salt) if salt and "email" in cols else ""
        if not name and not email_fp:
            continue
        unavailable, mismatches = [], 0
        for period in PERIODS:
            days, wrong = parse_days(cell(period), month.year, month.month)
            mismatches += wrong
            unavailable += [availability_key(date(month.year, month.month, d).isoformat(), period) for d in days]
        person_id, match, suggestions = _match_reply(name, email_fp, candidates, by_email)
        owner = by_email.get(email_fp) if email_fp else None
        email_state = emails.status(next((p for p in candidates if p.id == person_id), None), email_fp, owner)
        row = {
            "name": name or "(no name)", "person_id": person_id, "match": match, "suggestions": suggestions,
            # The address is not sent: its fingerprint, and who it already belongs to.
            "email_fp": email_fp, "email_owner": owner,
            "email_status": email_state, "remember_email": emails.remember_by_default(email_state),
            "unavailable": sorted(unavailable), "comment": cell("comment"), "sent": cell("timestamp"),
            "weekday_mismatches": mismatches, "replaced": False,
        }
        # A second reply from the same person replaces the first (the form was sent again).
        if person_id in by_person:
            rows[by_person[person_id]]["replaced"] = True
        if person_id:
            by_person[person_id] = len(rows)
        rows.append(row)
    return {
        "rows": rows,
        "counts": {
            "replies": len(rows),
            "exact": sum(1 for r in rows if r["match"] == "exact" and not r["replaced"]),
            "check": sum(1 for r in rows if r["match"] == "close" and not r["replaced"]),
            "unknown": sum(1 for r in rows if r["match"] == "none"),
            "replaced": sum(1 for r in rows if r["replaced"]),
            "mismatches": sum(r["weekday_mismatches"] for r in rows),
        },
    }


def apply(month: Month, people: List[Person], replies: List[Json]) -> int:
    """Save confirmed replies ({person_id, unavailable, comment}); returns how many."""
    known = {p.id for p in people}
    valid_dates = set(month.dates())
    saved = 0
    for reply in replies[:MAX_ROWS]:
        person_id = reply.get("person_id")
        if person_id not in known:
            continue
        keys = sorted({k for k in reply.get("unavailable") or []
                       if k.partition("/")[0] in valid_dates and k.partition("/")[2] in PERIODS})
        month.availability[person_id] = PersonAvailability(unavailable=keys, comment=str(reply.get("comment") or "")[:2000])
        saved += 1
    return saved


def set_person(month: Month, people: List[Person], person_id: str, unavailable: List[str], responded: bool = True) -> bool:
    """Change one person's reply by hand."""
    if person_id not in {p.id for p in people}:
        raise ValueError("Unknown person.")
    valid_dates = set(month.dates())
    keys = sorted({k for k in unavailable if k.partition("/")[0] in valid_dates and k.partition("/")[2] in PERIODS})
    reply = month.availability.get(person_id) or PersonAvailability()
    reply.unavailable, reply.responded = keys, responded
    month.availability[person_id] = reply
    return True


def mark_comment(month: Month, person_id: str, done: bool) -> bool:
    reply = month.availability.get(person_id)
    if reply is None or reply.comment_done == done:
        return False
    reply.comment_done = done
    return True


# --------------------------------------------------------------------------
# Comments may say why someone is away (health…): they are kept only while
# the rota needs them, then deleted everywhere, backups and undo included.
# --------------------------------------------------------------------------


def forget_comments(month: Month) -> int:
    """Delete the comments of the replies; days away stay. Returns how many there were."""
    count = 0
    for reply in month.availability.values():
        count += bool(reply.comment)
        reply.comment, reply.comment_done = "", False
    return count


def scrub_comments(document: Json) -> bool:
    """The same, on a month as stored (a backup, an undo step). Returns whether anything was removed."""
    changed = False
    for reply in (document.get("availability") or {}).values():
        if reply.get("comment"):
            reply["comment"], reply["comment_done"] = "", False
            changed = True
    return changed


def has_comments(month: Month) -> bool:
    return any(reply.comment for reply in month.availability.values())


def away_regulars(month: Month, people: List[Person], settings: Any) -> List[Assignment]:
    """Placements from the regular week on days the person said they are away.

    Forced or locked placements are the coordinator's choice and are left alone.
    """
    schedule = Schedule(month, people, settings)
    away = []
    for a in month.assignments:
        person = schedule.people.get(a.person_id)
        if a.source != "regular" or person is None or a.task_id not in schedule.tasks or a.locked or a.forced_reason is not None:
            continue
        placement = rules.Placement(schedule, person, schedule.task_on(a.task_id, a.date), a.date, ignore=a)
        if any(f.rule in ("unavailable", "never") for f in rules.evaluate(placement)):
            away.append(a)
    return away


def free_away_regulars(month: Month, people: List[Person], settings: Any) -> int:
    """Take regulars off the days they are away; their slots show "cover needed"."""
    away = away_regulars(month, people, settings)
    for assignment in away:
        month.assignments.remove(assignment)
    return len(away)


def summary(month: Month, people: List[Person]) -> Json:
    """Who replied, who did not, and the comments still to read."""
    active = sorted((p for p in people if p.active and not p.archived), key=lambda p: p.name)
    replies = month.availability
    return {
        "people": [
            {
                "id": p.id, "name": p.name, "full": f"{p.first_name} {p.last_name}".strip() or p.name,
                "has_email": bool(p.email_fp),
                "replied": p.id in replies and replies[p.id].responded,
                "unavailable": replies[p.id].unavailable if p.id in replies else [],
                "comment": replies[p.id].comment if p.id in replies else "",
                "comment_done": replies[p.id].comment_done if p.id in replies else False,
            }
            for p in active
        ],
        "dates": month.dates(),
    }
