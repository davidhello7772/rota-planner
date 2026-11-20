"""Optional: e-mailing each person their schedule, with their calendar file.

Off until the coordinator sets up a sending account (Settings › Schedules by e-mail) and a
test connection has worked. Until then nothing here ever connects anywhere.

- **The account** is kept in ``<data>/mail.json``: this computer only, never
  exported, never synchronised. The app password has to stay readable (it
  logs in), so this private file is the one place a secret is kept in clear;
  it is never sent to the screen.
- **The connection** is the program's only outgoing one: to the mail server
  the coordinator chose (Gmail by default), always encrypted with the
  server's certificate checked, and only when a test or Send is asked for.
- **Addresses are never stored.** The coordinator gives a list of names and
  addresses when sending; each address is recognised by its fingerprint
  (``emails.py``), kept in memory for the time of the sending, then dropped.
  Only who was e-mailed for which version is recorded (``Month.mailed``).
"""

from __future__ import annotations

import json
import re
import smtplib
import ssl
import threading
import time
from dataclasses import dataclass
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import availability, emails, tls
from .model import Person
from .store import write_private_json

Json = Dict[str, Any]

FILE_NAME = "mail.json"
DEFAULT_HOST, DEFAULT_PORT = "smtp.gmail.com", 587
SECURITIES = ("starttls", "ssl")
TIMEOUT_SECONDS = 25
PAUSE_SECONDS = 0.4          # between two messages: gentle with the provider
MAX_ROWS = 1000
MAX_FAILURES_IN_A_ROW = 5    # then stop: something is wrong with the account or the network
ANGLE = re.compile(r"<\s*([^<>\s]+@[^<>\s]+)\s*>")
HOST = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
NAME_WORDS = ("first name", "firstname", "last name", "lastname", "surname", "name", "prénom", "prenom", "nom")
WHO = ("all", "changed", "new")


class MailError(ValueError):
    """Something the coordinator must know (the message is for them)."""


@dataclass
class Outgoing:
    """One message to send. Lives in memory only, for the time of the sending."""

    person_id: str
    name: str
    address: str
    subject: str
    text: str
    calendar_name: str
    calendar: str


@dataclass
class Entry:
    """One line of the list given when sending."""

    row: int
    name: str
    address: str


# --------------------------------------------------------------------------
# The list of names and addresses given when sending
# --------------------------------------------------------------------------


def mask(address: str) -> str:
    """An address shown back for checking: "an•••@example.org"."""
    local, _, domain = address.partition("@")
    return f"{local[:2]}•••@{domain}"


def read_list(text: str = "", file_bytes: bytes = b"", filename: str = "") -> List[Entry]:
    """Names and addresses from a pasted list, a CSV or an Excel file.

    Any table works (the availability form's reply sheet as it is): the
    address is the cell that is an address; the name comes from the columns
    the first row calls a name, else from the first other cell. Lines such as
    ``Ana Berg <ana@example.org>`` work too.
    """
    table = availability.read_table(text, file_bytes, filename)
    entries: List[Entry] = []
    name_columns: List[int] = []
    for index, raw in enumerate(table[:MAX_ROWS]):
        cells = [" ".join(str(cell).split()) for cell in raw]
        address, others, where = "", [], -1
        for position, cell in enumerate(cells):
            found = ANGLE.search(cell)
            candidate = emails.normalise(found.group(1) if found else cell)
            if candidate and not address:
                address, where = candidate, position
                if found and cell[:found.start()].strip():
                    others.append((position, cell[:found.start()].strip().strip('"')))
            elif cell:
                others.append((position, cell))
        if not address:
            if index == 0:  # a first row without an address names the columns
                name_columns = [i for i, cell in enumerate(cells) if any(w in availability.norm(cell) for w in NAME_WORDS)
                                and "user" not in availability.norm(cell)]
            continue
        if name_columns:
            name = " ".join(cell for position, cell in others if position in name_columns or position == where)
        else:
            name = others[0][1] if others else ""
        entries.append(Entry(row=index, name=name[:80], address=address))
    return entries


