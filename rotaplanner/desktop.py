"""Rota Planner on this computer: its icon, and uninstalling it from its window.

``install-rota-planner.sh`` installs, updates and uninstalls (Mac and Linux).
The window offers "Uninstall…" and runs that script's ``--uninstall``: one
way of removing the program, the same from a Terminal and from the window.
What the window says before (``advice``) is decided here; the window only
shows it.

Older versions also made an "Update Rota Planner" icon. New versions are now
offered by the program itself (Settings, and its window), so that icon goes:
the installer removes it when it runs, and ``tidy_old_update_icon`` when a
version updated from inside the program starts.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

Json = Dict[str, Any]
SCRIPT = "install-rota-planner.sh"
KEY_NAME = "rota-planner-deploy"         # the installer's download key (a private repository only)
BUNDLE_ID = "local.rotaplanner"          # the installer's Mac apps
UNINSTALL_SECONDS = 300                  # sending the last changes to a slow server included


def home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


# -- can this copy be uninstalled from its window? ---------------------------------

def supported(program: Path, usual_data: bool, updates_reason: str) -> bool:
    """Installed by the installer, on a Mac or Linux, with its usual data folder
    (not the demo, not ``--data``). ``updates_reason``: ``Updates.unsupported()``,
    "" when the copy follows the program's repository like an installed one."""
    return (os.name == "posix" and usual_data and not updates_reason
            and (program / SCRIPT).is_file() and (program / ".git").exists()
            and shutil.which("bash") is not None)


def kept_folder_name(today: Optional[str] = None) -> str:
    """Where the installer puts the data it keeps (in the home folder; " 2" and so
    on are added if that name is taken)."""
    return f"Rota Planner data (kept {today or time.strftime('%Y-%m-%d')})"


def has_download_key() -> bool:
    return (home() / ".ssh" / KEY_NAME).is_file()


# -- what the window says first ------------------------------------------------------

Line = Tuple[str, str]  # (tone: "ok" | "warn" | "bad" | "", words)


def advice(sync: Optional[Json], delete: bool) -> Json:
    """The lines about the other computers, and whether the uninstall may go
    ahead. ``sync``: ``Sync.status()`` (just after a round with the place), or
    None while it is not known yet. Never stops keeping the data; deleting it
    is refused while changes made here cannot reach the others."""
    lines: List[Line] = []
    if not sync or not sync.get("configured"):
        if delete:
            lines.append(("warn", "There is no other copy of this data: it cannot be brought back."))
        return {"lines": lines, "allowed": True, "shared": False}
    online = bool((sync.get("connection") or {}).get("online"))
    pending = bool(sync.get("pending"))
    allowed = True
    if not pending:
        lines.append(("ok", "This computer's changes are sent."))
    elif online:
        lines.append(("warn", "Some changes made here are not sent yet: they are sent first."))
    elif delete:
        allowed = False
        lines.append(("bad", "Some changes made here have not reached the other computers, and the meeting "
                             "place cannot be reached now. Connect to the internet and try again, "
                             "or keep the data."))
    else:
        lines.append(("warn", "Some changes made here have not reached the other computers (no connection "
                              "now). They stay in the kept data."))
    if (sync.get("pen") or {}).get("mine"):
        lines.append(("ok", "This computer is editing: it hands over, so another computer can edit."))
    others = [o for o in sync.get("others") or [] if o.get("state") != "problem"]
    differ = any(u.get("state") == "both" for u in sync.get("units") or [])
    for other in others:
        name = other.get("name") or "Another computer"
        behind = other.get("state") == "behind" or bool(other.get("behind")) or differ
        gone = f"{other.get('text') or 'Not seen for a long time'}. " if other.get("state") == "gone" else ""
        if behind:
            who = f"{name}: {gone}It" if gone else name
            lines.append(("warn", f"{who} has not taken everything from this computer yet, so "
                                  "this computer's file stays at the meeting place until it has."))
        elif gone:
            lines.append(("warn", f"{name}: {gone.strip()}"))
        else:
            lines.append(("ok", f"{name} has everything from this computer."))
    if not others and delete:
        lines.append(("warn", "No other computer has this data: it cannot be brought back."))
    lines.append(("", "Nothing changes on the other computers: their data stays."))
    return {"lines": lines, "allowed": allowed, "shared": True}


# -- uninstalling --------------------------------------------------------------------

