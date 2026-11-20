"""Is there a newer version of the program, and installing it when asked.

For a copy installed with git (the installer does that). Looking is a
``git fetch`` from the program's own repository: it sends nothing about the
data, only asks GitHub what is new. It happens when the coordinator presses
"Check now", and by itself once a day unless that is turned off (Settings).

Installing is never automatic: it lists what is new and waits for a yes.
Then, in this order, and stopping at the first thing that is not right:

1. program files changed by hand on this computer → nothing is touched;
2. a new version that would put files in the data folders → refused (git
   treats ignored files as disposable: it could replace the data);
3. what is about to be installed is not what the screen listed (something
   newer arrived meanwhile) → refused: look at the list again;
4. all the data is copied to ``backups/before-update-…zip``;
5. the new version is taken (fast-forward only);
6. it is tried with this computer's Python; if it does not load, the previous
   version is put back;
7. the program starts again.

The same steps as ``install-rota-planner.sh``, which stays the way to update
when the program is closed.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, ContextManager, Dict, Optional

from . import transfer
from .store import Store, write_private_json

Json = Dict[str, Any]

STATE_FILE = "updates.json"       # this computer only: never exported or synchronised
CHECK_EVERY_SECONDS = 20 * 3600   # "once a day"
TRY_AGAIN_SECONDS = 3600          # after a look that could not reach the repository
FIRST_CHECK_SECONDS = 45          # after the program started
FETCH_SECONDS = 60
SHOWN = 15                        # lines of "what is new"
DATA_FOLDERS = ("data", "data-demo", "exports")
BOOT = secrets.token_hex(8)       # this run of the program: the page sees it change when the program started again
# The program's files are written as the person's own settings want them, not
# with the private mode this program gives its data (set by __main__).
FILES_UMASK: Optional[int] = None


class UpdateError(ValueError):
    """The update cannot go ahead; the message says why, for the coordinator."""


class Updates:
    def __init__(self, program: Path, store: Store, clock: Callable[[], float] = time.time) -> None:
        self.program, self.store, self.clock = Path(program), store, clock
        self.path = store.root / STATE_FILE
        self.lock = threading.Lock()   # one look or one installation at a time
        self.busy = ""                 # "checking" | "installing"
        self.restart = threading.Event()  # set once a new version is in place: the program starts again
        self.stopped = threading.Event()  # the program is being uninstalled: nothing is looked at or written
        self.tried = 0.0               # the last look, reached or not
        self.state: Json = {"auto": True, "checked": 0.0, "available": 0, "items": [], "target": "", "problem": ""}
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                self.state.update({k: saved[k] for k in self.state if k in saved})
        except (OSError, ValueError):
            pass
        if self.state["available"]:
            self._compare()  # it may have been installed meanwhile, with the installer

    # -- git ---------------------------------------------------------------

    def _git(self, *args: str, timeout: float = 20) -> subprocess.CompletedProcess:
        # The copy's own settings decide how to reach GitHub, not this session's variables.
        env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
        env["GIT_TERMINAL_PROMPT"] = "0"  # never a password prompt nobody would see
        more: Dict[str, Any] = {}
        if os.name == "nt":
            more["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        elif FILES_UMASK is not None:
            more["umask"] = FILES_UMASK
        return subprocess.run(["git", "-C", str(self.program), *args], capture_output=True, text=True,
                              timeout=timeout, env=env, **more)

    def _out(self, *args: str) -> str:
        try:
            done = self._git(*args)
        except (OSError, subprocess.SubprocessError):
            return ""
        return done.stdout.strip() if done.returncode == 0 else ""

    def _did(self, *args: str, timeout: float = 60) -> bool:
        try:
            return self._git(*args, timeout=timeout).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def unsupported(self) -> str:
        """Why this copy cannot be updated from inside the program ("" when it can)."""
        if shutil.which("git") is None or not (self.program / ".git").exists():
            return ("This copy was not installed with the installer, so it is updated by hand: "
                    "download the new version and copy it over the program's files, keeping the data folder.")
        if not self._out("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"):
            return "This copy does not follow the program's repository, so it cannot be updated from here."
        return ""

    def version(self) -> str:
        """The date of the version in place."""
        return self._out("log", "-1", "--format=%cd", "--date=short")

    def _save(self) -> None:
        if self.stopped.is_set():
            return  # the data folder may be gone already: never make it again
        try:
            write_private_json(self.path, self.state)
        except OSError:
            pass

    def _compare(self) -> None:
        """What the repository has that this copy does not, as last fetched (no network)."""
        count = self._out("rev-list", "--no-merges", "--count", "HEAD..@{u}")
        items = self._out("log", "--no-merges", "--format=%s", "-n", str(SHOWN), "HEAD..@{u}")
        self.state.update(available=int(count) if count.isdigit() else 0, items=[i for i in items.splitlines() if i],
                          target=self._out("rev-parse", "@{u}"))

    # -- looking -----------------------------------------------------------

    def info(self) -> Json:
        """Everything the screens show. Never touches the network."""
        reason = self.unsupported()
        return {
            "supported": not reason, "reason": reason, "version": self.version() if not reason else "",
            "auto": bool(self.state["auto"]), "checked": float(self.state["checked"] or 0),
            "checked_text": (time.strftime("%d %b, %H:%M", time.localtime(self.state["checked"]))
                             if self.state["checked"] else ""),
            "available": int(self.state["available"]) if not reason else 0,
            "items": list(self.state["items"]) if not reason else [],
            "more": max(0, int(self.state["available"]) - len(self.state["items"])) if not reason else 0,
            "target": str(self.state["target"]) if not reason else "",
            "problem": self.state["problem"], "busy": self.busy, "boot": BOOT,
        }

    def set_auto(self, on: bool) -> Json:
        self.state["auto"] = bool(on)
        self._save()
        return self.info()

    def due(self) -> bool:
        now = self.clock()
        return (bool(self.state["auto"]) and now - float(self.state["checked"] or 0) > CHECK_EVERY_SECONDS
                and now - self.tried > TRY_AGAIN_SECONDS)

    def check(self) -> Json:
        """Ask the repository what is new (the only thing here that uses the network)."""
        reason = self.unsupported()
        if reason or self.stopped.is_set():
            return self.info()
        if not self.lock.acquire(blocking=False):
            return self.info()  # already looking, or installing
        self.busy = "checking"
        self.tried = self.clock()
        try:
            try:
                done = self._git("fetch", "--quiet", timeout=FETCH_SECONDS)
                problem = "" if done.returncode == 0 else (
                    "The program's repository could not be reached. Is this computer connected to the internet?")
            except (OSError, subprocess.SubprocessError):
                problem = "The program's repository did not answer in time."
            self.state["problem"] = problem
            if not problem:
                self.state["checked"] = self.clock()
                self._compare()
            self._save()
        finally:
            self.busy = ""
            self.lock.release()
        return self.info()

    def start_background(self) -> None:
        """Look once a day, by itself, while the program runs (unless turned off)."""
        def loop() -> None:
            self.stopped.wait(FIRST_CHECK_SECONDS)
            while not self.stopped.is_set():
                try:
                    if self.due():
                        self.check()
                except Exception:  # noqa: BLE001 — never stop the program for this
                    pass
                self.stopped.wait(900)
        threading.Thread(target=loop, name="updates", daemon=True).start()

    def stop(self) -> None:
        """The program is being uninstalled: wait for a look or an installation
        under way to end, then never look or write again."""
        self.stopped.set()
        with self.lock:
            pass

    # -- installing ----------------------------------------------------------

    def install(self, works: Optional[Callable[[], bool]] = None, expected: str = "",
                data_lock: Optional[ContextManager] = None) -> Json:
        """Put the new version in place (see the steps at the top). Raises
        ``UpdateError`` when it must not or could not; nothing is half done.
        ``expected`` is the version the screen listed (``info()["target"]``);
        ``data_lock`` is held only while the data is copied. On success
        ``restart`` is set: the caller starts the program again."""
        reason = self.unsupported()
        if reason:
            raise UpdateError(reason)
        if self.stopped.is_set():
            raise UpdateError("Rota Planner is being uninstalled.")
        if not self.lock.acquire(blocking=False):
            raise UpdateError("Rota Planner is already looking for, or installing, a new version.")
        self.busy = "installing"
        try:
            if not self._did("fetch", "--quiet", timeout=FETCH_SECONDS):
                raise UpdateError("The program's repository could not be reached. Nothing was changed.")
            here, there = self._out("rev-parse", "HEAD"), self._out("rev-parse", "@{u}")
            if not here or not there:
                raise UpdateError("This copy's history cannot be read. Nothing was changed.")
            self.state.update(checked=self.clock(), problem="")
            self._compare()
            self._save()
            if here == there:
                raise UpdateError("Rota Planner is already up to date.")
            if expected and expected != there:
                raise UpdateError("Something newer arrived since this list was shown. Look at the list again, then update.")
            if self._out("status", "--porcelain", "--untracked-files=no"):
                raise UpdateError("Files of the program were changed on this computer, so it was not updated "
                                  "(your changes are kept). Ask whoever looks after the program.")
            if not self._did("merge-base", "--is-ancestor", here, there):
                raise UpdateError("This copy has changes of its own that are not in the repository, so it was not updated.")
            if self._in_data_folders(there):
                raise UpdateError("The new version contains files in the data folder, which could replace your data, "
                                  "so it was not applied. Tell whoever looks after the program.")
            safety = self._safety_copy(data_lock)
            if not self._did("merge", "--quiet", "--ff-only", there):
                self._put_back(here)  # cut short half way, perhaps
                raise UpdateError("The update could not be applied; the program was not changed.")
            if not (works or self._works)():
                if not self._put_back(here):
                    raise UpdateError("The new version does not start on this computer, and the previous one could not "
                                      "be put back. Close Rota Planner and tell whoever looks after the program.")
                raise UpdateError("The new version does not start on this computer, so the previous one was put back. "
                                  "Tell whoever looks after the program.")
            self._compare()
            self._save()
            self.restart.set()
            return dict(self.info(), installed=True, safety=safety, restarting=True)
        finally:
            self.busy = ""
            self.lock.release()

    def _in_data_folders(self, version: str) -> bool:
        """Would this version put files where the data lives? Git treats ignored files
        as disposable. Names are compared without their case: on a Mac or Windows
        disk, ``Data/`` is the same folder as ``data/``."""
        try:
            done = self._git("ls-tree", "-z", "--name-only", version)
        except (OSError, subprocess.SubprocessError):
            return True  # cannot tell: do not apply
        return done.returncode != 0 or any(name.lower() in DATA_FOLDERS for name in done.stdout.split("\0"))

    def _safety_copy(self, data_lock: Optional[ContextManager]) -> str:
        if self.store.is_empty():
            return ""
        if data_lock is None:
            return transfer.keep_safety_copy(self.store, "before-update")
        with data_lock:
            return transfer.keep_safety_copy(self.store, "before-update")

    def _put_back(self, version: str) -> bool:
        """The previous version again, and none of the new one's compiled files:
        Python would trust one made in the same second as the file put back."""
        back = self._did("reset", "--quiet", "--hard", version)
        for cache in list((self.program / "rotaplanner").rglob("__pycache__")):
            shutil.rmtree(cache, ignore_errors=True)
        self._compare()
        return back

    def _works(self) -> bool:
        """Does the version now in place load with this computer's Python? Tried in
        another process (this one still runs the previous version), which writes
        no compiled file: every file is compiled in memory, then the program is imported."""
        code = ("import pathlib, sys; root = pathlib.Path(sys.argv[1]); "
                "[compile(path.read_bytes(), str(path), 'exec') for path in sorted((root / 'rotaplanner').rglob('*.py'))]; "
                "sys.path.insert(0, str(root)); import rotaplanner.app, rotaplanner.server, rotaplanner.__main__")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            return subprocess.run([sys.executable, "-I", "-B", "-c", code, str(self.program)], capture_output=True,
                                  timeout=180, creationflags=flags).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
