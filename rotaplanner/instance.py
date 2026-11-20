"""One Rota Planner at a time for each data folder.

Two programs writing the same files would undo each other's work, so the
first one takes a lock on ``<data>/.lock`` (released by the system when the
process ends, even after a crash). A second start finds the lock taken and,
instead of running, wakes the first: its window comes to the front, or the
browser opens on it.

The first program leaves a private note, ``<data>/.instance``, saying where it
listens and the address of its "wake-up door": a socket on 127.0.0.1 that
only reacts to the secret written in the note. Only someone who can read the
data folder (the same user) can wake it, and waking does nothing more than
bring the window forward.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import socket
import threading
import time
from pathlib import Path
from typing import Callable, Optional

LOCK_FILE = ".lock"
INFO_FILE = ".instance"
WAKE_WORD = "show"


def _try_lock(fd: int) -> bool:
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


class Instance:
    """The lock and the wake-up door of the Rota Planner using a data folder."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.on_wake: Optional[Callable[[], None]] = None
        self._fd: Optional[int] = None
        self._door: Optional[socket.socket] = None
        self._secret = secrets.token_hex(16)
        self._info = {"pid": os.getpid(), "port": None, "door": None, "window": False}
        self._writing = threading.Lock()

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> bool:
        """Take the lock. False when another Rota Planner already uses this folder."""
        if self._fd is not None:
            return True
        self.root.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.root / LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o600)
        if not _try_lock(fd):
            os.close(fd)
            return False
        self._fd = fd
        self._write()  # at once: a note left by a program that was killed must not be believed
        return True

    def open_door(self) -> None:
        """Listen for a second start asking this one to show itself."""
        if self._door is not None:
            return
        door = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        door.bind(("127.0.0.1", 0))
        door.listen(4)
        self._door = door
        self._info["door"] = door.getsockname()[1]
        self._info["window"] = True
        threading.Thread(target=self._listen, name="instance-door", daemon=True).start()
        self._write()

    def _listen(self) -> None:
        door = self._door
        while door is not None:
            try:
                connection, _ = door.accept()
            except OSError:
                if self._door is None:
                    return  # closed: the program is ending
                time.sleep(0.05)  # a caller that went away before it was answered
                continue
            with connection:
                connection.settimeout(2)
                try:
                    said = connection.recv(128).decode("ascii", "replace").split()
                except OSError:
                    continue
                right = len(said) == 2 and said[0] == WAKE_WORD and hmac.compare_digest(said[1].encode("utf-8", "replace"), self._secret.encode("ascii"))
                if right and self.on_wake is not None:
                    try:
                        self.on_wake()
                    except Exception:  # noqa: BLE001 — a failed wake must not end the door
                        pass

    def serving(self, port: Optional[int]) -> None:
        """Note where the program listens now (None when stopped)."""
        self._info["port"] = port
        self._write()

    def _write(self) -> None:
        """Replace the note in one go. Never fatal: without a note a second start
        only says the program is open, instead of bringing it forward."""
        with self._writing:  # the main thread and the server's threads both write it
            if self._fd is None:
                return
            note = dict(self._info, secret=self._secret)
            path = self.root / INFO_FILE
            temporary = path.with_name(path.name + ".tmp")
            try:
                fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(note, handle)
                os.replace(temporary, path)
            except OSError:
                pass

    def release(self) -> None:
        door, self._door = self._door, None
        if door is not None:
            door.close()
        if self._fd is not None:
            try:
                (self.root / INFO_FILE).unlink()
            except OSError:
                pass
            os.close(self._fd)  # also releases the lock
            self._fd = None


def running_note(root: Path) -> Optional[dict]:
    """What the Rota Planner holding the folder wrote about itself, if anything."""
    try:
        note = json.loads((Path(root) / INFO_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return note if isinstance(note, dict) else None


def wake(root: Path, timeout: float = 2.0) -> bool:
    """Ask the Rota Planner holding the folder to bring its window forward."""
    note = running_note(root) or {}
    door, secret = note.get("door"), note.get("secret")
    if not isinstance(door, int) or not isinstance(secret, str):
        return False
    try:
        with socket.create_connection(("127.0.0.1", door), timeout=timeout) as connection:
            connection.sendall(f"{WAKE_WORD} {secret}\n".encode("ascii"))
        return True
    except OSError:
        return False
