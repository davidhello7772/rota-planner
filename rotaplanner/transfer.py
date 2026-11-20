"""Moving the data to another computer: everything in one file, and back.

The file is a zip of the data folder's documents (set-up, people, settings,
templates, months) with a small description. Backups, exports and the
session token stay behind. Availability comments can be left out, since they
may say something about someone's health.

Reading a file back is strict: only the expected names, JSON documents of a
format this program understands, and a size limit, so that a damaged or
unexpected file cannot harm the data folder. Before replacing anything, the
current data is kept in a zip in the backups folder.
"""

from __future__ import annotations

import io
import json
import re
import time
import zipfile
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

from . import view
from .availability import scrub_comments
from .model import SCHEMA_VERSION, Catalog, ConfigTemplate, Month, Person, Settings
from .store import Store, StoreError, migrate

Json = Dict[str, Any]

MANIFEST = "rota-planner.json"
FORMAT = 1
TOP_LEVEL = ("catalog.json", "people.json", "settings.json", "templates.json")
MONTH_NAME = re.compile(r"^months/([0-9]{4}-(?:0[1-9]|1[0-2]))\.json\Z")
SAFETY_COPIES_KEPT = 5
MAX_UNPACKED_BYTES = 100_000_000
FILE_SUFFIX = ".rotaplanner.zip"


class TransferError(ValueError):
    """The file is not a Rota Planner export, or cannot be used."""


def _documents(store: Store) -> Dict[str, Json]:
    """Every document of the data folder, by name in the export."""
    found = {}
    for name in TOP_LEVEL:
        path = store.root / name
        if path.exists():
            found[name] = json.loads(path.read_text(encoding="utf-8"))
    for month_id in store.list_months():
        found[f"months/{month_id}.json"] = json.loads((store.months_dir / f"{month_id}.json").read_text(encoding="utf-8"))
    return found


def scrub_safety_copies(store: Store) -> int:
    """Remove availability comments from the copies kept before imports
    (older versions of the program kept them there). Returns how many were rewritten."""
    rewritten = 0
    for path in sorted(store.backups_dir.glob("before-*.zip")):  # before-import-…, before-sync-…
        try:
            with zipfile.ZipFile(path) as archive:
                contents = {name: archive.read(name) for name in archive.namelist()}
        except (OSError, zipfile.BadZipFile):
            continue
        changed = False
        for name, raw in contents.items():
            if not MONTH_NAME.match(name) and name != MANIFEST:
                continue
            try:
                document = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                continue
            touched = scrub_comments(document) if name != MANIFEST else bool(document.pop("comments", False))
            if touched:
                contents[name] = json.dumps(document, ensure_ascii=False, indent=1).encode("utf-8")
                changed = True
        if changed:
            temporary = path.with_name(path.name + ".tmp")
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, raw in contents.items():
                    archive.writestr(name, raw)
            temporary.replace(path)
            rewritten += 1
    return rewritten


def months_in(documents: Dict[str, Json]) -> List[str]:
    return sorted(MONTH_NAME.match(n).group(1) for n in documents if MONTH_NAME.match(n))


def export_bytes(store: Store, include_comments: bool = False) -> bytes:
    documents = _documents(store)
    if not include_comments:
        for name, document in documents.items():
            if MONTH_NAME.match(name):
                scrub_comments(document)
    people = len((documents.get("people.json") or {}).get("people", []))
    months = months_in(documents)
    manifest = {"format": FORMAT, "schema_version": SCHEMA_VERSION, "created": date.today().isoformat(),
                "people": people, "months": months, "comments": include_comments}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(MANIFEST, json.dumps(manifest, indent=1))
        for name, document in sorted(documents.items()):
            archive.writestr(name, json.dumps(document, ensure_ascii=False, indent=1))
    return buffer.getvalue()


def export_name() -> str:
    return f"Rota Planner data {date.today().isoformat()}{FILE_SUFFIX}"


