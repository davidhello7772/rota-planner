"""Where the computers meet: a place that holds one small file per computer.

Two kinds, with the same few gestures (``list``, ``read``, ``write``,
``delete``, ``check``), so that synchronisation (``sync.py``) does not care
which one it talks to, and another kind can be added later:

* ``FolderPlace``: a shared folder already on this computer (Google Drive,
  Synology Drive, a network folder).
* ``WebDavPlace``: a WebDAV server on the internet (Koofr, Nextcloud…), with
  nothing to install. HTTPS with the certificate checked, a user name and an
  app password, no redirect ever followed (the password goes nowhere else).

Everything stored there is already encrypted by ``sync.py``; a place only
sees file names, sizes and times. Problems are raised as ``PlaceError`` with
a ``kind`` the program can act on and a message for the coordinator.
"""

from __future__ import annotations

import base64
import http.client
import math
import os
import re
import secrets
import socket
import ssl
import stat
import threading
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Dict, NamedTuple, Optional, Tuple
from urllib.parse import quote, unquote, urlsplit
from xml.etree import ElementTree
from xml.parsers import expat as pyexpat

from . import tls
from .xlsx_read import SAFE_EXPAT  # older XML parsers can be made to freeze

# The only names a place is asked about: a computer's data file and its "pen" note.
NAME = re.compile(r"^(rota|pen)-(c-[0-9a-f]{12})\.rpsync\Z")
MAX_FILE_BYTES = 40_000_000    # one computer's whole data, sealed: far more than it ever is
MAX_LISTING_BYTES = 2_000_000
TIMEOUT_SECONDS = 20
LOOPBACK = ("127.0.0.1", "localhost", "::1")
PROPFIND = (b'<?xml version="1.0" encoding="utf-8"?><d:propfind xmlns:d="DAV:"><d:prop>'
            b"<d:getetag/><d:getcontentlength/><d:getlastmodified/></d:prop></d:propfind>")


class PlaceError(Exception):
    """The place could not do what was asked. ``kind``: offline, auth, refused,
    missing, full, busy, tls, moved, folder, other. The message is for the coordinator."""

    def __init__(self, kind: str, message: str, retry_after: float = 0.0) -> None:
        super().__init__(message)
        self.kind, self.retry_after = kind, retry_after


class Entry(NamedTuple):
    signature: str   # changes whenever the file changes
    modified: float  # when it was last written, by the place's clock
    size: int


class Listing(NamedTuple):
    files: Dict[str, Entry]
    now: float       # the place's clock at the time of the listing


# --------------------------------------------------------------------------
# A shared folder
# --------------------------------------------------------------------------


class FolderPlace:
    kind = "folder"
    local = True  # looking costs nothing: the screens may look at once

    def __init__(self, folder: str) -> None:
        self.folder = Path(folder)

    def describe(self) -> Dict[str, Any]:
        return {"kind": "folder", "folder": str(self.folder), "label": str(self.folder)}

    def _there(self) -> None:
        if not self.folder.is_dir():
            raise PlaceError("folder", "The shared folder was not found. Is the drive connected?")

    def list(self) -> Listing:
        self._there()
        files = {}
        try:
            for path in self.folder.iterdir():
                if NAME.match(path.name):
                    stat = path.stat()
                    files[path.name] = Entry(f"{stat.st_mtime_ns}-{stat.st_size}", stat.st_mtime, stat.st_size)
        except OSError:
            raise PlaceError("folder", "The shared folder cannot be read.") from None
        return Listing(files, time.time())

    def read(self, name: str, limit: int = MAX_FILE_BYTES) -> bytes:
        _named(name)
        path = self.folder / name
        try:
            # A plain file only (never a link, a pipe or a device), and never more than the limit.
            if not stat.S_ISREG(os.lstat(path).st_mode):
                raise PlaceError("other", "Something in the shared folder is not a plain file.")
            with open(path, "rb") as handle:
                data = handle.read(limit + 1)
        except OSError:
            raise PlaceError("missing", "A file in the shared folder cannot be read.") from None
        if len(data) > limit:
            raise PlaceError("other", "A file in the shared folder is far too big to be Rota Planner data.")
        return data

    def write(self, name: str, data: bytes) -> None:
        _named(name)
        self._there()
        own = self.folder / name
        temporary = own.with_name(f".{name}.{secrets.token_hex(4)}.tmp")
        try:
            temporary.write_bytes(data)
            os.replace(temporary, own)
        except OSError:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise PlaceError("folder", "Rota Planner cannot write in the shared folder.") from None

    def delete(self, name: str) -> None:
        _named(name)
        try:
            (self.folder / name).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            raise PlaceError("folder", "Rota Planner cannot write in the shared folder.") from None

    def check(self) -> None:
        if not self.folder.is_absolute():
            raise PlaceError("folder", "Give the full path of the shared folder, for example "
                                       "/home/you/Google Drive/Rota or G:\\My Drive\\Rota.")
        if not self.folder.is_dir():
            raise PlaceError("folder", "This folder was not found. Is the drive connected, and the folder created?")
        probe = self.folder / f".rota-planner-check-{secrets.token_hex(4)}"
        try:
            probe.write_bytes(b"")
            probe.unlink()
        except OSError:
            raise PlaceError("folder", "Rota Planner cannot write in this folder.") from None


