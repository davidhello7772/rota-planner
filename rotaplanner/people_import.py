"""Adding many people at once: a pasted list, a CSV file or an Excel file.

``preview`` reads the input and says, for each line, whether it is a new
person, someone already in the list, or possibly a duplicate (a very close
name). Nothing changes until ``apply`` is called with the lines to keep.

An e-mail column is turned into fingerprints (``emails.py``) as soon as it is
read: the addresses themselves are neither returned nor kept. For someone
already in the list, the preview says whether the address is new, the same,
or different from the one known, so the coordinator chooses to remember it.
"""

from __future__ import annotations

import csv
import difflib
import io
import re
from typing import Any, Dict, List, Optional

from . import emails
from .editing import rota_name
from .model import Catalog, Person, new_id
from .xlsx_read import Workbook, XlsxError

Json = Dict[str, Any]
MAX_ROWS = 1000
MAX_CELL = 80  # longest name or cell kept from an imported list
SIMILAR = 0.85
YES = {"yes", "y", "true", "1", "x", "oui", "✓"}

HEADER_WORDS = {
    "first_name": ("first name", "firstname", "first", "given name", "prénom", "prenom", "name"),
    "last_name": ("last name", "lastname", "surname", "family name", "nom de famille", "nom"),
    "name": ("name on rota", "rota name", "short name", "display name"),
    "phone": ("phone", "phone number", "mobile", "telephone", "téléphone"),
    "email": ("email", "e mail", "email address", "e mail address", "mail", "courriel", "adresse mail", "adresse e mail"),
    "resident": ("resident", "résident"),
    "active": ("active", "active on rota"),
}


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", text.casefold()).split())


def _yes(value: str) -> bool:
    return _norm(value) in YES


# --------------------------------------------------------------------------
# Reading the input
# --------------------------------------------------------------------------


def _table_from_text(text: str) -> List[List[str]]:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    sample = "\n".join(lines[:20])
    if any(sep in sample for sep in "\t;,"):
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters="\t;,")
            return [row for row in csv.reader(io.StringIO("\n".join(lines)), dialect)]
        except csv.Error:
            pass
    return [[line] for line in lines]


def _table_from_xlsx(data: bytes) -> List[List[str]]:
    book = Workbook(io.BytesIO(data))
    if not book.sheet_names:
        raise XlsxError("This workbook has no sheet.")
    return book.sheet(book.sheet_names[0]).table()


def _header_map(row: List[str], skills: List[Any]) -> Optional[Dict[int, str]]:
    """Columns of a header row: field names, or ``skill:<id>``. None if not a header."""
    mapping: Dict[int, str] = {}
    skill_names = {_norm(s.name): s.id for s in skills}
    for index, cell in enumerate(row):
        key = _norm(cell)
        if not key:
            continue
        if key in skill_names:
            mapping[index] = f"skill:{skill_names[key]}"
            continue
        for field_name, words in HEADER_WORDS.items():
            if key in words and field_name not in mapping.values():
                mapping[index] = field_name
                break
    known = set(mapping.values())
    return mapping if known & {"first_name", "name", "last_name"} else None


def _rows(table: List[List[str]], skills: List[Any]) -> List[Json]:
    table = [[" ".join(str(c).split())[:MAX_CELL] for c in row] for row in table if any(str(c).strip() for c in row)]
    if not table:
        return []
    mapping = _header_map(table[0], skills)
    body = table[1:] if mapping else table
    rows = []
    for raw in body[:MAX_ROWS]:
        row: Json = {"first_name": "", "last_name": "", "name": "", "phone": "", "email": "", "skills": []}
        if mapping is None:
            words = (raw[0] if len(raw) == 1 else " ".join(raw[:2])).split()
            row["first_name"], row["last_name"] = (words[0] if words else ""), " ".join(words[1:])
        else:
            for index, field_name in mapping.items():
                value = raw[index] if index < len(raw) else ""
                if field_name.startswith("skill:"):
                    if _yes(value):
                        row["skills"].append(field_name[6:])
                elif field_name in ("resident", "active"):
                    row[field_name] = _yes(value) if value else True
                else:
                    row[field_name] = value
            if not row["last_name"] and " " in row["first_name"] and "name" not in mapping.values():
                row["first_name"], _, row["last_name"] = row["first_name"].partition(" ")
        rows.append(row)
    return rows


# --------------------------------------------------------------------------
# Preview and apply
# --------------------------------------------------------------------------


