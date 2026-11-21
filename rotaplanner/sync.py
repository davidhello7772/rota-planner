"""Several computers, each with its own copy of the data, kept in step through
a meeting place: a WebDAV server on the internet, or a shared folder
(``places.py``).

How it travels
--------------
Each computer puts **one file of its own** at the place,
``rota-<computer id>.rpsync``, holding all its data, and never writes
another's. So no file has two writers, and the programs never connect to each
other; they need not even run at the same time.

The file is encrypted and signed with a key made from a **pairing code** (PBKDF2),
typed once on each computer and never put at the place. The standard library
has no cipher, so it is built from its primitives: a keystream from SHAKE-256
(key and a fresh random nonce) XORed with the compressed content, then
HMAC-SHA256 over everything (encrypt-then-MAC), checked before anything is
read. A file that fails the check is either still arriving or made with
another code.

Without connection
------------------
Only the **courier** (``exchange``, a background thread) talks to the place.
It keeps a mirror of the others' files in ``<data>/sync-cache`` (still
sealed) and this computer's own file ready to leave. Everything the screens
ask (``status``, ``preview``, ``apply``) is answered from that mirror, so
nothing ever waits for a server and all of it works offline; what changed
here is sent, and what changed elsewhere fetched, when the place answers again.

Who is ahead
------------
The data is cut in **units**: People, Tasks & areas, Settings, Saved task
templates, and each month. Each unit carries a version counter per computer
(a version vector); a computer adds one to its own counter when it sees that
a unit changed. ``rank`` compares every computer's version of a unit:

==========================================  ==============================
all the same content                         in sync
this computer's includes all the others      sent: the others will take it
one other includes all, this one's too       to get: taken by itself
several that include one another's not       both changed: the coordinator chooses
==========================================  ==============================

Versions only grow, so an old file (a slow service, a restored copy) can
never bring old data back over newer.

What changes, and when
----------------------
What another computer has newer, and this one did not touch, is taken
automatically (``apply_automatic``); a choice between versions is only ever
the coordinator's (``preview``, ``apply``). Either way every document is
checked as strictly as a file brought from another computer, all the current
data is kept first in ``backups/``, and nothing is mixed inside a unit: one
version is kept whole.

The pen
-------
Only one computer edits at a time (``ensure_pen``, ``take_pen``,
``release_pen``); another can ask for it (``ask_pen``): the one editing then
sends its changes and hands over by itself. See the comment above those.

Privacy: comments of a month that is over are never put in the file; the
local state (``sync.json``: the pairing code, the place and its password, the
versions) never leaves the data folder.
"""

from __future__ import annotations

import calendar
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import threading
import time
import zlib
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import places, transfer
from .availability import scrub_comments
from .model import SCHEMA_VERSION, Person
from .store import Store, StoreError, migrate, write_private_json

Json = Dict[str, Any]
Vector = Dict[str, int]

STATE_FILE = "sync.json"
CACHE_FOLDER = "sync-cache"       # the others' files as received (still sealed), and this computer's ready to leave
OUTBOX = "outbox.rpsync"
PEN_NAME = re.compile(r"^pen-(c-[0-9a-f]{12})\.rpsync\Z")
LOG_KEPT = 40
RESEAL_SECONDS = 24 * 3600        # this computer's file is sent again daily: "still here"
GONE_SECONDS = 14 * 24 * 3600     # a computer not seen for this long is shown as gone
PEN_BEAT_SECONDS = 60             # the pen's holder says "still here" this often
PEN_STALE_SECONDS = 5 * 60        # without it for this long, the holder is taken as away
PEN_IDLE_SECONDS = 20 * 60        # the pen is given back after this long without a change
PEN_FORGOTTEN_SECONDS = 24 * 3600
ASKING_SECONDS = 5                # between two rounds while asking for the pen
ASK_GIVE_UP_SECONDS = 10 * 60     # no hand-over by then from a computer still there: it does not understand the asking
OFFLINE_TRUSTED_SECONDS = 30       # "no connection" is believed for this long before trying again for the pen
ALONE_TRUSTED_SECONDS = 90         # "no other computer" is believed for this long after looking
LOCAL_SECONDS, SERVER_SECONDS, IDLE_SECONDS = 20, 45, 30   # between two rounds of the courier
BACKOFF_MIN, BACKOFF_MAX = 15, 300                         # ... when the place does not answer
WATCHED_SECONDS = 20                                       # ... while someone is at the screen
STOP_WAIT_SECONDS = 30                                     # "Stop synchronising" waits this long for a round to end
GROUP_SECONDS = 4                                          # changes made in a row are sent together
ROUTINE_COPIES, CHOICE_COPIES = 20, 30                     # safety copies kept before a taking
SYNC_FORMAT = 1
MAGIC = b"RPSYNC1\n"
FILE_NAME = re.compile(r"^rota-(c-[0-9a-f]{12})\.rpsync\Z")
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L
CODE_LENGTH = 20  # about 99 bits
KDF_SALT = b"rota-planner sync v1"
MAX_FILE_BYTES = places.MAX_FILE_BYTES
PEN_BYTES = 8_000                 # a pen note is a few hundred bytes
MAX_COMPUTERS = 16                # other computers looked at (the place may hold junk)
ROUND_BYTES = 2 * MAX_FILE_BYTES  # fetched in one round at most; the rest waits for the next
TOP_LABELS = {"people.json": "People", "catalog.json": "Tasks & areas", "settings.json": "Settings",
              "templates.json": "Saved task templates"}
# Why the place could not be used, in two families: what passes by itself (no
# connection, a busy server, a folder not there for now), and what the
# coordinator must put right in Settings.
PASSING_KINDS = ("offline", "busy", "folder")
SETTINGS_KINDS = ("auth", "refused", "missing", "full", "tls", "moved", "other")


class SyncError(ValueError):
    """Something the coordinator must know (the message is for them)."""


# --------------------------------------------------------------------------
# Pairing code and key
# --------------------------------------------------------------------------


def new_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    return "-".join(raw[i:i + 4] for i in range(0, CODE_LENGTH, 4))


def normalise_code(code: str) -> str:
    raw = re.sub(r"[\s-]+", "", str(code or "")).upper()
    if len(raw) != CODE_LENGTH or any(c not in CODE_ALPHABET for c in raw):
        raise SyncError("This is not a pairing code: it has 20 letters and digits, such as ABCD-EFGH-JKMN-PQRS-TUVW.")
    return raw


def derive_key(code: str) -> bytes:
    """PBKDF2, present in every Python build: both computers must derive the same key.
    The code itself holds about 99 bits, so the slow hash is a margin, not the defence."""
    return hashlib.pbkdf2_hmac("sha256", normalise_code(code).encode("ascii"), KDF_SALT, 200_000, dklen=32)


def key_id(key: bytes) -> str:
    """Says which code a file was made with, without saying anything about the code."""
    return hmac.new(key, b"key id", hashlib.sha256).hexdigest()[:16]


def _subkey(key: bytes, purpose: bytes) -> bytes:
    return hmac.new(key, purpose, hashlib.sha256).digest()


def _xor(data: bytes, stream: bytes) -> bytes:
    return (int.from_bytes(data, "big") ^ int.from_bytes(stream, "big")).to_bytes(len(data), "big")


def seal(key: bytes, computer_id: str, payload: bytes) -> bytes:
    """Encrypt then sign. Layout: MAGIC, a header line (JSON), the ciphertext, a 32-byte tag."""
    nonce = secrets.token_bytes(16)
    header = json.dumps({"key": key_id(key), "computer": computer_id, "nonce": nonce.hex()}).encode("ascii") + b"\n"
    plain = zlib.compress(payload, 6)
    stream = hashlib.shake_256(_subkey(key, b"encrypt") + nonce).digest(len(plain))
    cipher = _xor(plain, stream) if plain else b""
    body = MAGIC + header + cipher
    return body + hmac.new(_subkey(key, b"sign"), body, hashlib.sha256).digest()


def unseal(key: bytes, data: bytes) -> Tuple[Json, bytes]:
    """(header, payload), or SyncError("…", kind="other-code" | "damaged")."""
    if not data.startswith(MAGIC) or len(data) < len(MAGIC) + 33:
        raise _damaged()
    end = data.find(b"\n", len(MAGIC))
    if end < 0 or end > len(MAGIC) + 512:
        raise _damaged()
    try:
        header = json.loads(data[len(MAGIC):end].decode("ascii"))
        nonce = bytes.fromhex(header["nonce"])
    except (UnicodeDecodeError, ValueError, KeyError, TypeError):
        raise _damaged() from None
    if header.get("key") != key_id(key):
        raise _other_code()
    body, tag = data[:-32], data[-32:]
    if not hmac.compare_digest(hmac.new(_subkey(key, b"sign"), body, hashlib.sha256).digest(), tag):
        raise _damaged()
    cipher = body[end + 1:]
    stream = hashlib.shake_256(_subkey(key, b"encrypt") + nonce).digest(len(cipher))
    try:
        payload = zlib.decompress(_xor(cipher, stream) if cipher else b"")
    except zlib.error:
        raise _damaged() from None
    return header, payload


def _other_code() -> SyncError:
    error = SyncError("Another computer's file was made with another pairing code.")
    error.kind = "other-code"  # type: ignore[attr-defined]
    return error


def _damaged() -> SyncError:
    error = SyncError("Another computer's file is incomplete: it may still be arriving. Try again in a moment.")
    error.kind = "damaged"  # type: ignore[attr-defined]
    return error


# --------------------------------------------------------------------------
# Units, versions
# --------------------------------------------------------------------------


def content_hash(document: Json) -> str:
    content = {k: v for k, v in document.items() if k != "schema_version"}
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def compare(mine: Vector, theirs: Vector) -> str:
    keys = set(mine) | set(theirs)
    ahead = all(mine.get(k, 0) >= theirs.get(k, 0) for k in keys)
    behind = all(mine.get(k, 0) <= theirs.get(k, 0) for k in keys)
    if ahead and behind:
        return "same"
    if ahead:
        return "sent"
    if behind:
        return "get"
    return "both"


def merged(a: Vector, b: Vector) -> Vector:
    return {k: max(a.get(k, 0), b.get(k, 0)) for k in sorted(set(a) | set(b))}


def ago(written: float, now: Optional[float] = None) -> str:
    """How long ago, in plain words ("5 min ago")."""
    if not written:
        return ""
    minutes = max(0, round(((time.time() if now is None else now) - written) / 60))
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    hours = round(minutes / 60)
    return f"{hours} h ago" if hours < 48 else f"{round(hours / 24)} days ago"


def summary(status: Dict[str, Any], now: Optional[float] = None) -> List[str]:
    """What the top bar and the window say about the other computers:
    [short text, tone (ok / warn / bad / ""), longer explanation]."""
    if not status.get("configured"):
        return ["Not set up", "", "Only this computer has the data."]
    others = status.get("others") or []
    other = (status.get("other") or {}).get("name") or ("the other computers" if len(others) > 1 else "the other computer")
    counts = status.get("counts") or {}
    sent, get, both = counts.get("sent", 0), counts.get("get", 0), counts.get("both", 0)
    problem, kind = status.get("problem"), status.get("kind")
    waiting = kind in PASSING_KINDS
    if problem and kind != "alone" and not waiting:
        return ["Needs attention", "bad", str(problem)]
    if both:
        return [f"Both changed · {both}", "bad", "Changed on more than one computer: synchronise and choose which to keep."
                if len(others) > 1 else f"Changed here and on {other}: synchronise and choose which to keep."]
    if waiting:
        since = (status.get("connection") or {}).get("since")
        when = f" since {time.strftime('%H:%M', time.localtime(since))}" if since else ""
        pending = status.get("pending")
        return ["Offline · changes waiting" if pending else "Offline", "",
                f"No connection to the others{when}. Keep working: changes are kept here and sent when it is back."]
    if get:
        who = other if len(others) <= 1 else "Another computer"
        return [f"{get} to get", "warn", f"{who[0].upper()}{who[1:]} has newer changes: synchronise before working on them."]
    if kind == "alone":
        return [f"{sent} waiting" if sent else "Waiting", "", str(problem or "")]
    if sent:
        behind = [c.get("name") or "another computer" for c in others if c.get("state") == "behind"]
        who = other if not behind else _listed(behind) if len(behind) <= 2 else f"{len(behind)} computers"
        return [f"{sent} sent", "", f"Sent: waiting for {who} to take them."]
    if len(others) > 1:
        return ["In sync", "ok", f"Same data on all {len(others) + 1} computers."]
    written = (status.get("other") or {}).get("written")
    when = f" (its file: {ago(written, now)})" if written else ""
    return ["In sync", "ok", f"Same data as {other}{when}."]


