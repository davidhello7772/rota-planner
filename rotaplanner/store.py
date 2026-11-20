"""Reading and writing the data folder.

Each document is a readable JSON file. Writes are atomic (write to a temporary
file, then rename) so a crash never leaves a half-written file, and the
previous content is kept in ``backups/`` so a mistake can be undone by hand.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .model import SCHEMA_VERSION, Catalog, ConfigTemplate, Month, Person, Settings

MONTH_ID = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
BACKUPS_KEPT_PER_FILE = 30
BACKUP_MIN_INTERVAL_SECONDS = 10 * 60

Migration = Callable[[Dict[str, Any]], Dict[str, Any]]

def _tasks_get_periods(document: Dict[str, Any]) -> Dict[str, Any]:
    """Format 1 → 2: a task's single ``period`` becomes a list, ``periods``.

    Tasks live in the catalog, in each month and in templates; they are the
    records with both an ``area_id`` and a ``period``.
    """
    def upgrade(value: Any) -> Any:
        if isinstance(value, dict):
            if "area_id" in value and "period" in value:
                value["periods"] = [value.pop("period")]
            for child in value.values():
                upgrade(child)
        elif isinstance(value, list):
            for child in value:
                upgrade(child)
        return value

    return upgrade(document)


def _events_decide_main_rota(document: Dict[str, Any]) -> Dict[str, Any]:
    """Format 2 → 3: whether event tasks show on the main rota is said once per
    event (and per event type); a task only keeps its own answer when it differs."""
    def settle(owner: Dict[str, Any], tasks: list) -> None:
        answers = [task.get("on_month_rota", True) is not False for task in tasks]
        shown = sum(answers) * 2 >= len(answers) if answers else not owner.get("big", False)
        owner["tasks_on_main_rota"] = shown
        for task, answer in zip(tasks, answers):
            task["on_month_rota"] = None if answer == shown else answer

    tasks = document.get("tasks") or []
    for event in document.get("events") or []:
        settle(event, [t for t in tasks if t.get("event_id") == event.get("id")])
    for event_type in document.get("event_types") or []:
        settle(event_type, event_type.get("tasks") or [])
    usual = [t for t in tasks if not t.get("event_id")]
    usual += [t for template in document.get("templates") or [] for t in template.get("tasks") or []]
    for task in usual:
        task["on_month_rota"] = None
    return document


def _fields_older_programs_would_drop(document: Dict[str, Any]) -> Dict[str, Any]:
    """Format 3 → 4: nothing to transform. The number changes because the files
    now hold things an older program would silently drop when saving (who a
    version was for, e-mail fingerprints and their salt): with a higher number
    it refuses them instead, on a second computer not yet updated for example."""
    return document


MIGRATIONS: Dict[int, Migration] = {1: _tasks_get_periods, 2: _events_decide_main_rota, 3: _fields_older_programs_would_drop}
"""Upgrade steps, keyed by the version they upgrade *from*.

To change the file format: bump ``SCHEMA_VERSION`` and register here a
function turning a version-N document into a version-N+1 document.
"""


def write_private_json(path: Path, document: Dict[str, Any]) -> None:
    """A small settings file of this computer only (``access.json``, ``sync.json``):
    written whole (temporary file, then rename), readable by this user only, no backup."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=1)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        temporary.chmod(0o600)
    except OSError:
        pass  # not every file system has permissions (Windows keeps its own)
    os.replace(temporary, path)


class StoreError(Exception):
    """A data file is missing, unreadable or from a newer program version."""


def migrate(document: Dict[str, Any]) -> Dict[str, Any]:
    version = document.get("schema_version", 1)
    if version > SCHEMA_VERSION:
        raise StoreError(
            f"This file was written by a newer version of Rota Planner "
            f"(format {version}, this program reads up to {SCHEMA_VERSION})."
        )
    while version < SCHEMA_VERSION:
        document = MIGRATIONS[version](document)
        version += 1
        document["schema_version"] = version
    return document