def preview(people: List[Person], catalog: Catalog, text: str = "", file_bytes: bytes = b"", filename: str = "",
            salt: bytes = b"") -> Json:
    if file_bytes and filename.lower().endswith(".xlsx"):
        try:
            table = _table_from_xlsx(file_bytes)
        except XlsxError as error:
            raise ValueError(str(error)) from error
    elif file_bytes:
        table = _table_from_text(file_bytes.decode("utf-8-sig", errors="replace"))
    else:
        table = _table_from_text(text)
    rows = _rows(table, catalog.skills)
    for row in rows:
        address = row.pop("email", "")
        row["email_fp"] = emails.fingerprint(address, salt) if address and salt else ""
        row["email_invalid"] = bool(address) and not row["email_fp"]
    owners = emails.owners(people)
    names_by_id = {p.id: p.name for p in people}

    names = [p.name for p in people]
    full_names = {_norm(f"{p.first_name} {p.last_name}"): p for p in people if p.first_name}
    rota_names = {_norm(p.name): p for p in people}
    result = []
    for row in rows:
        full = _norm(f"{row['first_name']} {row['last_name']}")
        if not (row["first_name"] or row["name"]):
            result.append({**row, "status": "invalid", "reason": "No name"})
            continue
        row["name"] = row["name"] or rota_name(row["first_name"], row["last_name"], names)
        same = full_names.get(full) or rota_names.get(_norm(row["name"]))
        owner = owners.get(row["email_fp"]) if row["email_fp"] else None
        if same:
            state = emails.status(same, row["email_fp"], owner)
            result.append({**row, "status": "exists", "match": same.name, "person_id": same.id, "email_status": state,
                           "remember_email": emails.remember_by_default(state),
                           "email_owner": names_by_id.get(owner) if state == "other" else None})
            continue
        row["email_owner"] = names_by_id.get(owner) if owner else None
        close = difflib.get_close_matches(full, list(full_names), n=1, cutoff=SIMILAR)
        status = {"status": "duplicate", "match": full_names[close[0]].name} if close else {"status": "new"}
        result.append({**row, **status})
        names.append(row["name"])
        rota_names[_norm(row["name"])] = Person(id="", name=row["name"])
        if row["first_name"]:
            full_names.setdefault(full, Person(id="", name=row["name"], first_name=row["first_name"], last_name=row["last_name"]))
    return {"rows": result, "counts": {s: sum(1 for r in result if r["status"] == s)
                                       for s in ("new", "exists", "duplicate", "invalid")}}


def remember_emails(people: List[Person], rows: List[Json], salt: bytes) -> int:
    """For people already in the list: keep the addresses the coordinator chose to remember."""
    known = {p.id for p in people}
    return sum(emails.remember(people, str(row.get("person_id")), row.get("email_fp"), salt)
               for row in rows[:MAX_ROWS]
               if row.get("status") == "exists" and row.get("remember_email") and row.get("person_id") in known)


def apply(people: List[Person], catalog: Catalog, rows: List[Json], salt: bytes = b"") -> List[Person]:
    """Create the people in ``rows`` (as returned by ``preview``, after the user's choices)."""
    skill_ids = {s.id for s in catalog.skills}
    added = []
    for row in rows[:MAX_ROWS]:
        if row.get("status") == "exists":
            continue
        first = " ".join(str(row.get("first_name") or "").split())[:MAX_CELL]
        last = " ".join(str(row.get("last_name") or "").split())[:MAX_CELL]
        if not first:  # only a name on the rota ("Zed Q."): it gives the first name
            first = " ".join(str(row.get("name") or "").split()).split(" ")[0]
        if not first:
            continue
        taken = [p.name for p in people]
        name = " ".join(str(row.get("name") or "").split())[:MAX_CELL]
        if not name or name.casefold() in {t.casefold() for t in taken}:
            name = rota_name(first, last, taken)
        person = Person(
            id=new_id("person"), name=name, first_name=first, last_name=last,
            phone=" ".join(str(row.get("phone") or "").split()),
            resident=bool(row.get("resident", True)), active=bool(row.get("active", True)),
            skills=[s for s in row.get("skills") or [] if s in skill_ids],
        )
        email_fp = row.get("email_fp")
        if salt and emails.valid(email_fp, salt) and email_fp not in emails.owners(people):  # one person per address
            person.email_fp = email_fp
        people.append(person)
        added.append(person)
    return added