def _named(name: str) -> None:
    if not NAME.match(name):
        raise PlaceError("other", "Not a Rota Planner file name.")


# --------------------------------------------------------------------------
# A WebDAV server
# --------------------------------------------------------------------------


def webdav_address(url: str) -> Tuple[str, str, int, str]:
    """(scheme, host, port, path ending with /) of a WebDAV folder address, checked."""
    text = str(url or "").strip()
    if not re.match(r"^https?://", text, re.I):
        text = "https://" + text if text else text
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        raise PlaceError("other", "This is not a web address.") from None
    host = (parts.hostname or "").lower()
    if not host or parts.username or parts.password or parts.query or parts.fragment:
        raise PlaceError("other", "Give the address of the folder on the server, such as "
                                  "https://app.koofr.net/dav/Koofr/Rota/ (the user name and password go in their own boxes).")
    scheme = parts.scheme.lower()
    if scheme != "https" and host not in LOOPBACK:
        raise PlaceError("other", "The address must start with https:// so that the password travels encrypted.")
    path = parts.path or "/"
    if any(ord(c) < 32 for c in unquote(path)):
        raise PlaceError("other", "This is not a web address.")
    if not path.endswith("/"):
        path += "/"
    # As it travels: a space or an accent in a folder's name is written %20, %C3%A9…
    # (typed either way, it becomes the same address).
    path = quote(unquote(path), safe="/")
    return scheme, host, port or (443 if scheme == "https" else 80), path