class Store:
    """All file access for one data folder."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.months_dir = self.root / "months"
        self.backups_dir = self.root / "backups"
        self._email_salt: Optional[str] = None

    # -- documents ---------------------------------------------------------

    def is_empty(self) -> bool:
        return not (self.root / "catalog.json").exists()

    def load_catalog(self) -> Catalog:
        return Catalog.from_dict(self._read(self.root / "catalog.json", default={}))

    def save_catalog(self, catalog: Catalog) -> None:
        self._write(self.root / "catalog.json", catalog.to_dict())

    def load_people(self) -> List[Person]:
        document = self._read(self.root / "people.json", default={"people": []})
        salt = document.get("email_salt")
        self._email_salt = salt if isinstance(salt, str) and len(salt) == 32 else None
        return [Person.from_dict(item) for item in document["people"]]

    def save_people(self, people: List[Person]) -> None:
        document: Dict[str, Any] = {"people": [p.to_dict() for p in people]}
        salt = self._email_salt or self._stored_salt()  # never dropped, even by a store that did not load
        if salt:
            document["email_salt"] = salt
        self._write(self.root / "people.json", document)

    def _stored_salt(self) -> Optional[str]:
        salt = self._read(self.root / "people.json", default={}).get("email_salt")
        return salt if isinstance(salt, str) and len(salt) == 32 else None

    def email_salt(self) -> bytes:
        """The salt of the e-mail fingerprints (``emails.py``), kept with the people.

        Read from ``people.json``; made and saved there at once the first time
        it is needed, so it is never only in memory.
        """
        salt = self._stored_salt()
        if salt is None:
            salt = secrets.token_hex(16)
            document = self._read(self.root / "people.json", default={"people": []})
            document["email_salt"] = salt
            self._write(self.root / "people.json", document)
        self._email_salt = salt
        return bytes.fromhex(salt)

    def load_settings(self) -> Settings:
        return Settings.from_dict(self._read(self.root / "settings.json", default={}))

    def save_settings(self, settings: Settings) -> None:
        self._write(self.root / "settings.json", settings.to_dict())

    def load_templates(self) -> List[ConfigTemplate]:
        document = self._read(self.root / "templates.json", default={"templates": []})
        return [ConfigTemplate.from_dict(item) for item in document["templates"]]

    def save_templates(self, templates: List[ConfigTemplate]) -> None:
        self._write(self.root / "templates.json", {"templates": [t.to_dict() for t in templates]})

    def list_months(self) -> List[str]:
        """Ids of the months saved ("2026-12"); other files in the folder are ignored."""
        if not self.months_dir.is_dir():
            return []
        return sorted(path.stem for path in self.months_dir.glob("*.json") if MONTH_ID.match(path.stem))

    def load_month(self, month_id: str) -> Month:
        path = self._month_path(month_id)
        if not path.exists():
            raise StoreError(f"No rota saved for {month_id}.")
        return Month.from_dict(self._read(path, default={}))

    def save_month(self, month: Month) -> None:
        self._write(self._month_path(month.id), month.to_dict())

    # -- files -------------------------------------------------------------

    def _month_path(self, month_id: str) -> Path:
        # Month ids come from URLs; refuse anything that is not YYYY-MM so a
        # crafted id can never point outside the months folder.
        year, sep, month = month_id.partition("-")
        if not (sep and len(year) == 4 and len(month) == 2 and (year + month).isdigit()):
            raise StoreError(f"Invalid month id: {month_id!r}")
        return self.months_dir / f"{month_id}.json"

    def _read(self, path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
        if not path.exists():
            return default
        try:
            with path.open(encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, ValueError) as error:
            raise StoreError(f"Cannot read {path.name}: {error}") from error
        return migrate(document)

    def _write(self, path: Path, document: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._back_up(path)
        payload = dict(document, schema_version=SCHEMA_VERSION)
        temporary = path.with_name(path.name + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def backed_up_months(self) -> List[str]:
        """Ids of every month that has a backup, whether the month still exists or not."""
        if not self.backups_dir.is_dir():
            return []
        return sorted({path.name[:7] for path in self.backups_dir.glob("*.json") if MONTH_ID.match(path.name[:7])})

    def scrub_backups(self, month_id: str, scrub: Callable[[Dict[str, Any]], bool]) -> int:
        """Apply ``scrub`` to every backup of a month, rewriting those it changed.

        Used to delete information that must not be kept (comments that may
        say something about someone's health). Returns how many were rewritten.
        """
        rewritten = 0
        for backup in sorted(self.backups_dir.glob(f"{month_id}.*.json")):
            try:
                document = json.loads(backup.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if scrub(document):
                temporary = backup.with_name(backup.name + ".tmp")
                temporary.write_text(json.dumps(document, ensure_ascii=False, indent=1), encoding="utf-8")
                os.replace(temporary, backup)
                rewritten += 1
        return rewritten

    def _back_up(self, path: Path) -> None:
        """Keep the version about to be replaced, at most once per interval."""
        if not path.exists():
            return
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        existing = sorted(self.backups_dir.glob(f"{path.stem}.*.json"))
        if existing and time.time() - existing[-1].stat().st_mtime < BACKUP_MIN_INTERVAL_SECONDS:
            return
        stamp = time.strftime("%Y%m%d-%H%M%S")
        (self.backups_dir / f"{path.stem}.{stamp}.json").write_bytes(path.read_bytes())
        for stale in existing[: max(0, len(existing) + 1 - BACKUPS_KEPT_PER_FILE)]:
            stale.unlink()
