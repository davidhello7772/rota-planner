"""Rota Planner's web server, started and stopped on demand.

Used both by the window (Start, Stop) and by the terminal mode. The program
(``App``) stays loaded while the server is stopped.

**Stopping keeps the address.** While stopped, the server still holds its
port but answers every request with a short "Rota Planner is stopped" page,
before looking at any token. Giving the port up instead would let another
account on the same computer take it, and the browser tab still open on Rota
Planner would then hand that account its session token. If the usual port is
no longer free when the server opens again, the new one gets a new session
token: whatever took the old port may have seen the old one.
"""

from __future__ import annotations

import threading
import time
from http import HTTPStatus
from typing import Callable, List, Optional

from .app import App
from .server import SECURITY_HEADERS, first_free_server, make_handler

STOPPED, STARTING, RUNNING, STOPPING, FAILED = "stopped", "starting", "running", "stopping", "failed"

STOPPED_PAGE = ('<!doctype html><meta charset="utf-8"><title>Rota Planner is stopped</title>'
                '<p>Rota Planner is stopped. Start it again from its window, then reload this page.</p>').encode()


def guarded(handler: type) -> type:
    """The request handler, answering only "stopped" while the server is paused."""

    class Guarded(handler):  # type: ignore[valid-type, misc]
        def parse_request(self) -> bool:
            if not super().parse_request():
                return False
            if not getattr(self.server, "paused", False):
                return True
            self.close_connection = True
            self.send_response(HTTPStatus.SERVICE_UNAVAILABLE)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(STOPPED_PAGE)))
            self.send_header("Connection", "close")
            for name, value in SECURITY_HEADERS.items():
                self.send_header(name, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(STOPPED_PAGE)
            return False  # the request goes no further: no token, no data

    return Guarded


class Service:
    def __init__(self, app: App, token: str, port: int,
                 renew_token: Optional[Callable[[], str]] = None) -> None:
        self.app, self.token = app, token
        self.renew_token = renew_token
        self.preferred = port
        self.state = STOPPED
        self.port: Optional[int] = None
        self.since: Optional[float] = None
        self.problem = ""
        self.listeners: List[Callable[[str], None]] = []
        self._server = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()

    # -- state ------------------------------------------------------------

    def _set(self, state: str) -> None:
        self.state = state
        for listener in list(self.listeners):
            try:
                listener(state)
            except Exception:  # noqa: BLE001 — a display problem must not stop the server
                pass

    @property
    def running(self) -> bool:
        return self.state == RUNNING

    def url(self) -> str:
        """The address to open on this computer, with the session token."""
        if not self.running:
            return ""
        return f"http://127.0.0.1:{self.port}/?t={self.token}"

    def plain_url(self) -> str:
        """The address without the token: safe to show and copy."""
        return f"http://127.0.0.1:{self.port}/" if self.running else ""

    # -- start and stop ---------------------------------------------------

    def start(self) -> bool:
        """Start answering. False (with ``problem`` set) when it could not."""
        with self._lock:
            if self.state == RUNNING:
                return True
            self._set(STARTING)
            if self._server is not None:  # stopped: the port was kept, open the door again
                self._server.paused = False
            elif not self._bind():
                self._set(FAILED)
                return False
            self.problem = ""
            self.since = time.time()
            self._set(RUNNING)
            return True

    def _bind(self) -> bool:
        try:
            server = first_free_server(self.app, self.token, self.preferred)
        except OSError as error:
            self.problem = str(error)
            return False
        port = server.server_address[1]
        if self.port is not None and port != self.port and self.renew_token is not None:
            # The usual port was taken meanwhile, perhaps by another account that
            # received the browser's token: it is not valid any more.
            self.token = self.renew_token()
        server.RequestHandlerClass = guarded(make_handler(self.app, self.token, port))
        server.paused = False
        self._server = server
        self.port = self.preferred = port
        self._thread = threading.Thread(target=server.serve_forever, name="rota-server", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        """Stop answering, but keep the address (see the module's note)."""
        with self._lock:
            if self._server is None or self.state == STOPPED:
                return
            self._set(STOPPING)
            self._server.paused = True
            self.since = None
            self._set(STOPPED)

    def close(self) -> None:
        """Give the address up: only when the program ends."""
        with self._lock:
            server, thread = self._server, self._thread
            if server is None:
                return
            if self.state != STOPPED:
                self._set(STOPPING)
            server.shutdown()
            server.server_close()
            if thread is not None:
                thread.join(timeout=5)
            self._server = self._thread = None
            self.since = None
            if self.state != STOPPED:
                self._set(STOPPED)