def label(unit: str) -> str:
    if unit in TOP_LABELS:
        return TOP_LABELS[unit]
    match = transfer.MONTH_NAME.match(unit)
    if match:
        year, number = match.group(1).split("-")
        return f"{calendar.month_name[int(number)]} {year}"
    return unit


def _sort_key(unit: str) -> Tuple[int, str]:
    order = list(TOP_LABELS)
    return (order.index(unit), unit) if unit in order else (len(order), unit)


def _month_over(unit: str, today: date) -> bool:
    match = transfer.MONTH_NAME.match(unit)
    return bool(match) and match.group(1) < f"{today.year:04d}-{today.month:02d}"


def _person_as_read(person: Json) -> Json:
    """A person of ``people.json`` as the program reads it (older forms read the
    new way); the document as it is if it cannot be read."""
    try:
        return Person.from_dict(person).to_dict()
    except Exception:  # noqa: BLE001 — anything unreadable is compared as it is
        return person


def difference(unit: str, mine: Optional[Json], theirs: Optional[Json]) -> str:
    """What differs, in a few plain words, to help choose which to keep."""
    mine, theirs = mine or {}, theirs or {}
    if unit == "people.json":
        # Each person as the program reads them: a side still in an older form
        # (a target in hours a month) must not look as if everyone changed.
        a = {p.get("id"): _person_as_read(p) for p in mine.get("people", [])}
        b = {p.get("id"): _person_as_read(p) for p in theirs.get("people", [])}
        parts = [f"{len(set(a) - set(b))} only here", f"{len(set(b) - set(a))} only there",
                 f"{sum(1 for k in set(a) & set(b) if a[k] != b[k])} changed"]
        return "People: " + ", ".join(p for p in parts if not p.startswith("0 ")) if any(not p.startswith("0 ") for p in parts) else "Small changes."
    if transfer.MONTH_NAME.match(unit):
        def shifts(doc: Json) -> set:
            return {(x.get("task_id"), x.get("date"), x.get("position"), x.get("person_id"), x.get("guest"))
                    for x in doc.get("assignments", [])}
        a, b = shifts(mine), shifts(theirs)
        moved = len({s[:3] for s in a ^ b})
        replies = sum(1 for k in set(mine.get("availability", {})) | set(theirs.get("availability", {}))
                      if mine.get("availability", {}).get(k) != theirs.get("availability", {}).get(k))
        parts = [f"{moved} shift{'s' if moved != 1 else ''} differ" if moved else "",
                 f"{replies} availability repl{'ies' if replies != 1 else 'y'} differ" if replies else "",
                 "events differ" if mine.get("events") != theirs.get("events") else ""]
        return ", ".join(p for p in parts if p).capitalize() or "Other changes (notes, closed days, tasks…)."
    if unit == "catalog.json":
        a = {t.get("id"): t for t in mine.get("tasks", [])}
        b = {t.get("id"): t for t in theirs.get("tasks", [])}
        changed = sum(1 for k in set(a) | set(b) if a.get(k) != b.get(k))
        return f"{changed} task{'s' if changed != 1 else ''} differ" if changed else "Skills, areas or the regular week differ."
    return "Changed on more than one computer."



def _write_private(path: Path, data: bytes) -> None:
    """A file of the cache: written whole, readable by this user only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    os.replace(temporary, path)


def _shown(row: Json) -> Json:
    """A unit's row as the screen gets it."""
    return {"unit": row["unit"], "label": row["label"], "state": row["state"], "from": row["source_name"],
            "candidates": row["candidate_names"], "auto": row["auto"]}


def _listed(words: Sequence[str]) -> str:
    words = list(words)
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1] if words else ""


def _icon(status: Json) -> str:
    """Which cloud the top bar shows: ok, sending, offline or alert."""
    tone = (status.get("summary") or ["", ""])[1]
    if tone == "bad":
        return "alert"
    if (status.get("connection") or {}).get("online") is False:
        return "offline"
    if status.get("pending") or (status.get("counts") or {}).get("get"):
        return "sending"
    return "ok"


# --------------------------------------------------------------------------
# Who has which version of a unit
# --------------------------------------------------------------------------


def rank(mine: Optional[Tuple[Vector, str]], others: Dict[str, Tuple[Vector, str]],
         missing: Sequence[str] = ()) -> Json:
    """Where one unit stands among the computers.

    ``mine``: this computer's (vector, content hash), or None when it has no
    such unit. ``others``: computer id → (vector, hash) for those that have
    it; ``missing``: the computers that do not.

    Versions with the same content are one version. Those that no other
    version includes are the candidates: one candidate, and it is this
    computer's → ``same`` or ``sent`` (``waiting``: who has not taken it yet);
    one candidate elsewhere → ``get`` from ``source``; several → ``both``,
    ``candidates`` to choose from ("" stands for this computer).
    """
    groups: Dict[str, Json] = {}
    for computer, version in ([("", mine)] if mine is not None else []) + sorted(others.items()):
        group = groups.setdefault(version[1], {"v": {}, "ids": []})
        group["v"] = merged(group["v"], version[0])
        group["ids"].append(computer)
    result: Json = {"state": "same", "source": "", "candidates": [], "waiting": []}
    if not groups:
        return result
    top = [g for g in groups.values()
           if not any(compare(g["v"], h["v"]) == "get" for h in groups.values() if h is not g)]
    if len(top) > 1:
        result.update(state="both", candidates=[g["ids"][0] for g in top])
    elif "" in top[0]["ids"]:
        waiting = sorted([c for c in others if c not in top[0]["ids"]] + list(missing))
        result.update(state="sent" if waiting else "same", waiting=waiting)
    else:
        result.update(state="get", source=top[0]["ids"][0])
    return result


# --------------------------------------------------------------------------
# The synchronisation of one data folder
# --------------------------------------------------------------------------


class PenError(SyncError):
    """A change is not possible now: another computer is editing, or the pen
    cannot be taken. ``info`` is what the screen shows."""

    def __init__(self, message: str, kind: str, info: Optional[Json] = None) -> None:
        super().__init__(message)
        self.kind, self.info = kind, info or {}