def uninstall(program: Path, delete: bool) -> Json:
    """Run the installer's ``--uninstall`` (the program must already be stopped,
    its lock and its ``.running`` note gone). Returns {"done", "kept", "sync",
    "message"}: ``kept`` is the folder the data was moved to (""), ``sync`` what
    became of this computer's file at the meeting place, ``message`` what the
    installer said (for a refusal, its reason)."""
    command = ["bash", str(program / SCRIPT), "--uninstall", "--yes", "--folder", str(program)]
    if delete:
        command.append("--delete-data")
    with tempfile.TemporaryDirectory(prefix="rota-planner-") as folder:
        result_file = Path(folder) / "result.txt"
        result_file.touch()
        command += ["--result", str(result_file)]
        env = {name: value for name, value in os.environ.items() if not name.startswith(("GIT_", "ROTA_"))}
        try:
            run = subprocess.run(command, cwd=str(home()), env=env, stdin=subprocess.DEVNULL,
                                 capture_output=True, text=True, timeout=UNINSTALL_SECONDS)
            said, code = run.stdout, run.returncode
        except (OSError, subprocess.SubprocessError) as error:
            said, code = f"The uninstaller could not run: {error}", 1
        values = result_values(result_file.read_text(encoding="utf-8", errors="replace"))
    done = code == 0 and values.get("status") == "done"
    return {"done": done, "kept": values.get("kept", ""), "sync": values.get("sync", ""),
            "message": closing_words(said, done)}


def result_values(text: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for line in text.splitlines():
        name, _, value = line.partition("=")
        if name:
            values[name] = value  # the last one counts
    return values


def closing_words(said: str, done: bool) -> str:
    """The part of the installer's words worth showing, its lines joined again
    (a Terminal needs them short, a window wraps them itself): after an
    uninstall, what it said about the meeting place and the key; after a
    refusal, its reason."""
    if done:
        lines = said.splitlines()
        start = next((at + 1 for at, line in enumerate(lines) if line.startswith("Rota Planner is uninstalled.")), None)
        if start is None:
            return ""
        said = "\n".join(line for line in lines[start:] if not line.startswith("Your data is in:"))
    paragraphs = [" ".join(line.strip() for line in p.splitlines() if line.strip())
                  for p in said.strip().split("\n\n")]
    paragraphs = [p for p in paragraphs if p]
    if not paragraphs:
        return ""
    return "\n\n".join(paragraphs) if done else paragraphs[-1]


def show_folder(path: str) -> None:
    """Open a folder in the Finder or the file manager."""
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    try:
        subprocess.Popen([opener, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         cwd=str(home()), start_new_session=True)  # the program's own folder is gone
    except OSError:
        pass


# -- the old "Update Rota Planner" icon ------------------------------------------------

def _same(folder: str, program: Path) -> bool:
    """The folder an icon names is this program's (the installer writes it as given,
    which may go through a link)."""
    try:
        return bool(folder) and Path(folder).resolve() == program
    except (OSError, RuntimeError):
        return False


def tidy_old_update_icon(program: Path) -> bool:
    """Remove the "Update Rota Planner" icon an older installer made for this copy
    (only that one: never another copy's, never one it did not make). Returns
    whether one was removed."""
    program = Path(program).resolve()
    if os.name != "posix" or not (program / SCRIPT).is_file():
        return False
    try:
        if sys.platform == "darwin":
            app = home() / "Applications" / "Update Rota Planner.app"
            plist = app / "Contents" / "Info.plist"
            if not plist.is_file() or BUNDLE_ID not in plist.read_text(encoding="utf-8", errors="replace"):
                return False
            named = [found for part in ("MacOS", "Resources") for path in (app / "Contents" / part).glob("*")
                     if path.is_file() and path.stat().st_size < 100_000
                     for found in re.findall(r'"([^"]+)/install-rota-planner\.sh"',
                                             path.read_text(encoding="utf-8", errors="replace"))]
            if not any(_same(folder, program) for folder in named):
                return False
            shutil.rmtree(app)
            return True
        share = Path(os.environ.get("XDG_DATA_HOME") or home() / ".local" / "share")
        entry = share / "applications" / "rota-planner-update.desktop"
        if not entry.is_file():
            return False
        folders = [line[len("Path="):] for line in entry.read_text(encoding="utf-8", errors="replace").splitlines()
                   if line.startswith("Path=")]
        if not any(_same(folder, program) for folder in folders):
            return False
        entry.unlink()
        return True
    except OSError:
        return False