def read_export(data: bytes) -> Dict[str, Json]:
    """The documents of an export file, checked and brought to the current format."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise TransferError("This is not a Rota Planner file (it should end in .rotaplanner.zip).") from None
    with archive:
        names = archive.namelist()
        if MANIFEST not in names:
            raise TransferError("This is not a Rota Planner file (it should end in .rotaplanner.zip).")
        if sum(info.file_size for info in archive.infolist()) > MAX_UNPACKED_BYTES:
            raise TransferError("This file is far too big to be Rota Planner data.")
        unexpected = [n for n in names if n != MANIFEST and n not in TOP_LEVEL and not MONTH_NAME.match(n)]
        if unexpected:
            raise TransferError(f"Unexpected content in the file: {', '.join(unexpected[:3])}.")
        documents = {}
        for name in names:
            try:
                documents[name] = json.loads(archive.read(name).decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise TransferError(f"{name} in the file is damaged.") from None
    manifest = documents.pop(MANIFEST)
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise TransferError("This file was made by another version of Rota Planner.")
    if "catalog.json" not in documents or "people.json" not in documents:
        raise TransferError("The file has no tasks or no people: it cannot replace the data.")
    return {"manifest": manifest, "documents": checked(documents)}


def checked(documents: Dict[str, Json]) -> Dict[str, Json]:
    """The documents brought to the current format, once each reads as what it
    claims to be and each month is one the program can really show.

    Used for a file brought from another computer and for synchronisation:
    a set of documents that passes here works. Raises ``TransferError``.
    """
    unexpected = [n for n in documents if n not in TOP_LEVEL and not MONTH_NAME.match(n)]
    if unexpected:
        raise TransferError(f"Unexpected content: {', '.join(unexpected[:3])}.")
    if not all(isinstance(d, dict) for d in documents.values()):
        raise TransferError("The content is not what Rota Planner expects.")
    try:
        documents = {name: migrate(document) for name, document in documents.items()}
        catalog = Catalog.from_dict(documents.get("catalog.json", {}))
        people = [Person.from_dict(p) for p in documents.get("people.json", {}).get("people", [])]
        settings = Settings.from_dict(documents.get("settings.json", {}))
        [ConfigTemplate.from_dict(t) for t in documents.get("templates.json", {}).get("templates", [])]
        for name, document in documents.items():
            match = MONTH_NAME.match(name)
            if not match:
                continue
            month = Month.from_dict(document)
            if month.id != match.group(1) or month.id != Month.make_id(int(month.year), int(month.month)):
                raise TransferError(f"{name} does not hold the month it should.")
            view.month_view(month, catalog, people, settings)
    except TransferError:
        raise
    except StoreError as error:
        raise TransferError(str(error)) from None
    except Exception:  # noqa: BLE001 — whatever is wrong with it, the file is not usable
        raise TransferError("The content is not what Rota Planner expects.") from None
    return documents


def summary(store: Store, data: bytes) -> Json:
    """What importing the file would bring, against what is here now."""
    found = read_export(data)
    documents, manifest = found["documents"], found["manifest"]
    months = months_in(documents)
    here_people = len(store.load_people())
    return {
        "created": manifest.get("created", ""),
        "people": len(documents["people.json"].get("people", [])),
        "tasks": len(documents["catalog.json"].get("tasks", [])),
        "months": months,
        "comments": bool(manifest.get("comments")),
        "here": {"people": here_people, "months": store.list_months()},
    }


def keep_safety_copy(store: Store, prefix: str, keep: int = SAFETY_COPIES_KEPT) -> str:
    """Keep all the current data in ``backups/<prefix>-<time>.zip`` (the last
    ``keep`` are kept), so a replacement can be undone by hand. Returns the file name."""
    store.backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safety = store.backups_dir / f"{prefix}-{stamp}.zip"
    extra = 1
    while safety.exists():  # two in the same second: never overwrite a copy
        extra += 1
        safety = store.backups_dir / f"{prefix}-{stamp}-{extra}.zip"
    # Without the availability comments: they may be private, and nothing would scrub this copy later.
    safety.write_bytes(export_bytes(store, include_comments=False))
    for old in sorted(store.backups_dir.glob(f"{prefix}-*.zip"))[:-max(1, keep)]:
        old.unlink()
    return safety.name


def replace_data(store: Store, data: bytes) -> str:
    """Replace the data folder's documents with those of the file.

    The current documents are first kept in ``backups/before-import-<time>.zip``
    (returned), so the import itself can be undone by hand.
    """
    documents = read_export(data)["documents"]
    safety = keep_safety_copy(store, "before-import")
    for month_id in store.list_months():
        (store.months_dir / f"{month_id}.json").unlink()
    for name in TOP_LEVEL:
        path = store.root / name
        if path.exists() and name not in documents:
            path.unlink()
    for name, document in documents.items():
        target = store.root / Path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(document, schema_version=SCHEMA_VERSION)
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        temporary.replace(target)
    return safety