class Sync:
    def __init__(self, store: Store, today: Callable[[], date] = date.today,
                 connect: Optional[Callable[..., Any]] = None) -> None:
        self.store = store
        self.path = store.root / STATE_FILE
        self.cache = store.root / CACHE_FOLDER
        self.today = today
        self.lock = threading.RLock()   # this computer's state and files
        self._io = threading.Lock()     # one exchange with the place at a time
        self._taking = threading.Lock()  # one take of the pen at a time (taken before the two others)
        self._snapshot = 0              # counts the statuses made: the screen keeps the newest
        self.state: Optional[Json] = None
        self._key: Optional[bytes] = None
        self._place: Any = None
        self._connect = connect         # a pretend connection, for tests
        self._parsed: Dict[str, Tuple[Any, Json]] = {}
        self._incoming = False          # the others' files changed: the app is told (deliver)
        self._pen_wrote = False
        self._last_round = 0.0
        self._last_edit = 0.0
        self._own_checked = False
        self.connection: Json = {"online": None, "since": 0.0, "last_ok": 0.0, "problem": "", "kind": "", "retry": 0.0}
        self.on_incoming: Optional[Callable[[], None]] = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._load()

    # -- state file ----------------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
            self._key = bytes.fromhex(state["key"])
            if not FILE_NAME.match(f"rota-{state['computer_id']}.rpsync"):
                raise ValueError
            if "place" not in state:  # set up before WebDAV existed: a shared folder
                state["place"] = {"kind": "folder", "folder": state.pop("folder")}
            for name, default in (("versions", {}), ("hashes", {}), ("over", []), ("mirror", {}), ("own", {}),
                                  ("log", []), ("pen", {}), ("applied", {"seq": 0}), ("auto", True), ("hold", ""),
                                  ("clock", 0.0)):
                state.setdefault(name, default)
            self._place = places.make(state["place"], connect=self._connect)
            self.state = state
        except (OSError, ValueError, KeyError, TypeError, places.PlaceError):
            self.state, self._key, self._place = None, None, None  # unreadable: as if not set up

    def _save(self) -> None:
        assert self.state is not None
        write_private_json(self.path, self.state)

    @property
    def configured(self) -> bool:
        return self.state is not None

    @property
    def local(self) -> bool:
        """A place that costs nothing to look at (a folder): the screens look at once."""
        return self._place is not None and self._place.local

    def _machine(self) -> str:
        """Tells this computer's file from a copy made by another computer with the same data folder."""
        return hashlib.sha256(f"{socket.gethostname()}|{self.store.root.resolve()}".encode()).hexdigest()[:16]

    def _note(self, text: str, tone: str = "") -> None:
        """One line of the activity list (what happened, in plain words)."""
        assert self.state is not None
        self.state["log"] = (self.state["log"] + [{"at": time.time(), "text": text, "tone": tone}])[-LOG_KEPT:]

    # -- setting up ----------------------------------------------------------

    def _settings(self, place: Any) -> Json:
        """Place settings from what the screen sends: a folder path, or a dictionary.
        A WebDAV password left empty means "the one already kept" (same server, same user)."""
        if not isinstance(place, dict):
            return {"kind": "folder", "folder": str(place or "").strip()}
        kind = str(place.get("kind") or "folder")
        if kind == "folder":
            return {"kind": "folder", "folder": str(place.get("folder") or "").strip()}
        settings = {"kind": "webdav", "url": str(place.get("url") or "").strip(), "user": str(place.get("user") or "").strip(),
                    "password": str(place.get("password") or "")}
        kept = (self.state or {}).get("place") or {}
        if not settings["password"] and kept.get("kind") == "webdav" and kept.get("user") == settings["user"]:
            try:
                same = places.webdav_address(kept.get("url", "")) == places.webdav_address(settings["url"])
            except places.PlaceError:
                same = False
            if same:
                settings["password"] = kept.get("password", "")
        return settings

    def _checked_place(self, settings: Json) -> Any:
        """The place, once it is sure this computer can use it (talks to it)."""
        try:
            if settings["kind"] == "folder":
                if not settings["folder"]:
                    raise places.PlaceError("folder", "Give the full path of the shared folder, for example "
                                                      "/home/you/Google Drive/Rota or G:\\My Drive\\Rota.")
                settings = dict(settings, folder=str(Path(settings["folder"]).expanduser()))
            elif not settings["user"] or not settings["password"]:
                raise places.PlaceError("auth", "Give the user name and the app password of the account on the server.")
            place = places.make(settings, connect=self._connect)
            place.check()
        except places.PlaceError as error:
            raise SyncError(str(error)) from None
        if place.kind == "folder":
            folder, root = place.folder.resolve(), self.store.root.resolve()
            if folder == root or root in folder.parents:
                raise SyncError("Choose a folder outside Rota Planner's own data folder.")
            settings = dict(settings, folder=str(place.folder))
        else:
            settings = dict(settings, url=place.url)
        place.settings = settings
        return place

    def test_place(self, place: Any) -> Json:
        """Can this computer use that place? (Set-up: "Test the connection".)"""
        return self._checked_place(self._settings(place)).describe()

    def _begin(self, name: str, place: Any, code: str) -> None:
        with self.lock:
            if self.state is not None:
                # A new identity would leave this computer's old file behind, read as one more computer.
                raise SyncError("Synchronisation is already set up: stop it first to set it up again.")
        key = derive_key(code)
        checked = self._checked_place(self._settings(place))
        with self.lock:
            if self.state is not None:
                raise SyncError("Synchronisation is already set up: stop it first to set it up again.")
            self.state = {
                "computer_id": f"c-{secrets.token_hex(6)}",
                "name": " ".join(str(name or "").split())[:40] or socket.gethostname()[:40],
                "place": checked.settings, "code": normalise_code(code), "key": key.hex(),
                "versions": {}, "hashes": {}, "over": [], "mirror": {}, "own": {}, "log": [], "pen": {},
                "applied": {"seq": 0}, "auto": True, "hold": "", "clock": 0.0,
            }
            self._key, self._place, self._parsed, self._own_checked = key, checked, {}, True
            self._wipe_cache()
            self._save()
        self.exchange(force=True)

    def start(self, name: str, place: Any) -> str:
        """First computer: make the pairing code (shown to type on the others)."""
        code = new_code()
        self._begin(name, place, code)
        return code

    def join(self, name: str, place: Any, code: str) -> None:
        """Another computer: the code typed from the first."""
        self._begin(name, place, code)

    def change_place(self, place: Any) -> None:
        """Meet somewhere else (another server, a new password) without pairing again."""
        with self.lock:
            if self.state is None:
                raise SyncError("Synchronisation is not set up.")
        checked = self._checked_place(self._settings(place))
        old, mine = self._place, self._own_names()
        with self._io:
            for name in mine:
                try:
                    old.delete(name)
                except places.PlaceError:
                    pass  # not reachable: another computer can forget it
            with self.lock:
                self.state["place"], self.state["mirror"], self.state["own"] = checked.settings, {}, {}
                self._place, self._parsed = checked, {}
                self._wipe_cache()
                self._note(f"Now meeting at {checked.describe()['label']}.")
                self._save()
        self.exchange(force=True)

    def stop(self) -> bool:
        """Stop synchronising: this computer's files leave the place; the data stays.
        Returns whether they could be removed (if not, another computer can forget this one)."""
        with self.lock:
            if self.state is None:
                return True
            place, mine = self._place, self._own_names()
        removed = True
        free = self._io.acquire(timeout=STOP_WAIT_SECONDS)  # a round with a slow server must not prevent stopping
        try:
            if free:
                for name in mine:
                    try:
                        place.delete(name)
                    except places.PlaceError:
                        removed = False
            else:
                removed = False
            with self.lock:
                self.path.unlink(missing_ok=True)
                self._wipe_cache()
                self.state, self._key, self._place, self._parsed = None, None, None, {}
        finally:
            if free:
                self._io.release()
        return removed

    def leave_place(self) -> bool:
        """Remove this computer's files from the place, keeping the set-up (used when
        the program is uninstalled with its data). Returns whether they are gone."""
        with self.lock:
            if self.state is None:
                return True
            place, mine = self._place, self._own_names()
        try:
            with self._io:
                for name in mine:
                    place.delete(name)
        except places.PlaceError:
            return False
        return True

    def farewell(self) -> Json:
        """This computer is uninstalled: its last changes are sent, the pen goes
        back, and its files leave the place, but only once every other computer
        has taken what it sent (otherwise they stay, so that one still gets it).

        Returns {"outcome", "behind", "pending"}: outcome "left" (its files are
        gone, or it never took part), "kept" (``behind`` names the computers that
        still need its file), "unsent" (changes made here could not be sent) or
        "failed" (sent, but its files could not be removed)."""
        if not self.configured:
            return {"outcome": "left", "behind": [], "pending": False}
        try:
            if self.pen().get("mine") or self.asking():
                self.release_pen()  # sends what changed, then lets go
            result = self.exchange()
        except (SyncError, OSError):
            result = {"ok": False}
        with self.lock:
            if self.state is None:
                return {"outcome": "left", "behind": [], "pending": False}
            pending = self.state["own"].get("sent") != self.state["own"].get("digest")
        if not result.get("ok"):
            return {"outcome": "unsent" if pending else "failed", "behind": [], "pending": pending}
        behind = self.behind()
        if behind:
            return {"outcome": "kept", "behind": behind, "pending": False}
        return {"outcome": "left" if self.leave_place() else "failed", "behind": [], "pending": False}

    def behind(self) -> List[str]:
        """The other computers that have not taken everything this one sent (by
        name), as last seen: they still need this computer's file at the place."""
        with self.lock:
            if self.state is None:
                return []
            _, _, others, rows = self._units()
            healthy = {o["id"]: o.get("name") or "Another computer" for o in others if "units" in o}
            ids = {c for r in rows for c in r["waiting"] if c in healthy}
            if any(r["state"] == "both" and "" in r["candidates"] for r in rows):
                ids = set(healthy)  # this computer's version of something is not taken by all
            return sorted(healthy[c] for c in ids)

    def detach(self) -> None:
        """Forget the set-up in memory only (the program is being uninstalled):
        from now on nothing is sent or written, by the courier or at the end.
        ``sync.json`` itself is left to the uninstaller."""
        self._stop.set()
        self._wake.set()
        with self._io:
            with self.lock:
                self.state, self._key, self._place, self._parsed = None, None, None, {}

    def forget(self, computer: str) -> None:
        """A computer that left: remove its files from the place."""
        with self.lock:
            if self.state is None:
                raise SyncError("Synchronisation is not set up.")
            if not FILE_NAME.match(f"rota-{computer}.rpsync") or computer == self.state["computer_id"]:
                raise SyncError("This is not another computer.")
            place = self._place
        try:
            with self._io:
                place.delete(f"rota-{computer}.rpsync")
                place.delete(f"pen-{computer}.rpsync")
        except places.PlaceError as error:
            raise SyncError(str(error)) from None
        self.exchange()

    def rename(self, name: str) -> None:
        with self.lock:
            if self.state is None:
                raise SyncError("Synchronisation is not set up.")
            self.state["name"] = " ".join(str(name or "").split())[:40] or self.state["name"]
            self._save()
        self.touch()

    def set_auto(self, on: bool) -> None:
        with self.lock:
            if self.state is None:
                raise SyncError("Synchronisation is not set up.")
            self.state["auto"] = bool(on)
            self._save()
        self._incoming = True

    def code(self) -> str:
        with self.lock:
            if self.state is None:
                raise SyncError("Synchronisation is not set up.")
            code = self.state["code"]
            return "-".join(code[i:i + 4] for i in range(0, len(code), 4))

    # -- this computer's documents ------------------------------------------------

    def _own_names(self) -> List[str]:
        assert self.state is not None
        return [f"rota-{self.state['computer_id']}.rpsync", f"pen-{self.state['computer_id']}.rpsync"]

    def _wipe_cache(self) -> None:
        if self.cache.is_dir():
            for path in self.cache.iterdir():
                if path.is_file():
                    path.unlink()

    def _as_sent(self, unit: str, document: Json) -> Json:
        """A document as it is compared and sent: a month that is over keeps no comments."""
        if _month_over(unit, self.today()):
            scrub_comments(document)
        return document

    def _local(self) -> Dict[str, Json]:
        """This computer's documents, as they would be sent."""
        return {unit: self._as_sent(unit, document) for unit, document in transfer._documents(self.store).items()}

    def _refresh(self) -> Tuple[Dict[str, Json], Dict[str, str]]:
        """Documents, their hashes; counts a new version for each that changed since last seen."""
        assert self.state is not None
        documents = self._local()
        hashes = {unit: content_hash(doc) for unit, doc in documents.items()}
        me, today = self.state["computer_id"], self.today()
        over = set(self.state["over"])
        changed = False
        for unit, value in hashes.items():
            if self.state["hashes"].get(unit) == value:
                continue
            if _month_over(unit, today) and unit not in over and unit in self.state["hashes"]:
                # The month just ended: its comments are no longer sent. Every computer does
                # the same on its own, so this is not a new version (else all would disagree).
                over.add(unit)
            else:
                vector = self.state["versions"].setdefault(unit, {})
                vector[me] = vector.get(me, 0) + 1
            self.state["hashes"][unit] = value
            changed = True
        for unit in hashes:
            if _month_over(unit, today) and unit not in over:
                over.add(unit)
                changed = True
        if changed:
            self.state["over"] = sorted(over)
            self._save()
        return documents, hashes

    def _seal(self, documents: Dict[str, Json], hashes: Dict[str, str], force: bool = False) -> bool:
        """This computer's file, ready to leave (in the cache). Returns whether it was made anew."""
        assert self.state is not None and self._key is not None
        own = self.state["own"]
        digest = hashlib.sha256(json.dumps(
            [self.state["computer_id"], self.state["name"],
             {unit: [self.state["versions"].get(unit, {}), value] for unit, value in hashes.items()}],
            sort_keys=True).encode()).hexdigest()
        outbox = self.cache / OUTBOX
        fresh = time.time() - float(own.get("sealed", 0)) < RESEAL_SECONDS  # re-sent daily: "still here"
        if digest == own.get("digest") and outbox.exists() and fresh and not force:
            return False
        payload = {
            "kind": "rota",  # sealed inside: a data file cannot be passed off as a pen note, or the reverse
            "format": SYNC_FORMAT, "schema_version": SCHEMA_VERSION, "computer_id": self.state["computer_id"],
            "name": self.state["name"], "machine": self._machine(), "written": time.time(),
            "units": {unit: {"v": self.state["versions"].get(unit, {}), "hash": hashes[unit], "doc": doc}
                      for unit, doc in documents.items()},
        }
        data = seal(self._key, self.state["computer_id"], json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        _write_private(outbox, data)
        own.update(digest=digest, sealed=time.time(), sent="")
        self._save()
        return True

    def touch(self) -> None:
        """Something changed here: have it sent soon (never waits for the network)."""
        self._wake.set()

    def watched(self) -> None:
        """Someone is looking at the screen: look at the server more often than
        when nobody is (who is editing, what arrived), without ever waiting for it."""
        if not self.local and time.time() - self._last_round > WATCHED_SECONDS:
            self._wake.set()

    def publish(self, force: bool = False) -> bool:
        """Get this computer's file to the place now. Returns whether it was written there."""
        return self.exchange(force=force)["sent"]

    # -- the exchange with the place ----------------------------------------------

    def exchange(self, force: bool = False) -> Json:
        """One round with the place: look at what is there and fetch what changed,
        then count this computer's own changes and send its file if needed.

        Looking comes first: what the others hold is known before a change made
        here is counted as a new version. The only thing that talks to the
        place; no lock on the data is held while it does.
        Returns {"ok", "sent", "changed"}."""
        result = {"ok": False, "sent": False, "changed": False}
        with self._io:
            self._last_round = time.time()
            with self.lock:
                if self.state is None or self._key is None:
                    return result
                place = self._place
                own_name = f"rota-{self.state['computer_id']}.rpsync"
                mirror = dict(self.state["mirror"])
                was_online = self.connection["online"]
            listing: Optional[places.Listing] = None
            fetched: set = set()
            gone: set = set()
            bad: Dict[str, str] = {}
            try:
                listing = place.list()
                self._check_identity(place, listing, own_name)
                with self.lock:
                    if self.state is None:
                        return result
                    mine = self._own_names()
                    key = self._key
                # Computers already known first, a bounded number of them, one file at a time:
                # whoever can write at the place (without the key) must not be able to fill
                # this computer's memory or disk.
                wanted = sorted((n for n in listing.files if n not in mine), key=lambda n: (n not in mirror, n))
                ids: List[str] = []
                for name in wanted:
                    computer = places.NAME.match(name).group(2)  # type: ignore[union-attr]
                    if computer not in ids and len(ids) < MAX_COMPUTERS:
                        ids.append(computer)
                budget = ROUND_BYTES
                for name in wanted:
                    entry = listing.files[name]
                    pen_note = name.startswith("pen-")
                    if places.NAME.match(name).group(2) not in ids:  # type: ignore[union-attr]
                        gone.add(name)  # beyond what is looked at: as if not there
                        continue
                    known = mirror.get(name, {})
                    if known.get("sig") == entry.signature and (known.get("bad") or (self.cache / name).exists()):
                        if known.get("bad"):
                            bad[name] = known["bad"]
                        continue
                    limit = PEN_BYTES if pen_note else MAX_FILE_BYTES
                    if entry.size > limit:
                        bad[name] = "damaged"
                        (self.cache / name).unlink(missing_ok=True)
                        fetched.add(name)
                        continue
                    if budget <= 0:
                        gone.add(name)  # next round
                        continue
                    try:
                        data = place.read(name, limit)
                    except places.PlaceError as error:
                        if error.kind == "missing":
                            gone.add(name)  # removed since the listing (a computer left just now)
                            continue
                        if error.kind != "other":
                            raise
                        data = b""  # too big after all, or not a plain file: not Rota Planner's
                    budget -= len(data)
                    try:
                        unseal(key, data)  # type: ignore[arg-type]
                    except SyncError as error:
                        # Not sealed with this code, or cut on the way: said so, never kept.
                        bad[name] = getattr(error, "kind", "damaged")
                        (self.cache / name).unlink(missing_ok=True)
                    else:
                        _write_private(self.cache / name, data)
                    fetched.add(name)
            except places.PlaceError as error:
                problem: Optional[places.PlaceError] = error
            else:
                problem = None
            with self.lock:
                if self.state is None:
                    return result
                own_name = f"rota-{self.state['computer_id']}.rpsync"
                if problem is None and listing is not None:
                    listing = places.Listing({n: e for n, e in listing.files.items() if n not in gone}, listing.now)
                    for name in fetched:
                        self._parsed.pop(name, None)
                    for name in list(mirror):
                        if name not in listing.files:  # a computer left, or was forgotten
                            (self.cache / name).unlink(missing_ok=True)
                            self._parsed.pop(name, None)
                    kept_own = {n: v for n, v in self.state["mirror"].items() if n in self._own_names() and n in listing.files}
                    self.state["mirror"] = dict(kept_own, **{
                        n: dict({"sig": e.signature, "modified": e.modified}, **({"bad": bad[n]} if n in bad else {}))
                        for n, e in listing.files.items()})
                    self.state["clock"] = listing.now - time.time()
                    names = {n for n in fetched | (set(mirror) - set(listing.files))
                             if n.startswith("rota-") and n not in self._own_names()}
                    result["changed"] = bool(names)
                # What the others hold that this computer last saw is its history too,
                # before a change made here counts as a new version.
                self._absorb([o for o in self._others() if "units" in o])
                if problem is None:
                    self._hand_over()
                documents, hashes = self._refresh()
                self._seal(documents, hashes, force)
                if problem is not None:
                    self._offline(problem, was_online)
                    return result
                assert listing is not None
                pending = self.state["own"].get("sent") != self.state["own"].get("digest")
                outbox = (self.cache / OUTBOX).read_bytes() if (pending or force or own_name not in listing.files) else b""
            try:
                if outbox:
                    place.write(own_name, outbox)
                    result["sent"] = True
                self._pen_round(place, listing)
                after = place.list() if (result["sent"] or self._pen_wrote) else listing
            except places.PlaceError as error:
                with self.lock:
                    if self.state is not None:
                        self._offline(error, was_online)
                        self._save()
                return result
            with self.lock:
                if self.state is None:
                    return result
                for name in self._own_names():
                    if name in after.files:
                        self.state["mirror"][name] = {"sig": after.files[name].signature, "modified": after.files[name].modified}
                    else:
                        self.state["mirror"].pop(name, None)
                if result["sent"]:
                    self.state["own"]["sent"] = self.state["own"].get("digest")
                    if own_name in after.files:
                        self.state["own"]["sig"] = after.files[own_name].signature
                if was_online is False:
                    self._note("Back online." + (" Changes sent." if result["sent"] else ""), "ok")
                self.connection.update(online=True, since=self.connection["since"] if was_online else time.time(),
                                       last_ok=time.time(), problem="", kind="", retry=0.0)
                if result["changed"]:
                    self._incoming = True
                self._restored_elsewhere()
                self._save()
                result["ok"] = True
        return result

    def _offline(self, error: places.PlaceError, was_online: Optional[bool]) -> None:
        waiting = error.kind in PASSING_KINDS
        if was_online is not False and waiting:
            self._note(("Connection lost" if was_online else "No connection") + ": working offline. "
                       "Changes are kept here and sent when it is back.")
            self._save()
        elif error.kind != self.connection["kind"] and not waiting:
            self._note(str(error), "bad")
            self._save()
        self.connection.update(online=False, since=time.time() if was_online is not False else self.connection["since"],
                               problem=str(error), kind=error.kind, retry=error.retry_after)

    def _check_identity(self, place: Any, listing: places.Listing, own_name: str) -> None:
        """Is the file at the place under this computer's name really this computer's last one?

        If another computer wrote it (a copy of this data folder), or if it holds
        newer versions than this computer remembers (the data folder was restored
        from an older copy), this computer takes a new identity: counting on from
        old numbers would let old data pass for new."""
        entry = listing.files.get(own_name)
        with self.lock:
            known = self.state["own"].get("sig") if self.state else None
        if entry is None or (self._own_checked and entry.signature == known):
            self._own_checked = True
            return
        data = place.read(own_name)
        with self.lock:
            if self.state is None:
                return
            self._own_checked = True
            me = self.state["computer_id"]
            try:
                header, raw = unseal(self._key, data)  # type: ignore[arg-type]
                payload = json.loads(raw.decode("utf-8"))
                units = payload.get("units") or {}
                # Another computer's file, or a pen note, put there under this computer's name
                # says nothing about this computer: it is simply written over.
                if header.get("computer") != me or payload.get("computer_id") != me \
                        or payload.get("kind", "rota") != "rota" or not isinstance(units, dict):
                    raise ValueError
            except (ValueError, UnicodeDecodeError, AttributeError):
                self.state["own"]["sent"] = ""  # unreadable (cut on the way) or not ours: written again
                return
            if payload.get("machine") != self._machine():
                self._new_identity("This computer's data is a copy of another computer's (or its folder was moved): "
                                   "it now takes part under a name of its own.")
                copied = True
            else:
                copied = False
        if copied:
            self._drop_superseded(place, own_name)
            return
        with self.lock:
            if self.state is None:
                return
            me = self.state["computer_id"]
            ahead = any(int((entry_.get("v") or {}).get(me, 0)) > int(self.state["versions"].get(unit, {}).get(me, 0))
                        for unit, entry_ in units.items() if isinstance(entry_, dict))
            if not ahead:
                self.state["own"]["sig"] = entry.signature
                if payload.get("written", 0) and self.state["own"].get("sealed", 0) \
                        and abs(float(payload["written"]) - float(self.state["own"]["sealed"])) > 1:
                    self.state["own"]["sent"] = ""  # an older copy handed back by the service: sent again
                return
            self._restored()
        self._drop_superseded(place, own_name)

    @staticmethod
    def _drop_superseded(place: Any, name: str) -> None:
        """The file under this computer's former name goes: its new one replaces it. If
        another computer really is still writing it (a copied data folder), that
        computer puts it back at its next round, so nothing is lost either way."""
        for former in (name, name.replace("rota-", "pen-", 1)):  # its pen note too
            try:
                place.delete(former)
            except places.PlaceError:
                pass

    def _new_identity(self, why: str) -> None:
        assert self.state is not None
        self.state["computer_id"] = f"c-{secrets.token_hex(6)}"
        self.state["own"], self.state["pen"] = {}, {}
        (self.cache / OUTBOX).unlink(missing_ok=True)
        documents, hashes = self._refresh()
        self._seal(documents, hashes, force=True)
        self._note(why, "warn")
        self._save()

    def _restored(self) -> None:
        """This computer's data is older than what it had sent: a backup was put back."""
        assert self.state is not None
        if self.state.get("hold") == "restored":
            return
        self.state["hold"] = "restored"
        self._new_identity("This computer's data is older than what it had sent (was a backup put back?). "
                           "Nothing is taken or sent as newer until you say which to keep.")

    def _restored_elsewhere(self) -> None:
        """The same check from the others' files: do they remember more of this computer than it does?"""
        assert self.state is not None
        me = self.state["computer_id"]
        for other in self._others():
            for unit, entry in (other.get("units") or {}).items():
                if int(entry["v"].get(me, 0)) > int(self.state["versions"].get(unit, {}).get(me, 0)):
                    self._restored()
                    return

    def resume(self, keep: str) -> None:
        """After a restored backup: ``newer`` takes what the others have; ``mine``
        makes this computer's (older) data the newest everywhere."""
        with self.lock:
            if self.state is None or self.state.get("hold") != "restored":
                raise SyncError("Nothing is waiting for this choice.")
            if keep not in ("newer", "mine"):
                raise SyncError("Choose which data to keep.")
            if keep == "mine":
                me = self.state["computer_id"]
                for unit in self.state["hashes"]:
                    vector = self.state["versions"].setdefault(unit, {})
                    for other in self._others():
                        vector.update(merged(vector, (other.get("units") or {}).get(unit, {}).get("v", {})))
                    vector[me] = vector.get(me, 0) + 1
                self._note("This computer's data was kept as the newest.", "warn")
            else:
                self._note("The others' newer data will be taken.")
            self.state["hold"] = ""
            self._save()
            self._incoming = True
        self.touch()

    # -- the others' files (from the cache, never the network) ------------------------

    def _others(self) -> List[Json]:
        """Every other computer's file, read and checked: {"id", "name", "written",
        "modified", "units"} or {"id", "problem", "kind"}."""
        assert self.state is not None
        me, found = self.state["computer_id"], []
        for name in sorted(self.state["mirror"]):
            match = FILE_NAME.match(name)
            if not match or match.group(1) == me:
                continue
            refused = self.state["mirror"][name].get("bad")
            if refused:
                words = (_other_code() if refused == "other-code" else _damaged()).args[0]
                found.append({"id": match.group(1), "problem": words, "kind": refused,
                              "modified": float(self.state["mirror"][name].get("modified") or 0)})
                continue
            path = self.cache / name
            try:
                stat = path.stat()
            except OSError:
                continue
            signature = (stat.st_mtime_ns, stat.st_size, f"{self.today():%Y-%m}")
            if self._parsed.get(name, (None,))[0] != signature:
                self._parsed[name] = (signature, self._read_other(path, match.group(1)))
            other = dict(self._parsed[name][1], id=match.group(1),
                         modified=float(self.state["mirror"][name].get("modified") or 0))
            found.append(other)
        return found

    def _read_other(self, path: Path, computer: str) -> Json:
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                return {"problem": "Another computer's file is far too big to be Rota Planner data.", "kind": "damaged"}
            header, raw = unseal(self._key, path.read_bytes())  # type: ignore[arg-type]
            payload = json.loads(raw.decode("utf-8"))
            if header.get("computer") != computer or payload.get("computer_id") != computer \
                    or payload.get("kind", "rota") != "rota":
                raise _damaged()
            if payload.get("format") != SYNC_FORMAT or not isinstance(payload.get("units"), dict):
                raise SyncError("Another computer uses another version of Rota Planner: update them all.")
            if int(payload.get("schema_version", 0)) > SCHEMA_VERSION:
                raise SyncError("Another computer has a newer Rota Planner: update this one first.")
            units = {}
            for unit, entry in payload["units"].items():
                if not (unit in transfer.TOP_LEVEL or transfer.MONTH_NAME.match(unit)) or not isinstance(entry, dict) \
                        or not isinstance(entry.get("doc"), dict) or not isinstance(entry.get("v"), dict):
                    raise _damaged()
                # Compared as this computer would hold it: brought to this program's format,
                # and without the comments of a month that is over.
                document = self._as_sent(unit, migrate(entry["doc"]))
                units[unit] = {"v": {str(k): int(v) for k, v in entry["v"].items()}, "doc": document,
                               "hash": content_hash(document)}
            return {"name": str(payload.get("name") or "")[:40], "written": float(payload.get("written") or 0), "units": units}
        except OSError:
            return {"problem": "Another computer's file cannot be read.", "kind": "damaged"}
        except SyncError as error:
            return {"problem": str(error), "kind": getattr(error, "kind", "version")}
        except (ValueError, TypeError, UnicodeDecodeError, AttributeError, StoreError):
            return {"problem": _damaged().args[0], "kind": "damaged"}

    def _absorb(self, others: List[Json]) -> None:
        """Where another computer holds what this one last saw, its history is ours too.

        Without this, the same data made separately on several computers, then
        changed on one, would look changed on all.
        """
        changed = False
        for other in others:
            for unit, entry in (other.get("units") or {}).items():
                if self.state["hashes"].get(unit) == entry["hash"]:
                    vector = merged(self.state["versions"].get(unit, {}), entry["v"])
                    if vector != self.state["versions"].get(unit):
                        self.state["versions"][unit] = vector
                        changed = True
        if changed:
            self._save()

    # -- status, preview, apply ------------------------------------------------------

    def _look(self) -> None:
        """Before showing anything: a folder is looked at on the spot (it costs
        nothing); a server never is (the courier does, in the background)."""
        if self.local and not self._io.locked():  # never waits behind another exchange (a take)
            self.exchange()

    def _units(self) -> Tuple[Dict[str, Json], Dict[str, str], List[Json], List[Json]]:
        """(documents, hashes, the other computers, one row per unit)."""
        others = self._others()
        healthy = [o for o in others if "units" in o]
        self._absorb(healthy)
        documents, hashes = self._refresh()
        names = {o["id"]: o["name"] or "another computer" for o in healthy}
        rows: List[Json] = []
        units = set(documents)
        for other in healthy:
            units |= set(other["units"])
        for unit in sorted(units, key=_sort_key):
            mine = (self.state["versions"].get(unit, {}), hashes[unit]) if unit in documents else None
            theirs = {o["id"]: (o["units"][unit]["v"], o["units"][unit]["hash"]) for o in healthy if unit in o["units"]}
            row = rank(mine, theirs, [o["id"] for o in healthy if unit not in o["units"]])
            # A unit this computer once had and no longer has (removed by an import) is never
            # brought back by itself: that is for the coordinator to decide.
            row["auto"] = row["state"] == "get" and (mine is not None or unit not in self.state["hashes"])
            row.update(unit=unit, label=label(unit),
                       source_name=names.get(row["source"], ""),
                       candidate_names=["this computer" if c == "" else names.get(c, "") for c in row["candidates"]])
            rows.append(row)
        return documents, hashes, others, rows

    def _problem(self, others: List[Json]) -> Tuple[Optional[str], Optional[str]]:
        """(what is wrong, its kind) for the status, the most pressing first."""
        if self.state.get("hold") == "restored":
            return ("This computer's data is older than what it had sent (was a backup put back?). "
                    "Choose which to keep.", "restored")
        if self.connection["online"] is False:
            return self.connection["problem"], self.connection["kind"]
        broken = [o for o in others if "units" not in o]
        if broken and len(broken) == len(others):
            return broken[0]["problem"], broken[0]["kind"]
        if not others:
            return "No other computer has written its file yet.", "alone"
        if broken:
            return broken[0]["problem"], broken[0]["kind"]
        return None, None

    def status(self) -> Json:
        self._look()
        with self.lock:
            self._snapshot += 1
            if self.state is None:
                return {"configured": False}
            _, _, others, rows = self._units()
            healthy = [o for o in others if "units" in o]
            counts = {k: sum(1 for r in rows if r["state"] == k) for k in ("sent", "get", "both")}
            if not healthy:
                counts["get"] = counts["both"] = 0
            problem, kind = self._problem(others)
            now = time.time() + float(self.state.get("clock") or 0)
            pen = self._pen_status()
            computers = []
            for other in others:
                behind = sum(1 for r in rows if other["id"] in r["waiting"])
                ahead = sum(1 for r in rows if r["state"] == "get" and r["source"] == other["id"])
                gone = bool(other["modified"]) and now - other["modified"] > GONE_SECONDS
                seen = ago(other["modified"], now) if other["modified"] else ""
                state = "problem" if "units" not in other else "gone" if gone else "behind" if behind \
                    else "ahead" if ahead else "ok"
                editing = pen.get("holder") == other["id"] and not pen.get("stale")
                words = {"problem": other.get("problem", ""),
                         "gone": f"Not seen for {seen.replace(' ago', '')}",
                         "behind": f"Has not taken {behind} change{'s' if behind != 1 else ''} yet",
                         "ahead": f"Has {ahead} newer change{'s' if ahead != 1 else ''}",
                         "ok": "Editing now" if editing else "Up to date"}[state]
                if state in ("behind", "ahead", "ok") and seen:
                    words += f" · seen {seen}"
                computers.append({"id": other["id"], "name": other.get("name") or "Another computer", "state": state,
                                  "behind": behind,
                                  "text": words, "editing": editing})
            first = healthy[0] if healthy else None
            describe = self._place.describe()
            auto = bool(self.state.get("auto", True))
            units = [_shown(r) for r in rows if r["state"] != "same"] if healthy else []
            pending = self.state["own"].get("sent") != self.state["own"].get("digest")
            result = {
                "configured": True,
                "name": self.state["name"],
                "folder": describe.get("folder", ""),
                "place": dict(describe, has_password=bool(self.state["place"].get("password"))),
                "problem": problem,
                "kind": kind,
                "other": {"name": first["name"], "written": first["written"]} if first else None,
                "others": computers,
                "units": units,
                # Something for the coordinator to decide: versions that differ, or newer
                # data that is not taken by itself.
                "needs_choice": any(u["state"] == "both" or (u["state"] == "get" and not (auto and u.get("auto")))
                                    for u in units),
                # A problem that does not pass by itself: to put right in Settings.
                "to_fix": bool(problem) and kind in SETTINGS_KINDS + ("other-code", "version"),
                "passing": kind in PASSING_KINDS,
                "me": {"name": self.state["name"], "state": "editing" if pen.get("mine") else "ok",
                       "editing": bool(pen.get("mine")),
                       "text": "Editing now" if pen.get("mine") else "Has changes to send" if pending else "Up to date"},
                "counts": counts,
                "connection": {"online": self.connection["online"], "since": self.connection["since"],
                               "last_ok": self.connection["last_ok"], "local": self.local,
                               "text": self._connection_words()},
                "pending": pending,
                "auto": auto,
                "hold": self.state.get("hold", ""),
                "applied": self.state.get("applied", {"seq": 0}),
                "pen": pen,
                "snapshot": self._snapshot,
                "log": [dict(e, when=time.strftime("%H:%M", time.localtime(e["at"]))) for e in self.state["log"][-12:]][::-1],
            }
            result["summary"] = summary(result)
            result["icon"] = _icon(result)
            return result

    def _connection_words(self) -> str:
        """The line between this computer and the place, in a few words."""
        def clock(moment: float) -> str:
            return time.strftime("%H:%M", time.localtime(moment)) if moment else ""
        connection = self.connection
        if connection["online"] is None:
            return "Looking…"
        if connection["online"]:
            return "Shared folder found" if self.local else f"Connected · last contact {clock(connection['last_ok'])}"
        if connection["kind"] in PASSING_KINDS:
            return f"No connection since {clock(connection['since'])}"
        return "Not working: see below"

    def _plan(self) -> Json:
        documents, hashes, others, rows = self._units()
        healthy = {o["id"]: o for o in others if "units" in o}
        if not healthy:
            raise SyncError(self._problem(others)[0] or "Nothing to synchronise with.")
        token = hashlib.sha256(json.dumps(
            [[r["unit"], r["state"], hashes.get(r["unit"]),
              sorted([cid, o["units"][r["unit"]]["hash"], sorted(o["units"][r["unit"]]["v"].items())]
                     for cid, o in healthy.items() if r["unit"] in o["units"])] for r in rows]
        ).encode()).hexdigest()
        return {"documents": documents, "hashes": hashes, "others": healthy, "rows": rows, "token": token}

    def preview(self) -> Json:
        self._look()
        with self.lock:
            if self.state is None:
                raise SyncError("Synchronisation is not set up.")
            plan = self._plan()
            items = []
            for row in plan["rows"]:
                if row["state"] not in ("get", "both"):
                    continue
                unit, item = row["unit"], _shown(row)
                if row["state"] == "both":
                    item["choices"] = []
                    for computer, name in zip(row["candidates"], row["candidate_names"]):
                        theirs = plan["others"][computer]["units"][unit]["doc"] if computer else None
                        item["choices"].append({
                            "id": computer or "mine", "name": name,
                            "difference": difference(unit, plan["documents"].get(unit), theirs) if computer else ""})
                    elsewhere = [c for c in item["choices"] if c["id"] != "mine"]
                    item["difference"] = elsewhere[0]["difference"] if elsewhere else ""
                items.append(item)
            names = sorted({o["name"] or "another computer" for o in plan["others"].values()})
            return {"plan": plan["token"], "other": ", ".join(names), "items": items,
                    "sent": [r["label"] for r in plan["rows"] if r["state"] == "sent"]}

    def apply(self, token: str, choices: Dict[str, str]) -> Json:
        """Take what the others have newer, and what the coordinator chose."""
        self._look()
        with self.lock:
            if self.state is None:
                raise SyncError("Synchronisation is not set up.")
            plan = self._plan()
            if token != plan["token"]:
                raise SyncError("Something changed on one of the computers since: look again before synchronising.")
            result = self._take(plan, choices, automatic=False)
        self._look()  # never with the lock held: the courier takes the two locks the other way round
        return result

    def apply_automatic(self) -> Json:
        """Take, without asking, what another computer has newer and this one did
        not touch. Anything that needs a choice is left for the coordinator."""
        result = self._apply_automatic()
        if result["taken"]:
            self._look()
        return result

    def _apply_automatic(self) -> Json:
        with self.lock:
            nothing = {"taken": [], "kept": [], "replaced": [], "replaced_months": [], "warnings": [], "safety": "", "from": []}
            # While asking for the pen, what is newer is taken even if taking by itself is off:
            # the coordinator asked to carry on from there.
            wanted_anyway = bool(self.state is not None and self.state["pen"].get("asking"))
            if self.state is None or not (self.state.get("auto", True) or wanted_anyway) or self.state.get("hold"):
                return nothing
            try:
                plan = self._plan()
            except SyncError:
                return nothing
            wanted = [r["unit"] for r in plan["rows"] if r["auto"]]
            signature = hashlib.sha256(json.dumps([plan["token"], wanted]).encode()).hexdigest()
            if not wanted or self.state.get("refused") == signature:
                return nothing  # what could not be used is not tried again until something changes
            try:
                return self._take(plan, {}, automatic=True)
            except SyncError as error:
                self.state["refused"] = signature
                self._note(str(error), "bad")
                self._save()
                return nothing

    def _take(self, plan: Json, choices: Dict[str, str], automatic: bool) -> Json:
        take: Dict[str, str] = {}  # unit -> the computer it is taken from
        keep: List[str] = []
        chosen: List[str] = []     # units where a choice was made between versions
        for row in plan["rows"]:
            unit = row["unit"]
            if row["state"] == "get":
                if row["auto"] or not automatic:
                    take[unit] = row["source"]
            elif row["state"] == "both" and not automatic:
                choice = choices.get(unit)
                elsewhere = [c for c in row["candidates"] if c]
                if choice == "theirs" and len(elsewhere) == 1:
                    choice = elsewhere[0]
                if choice == "mine" and "" in row["candidates"]:
                    keep.append(unit)
                elif choice in elsewhere:
                    take[unit] = choice
                else:
                    raise SyncError(f"Choose which {row['label']} to keep.")
                chosen.append(unit)
        documents = dict(plan["documents"])
        for unit, computer in take.items():
            documents[unit] = plan["others"][computer]["units"][unit]["doc"]
        try:
            documents = transfer.checked(documents)
        except transfer.TransferError as error:
            raise SyncError(f"What another computer sent cannot be used, so nothing was changed: {error}") from None
        safety = ""
        if take:
            # Replacing something that was changed here is kept apart, and for longer.
            replaces_work = any(unit in chosen for unit in take)
            safety = transfer.keep_safety_copy(self.store, "before-choice" if replaces_work else "before-sync",
                                               keep=CHOICE_COPIES if replaces_work else ROUTINE_COPIES)
        for unit in take:
            self.store._write(self.store.root / unit, documents[unit])  # noqa: SLF001 — same atomic write, with backup
            match = transfer.MONTH_NAME.match(unit)
            if match and not any(r.get("comment") for r in (documents[unit].get("availability") or {}).values()):
                self.store.scrub_backups(match.group(1), scrub_comments)  # deleted there: not kept here either
        me = self.state["computer_id"]
        for row in plan["rows"]:
            unit = row["unit"]
            if unit not in take and unit not in keep:
                continue
            vector = self.state["versions"].get(unit, {})
            involved = [take[unit]] if unit in take and unit not in chosen else [c for c in row["candidates"] if c]
            for computer in involved:
                vector = merged(vector, plan["others"][computer]["units"][unit]["v"])
            # Every choice made between versions is a new version of this computer,
            # whichever was kept: if two computers choose at the same moment, each
            # taking the other's, they see it instead of believing they agree.
            if unit in chosen:
                vector[me] = vector.get(me, 0) + 1
            self.state["versions"][unit] = vector
        for unit in take:
            self.state["hashes"][unit] = content_hash(self._as_sent(
                unit, json.loads((self.store.root / unit).read_text(encoding="utf-8"))))
        taken_from = sorted({plan["others"][c]["name"] or "another computer" for c in take.values()})
        if take:
            words = f"Took {_listed([label(u) for u in take])} from {_listed(taken_from)}."
            self._note(words, "ok")
            self.state["applied"] = {"seq": int(self.state["applied"].get("seq", 0)) + 1, "at": time.time(),
                                     "text": words, "units": sorted(take), "automatic": automatic}
        if keep:
            self._note(f"Kept this computer's {_listed([label(u) for u in keep])}.")
        self.state.pop("refused", None)
        self._save()
        documents_now, hashes = self._refresh()
        self._seal(documents_now, hashes, force=True)
        self.touch()  # the callers send it at once to a folder, once the lock is released
        return {"taken": [label(u) for u in take], "kept": [label(u) for u in keep], "safety": safety,
                "warnings": dangling(documents), "replaced_months": [u[7:14] for u in take if u.startswith("months/")],
                "replaced": sorted(take), "from": taken_from}

    # -- in the background ---------------------------------------------------------

    def deliver(self) -> bool:
        """Tell the app that the others' files changed (it may take them). Never
        called with a lock held: the app takes its own first. Returns whether it was told."""
        if not self._incoming or self.on_incoming is None:
            return False
        self._incoming = False
        try:
            self.on_incoming()
        except Exception:  # noqa: BLE001 — never stop the courier for this
            pass
        return True

    def start_background(self) -> None:
        """The courier: keeps this computer's file sent and the others' fetched while the program runs."""
        def loop() -> None:
            wait = 2.0
            while not self._stop.is_set():
                woken = self._wake.wait(wait)
                if woken and not self.local:
                    self._stop.wait(GROUP_SECONDS)  # several changes in a row leave together
                if self._stop.is_set():
                    return
                self._wake.clear()
                try:
                    result = self.exchange()
                    self.deliver()
                    self.follow_up()
                except Exception:  # noqa: BLE001 — never stop the program for this
                    result = {"ok": False}
                if not self.configured:
                    wait = IDLE_SECONDS
                elif result["ok"] and self.asking() and not self.pen().get("stale"):
                    wait = ASKING_SECONDS  # not while it has gone quiet: that may last all night
                elif result["ok"]:
                    wait = LOCAL_SECONDS if self.local else SERVER_SECONDS
                else:
                    retry = float(self.connection.get("retry") or 0)
                    slow = self.connection.get("kind") in SETTINGS_KINDS
                    wait = max(retry, BACKOFF_MAX if slow else min(BACKOFF_MAX, max(BACKOFF_MIN, wait * 2)))
        threading.Thread(target=loop, name="sync-courier", daemon=True).start()

    def goodbye(self) -> None:
        """The program ends: one last round, and the pen goes back."""
        self._stop.set()
        self._wake.set()
        try:
            if self.configured:
                if self.pen().get("mine") or self.asking():
                    # Not for long behind a take stuck on the network: the program is closing.
                    taken = self._taking.acquire(timeout=3)
                    try:
                        self._release_pen()
                    finally:
                        if taken:
                            self._taking.release()
                else:
                    self.exchange()
        except Exception:  # noqa: BLE001 — leaving must never fail
            pass

    def check_now(self) -> Json:
        """"Check now": one round at once, then the status."""
        self.exchange()
        self.deliver()
        return self.status()

    # -- the pen: one computer edits at a time ------------------------------------
    #
    # Each computer writes only its own small note (``pen-<id>.rpsync``, sealed
    # like the rest), so no file ever has two writers. A note says "I ask for the
    # pen" or "I hold it", with the time it was written (``beat``, by the place's
    # clock as this computer last saw it), and is written again every minute
    # while held. Everything that counts is inside the seal: a note is believed
    # only if it opens with the key, is a pen note, names its own computer, and
    # is recent. So someone who can write at the place but has no key cannot
    # hold, block or take the pen with copied, replayed or made-up files.
    #
    # Asking for the pen (``ask_pen``): the note says "I ask <the holder> to hand
    # over". The holder, at its next round, sends its changes and lets go
    # (``_hand_over``, before its file is sealed, so the file carries every change);
    # the asking computer then takes what is newer and the pen (``follow_up``).
    # Nobody confirms on the holder: it is usually the same person at another
    # computer. A holder that does not answer (asleep, switched off) goes stale
    # after a few minutes, and "Take over" is offered as before.
    #
    # Taking the pen (``take_pen``) runs one at a time (``_taking``): a second
    # press, a change or the courier's ``follow_up`` waits for the first, then
    # finds the pen taken. Its state changes only once its notes are written, in
    # the same exchange with the place, so no round sees a take half done.

    def _now_there(self) -> float:
        """The place's clock, as last seen."""
        return time.time() + float((self.state or {}).get("clock") or 0)

    def _pen_note(self, holding: bool, since: Optional[float] = None, asking: Optional[str] = None,
                  asked: Optional[float] = None) -> bytes:
        """This computer's note, sealed. What is not given is the state's."""
        assert self.state is not None and self._key is not None
        pen = self.state["pen"]
        since = float(pen.get("since") or 0) if since is None else since
        asking = (pen.get("asking") or "") if asking is None else asking
        asked = float(pen.get("asked_there") or 0) if asked is None else asked
        note = {"kind": "pen", "computer_id": self.state["computer_id"], "name": self.state["name"],
                "holding": holding, "since": since, "beat": self._now_there()}
        if not holding and asking:
            note["asking"] = asking
        if not holding and asked:
            note["asked"] = asked  # its place in line
        return seal(self._key, self.state["computer_id"], json.dumps(note).encode("utf-8"))

    def _read_pen(self, data: bytes, computer: str) -> Optional[Json]:
        try:
            header, raw = unseal(self._key, data)  # type: ignore[arg-type]
            note = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(note, dict) or note.get("kind") != "pen" \
                or header.get("computer") != computer or note.get("computer_id") != computer:
            return None
        try:
            beat, since = float(note.get("beat") or 0), float(note.get("since") or 0)
            asked = float(note.get("asked") or 0)
        except (TypeError, ValueError):
            return None
        asking = note.get("asking")
        asking = asking if isinstance(asking, str) and FILE_NAME.match(f"rota-{asking}.rpsync") else ""
        return {"id": computer, "name": str(note.get("name") or "")[:40] or "Another computer",
                "holding": bool(note.get("holding")), "since": since, "beat": beat,
                "asking": "" if note.get("holding") else asking,
                "asked": 0.0 if note.get("holding") else asked,
                # Recent by its own (sealed) time: putting an old note back does not revive it.
                "live": -PEN_STALE_SECONDS < self._now_there() - beat < PEN_STALE_SECONDS}

    def _pens(self) -> List[Json]:
        """The other computers' pen notes, from the cache."""
        assert self.state is not None
        me, found = self.state["computer_id"], []
        for name in sorted(self.state["mirror"]):
            match = PEN_NAME.match(name)
            if not match or match.group(1) == me:
                continue
            try:
                note = self._read_pen((self.cache / name).read_bytes(), match.group(1))
            except OSError:
                note = None
            if note is not None:
                found.append(dict(note, modified=note["beat"]))
        return found

    def _pen_status(self) -> Json:
        """Who edits, for the screens (the words are made here)."""
        assert self.state is not None
        pen, now = self.state["pen"], self._now_there()
        others = any(FILE_NAME.match(n) and n not in self._own_names() for n in self.state["mirror"])
        pens = self._pens()
        holders = sorted((p for p in pens if p["holding"]), key=lambda p: (p["since"], p["id"]))
        live = [p for p in holders if p["live"]]
        stale = [p for p in holders if not p["live"] and now - p["modified"] < PEN_FORGOTTEN_SECONDS]
        clock = float(self.state.get("clock") or 0)
        notice = pen.get("notice") or ({"text": pen["lost"], "tone": "warn"} if pen.get("lost") else None)
        result: Json = {"holder": None, "mine": False, "name": "", "stale": False, "needed": others,
                        "working_offline": bool(pen.get("offline")), "lost": pen.get("lost", ""), "text": "",
                        "asking": bool(pen.get("asking")), "notice": notice}
        if pen.get("asking"):
            asked = pen["asking"]
            note = next((p for p in holders if p["id"] == asked), None)
            name = note["name"] if note else pen.get("name") or "the other computer"
            result.update(holder=asked, name=name)
            if note is None and live:  # it let go, but a third computer took the pen meanwhile
                result.update(holder=live[0]["id"], name=live[0]["name"],
                              text=f"{live[0]['name']} is editing: you can look, not change.")
            elif note is None:
                result.update(handed=True, text=f"{name} has handed over: taking its changes, then you can edit…")
            elif note["live"]:
                left = max(1, -(-int(PEN_STALE_SECONDS - (now - note["modified"])) // 60))
                result.update(text=f"{name} is there after all: asking it to send its changes and hand over…"
                              if pen.get("found") else f"Asking {name} to send its changes and hand over the editing…",
                              hint=f"Usually within a minute. If {name} is switched off or asleep, you can "
                                   f"take over in {left} min.")
            else:
                result.update(stale=True, text=f"{name} does not answer (not seen for {ago(note['modified'], now).replace(' ago', '')}).")
        elif pen.get("mine"):
            result.update(holder="me", mine=True, name=self.state["name"], text="You are editing.")
        elif pen.get("offline"):
            result.update(text="Working offline: your changes are sent when the connection is back, and may then "
                               "have to be chosen against the others'.")
        elif live:
            since = time.strftime("%H:%M", time.localtime(live[0]["since"] - clock)) if live[0]["since"] else ""
            result.update(holder=live[0]["id"], name=live[0]["name"],
                          text=f"{live[0]['name']} is editing{' since ' + since if since else ''}: you can look, not change.")
        elif stale:
            result.update(holder=stale[0]["id"], name=stale[0]["name"], stale=True,
                          text=f"{stale[0]['name']} was editing, not seen for {ago(stale[0]['modified'], now).replace(' ago', '')}.")
        else:
            asker = self._asked_by(pens)  # this computer handed over: the one that asked takes it in a moment
            if asker is not None:
                result.update(holder=asker["id"], name=asker["name"], handing=True,
                              text=f"{asker['name']} is taking over the editing: you can look, not change.")
        # Look only: someone else edits (or is about to), so nothing here can be changed.
        return _looking(result)

    def _pen_round(self, place: Any, listing: places.Listing) -> None:
        """While this computer holds the pen: say so again, notice a take-over,
        give it back after a long while without a change. While it does not:
        make sure no note of its own lingers at the place."""
        self._pen_wrote = False
        with self.lock:
            if self.state is None:
                return
            pen, me = self.state["pen"], self.state["computer_id"]
            name = f"pen-{me}.rpsync"
            holding = bool(pen.get("mine"))
            # Too long, by the place's clock, since this computer last said "I hold it"?
            lapsed = listing.now - float(pen.get("there") or 0) >= PEN_STALE_SECONDS
            idle = time.time() - self._last_edit > PEN_IDLE_SECONDS
            due = time.time() - float(pen.get("beat") or 0) >= PEN_BEAT_SECONDS
        if not holding:
            with self.lock:
                asking = bool(self.state is not None and self.state["pen"].get("asking"))
            if asking:  # said again, like a held pen: a request nobody repeats lapses
                if due or name not in listing.files:
                    with self.lock:
                        note = self._pen_note(holding=False)
                    place.write(name, note)
                    with self.lock:
                        if self.state is not None and self.state["pen"].get("asking"):
                            self.state["pen"]["beat"] = time.time()
                            self.state["pen"].pop("found", None)  # said again: no longer news
                    self._pen_wrote = True
            elif name in listing.files:  # left by a crash, or put back by someone else: it means nothing
                place.delete(name)
                self._pen_wrote = True
            return
        taken = False
        if lapsed:
            # Away long enough for the others to take the pen: did one of them (a real, recent note)?
            for other in sorted(listing.files):
                match = PEN_NAME.match(other)
                if not match or other == name or listing.files[other].size > PEN_BYTES:
                    continue
                try:
                    rival = self._read_pen(place.read(other, PEN_BYTES), match.group(1))
                except places.PlaceError:
                    rival = None
                if rival and rival["live"]:
                    taken = True
                    break
        if taken:
            try:
                place.delete(name)
            except places.PlaceError:
                pass
            with self.lock:
                self.state["pen"] = {"lost": "Another computer took over the editing while this one was not connected."}
                self._note(self.state["pen"]["lost"], "warn")
            self._pen_wrote = True
        elif idle:
            place.delete(name)
            with self.lock:
                self.state["pen"] = {}
                self._note("The pen was given back after a long while without a change.")
            self._pen_wrote = True
        elif due or name not in listing.files:
            with self.lock:
                note = self._pen_note(holding=True)
            place.write(name, note)
            with self.lock:
                self.state["pen"].update(beat=time.time(), there=self._now_there())
            self._pen_wrote = True

    def take_pen(self, force: bool = False, pressed: bool = False) -> Json:
        """Become the one computer that edits. ``force``: take over from a computer
        that held the pen and has not been seen for a while. ``pressed``: the
        coordinator asked for it (a button, or the request ``follow_up`` carries on):
        the network is always tried, and a holder found there after all is asked to
        hand over rather than refused. Returns the pen as the screens show it.
        One take at a time: a second one waits, then finds the pen taken."""
        with self._taking:
            return self._take_pen(force, pressed)

    def _take_pen(self, force: bool, pressed: bool) -> Json:
        with self.lock:
            if self.state is None:
                raise SyncError("Synchronisation is not set up.")
            if self.state["pen"].get("mine"):
                return self._pen_status()
        # A change, known to be without connection a moment ago: said at once rather than wait for the network again.
        known_offline = (not (force or pressed) and self.connection["online"] is False
                         and self.connection["kind"] in PASSING_KINDS
                         and time.time() - self._last_round < OFFLINE_TRUSTED_SECONDS)
        result = {"ok": False} if known_offline else self.exchange()
        self.deliver()  # what is newer is taken first: nobody edits old data
        if not result["ok"]:
            raise PenError(_no_connection("take the pen"), "offline", self.pen())
        ask = force or pressed  # a holder there after all is asked to hand over; a change is refused
        with self.lock:
            if self.state is None:
                raise SyncError("Synchronisation is not set up.")
            pen = self.state["pen"]
            if any(r["state"] == "get" for r in self._units()[3]):
                raise PenError("Another computer has newer changes: synchronise first, then try again.", "behind", self._pen_status())
            status = self._pen_status()
            asker = self._asked_by()
            if asker is not None:
                raise PenError(f"{asker['name']} is taking over the editing: you can look, not change.", "handing",
                               _looking(status, holder=asker["id"], name=asker["name"], handing=True,
                                        text=f"{asker['name']} is taking over the editing: you can look, not change."))
            found: Optional[Json] = None
            if status["holder"] not in (None, "me") and not status["stale"] and not status.get("handed"):
                if not ask:
                    raise PenError(status["text"], "held", status)
                found = {"id": status["holder"], "name": status["name"]}
            elif status["stale"] and not force:
                raise PenError(_only_if_sure(status["text"]), "stale", status)
            since = self._now_there()
            asked = float(pen.get("asked_there") or 0)  # taking after asking: its place in line
            place, me = self._place, self.state["computer_id"]
            name = f"pen-{me}.rpsync"
            # The state stays as it is until the outcome is known: a round meanwhile sees nothing half done.
            claim = self._pen_note(holding=False, since=since, asking="", asked=asked)
            held = self._pen_note(holding=True, since=since)
        winner: Optional[Json] = None
        try:
            with self._io:
                if found is None:
                    winner = self._claim(place, name, claim, held, asked, me, ask)
                    if winner is not None and winner["holding"] and ask:
                        found = winner
                if found is not None:
                    return self._ask_found(place, name, found)
                with self.lock:
                    if self.state is None:
                        raise SyncError("Synchronisation is not set up.")
                    if winner is None:
                        self.state["pen"] = {"mine": True, "since": since, "beat": time.time(), "there": self._now_there()}
                        self._last_edit = time.time()
                        self._note(f"{status['name']} handed over: this computer edits now." if status.get("handed")
                                   else "This computer took the pen.", "ok" if status.get("handed") else "")
                        self._save()
                        return self._pen_status()
                    self._say_again()
                    # Holding it, or asking for it first (at the same moment, or another computer already).
                    words = (f"{winner['name']} is editing" if winner["holding"] else f"{winner['name']} asked for the editing first")
                    raise PenError(f"{words}: you can look, not change.", "held",
                                   _looking(self._pen_status(), holder=winner["id"], name=winner["name"],
                                            text=f"{words}: you can look, not change."))
        except places.PlaceError as error:
            with self.lock:
                if self.state is not None:
                    self._say_again()
            raise PenError(f"The pen could not be taken: {error}", "offline", self.pen()) from None

    def _say_again(self) -> None:
        """A take that did not happen wrote over this computer's request: said again at the next round."""
        assert self.state is not None
        if self.state["pen"].get("asking"):
            self.state["pen"]["beat"] = 0

    def _claim(self, place: Any, name: str, claim: bytes, held: bytes, asked: float, me: str,
               ask: bool) -> Optional[Json]:
        """While taking, ``_io`` held: write the claim, then read the others' notes.
        Returns the one that comes first (it holds the pen, or asked before), the
        claim taken away (left when it holds and ``ask``: the request goes over it);
        or None, once "I hold it" is written. Without connection the claim goes, if it can."""
        place.write(name, claim)
        try:
            listing = place.list()
            mine = listing.files.get(name)
            for other, entry in sorted(listing.files.items()):
                match = PEN_NAME.match(other)
                if not match or other == name or entry.size > PEN_BYTES:
                    continue
                try:
                    data = place.read(other, PEN_BYTES)
                except places.PlaceError as error:
                    if error.kind == "missing":
                        continue  # given back since the listing: it holds nothing
                    raise
                rival = self._read_pen(data, match.group(1))
                if not rival or not rival["live"]:
                    continue  # not a real note, or an old one: it holds nothing
                if rival["holding"] or mine is None or _ahead(rival, entry, asked, mine, me):
                    if not (rival["holding"] and ask):
                        place.delete(name)
                    return dict(rival, file=other, entry=entry, data=data)
            place.write(name, held)
            return None
        except places.PlaceError:
            try:
                place.delete(name)
            except places.PlaceError:
                pass  # not reachable: the claim lapses by itself after a few minutes
            raise

    def _ask_found(self, place: Any, name: str, found: Json) -> Json:
        """While taking, ``_io`` held: the holder is there after all. Ask it to hand
        over, as "Ask to edit" does (keeping this computer's place in line if it was
        already asking it), and say so."""
        with self.lock:
            if self.state is None:
                raise SyncError("Synchronisation is not set up.")
            pen = self.state["pen"]
            same = pen.get("asking") == found["id"] and pen.get("asked") and pen.get("asked_there")
            asking = {"asking": found["id"], "name": found["name"],
                      "asked": float(pen["asked"]) if same else time.time(),
                      "asked_there": float(pen["asked_there"]) if same else self._now_there(), "found": True}
            note = self._pen_note(holding=False, since=0.0, asking=found["id"], asked=asking["asked_there"])
        try:
            place.write(name, note)
        except places.PlaceError:
            try:
                place.delete(name)  # the claim, if there is one
            except places.PlaceError:
                pass
            raise
        with self.lock:
            if self.state is None:
                raise SyncError("Synchronisation is not set up.")
            if "data" in found:  # read at the place just now: newer than this computer's copy
                _write_private(self.cache / found["file"], found["data"])
                self.state["mirror"][found["file"]] = {"sig": found["entry"].signature, "modified": found["entry"].modified}
            self.state["pen"] = dict(asking, beat=time.time())
            self._note(f"{found['name']} is there after all: asked it to hand over the editing.")
            self._save()
            status = self._pen_status()
        self._wake.set()
        return status

    def release_pen(self) -> Json:
        """"I have finished": send what was changed, then let another computer edit.
        Never in the middle of a take: "Cancel" pressed while one runs gives back
        what it took, and leaves no note behind."""
        with self._taking:
            return self._release_pen()

    def _release_pen(self) -> Json:
        with self.lock:
            if self.state is None:
                raise SyncError("Synchronisation is not set up.")
            pen = self.state["pen"]
            place, name = self._place, f"pen-{self.state['computer_id']}.rpsync"
            if pen.get("asking"):  # "Cancel": the request goes
                self.state["pen"] = {}
                self._note("No longer asking for the pen.")
                self._save()
            elif not pen.get("mine"):
                if pen.get("offline") or pen.get("lost") or pen.get("notice"):
                    self.state["pen"] = {}
                    self._save()
                return self._pen_status()
        if pen.get("asking"):
            with self._io:
                try:
                    place.delete(name)
                except places.PlaceError:
                    pass  # not reachable: the request lapses by itself after a few minutes
            with self.lock:
                return self._pen_status() if self.state is not None else {}
        self.exchange()
        with self.lock:
            if self.state is not None and not self.state["pen"].get("mine"):
                return self._pen_status()  # handed over on the way (asked by another computer): already said
        with self._io:
            try:
                place.delete(name)
            except places.PlaceError:
                pass  # not reachable: the note lapses by itself after a few minutes
        with self.lock:
            if self.state is not None:
                self.state["pen"] = {}
                self._note("This computer gave the pen back.")
                self._save()
                return self._pen_status()
        return {}

    def _asked_by(self, pens: Optional[List[Json]] = None) -> Optional[Json]:
        """Another computer asking this one, by a real and recent note, to hand over the pen."""
        me = self.state["computer_id"]  # type: ignore[index]
        return next((p for p in (self._pens() if pens is None else pens) if p["live"] and p["asking"] == me), None)

    def _hand_over(self) -> None:
        """Asked for the pen, while holding it: let go now, before this round seals
        and sends this computer's file, so that file carries every change made here."""
        assert self.state is not None
        if not self.state["pen"].get("mine"):
            return
        if time.time() - self._last_edit < GROUP_SECONDS:
            return  # a change was let through a moment ago and may still be writing: at the next round
        asker = self._asked_by()
        if asker is None:
            return
        at = time.strftime("%H:%M")
        self.state["pen"] = {"notice": {"text": f"{asker['name']} took over the editing at {at}. "
                                                "Your changes were sent to it first.", "tone": "info"}}
        self._note(f"Handed the pen to {asker['name']}, after sending this computer's changes.")
        self._save()

    def asking(self) -> bool:
        with self.lock:
            return bool(self.state is not None and self.state["pen"].get("asking"))

    def ask_pen(self) -> Json:
        """"Ask to edit": ask the computer that holds the pen to send its changes
        and hand it over. It does so at its next round; ``follow_up`` then takes the pen.
        Never in the middle of a take or a giving back (``_taking``)."""
        with self._taking:
            return self._ask_pen()

    def _ask_pen(self) -> Json:
        with self.lock:
            if self.state is None:
                raise SyncError("Synchronisation is not set up.")
            if self.state["pen"].get("mine") or self.state["pen"].get("asking"):
                return self._pen_status()
        result = self.exchange()
        self.deliver()
        if not result["ok"]:
            raise PenError(_no_connection("ask for the pen"), "offline", self.pen())
        with self.lock:
            if self.state is None:
                raise SyncError("Synchronisation is not set up.")
            status = self._pen_status()
            if status["holder"] in (None, "me"):
                take = True  # nobody holds it (any more): simply take it
            elif status["stale"]:
                raise PenError(_only_if_sure(status["text"]), "stale", status)
            else:
                take = False
                holder, name = status["holder"], status["name"]
                self.state["pen"] = {"asking": holder, "name": name, "asked": time.time(), "asked_there": self._now_there()}
                place, note = self._place, self._pen_note(holding=False)
        if take:
            return self._take_pen(False, True)  # _taking is held already
        try:
            with self._io:
                place.write(f"pen-{self.state['computer_id']}.rpsync", note)
        except places.PlaceError as error:
            with self.lock:
                if self.state is not None:
                    self.state["pen"] = {}
            raise PenError(f"The other computer could not be asked: {error}", "offline", self.pen()) from None
        with self.lock:
            if self.state is None:
                return {}
            self.state["pen"]["beat"] = time.time()
            self._note(f"Asked {name} to hand over the editing.")
            self._save()
            status = self._pen_status()
        self._wake.set()
        return status

    def follow_up(self) -> None:
        """After a round, while asking: once the holder has let go, take the pen
        (and, first, everything newer; ``take_pen`` sees to that). Gives up after a
        long while if the holder is still there but never answers."""
        with self.lock:
            if self.state is None or not self.state["pen"].get("asking"):
                return
            pen = self.state["pen"]
            request = (pen["asking"], pen.get("asked"))
            note = next((p for p in self._pens() if p["id"] == pen["asking"] and p["holding"]), None)
            name = pen.get("name") or "The other computer"
            if note is not None:
                if note["live"] and time.time() - float(pen.get("asked") or 0) > ASK_GIVE_UP_SECONDS:
                    self.state["pen"] = {"notice": {"tone": "warn", "text": (
                        f"{name} did not hand over the editing. Is Rota Planner up to date on it? "
                        "You can also press “I have finished” there.")}}
                    self._note(f"{name} did not answer the request for the pen.", "warn")
                    self._save()
                return  # still there (wait), or gone quiet (the coordinator may take over)
        self._incoming = True  # what is newer is taken first, even if nothing arrived in this round
        self.deliver()
        try:
            self.take_pen(pressed=True)
        except PenError as error:
            with self.lock:
                now = self.state["pen"] if self.state is not None else {}
                if (now.get("asking"), now.get("asked")) != request:
                    return  # taken on the screen meanwhile, or asked anew: left as it is
                if error.kind == "offline":
                    now["beat"] = 0  # still asking: its note is written again at the next round
                    return
                self.state["pen"] = {"notice": {"text": str(error), "tone": "warn"}}
                self._save()

    def work_offline(self, on: bool) -> Json:
        """Change things without the pen, knowingly (no connection to ask for it)."""
        with self.lock:
            if self.state is None:
                raise SyncError("Synchronisation is not set up.")
            if on and not self.state["pen"].get("mine"):
                self.state["pen"] = {"offline": True}
                self._note("Working offline without the pen, by choice.", "warn")
            elif not on and self.state["pen"].get("offline"):
                self.state["pen"] = {}
            self._save()
            return self._pen_status()

    def pen(self) -> Json:
        with self.lock:
            return self._pen_status() if self.state is not None else {"holder": None, "mine": False, "needed": False,
                                                                          "read_only": False, "how": ""}

    def ensure_pen(self) -> bool:
        """Before any change to the data. Passes when this computer may edit (it
        holds the pen, takes it now, works offline by choice, or is alone);
        raises ``PenError`` otherwise. Returns True when newer data was taken on
        the way: what the screen was about to change may no longer be what it showed."""
        def alone() -> bool:
            return not any(FILE_NAME.match(n) and n not in self._own_names() for n in self.state["mirror"])

        with self.lock:
            if self.state is None:
                return False
            pen = self.state["pen"]
            if pen.get("mine") or pen.get("offline"):
                self._last_edit = time.time()
                return False
            before = int(self.state["applied"].get("seq", 0))
            unsure = alone() and time.time() - float(self.connection["last_ok"] or 0) > ALONE_TRUSTED_SECONDS
        if unsure:
            self.exchange()  # another computer may have joined since this one last looked
            self.deliver()
        with self.lock:
            if self.state is None:
                return False
            if alone():
                self._last_edit = time.time()
                return int(self.state["applied"].get("seq", 0)) != before
        self.take_pen()
        with self.lock:
            self._last_edit = time.time()
            return self.state is not None and int(self.state["applied"].get("seq", 0)) != before


def _looking(status: Json, **changes: Any) -> Json:
    """The pen as a refusal tells it: someone else edits, and how to get the editing back."""
    pen = dict(status, **changes)
    pen["read_only"] = pen["holder"] not in (None, "me")
    pen["how"] = _how_to_edit(pen) if pen["read_only"] else ""
    return pen


def _how_to_edit(pen: Json) -> str:
    """While only looking: how to get the editing back, in plain words."""
    name = pen["name"] or "the other computer"
    if pen.get("asking") and pen.get("handed"):
        return "Its changes are being taken: you can edit as soon as they are here."
    if pen.get("asking") and not pen["stale"]:
        return pen.get("hint", "")
    if pen["stale"]:
        return (f"If {name} is switched off or asleep, “Take over…” lets you edit here. What it changed and has "
                "not sent yet may then have to be chosen against your changes.")
    if pen.get("handing"):
        return f"Its changes arrive here by themselves. To edit here again, ask to edit once {name} has the pen."
    return (f"To change something, press “Ask to edit”: {name} sends its changes, then hands over by itself, "
            f"usually within a minute. Or press “I have finished” on {name}.")


def _no_connection(what: str) -> str:
    return (f"There is no connection to the other computers right now, so this computer cannot {what}. "
            "You can look; to change things all the same, choose “Work offline anyway”.")


def _ahead(rival: Json, entry: Any, asked: float, mine: Any, me: str) -> bool:
    """Between two computers taking the pen that nobody holds: is ``rival`` (its
    note, its entry at the place) before this one (``asked``: when it asked, or 0)?
    The one that asked for it first; one that asked before one that did not;
    otherwise the note that reached the place first. Never the order of the files
    alone for a computer that asked: a place may keep their times to the second
    only (WebDAV), and the asker writes its note again every few minutes. Both
    computers decide the same way, from the same two notes."""
    if rival["asked"] and asked:
        return (rival["asked"], rival["id"]) < (asked, me)
    if rival["asked"] or asked:
        return bool(rival["asked"])
    return (entry.modified, rival["id"]) < (mine.modified, me)


def _only_if_sure(why: str) -> str:
    return (f"{why} Take over only if you are sure they have stopped: what they changed "
            "and have not sent yet may then have to be chosen against yours.")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """``python3 -m rotaplanner.sync leave DATA_FOLDER``: for the uninstaller (``farewell``).
    Prints one line for it, and exits with 0 when this computer's files are gone (or it
    never took part), 3 when they stay for another computer that has not taken them yet
    (the names are printed), 4 when changes made here could not be sent, 1 otherwise."""
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] != "leave":
        print("usage: python3 -m rotaplanner.sync leave DATA_FOLDER")
        return 2
    data = Path(args[1])
    one = Sync(Store(data))
    if not one.configured:
        return 1 if (data / STATE_FILE).exists() else 0  # set up but unreadable here: not removed
    done = one.farewell()
    if done["outcome"] == "kept":
        print(_listed(done["behind"]))
        return 3
    return {"left": 0, "unsent": 4}.get(done["outcome"], 1)


def dangling(documents: Dict[str, Json]) -> List[str]:
    """Shifts that point at someone or a task no longer there, after choosing."""
    people = {p.get("id") for p in (documents.get("people.json") or {}).get("people", [])}
    warnings = []
    for unit, document in sorted(documents.items()):
        if not transfer.MONTH_NAME.match(unit):
            continue
        tasks = {t.get("id") for t in document.get("tasks", [])}
        lost = sum(1 for a in document.get("assignments", [])
                   if (a.get("person_id") and a.get("person_id") not in people) or a.get("task_id") not in tasks)
        if lost:
            warnings.append(f"{label(unit)}: {lost} shift{'s' if lost != 1 else ''} name someone or a task that is not in the list any more.")
    return warnings


if __name__ == "__main__":
    raise SystemExit(main())