def preview(entries: List[Entry], people: List[Person], shifts: Dict[str, List[Any]], salt: bytes,
            mailed: Dict[str, int], version: int) -> Json:
    """Who each address is, and who there is to write to. No address leaves here unmasked."""
    candidates = [p for p in people if not p.archived]
    by_id = {p.id: p for p in candidates}
    owners = emails.owners(candidates)
    rows: List[Json] = []
    values = [emails.fingerprint(entry.address, salt) for entry in entries]
    # A known address wins over a name: whoever is recognised by their address is taken first.
    taken: set = set()
    recognised_rows: set = set()
    for index, value in enumerate(values):
        if owners.get(value) and owners[value] not in taken:
            taken.add(owners[value])
            recognised_rows.add(index)
    for index, (entry, value) in enumerate(zip(entries, values)):
        owner = owners.get(value)
        suggestions: List[str] = []
        if owner:
            person_id, match = owner, "recognised"
            duplicate = index not in recognised_rows
        else:
            person_id, how, suggestions = availability.match_name(entry.name, candidates) if entry.name else (None, "none", [])
            match = {"exact": "name", "close": "check"}.get(how, "unknown")
            duplicate = bool(person_id) and person_id in taken
            if person_id and not duplicate and match == "name":
                taken.add(person_id)
        person = by_id.get(person_id) if person_id else None
        rows.append({
            "row": entry.row, "name": entry.name, "address": mask(entry.address), "person_id": person_id,
            "match": "duplicate" if duplicate else match, "suggestions": suggestions,
            # Sent by default when the address is theirs, or the name is exactly theirs.
            "send": match in ("recognised", "name") and not duplicate,
            "email_status": emails.status(person, value, owner),
            "remember": emails.remember_by_default(emails.status(person, value, owner)),
        })
    with_shifts = [p for p in candidates if shifts.get(p.id)]
    return {
        "rows": rows,
        "people": [{
            "id": p.id, "name": p.name, "shifts": len(shifts[p.id]),
            "changed": any(s.changed for s in shifts[p.id]),
            "new": mailed.get(p.id, -1) != version,
        } for p in sorted(with_shifts, key=lambda p: p.name.lower())],
        "everyone": [{"id": p.id, "name": p.name} for p in sorted(candidates, key=lambda p: p.name.lower())],
        "version": version,
    }


def plan(person_ids: List[str], people: List[Person], shifts: Dict[str, List[Any]], mailed: Dict[str, int],
         version: int, who: str) -> Json:
    """Among the people chosen in the list, who gets a message; and who is concerned but
    not in the list. The one place this is decided: the screen shows it, Send follows it."""
    chosen = list(dict.fromkeys(person_ids))
    known = {p.id: p for p in people if not p.archived}
    send_to = [pid for pid in chosen if pid in known and wanted(who, shifts.get(pid, []), mailed.get(pid, -1), version)]
    concerned = [p for p in known.values() if wanted(who, shifts.get(p.id, []), mailed.get(p.id, -1), version)]
    missing = sorted((p.name for p in concerned if p.id not in set(chosen)), key=str.lower)
    return {"send_to": send_to, "count": len(send_to), "missing": missing,
            "no_shift": sum(1 for pid in chosen if pid in known and not shifts.get(pid))}


def wanted(who: str, person_shifts: List[Any], last_mailed: int, version: int) -> bool:
    """Is this person among those to write to? (everyone with shifts · only where
    something changed since the published version · only those not yet e-mailed for it)"""
    if not person_shifts:
        return False
    if who == "changed":
        return any(s.changed for s in person_shifts)
    if who == "new":
        return last_mailed != version
    return True


# --------------------------------------------------------------------------
# The account, and sending
# --------------------------------------------------------------------------


def _connect(settings: Json) -> Any:
    """A logged-in connection to the mail server, encrypted, its certificate checked."""
    context = tls.context()
    if settings["security"] == "ssl":
        client: Any = smtplib.SMTP_SSL(settings["host"], settings["port"], timeout=TIMEOUT_SECONDS, context=context)
    else:
        client = smtplib.SMTP(settings["host"], settings["port"], timeout=TIMEOUT_SECONDS)
        client.ehlo()
        client.starttls(context=context)
        client.ehlo()
    client.login(settings["address"], settings["password"])
    return client


def explain(error: BaseException, host: str = "") -> str:
    """A mail error in plain words."""
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return ("The mail server refused the app password. Check the address and the code, that 2-Step Verification "
                "is on for the account, and that the code was not removed.")
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return "The address was refused by the mail server."
    if isinstance(error, smtplib.SMTPSenderRefused):
        return "The mail server refused the sending address."
    if isinstance(error, smtplib.SMTPDataError):
        return "The mail server refused the message (too many messages today, perhaps)."
    if isinstance(error, ssl.SSLError):
        return "The connection to the mail server could not be secured, so nothing was sent."
    if isinstance(error, smtplib.SMTPException):
        return "The mail server answered with an error."
    if isinstance(error, OSError):
        return f"The mail server{f' ({host})' if host else ''} could not be reached. Is this computer connected to the internet?"
    return "Something went wrong while sending."


