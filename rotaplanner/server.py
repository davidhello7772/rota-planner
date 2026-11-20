"""The web server: the screens and a small JSON API.

Security model:

- By default the server listens on 127.0.0.1 only, so nothing on the network
  can reach it. When the coordinator allows other addresses (``access.py``),
  it listens on the network but answers only this computer and those
  addresses, and then a password is always needed.
- Without a password, every API call carries the permanent session token;
  with one, a session id given at login (tied to the address that logged
  in). Either travels in a custom header, which other web sites cannot add
  without a CORS pre-flight this server never grants. A POST that names
  another origin is refused.
- Requests whose Host is not this server are refused (DNS rebinding): only
  127.0.0.1 / localhost, and, when other computers are allowed, an IP
  address or this computer's own name.
- Who may connect, the password and the firewall check can only be changed
  or run from this computer itself.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import os
import re
import socket
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

from . import firewall
from .access import Access, AccessError, is_loopback
from .app import ApiError, App, Json
from .sync import PenError, SyncError
from .store import StoreError

WEB_ROOT = Path(__file__).resolve().parent / "web"
TOKEN_HEADER = "X-Rota-Token"
MAX_BODY_BYTES = 30_000_000  # uploads of workbooks travel as base64 JSON

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Cross-Origin-Resource-Policy": "same-origin",  # other sites cannot load our files to detect the program
}

Route = Tuple[str, "re.Pattern[str]", Callable[..., Json]]

# Preparing a month: POST /api/months/<id>/<name>.
MONTH_ACTIONS = {
    "events/save": "save_event",
    "events/delete": "delete_event",
    "events/tasks/save": "save_event_task",
    "events/tasks/delete": "delete_event_task",
    "events/tasks/add-type": "add_type_tasks",
    "events/save-as-type": "save_event_as_type",
    "markers/save": "save_marker",
    "markers/delete": "delete_marker",
    "daytype": "set_day_type",
    "regulars/apply": "reapply_regulars",
    "availability/preview": "availability_preview",
    "availability/apply": "availability_apply",
    "availability/set": "availability_set",
    "availability/comment": "availability_comment",
    "availability/forget-comments": "forget_comments",
    "availability/free-regulars": "free_regulars",
    "prefill": "prefill",
    "rebalance": "rebalance",
    "proposals/accept": "accept_proposals",
    "proposals/discard": "discard_proposals",
    "swap": "swap",
    "mail/preview": "mail_preview",
    "mail/plan": "mail_plan",
    "mail/send": "mail_send",
}

# Set-up actions: POST /api/<name>, body passed as is.
SETUP_ACTIONS = {
    "people/save": "save_person",
    "people/skill": "person_skill",
    "people/import/preview": "import_people_preview",
    "people/import/apply": "import_people_apply",
    "skills/save": "save_skill",
    "skills/delete": "delete_skill",
    "areas/save": "save_area",
    "areas/order": "order_areas",
    "tasks/save": "save_task",
    "tasks/duplicate": "duplicate_task",
    "tasks/order": "order_tasks",
    "config/use": "use_config",
    "templates/save": "save_template",
    "templates/delete": "delete_template",
    "print-templates/save": "save_print_template",
    "print-templates/delete": "delete_print_template",
    "print-templates/duplicate": "duplicate_print_template",
    "event-types/save": "save_event_type",
    "event-types/delete": "delete_event_type",
    "event-types/tasks/save": "save_type_task",
    "event-types/tasks/delete": "delete_type_task",
    "event-types/tasks/order": "order_type_tasks",
    "regulars/set": "set_regular",
    "settings/save": "save_settings",
    "settings/message/preview": "message_preview",
    "import/workbook": "import_workbook",
    "import/workbook/preview": "import_workbook_preview",
    "data/export": "export_data",
    "data/import/preview": "import_preview",
    "data/import/apply": "import_apply",
    "sync/preview": "sync_preview",
    "sync/code": "sync_code",
    "mail/save": "mail_save",
    "mail/remove": "mail_remove",
    "mail/stop": "mail_stop",
}

# Only from the computer where the program runs: the meeting place and the pairing
# code of this computer, installing a new version, opening the exports folder (a
# window on this computer's screen), and everything about e-mail (the account, and
# the list of addresses given when sending, which must not cross the network).
LOCAL_ONLY = re.compile(r"/api/(sync/(setup|code|stop|test|forget|options)|mail(/[a-z]+)?|update/[a-z]+"
                        r"|months/\d{4}-\d{2}/(exports/open|mail/[a-z]+))")

# Changes that do not need the pen (see sync.py: one computer edits at a time): looking
# before deciding, this computer's own settings (e-mail account, saved files), and
# synchronisation itself. Every other POST changes the shared data.
PEN_FREE = re.compile(r"/api/(sync/[a-z-]+|mail/[a-z]+|update/[a-z]+|data/export|data/import/preview|people/import/preview"
                      r"|import/workbook/preview|settings/message/preview"
                      r"|months/\d{4}-\d{2}/(availability/preview|mail/preview|mail/plan|export|exports/open))")


def local_only(path: str) -> bool:
    path = path.rstrip()  # routes are matched with "$", which also accepts a final line break
    return LOCAL_ONLY.fullmatch(path) is not None


def unlocked(handler: Callable[..., Json]) -> Callable[..., Json]:
    """Mark a route that takes the app lock itself, only for as long as it needs it."""
    handler.unlocked = True  # type: ignore[attr-defined]
    return handler


PRINT_PATH = re.compile(r"^/print/(?P<month>\d{4}-\d{2})/(?P<kind>[a-z]+)$")


def build_routes(app: App) -> List[Route]:
    month = r"(?P<month>\d{4}-\d{2})"
    table = [
        ("GET", r"/api/setup", lambda q, b: app.setup()),
        ("GET", r"/api/config", lambda q, b: app.config(q.get("scope", ""))),
        ("GET", r"/api/new-month", lambda q, b: app.new_month_info()),
        ("GET", r"/api/exports", lambda q, b: app.exports_info()),
        ("POST", r"/api/months/create", lambda q, b: app.create_month(b)),
        ("GET", rf"/api/months/{month}/setup", lambda q, b, month: app.month_setup(month)),
        ("GET", rf"/api/months/{month}/availability", lambda q, b, month: app.availability(month)),
        ("GET", rf"/api/months/{month}/shortages", lambda q, b, month: app.shortages(month)),
        ("GET", rf"/api/months/{month}/check", lambda q, b, month: app.check(month)),
        *[("POST", rf"/api/months/{month}/{path}", (lambda method: lambda q, b, month: method(month, b))(getattr(app, name)))
          for path, name in MONTH_ACTIONS.items()],
        *[("POST", f"/api/{path}", (lambda method: lambda q, b: method(b))(getattr(app, name)))
          for path, name in SETUP_ACTIONS.items()],
        ("GET", r"/api/bootstrap", lambda q, b: app.bootstrap()),
        ("GET", r"/api/sync/status", unlocked(lambda q, b: app.sync_status())),
        # A newer version: looking asks the program's repository, installing takes the lock itself.
        ("GET", r"/api/update", unlocked(lambda q, b: app.update_info())),
        ("POST", r"/api/update/check", unlocked(lambda q, b: app.update_check(b))),
        ("POST", r"/api/update/options", unlocked(lambda q, b: app.update_options(b))),
        ("POST", r"/api/update/install", unlocked(lambda q, b: app.update_install(b))),
        # These talk to the meeting place (a server may be slow): never under the app lock.
        ("POST", r"/api/sync/apply", unlocked(lambda q, b: app.sync_choose(b))),
        ("POST", r"/api/sync/setup", unlocked(lambda q, b: app.sync_setup(b))),
        ("POST", r"/api/sync/test", unlocked(lambda q, b: app.sync_test(b))),
        ("POST", r"/api/sync/check", unlocked(lambda q, b: app.sync_check(b))),
        ("POST", r"/api/sync/options", unlocked(lambda q, b: app.sync_options(b))),
        ("POST", r"/api/sync/forget", unlocked(lambda q, b: app.sync_forget(b))),
        ("POST", r"/api/sync/pen", unlocked(lambda q, b: app.sync_pen(b))),
        ("POST", r"/api/sync/stop", unlocked(lambda q, b: app.sync_stop(b))),
        # E-mail: these talk to the mail server or only read its state, so they do not hold the app lock.
        ("GET", r"/api/mail", unlocked(lambda q, b: app.mail_info())),
        ("GET", r"/api/mail/status", unlocked(lambda q, b: app.mail_status())),
        ("POST", r"/api/mail/test", unlocked(lambda q, b: app.mail_test(b))),
        ("GET", rf"/api/months/{month}", lambda q, b, month: app.month_view(month)),
        ("GET", rf"/api/months/{month}/candidates", lambda q, b, month: app.candidates(month, q)),
        ("GET", rf"/api/months/{month}/people/(?P<person>[\w-]+)/eligible",
         lambda q, b, month, person: app.eligible(month, person)),
        ("POST", rf"/api/months/{month}/assign", lambda q, b, month: app.assign(month, b)),
        ("POST", rf"/api/months/{month}/clear", lambda q, b, month: app.clear(month, b)),
        ("POST", rf"/api/months/{month}/note", lambda q, b, month: app.set_note(month, b)),
        ("POST", rf"/api/months/{month}/highlight", lambda q, b, month: app.highlight(month, b)),
        ("POST", rf"/api/months/{month}/headcount", lambda q, b, month: app.headcount(month, b)),
        ("POST", rf"/api/months/{month}/time", lambda q, b, month: app.set_time(month, b)),
        ("POST", rf"/api/months/{month}/undo", lambda q, b, month: app.undo(month, b)),
        ("POST", rf"/api/months/{month}/export", unlocked(lambda q, b, month: app.export_pdf(month, b))),
        ("POST", rf"/api/months/{month}/exports/open", lambda q, b, month: app.open_exports(month, b)),
        ("GET", rf"/api/months/{month}/personal", lambda q, b, month: app.personal(month, q)),
        ("GET", rf"/api/months/{month}/messages", lambda q, b, month: app.messages(month)),
        ("GET", rf"/api/months/{month}/versions", lambda q, b, month: app.versions_info(month)),
        ("POST", rf"/api/months/{month}/versions/publish", lambda q, b, month: app.publish_version(month, b)),
        ("POST", rf"/api/months/{month}/redo", lambda q, b, month: app.redo(month, b)),
    ]
    return [(verb, re.compile(f"^{pattern}$"), handler) for verb, pattern, handler in table]


def this_computer() -> Json:
    """This computer's name and its address on the network, to type on another computer.

    Connecting a UDP socket sends nothing: it only asks the system which
    address it would use.
    """
    addresses = []
    for probe in ("192.168.0.1", "10.0.0.1", "8.8.8.8"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect((probe, 9))
                address = sock.getsockname()[0]
        except OSError:
            continue
        if address not in addresses and not is_loopback(address):
            addresses.append(address)
    return {"name": socket.gethostname(), "addresses": addresses}


def host_allowed(host: str, port: int, access: Access, own_names: List[str]) -> bool:
    """Is the Host header this server? (A name an attacker controls is not.)"""
    name, sep, host_port = host.rpartition(":")
    if not sep or host_port != str(port):
        return False
    name = name.lower()
    if name in ("127.0.0.1", "localhost"):
        return True
    if not access.network_open:
        return False
    if name in own_names:
        return True
    try:
        ipaddress.ip_address(name.strip("[]"))
    except ValueError:
        return False
    return True


def make_handler(app: App, access: Access, token: str, port: int) -> type:
    routes = build_routes(app)
    machine = socket.gethostname().lower()
    own_names = [machine, f"{machine}.local", f"{machine}.lan"]

    class Handler(BaseHTTPRequestHandler):
        server_version = "RotaPlanner"
        sys_version = ""

        def do_GET(self) -> None:  # noqa: N802 (name imposed by http.server)
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            """Stay quiet: requests may contain names."""

        @property
        def client(self) -> str:
            return str(self.client_address[0])

        def _dispatch(self, verb: str) -> None:
            if not access.allows(self.client):
                # Not this computer and not an allowed address: say nothing more.
                self._send(HTTPStatus.FORBIDDEN, b"Not allowed.", "text/plain; charset=utf-8")
                return
            host = self.headers.get("Host", "")
            if not host_allowed(host, port, access, own_names):
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "Unexpected host."})
                return
            origin = self.headers.get("Origin")
            if verb == "POST" and origin is not None and origin != f"http://{host}":
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "Request from another site."})
                return
            url = urlsplit(self.path)
            if url.path == "/api/login" and verb == "POST":
                self._login()
            elif url.path.startswith("/api/"):
                self._api(verb, url.path, url.query)
            elif url.path.startswith("/print/") and verb == "GET":
                self._print(url.path, url.query)
            elif verb == "GET":
                self._static(url.path)
            else:
                self._send_json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "Not allowed."})

        def _authorised(self, supplied: str) -> bool:
            if access.has_password:
                return access.check_session(supplied, self.client)
            return is_loopback(self.client) and hmac.compare_digest(supplied.encode(), token.encode())

        def _refuse(self) -> None:
            if access.has_password:
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "Please give the password.", "login": True})
            else:
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "Missing or wrong session token."})

        def _login(self) -> None:
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "A JSON request was expected."})
                return
            try:
                body = self._read_body()
            except ApiError as error:
                self._send_json(error.status, error.payload)
                return
            if not access.has_password:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "No password is set."})
                return
            wait = access.wait_left(self.client)
            session = None if wait else access.login(self.client, str(body.get("password") or ""))
            if session:
                self._send_json(HTTPStatus.OK, {"session": session})
                return
            wait = access.wait_left(self.client)
            message = (f"Too many wrong passwords: wait {wait} seconds, then try again." if wait
                       else "This is not the password.")
            if access.password_unreadable:
                message = ("The password file is damaged, so no password can be checked. On the computer where "
                           "Rota Planner runs, start it with: python3 run.py --reset-access, then set a new password.")
            self._send_json(HTTPStatus.TOO_MANY_REQUESTS if wait else HTTPStatus.UNAUTHORIZED,
                            {"error": message, "wait": wait, "login": True})

        def _access_route(self, verb: str, path: str, body: Json) -> Json:
            """Who may connect, the password, the firewall: changed from this computer only."""
            local = is_loopback(self.client)
            if path == "/api/logout" and verb == "POST":
                access.logout(self.headers.get(TOKEN_HEADER, ""))
                return {"ok": True}
            if path == "/api/access" and verb == "GET":
                return {**access.describe(), "local": local, "port": port, "computer": this_computer()}
            if not local:
                raise ApiError(HTTPStatus.FORBIDDEN, "This can only be changed on the computer where Rota Planner runs.")
            try:
                if path == "/api/access/network" and verb == "POST":
                    moved = access.save_network(str(body.get("mode") or ""), [str(a) for a in body.get("allowed") or []][:100])
                    if moved and access.on_listen_change:
                        access.on_listen_change()
                    return {**access.describe(), "local": local, "port": port, "computer": this_computer(), "moved": moved}
                if path == "/api/access/password" and verb == "POST":
                    new = str(body.get("new") or "")
                    if new:
                        access.set_password(str(body.get("current") or ""), new, keep_session=self.headers.get(TOKEN_HEADER, ""))
                        if not access.check_session(self.headers.get(TOKEN_HEADER, ""), self.client):
                            # Setting the first password: this browser gets its session at once.
                            return {**access.describe(), "session": access.login(self.client, new)}
                    else:
                        access.remove_password(str(body.get("current") or ""))
                        return {**access.describe(), "session": token}  # back to the permanent token
                    return access.describe()
                if path == "/api/access/firewall" and verb == "GET":
                    return firewall.status(port, access.allowed)
            except AccessError as error:
                raise ApiError(HTTPStatus.BAD_REQUEST, str(error)) from None
            raise ApiError(HTTPStatus.NOT_FOUND, "Unknown address.")

        def _api(self, verb: str, path: str, query: str) -> None:
            supplied = self.headers.get(TOKEN_HEADER, "")
            if not self._authorised(supplied):
                self._refuse()
                return
            try:
                body = self._read_body() if verb == "POST" else {}
                params = {key: values[-1] for key, values in parse_qs(query).items()}
                if path == "/api/logout" or path == "/api/access" or path.startswith("/api/access/"):
                    self._send_json(HTTPStatus.OK, self._access_route(verb, path, body))
                    return
                if local_only(path) and not is_loopback(self.client):
                    if path == "/api/mail" and verb == "GET":  # the screens only need to know it is not for them
                        self._send_json(HTTPStatus.OK, {"configured": False, "elsewhere": True})
                        return
                    raise ApiError(HTTPStatus.FORBIDDEN, "This can only be done on the computer where Rota Planner runs.")
                if path == "/api/bootstrap":
                    with app.lock:
                        result = app.bootstrap()
                    result["auth"] = {"password": access.has_password, "local": is_loopback(self.client)}
                    self._send_json(HTTPStatus.OK, result)
                    return
                for route_verb, pattern, handler in routes:
                    match = pattern.match(path)
                    if match and route_verb == verb:
                        if verb == "POST" and not PEN_FREE.fullmatch(path.rstrip()):
                            self._pen()
                        if getattr(handler, "unlocked", False):
                            result = handler(params, body, **match.groupdict())
                        else:
                            with app.lock:
                                result = handler(params, body, **match.groupdict())
                        if verb == "POST" and not PEN_FREE.fullmatch(path.rstrip()):
                            app._publish()  # noqa: SLF001 — what changed is sent to the other computers
                        if path.rstrip() == "/api/sync/status" and not is_loopback(self.client) and result.get("configured"):
                            # Where the computers meet (address, account) is this computer's business only.
                            result = dict(result, folder="", place={"kind": result["place"]["kind"], "label": ""})
                        self._send_json(HTTPStatus.OK, result)
                        return
                raise ApiError(HTTPStatus.NOT_FOUND, "Unknown address.")
            except ApiError as error:
                self._send_json(error.status, error.payload)
            except (StoreError, ValueError) as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            except OSError as error:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR,
                                {"error": f"A file could not be read or written ({error.strerror or error}). "
                                          "Is it open in another program, or the disk full?"})
            except Exception:  # noqa: BLE001 — the screen must always get an answer
                traceback.print_exc()
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR,
                                {"error": "Something went wrong. Nothing was changed; please try again."})

        def _pen(self) -> None:
            """Before a change: only one computer edits at a time. Takes the pen if it
            is free (a second or two the first time); refuses, in plain words, if not."""
            try:
                updated = app.sync.ensure_pen()
            except PenError as error:
                raise ApiError(HTTPStatus.LOCKED, str(error), kind=error.kind, pen=error.info) from None
            except SyncError as error:
                raise ApiError(HTTPStatus.LOCKED, str(error), kind="other") from None
            if updated:
                # What the screen was about to change is not what it showed any more.
                raise ApiError(HTTPStatus.CONFLICT, "Newer changes from another computer were just taken: "
                                                    "look again, then make your change.", updated=True)

        def _print(self, path: str, query: str) -> None:
            """A document to print, opened in its own tab: the token comes in the address
            (as for the main page), since a new tab cannot send it as a header."""
            params = {key: values[-1] for key, values in parse_qs(query).items()}
            match = PRINT_PATH.match(path)
            if not self._authorised(params.get("t", "")):
                self._send(HTTPStatus.FORBIDDEN, b"Missing or wrong session token. Open the document again from Rota Planner.",
                           "text/plain; charset=utf-8")
                return
            if match is None:
                self._send(HTTPStatus.NOT_FOUND, b"Not found", "text/plain; charset=utf-8")
                return
            try:
                with app.lock:
                    html = app.print_page(match["month"], match["kind"], params)
            except ApiError as error:
                self._send(error.status, str(error.payload.get("error", "")).encode(), "text/plain; charset=utf-8")
                return
            except (StoreError, ValueError) as error:
                self._send(HTTPStatus.BAD_REQUEST, str(error).encode(), "text/plain; charset=utf-8")
                return
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, b"Something went wrong.", "text/plain; charset=utf-8")
                return
            self._send(HTTPStatus.OK, html.encode("utf-8"), CONTENT_TYPES[".html"])

        def _read_body(self) -> Json:
            declared = (self.headers.get("Content-Length") or "0").strip()
            if not (declared.isascii() and declared.isdigit()):
                raise ApiError(HTTPStatus.BAD_REQUEST, "Bad request length.")
            length = int(declared)
            if length > MAX_BODY_BYTES:
                raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request too large.")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid JSON.") from None
            if not isinstance(body, dict):
                raise ApiError(HTTPStatus.BAD_REQUEST, "A JSON object was expected.")
            return body

        def _static(self, path: str) -> None:
            relative = "index.html" if path in ("", "/") else path.lstrip("/")
            try:
                target = (WEB_ROOT / relative).resolve()
                content_type = CONTENT_TYPES.get(target.suffix)
                inside = target == WEB_ROOT or WEB_ROOT in target.parents
                found = inside and content_type is not None and target.is_file()
            except (ValueError, OSError):  # a path the system refuses (a NUL character…)
                found = False
            if not found:
                self._send(HTTPStatus.NOT_FOUND, b"Not found", "text/plain; charset=utf-8")
                return
            self._send(HTTPStatus.OK, target.read_bytes(), content_type)

        def _send_json(self, status: HTTPStatus, payload: Json) -> None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for name, value in SECURITY_HEADERS.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

    return Handler


class _Server(ThreadingHTTPServer):
    # On Windows, "reuse address" would let a second program share the port.
    allow_reuse_address = os.name != "nt"


def create_server(app: App, access: Access, token: str, port: int) -> ThreadingHTTPServer:
    """Bind on 127.0.0.1, or on the network when other computers are allowed.
    ``port`` 0 lets the system choose a free port."""
    server = _Server((access.bind_host(), port), BaseHTTPRequestHandler)
    server.daemon_threads = True
    server.RequestHandlerClass = make_handler(app, access, token, server.server_address[1])
    return server


def first_free_server(app: App, access: Access, token: str, preferred: int, attempts: int = 20) -> ThreadingHTTPServer:
    """Use the preferred port if free (stable bookmarks), else a nearby one."""
    last_error: Optional[OSError] = None
    for port in range(preferred, preferred + attempts):
        try:
            return create_server(app, access, token, port)
        except OSError as error:
            last_error = error
    raise OSError(f"No free port between {preferred} and {preferred + attempts - 1}") from last_error