class WebDavPlace:
    kind = "webdav"
    local = False  # the network: only the background courier ever talks to it

    def __init__(self, url: str, user: str, password: str,
                 connect: Optional[Callable[..., Any]] = None, timeout: float = TIMEOUT_SECONDS) -> None:
        self.scheme, self.host, self.port, self.path = webdav_address(url)
        self.user, self.password = str(user or ""), str(password or "")
        self.timeout = timeout
        self._connect = connect
        if pyexpat.version_info < SAFE_EXPAT:
            raise PlaceError("other", "This Python is too old to read the server's answers safely. Please install a recent Python 3.")

    @property
    def url(self) -> str:
        default = 443 if self.scheme == "https" else 80
        port = "" if self.port == default else f":{self.port}"
        return f"{self.scheme}://{self.host}{port}{self.path}"

    def describe(self) -> Dict[str, Any]:
        """Never the password."""
        return {"kind": "webdav", "url": self.url, "user": self.user, "label": f"{self.host}{unquote(self.path)}"}

    # -- one request ---------------------------------------------------------

    def _open(self) -> Any:
        if self._connect is not None:
            return self._connect(self.host, self.port, self.timeout)
        if self.scheme == "https":
            return http.client.HTTPSConnection(self.host, self.port, timeout=self.timeout, context=tls.context())
        return http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)

    def _request(self, method: str, name: str = "", body: Optional[bytes] = None,
                 headers: Optional[Dict[str, str]] = None, limit: int = MAX_LISTING_BYTES) -> Tuple[int, Any, bytes]:
        """(status, headers, body). Network trouble is ``PlaceError("offline")``."""
        token = base64.b64encode(f"{self.user}:{self.password}".encode("utf-8")).decode("ascii")
        sent = {"Authorization": f"Basic {token}", "User-Agent": "RotaPlanner", "Accept": "*/*", "Connection": "close"}
        sent.update(headers or {})
        if body is not None:
            sent["Content-Length"] = str(len(body))
        connection = None
        late = threading.Event()

        def cut() -> None:
            """The whole exchange has a time limit, however slowly the server feeds it."""
            late.set()
            try:
                if connection is not None and connection.sock is not None:
                    connection.sock.shutdown(socket.SHUT_RDWR)  # wakes a read that is waiting
                    connection.sock.close()
            except OSError:
                pass

        watchdog = threading.Timer(self.timeout * 3 + len(body or b"") / 20_000, cut)
        watchdog.daemon = True
        try:
            connection = self._open()
            watchdog.start()
            connection.request(method, self.path + quote(name), body=body, headers=sent)
            response = connection.getresponse()
            chunks, size = [], 0
            while True:
                chunk = response.read1(65536)
                if late.is_set():
                    raise PlaceError("offline", "The server answers too slowly.")
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise PlaceError("other", "The server's answer is far too big to be Rota Planner data.")
                chunks.append(chunk)
            if late.is_set():
                raise PlaceError("offline", "The server answers too slowly.")
            return response.status, response.headers, b"".join(chunks)
        except ssl.SSLCertVerificationError:
            raise PlaceError("tls", "The server's security certificate was refused, so nothing was sent. "
                                    "Check the address; a server with a home-made certificate cannot be used.") from None
        except (socket.timeout, TimeoutError):
            raise PlaceError("offline", "The server did not answer in time.") from None
        except (OSError, http.client.HTTPException, ValueError):
            if late.is_set():
                raise PlaceError("offline", "The server answers too slowly.") from None
            raise PlaceError("offline", "The server cannot be reached. Is this computer connected to the internet?") from None
        finally:
            watchdog.cancel()
            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    pass

    def _refused(self, status: int, headers: Any, what: str) -> PlaceError:
        """The error for an answer that is not the one hoped for."""
        if status == 401:
            return PlaceError("auth", "The server refuses the user name or the app password. Check them "
                                      "(for Koofr: an app password made in Preferences › Password, not the account's own).")
        if status == 403:
            return PlaceError("refused", "The server does not allow this account to use that folder.")
        if status in (404, 409, 410):
            return PlaceError("missing", "The folder was not found on the server. Check the address.")
        if status == 507 or status == 413:
            return PlaceError("full", "The server's space is full (or the file is too big for it).")
        if status in (429, 502, 503, 504):
            try:
                wait = float(headers.get("Retry-After", "0") or 0)
            except (TypeError, ValueError):
                wait = 0.0
            if not math.isfinite(wait):
                wait = 0.0
            return PlaceError("busy", "The server is busy: Rota Planner will try again shortly.", min(max(wait, 0.0), 3600.0))
        if 300 <= status < 400:
            return PlaceError("moved", "The server answered with another address. Use the folder's final address "
                                       "(Rota Planner never follows a redirection, so the password goes nowhere else).")
        return PlaceError("other", f"The server could not {what} (answer {status}).")

    # -- the gestures ----------------------------------------------------------

    def list(self) -> Listing:
        status, headers, body = self._request("PROPFIND", body=PROPFIND,
                                              headers={"Depth": "1", "Content-Type": 'application/xml; charset="utf-8"'})
        if status != 207:
            if status == 200:  # a Wi-Fi sign-in page, a proxy: not the server
                raise PlaceError("offline", "Something answered in place of the server (a Wi-Fi sign-in page?).")
            raise self._refused(status, headers, "list the folder")
        return Listing(_parse_listing(body, self.path), _server_time(headers))

    def read(self, name: str, limit: int = MAX_FILE_BYTES) -> bytes:
        _named(name)
        status, headers, body = self._request("GET", name, limit=limit)
        if status != 200:
            raise self._refused(status, headers, "give a file")
        return body

    def write(self, name: str, data: bytes) -> None:
        _named(name)
        status, headers, _ = self._request("PUT", name, body=data, headers={"Content-Type": "application/octet-stream"})
        if status not in (200, 201, 204):
            raise self._refused(status, headers, "store a file")

    def delete(self, name: str) -> None:
        _named(name)
        status, headers, _ = self._request("DELETE", name)
        if status not in (200, 202, 204, 404):
            raise self._refused(status, headers, "remove a file")

    def check(self) -> None:
        """The folder is there (made if it can be) and this account can write in it."""
        status, headers, _ = self._request("PROPFIND", body=PROPFIND,
                                           headers={"Depth": "0", "Content-Type": 'application/xml; charset="utf-8"'})
        if status in (404, 409):
            made, made_headers, _ = self._request("MKCOL")
            if made not in (201, 405):
                raise self._refused(made, made_headers, "make the folder")
        elif status == 200:
            raise PlaceError("offline", "Something answered in place of the server (a Wi-Fi sign-in page?), or this "
                                        "address is not a WebDAV folder.")
        elif status != 207:
            raise self._refused(status, headers, "open the folder")
        probe = f"rota-c-{secrets.token_hex(6)}.rpsync"  # an allowed name, of no computer
        self.write(probe, b"check")
        try:
            if self.read(probe) != b"check":
                raise PlaceError("other", "The server did not give back what was stored: it cannot be used.")
        finally:
            self.delete(probe)