class Mail:
    """The sending account of one data folder, and the sending in progress."""

    def __init__(self, root: Path, connect: Callable[[Json], Any] = _connect, pause: float = PAUSE_SECONDS) -> None:
        self.path = Path(root) / FILE_NAME
        self.connect = connect
        self.pause = pause
        self._lock = threading.RLock()
        self._settings: Optional[Json] = None
        self._job: Json = {"running": False, "total": 0, "sent": 0, "failed": [], "stopped": False, "done": False, "problem": ""}
        self._stop = threading.Event()
        self._load()

    # -- the account ----------------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._settings = {
                "address": emails.normalise(data["address"]), "name": str(data.get("name") or "")[:80],
                "password": str(data["password"]), "host": str(data.get("host") or DEFAULT_HOST),
                "port": int(data.get("port") or DEFAULT_PORT),
                "security": data.get("security") if data.get("security") in SECURITIES else "starttls",
                "verified": bool(data.get("verified")),
            }
            if not self._settings["address"] or not self._settings["password"] or not HOST.match(self._settings["host"]):
                self._settings = None
        except (OSError, ValueError, KeyError, TypeError):
            self._settings = None  # unreadable: as if not set up

    @property
    def configured(self) -> bool:
        """Set up, and a test connection has worked."""
        return bool(self._settings and self._settings["verified"])

    def describe(self) -> Json:
        """For the screen: never the app password."""
        with self._lock:
            s = self._settings
            return {
                "configured": self.configured, "has_password": bool(s),
                "address": s["address"] if s else "", "name": s["name"] if s else "",
                "host": s["host"] if s else DEFAULT_HOST, "port": s["port"] if s else DEFAULT_PORT,
                "security": s["security"] if s else "starttls",
            }

    def save(self, data: Json) -> None:
        """Set or change the account. It counts as set up once ``test`` has worked."""
        with self._lock:
            if self._job["running"]:
                raise MailError("Messages are being sent: wait until it is finished.")
            address = emails.normalise(str(data.get("address") or ""))
            if not address:
                raise MailError("Give the address of the account that sends, such as rota@example.org.")
            password = re.sub(r"\s+", "", str(data.get("password") or ""))  # Google shows it in four groups
            host = str(data.get("host") or DEFAULT_HOST).strip().lower()
            # The code kept is for this account on this server: it is never sent to another one.
            if not password and self._settings and (address, host) == (self._settings["address"], self._settings["host"]):
                password = self._settings["password"]
            if not password:
                raise MailError("Paste the app password of the account"
                                + (" again: the address or the server changed." if self._settings else "."))
            if len(password) > 200:
                raise MailError("This app password is too long.")
            if not HOST.match(host):
                raise MailError("The mail server's name does not look right.")
            try:
                port = int(data.get("port") or DEFAULT_PORT)
            except (TypeError, ValueError):
                raise MailError("The port is a number, such as 587.") from None
            if not 1 <= port <= 65535:
                raise MailError("The port is a number, such as 587.")
            security = data.get("security") if data.get("security") in SECURITIES else ("ssl" if port == 465 else "starttls")
            name = " ".join(str(data.get("name") or "").split())[:80]
            fresh = {"address": address, "name": name, "password": password, "host": host, "port": port, "security": security}
            same = bool(self._settings) and all(self._settings[k] == fresh[k] for k in ("address", "password", "host", "port", "security"))
            self._settings = {**fresh, "verified": bool(same and self._settings["verified"])}
            write_private_json(self.path, self._settings)

    def remove(self) -> None:
        """Turn e-mail off again: the account and its app password are forgotten."""
        with self._lock:
            if self._job["running"]:
                raise MailError("Messages are being sent: wait until it is finished.")
            self._settings = None
            self.path.unlink(missing_ok=True)

    def test(self) -> str:
        """Connect, log in, and send one message to the account itself. Returns its address."""
        with self._lock:
            if not self._settings:
                raise MailError("Save the account first.")
            settings = dict(self._settings)
        message = self.build(settings, settings["address"], settings["name"] or "Rota Planner", "Rota Planner: test",
                             "This is a test from Rota Planner.\n\nIf you read it, sending schedules by e-mail works.")
        try:
            client = self.connect(settings)
            try:
                client.send_message(message)
            finally:
                _quit(client)
        except Exception as error:  # noqa: BLE001 — every failure is explained the same way
            raise MailError(explain(error, settings["host"])) from None
        with self._lock:
            if self._settings and all(self._settings[k] == settings[k] for k in ("address", "password", "host", "port")):
                self._settings["verified"] = True
                write_private_json(self.path, self._settings)
        return settings["address"]

    @staticmethod
    def build(settings: Json, address: str, name: str, subject: str, text: str,
              calendar_name: str = "", calendar: str = "") -> EmailMessage:
        """One message to one person. ``EmailMessage`` refuses line breaks in headers."""
        local, _, domain = address.partition("@")
        sender_local, _, sender_domain = settings["address"].partition("@")
        message = EmailMessage()
        message["From"] = Address(display_name=settings["name"], username=sender_local, domain=sender_domain)
        message["To"] = Address(display_name=" ".join(name.split())[:80], username=local, domain=domain)
        message["Subject"] = " ".join(subject.split())[:150]
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = make_msgid(domain=sender_domain)
        message.set_content(text)
        if calendar:
            message.add_attachment(calendar.encode("utf-8"), maintype="text", subtype="calendar",
                                   filename=calendar_name or "rota.ics", params={"method": "PUBLISH", "charset": "utf-8"})
        return message

    # -- sending ---------------------------------------------------------------

    def status(self) -> Json:
        """Counts and names only: no address."""
        with self._lock:
            return {**self._job, "failed": list(self._job["failed"])}

    def stop(self) -> None:
        self._stop.set()

    def send(self, outgoing: List[Outgoing], on_sent: Callable[[str], None]) -> None:
        """Send in the background, one message per person. ``on_sent`` is told each
        person reached, at once: closing the program half-way loses no record."""
        with self._lock:
            if not self.configured:
                raise MailError("Sending by e-mail is not set up (Settings › Schedules by e-mail).")
            if self._job["running"]:
                raise MailError("Messages are already being sent.")
            if not outgoing:
                raise MailError("Nobody to send to.")
            settings = dict(self._settings or {})
            self._job = {"running": True, "total": len(outgoing), "sent": 0, "failed": [], "stopped": False, "done": False, "problem": ""}
            self._stop.clear()
        threading.Thread(target=self._run, args=(settings, list(outgoing), on_sent), name="mail-send", daemon=True).start()

    def _run(self, settings: Json, outgoing: List[Outgoing], on_sent: Callable[[str], None]) -> None:
        problem, client, failures_in_a_row = "", None, 0
        try:
            for item in outgoing:
                if self._stop.is_set():
                    break
                try:
                    message = self.build(settings, item.address, item.name, item.subject, item.text,
                                         item.calendar_name, item.calendar)
                except Exception:  # noqa: BLE001 — an address the mail format refuses: only this person
                    self._failed(item.name, "This address cannot be used.")
                    continue
                if client is None:
                    try:
                        client = self.connect(settings)
                    except Exception as error:  # noqa: BLE001 — no connection, or refused: nothing more can work
                        problem = explain(error, settings["host"])
                        break
                try:
                    client.send_message(message)
                    failures_in_a_row = 0
                    with self._lock:
                        self._job["sent"] += 1
                    try:
                        on_sent(item.person_id)
                    except Exception:  # noqa: BLE001 — the message went; a record that fails must not stop the rest
                        pass
                except smtplib.SMTPRecipientsRefused as error:
                    self._failed(item.name, explain(error))
                except Exception as error:  # noqa: BLE001 — one message failing must not lose the others
                    self._failed(item.name, explain(error, settings["host"]))
                    _quit(client)
                    client = None  # a fresh connection for the next one
                    failures_in_a_row += 1
                    if failures_in_a_row >= MAX_FAILURES_IN_A_ROW:
                        problem = "Several messages in a row could not be sent: stopped. " + explain(error, settings["host"])
                        break
                if self.pause:
                    time.sleep(self.pause)
        finally:
            _quit(client)
            outgoing.clear()  # the addresses are not kept
            with self._lock:
                self._job.update(running=False, done=True, stopped=self._stop.is_set(), problem=problem)

    def _failed(self, name: str, reason: str) -> None:
        with self._lock:
            self._job["failed"].append({"name": name, "reason": reason})


def _quit(client: Any) -> None:
    if client is None:
        return
    try:
        client.quit()
    except Exception:  # noqa: BLE001 — closing a broken connection may fail: no matter
        pass
