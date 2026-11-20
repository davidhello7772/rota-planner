"""Who may use the program: this computer only, or also some addresses of
the network, and the password.

Kept in ``<data>/access.json``, which is private to this computer: it is never
exported, never synchronised and never sent to the screen. The password is
kept as a slow hash (scrypt), never in clear.

Sessions (after the password) live in memory only, so the password is asked
again each time the program starts.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from .store import write_private_json

Json = Dict[str, Any]
Network = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]

FILE_NAME = "access.json"
MODES = ("local", "network")
MIN_PASSWORD = 8
MAX_PASSWORD = 200
MAX_ALLOWED = 50
SESSION_IDLE_SECONDS = 8 * 3600
MAX_SESSIONS = 100
FREE_ATTEMPTS = 5
FIRST_WAIT_SECONDS = 30
LONGEST_WAIT_SECONDS = 15 * 60
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}
PBKDF2_ROUNDS = 600_000


class AccessError(ValueError):
    """A setting that cannot be accepted (the message is for the user)."""


# --------------------------------------------------------------------------
# Addresses
# --------------------------------------------------------------------------


def parse_allowed(lines: List[str]) -> List[Network]:
    """Addresses or ranges ("192.168.1.57", "192.168.0.0/23"), checked."""
    networks: List[Network] = []
    for raw in lines:
        text = str(raw).strip()
        if not text:
            continue
        try:
            network = ipaddress.ip_network(text, strict=False)
        except ValueError:
            raise AccessError(f"“{text[:60]}” is not an address such as 192.168.1.57 or a range such as 192.168.1.0/24.") from None
        if network.prefixlen == 0:
            raise AccessError("Allowing every address is not possible: list the computers (or the range) that need it.")
        if network not in networks:
            networks.append(network)
    if len(networks) > MAX_ALLOWED:
        raise AccessError(f"At most {MAX_ALLOWED} addresses or ranges.")
    return networks


def is_loopback(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def public_ranges(networks: List[Network]) -> List[str]:
    """The allowed addresses that are not on a private network (worth a warning)."""
    return [str(n) for n in networks if not (n.is_private or n.is_loopback or n.is_link_local)]


# --------------------------------------------------------------------------
# Password hashing
# --------------------------------------------------------------------------


def hash_password(password: str, salt: Optional[bytes] = None) -> Json:
    salt = salt or secrets.token_bytes(16)
    if hasattr(hashlib, "scrypt"):
        digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=32, **SCRYPT)
        return {"scheme": "scrypt", **SCRYPT, "salt": salt.hex(), "hash": digest.hex()}
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return {"scheme": "pbkdf2-sha256", "rounds": PBKDF2_ROUNDS, "salt": salt.hex(), "hash": digest.hex()}


def check_password(password: str, stored: Optional[Json]) -> bool:
    if not stored or not password:
        return False
    try:
        salt, expected = bytes.fromhex(stored["salt"]), bytes.fromhex(stored["hash"])
        if stored.get("scheme") == "scrypt":
            digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=len(expected),
                                    n=int(stored["n"]), r=int(stored["r"]), p=int(stored["p"]))
        elif stored.get("scheme") == "pbkdf2-sha256":
            digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(stored["rounds"]))
        else:
            return False
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
    return hmac.compare_digest(digest, expected)


def _check_new_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        raise AccessError(f"Choose a password of at least {MIN_PASSWORD} characters.")
    if len(password) > MAX_PASSWORD:
        raise AccessError("This password is too long.")
    return password


# --------------------------------------------------------------------------
# The settings, sessions and wrong-password waits
# --------------------------------------------------------------------------


class Access:
    """Thread-safe: requests are served in parallel."""

    def __init__(self, root: Path, clock: Callable[[], float] = time.monotonic) -> None:
        self.path = Path(root) / FILE_NAME
        self.clock = clock
        self._lock = threading.RLock()
        self.mode = "local"
        self.allowed: List[Network] = []
        self._password: Optional[Json] = None
        self._sessions: Dict[str, List[Any]] = {}  # session id → [last seen, address]
        self._failures: Dict[str, List[float]] = {}  # address → [count, wait until]
        self.on_listen_change: Optional[Callable[[], None]] = None
        self._load()

    # -- file ---------------------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A damaged file must not open the program wider than it was: stay local.
            # The password, if it cannot be read, cannot be checked either: it is
            # set again from this computer, or removed with --reset-access.
            self._password = {"scheme": "unreadable"}
            return
        mode = document.get("mode")
        self.mode = mode if mode in MODES else "local"
        try:
            self.allowed = parse_allowed(document.get("allowed") or [])
        except AccessError:
            self.allowed, self.mode = [], "local"
        password = document.get("password")
        self._password = password if isinstance(password, dict) else None
        if self.mode == "network" and not self._password:
            self.mode = "local"  # never open to the network without a password

    def _save(self) -> None:
        write_private_json(self.path, {"mode": self.mode, "allowed": [str(n) for n in self.allowed], "password": self._password})

    # -- who may connect ----------------------------------------------------

    @property
    def has_password(self) -> bool:
        return self._password is not None

    @property
    def password_unreadable(self) -> bool:
        return bool(self._password and self._password.get("scheme") == "unreadable")

    @property
    def network_open(self) -> bool:
        return self.mode == "network" and self.has_password and bool(self.allowed)

    def bind_host(self) -> str:
        return "0.0.0.0" if self.network_open else "127.0.0.1"

    def allows(self, address: str) -> bool:
        if is_loopback(address):
            return True
        if not self.network_open:
            return False
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        return any(ip in network for network in self.allowed)

    def describe(self) -> Json:
        with self._lock:
            return {
                "mode": self.mode,
                "allowed": [str(n) for n in self.allowed],
                "has_password": self.has_password,
                "password_unreadable": self.password_unreadable,
                "public": public_ranges(self.allowed),
            }

    def save_network(self, mode: str, allowed_lines: List[str]) -> bool:
        """Change who may connect. Returns whether the listening address changes."""
        if mode not in MODES:
            raise AccessError("Unknown choice.")
        allowed = parse_allowed(allowed_lines)
        with self._lock:
            if mode == "network":
                if not self.has_password or self._password.get("scheme") == "unreadable":
                    raise AccessError("Set a password first: other computers must give it.")
                if not allowed:
                    raise AccessError("Give the address of at least one other computer.")
            before = self.bind_host()
            self.mode, self.allowed = mode, allowed
            self._save()
            if self.mode == "local":
                self._sessions = {k: v for k, v in self._sessions.items() if is_loopback(v[1])}
            return self.bind_host() != before

    def set_password(self, current: str, new: str, keep_session: str = "") -> None:
        """Set or change the password (``current`` must match the one there is)."""
        with self._lock:
            if self.has_password and self._password.get("scheme") != "unreadable" \
                    and not check_password(current or "", self._password):
                raise AccessError("The current password is not right.")
            self._password = hash_password(_check_new_password(new))
            self._save()
            self._sessions = {k: v for k, v in self._sessions.items() if k == keep_session}

    def remove_password(self, current: str) -> None:
        with self._lock:
            if not self.has_password:
                return
            if self.mode == "network":
                raise AccessError("Choose “This computer only” first: other computers must always give a password.")
            if self._password.get("scheme") != "unreadable" and not check_password(current or "", self._password):
                raise AccessError("The current password is not right.")
            self._password = None
            self._save()
            self._sessions = {}

    def end_sessions(self) -> None:
        """Everyone must give the password again (when the address had to change)."""
        with self._lock:
            self._sessions = {}

    def reset(self) -> None:
        """Forgotten password: this computer only, no password (``--reset-access``)."""
        with self._lock:
            self.mode, self.allowed, self._password, self._sessions = "local", [], None, {}
            self._save()

    # -- password and sessions ------------------------------------------------

    def wait_left(self, address: str) -> int:
        """Seconds before this address may try a password again (0: now)."""
        with self._lock:
            entry = self._failures.get(address)
            return max(0, int(entry[1] - self.clock() + 0.999)) if entry else 0

    def login(self, address: str, password: str) -> Optional[str]:
        """A new session id, or None (wrong password, or must wait: see ``wait_left``).

        Each attempt counts as a wrong one *before* the password is checked, so
        many attempts sent at the same moment cannot slip past the wait.
        """
        with self._lock:
            entry = self._failures.get(address)
            if entry and entry[1] > self.clock():
                return None
            count = (entry or [0, 0])[0] + 1
            wait = 0.0
            if count >= FREE_ATTEMPTS:
                wait = min(LONGEST_WAIT_SECONDS, FIRST_WAIT_SECONDS * 2 ** (count - FREE_ATTEMPTS))
            self._failures.pop(address, None)
            self._failures[address] = [count, self.clock() + wait]  # most recent last
            while len(self._failures) > 1000:  # bounded memory, oldest first
                self._failures.pop(next(iter(self._failures)))
            stored = self._password
        good = check_password(str(password or "")[:MAX_PASSWORD], stored)  # slow: outside the lock
        with self._lock:
            if not good:
                return None
            self._failures.pop(address, None)
            session = secrets.token_urlsafe(32)
            self._sessions[session] = [self.clock(), address]
            while len(self._sessions) > MAX_SESSIONS:
                self._sessions.pop(min(self._sessions, key=lambda k: self._sessions[k][0]))
            return session

    def check_session(self, session: str, address: str) -> bool:
        """A session is valid while used, and only from the address that opened it."""
        if not session:
            return False
        with self._lock:
            now = self.clock()
            for key, (seen, _) in list(self._sessions.items()):
                if now - seen > SESSION_IDLE_SECONDS:
                    del self._sessions[key]
            for key, entry in self._sessions.items():
                if hmac.compare_digest(key.encode(), session.encode()):
                    if entry[1] != address:
                        return False
                    entry[0] = now
                    return True
            return False

    def logout(self, session: str) -> None:
        with self._lock:
            self._sessions.pop(session, None)