def _server_time(headers: Any) -> float:
    try:
        return parsedate_to_datetime(headers.get("Date", "")).timestamp()
    except (TypeError, ValueError, AttributeError):
        return time.time()


def _parse_listing(body: bytes, folder_path: str) -> Dict[str, Entry]:
    """The files named in a 207 answer (only Rota Planner's own names are kept)."""
    if b"<!DOCTYPE" in body or b"<!ENTITY" in body:
        raise PlaceError("other", "The server's answer is not one Rota Planner can read safely.")
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        raise PlaceError("offline", "The server's answer could not be read (cut off on the way?).") from None
    files: Dict[str, Entry] = {}
    for response in root.iter("{DAV:}response"):
        href = response.findtext("{DAV:}href") or ""
        path = unquote(urlsplit(href).path)
        if path.endswith("/"):
            continue  # the folder itself, or a folder inside it
        name = path.rsplit("/", 1)[-1]
        if not NAME.match(name):
            continue
        etag = length = modified = ""
        for propstat in response.iter("{DAV:}propstat"):
            if " 200" not in (propstat.findtext("{DAV:}status") or ""):
                continue  # properties the server could not give
            prop = propstat.find("{DAV:}prop")
            if prop is None:
                continue
            etag = prop.findtext("{DAV:}getetag") or etag
            length = prop.findtext("{DAV:}getcontentlength") or length
            modified = prop.findtext("{DAV:}getlastmodified") or modified
        try:
            when = parsedate_to_datetime(modified).timestamp() if modified else 0.0
        except (TypeError, ValueError):
            when = 0.0
        try:
            size = int(length or 0)
        except ValueError:
            size = 0
        files[name] = Entry(f"{etag}|{length}|{modified}", when, size)
    return files


# --------------------------------------------------------------------------
# From the saved settings
# --------------------------------------------------------------------------


def make(settings: Dict[str, Any], connect: Optional[Callable[..., Any]] = None) -> Any:
    """The place described by the saved settings (``sync.json``)."""
    kind = settings.get("kind")
    if kind == "webdav":
        return WebDavPlace(settings.get("url", ""), settings.get("user", ""), settings.get("password", ""), connect=connect)
    if kind == "folder":
        return FolderPlace(settings.get("folder", ""))
    raise PlaceError("other", "Unknown kind of meeting place.")
