"""A small WebDAV server for the tests and for trials on this computer only.

Files are kept in memory. It can pretend to be in trouble (``mode``):
"ok", "auth" (refuses the password), "full" (no space), "busy" (try later),
"portal" (a Wi-Fi sign-in page answers instead), "redirect", "down" (cuts
every connection). Its clock can be moved (``skew``), to try what happens
when time passes.

    python3 -m tests.webdav_server 8899     # http://127.0.0.1:8899/rota/  user "rota", password "secret"
"""

from __future__ import annotations

import base64
import sys
import threading
import time
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Tuple
from urllib.parse import unquote
from xml.sax.saxutils import escape

USER, PASSWORD = "rota", "secret"


class WebDavServer:
    def __init__(self, port: int = 0, folder: str = "/rota/") -> None:
        self.folder = folder
        self.files: Dict[str, Tuple[bytes, float, int]] = {}  # name -> (content, modified, version)
        self.exists = True
        self.mode = "ok"
        self.skew = 0.0
        self.requests: list = []
        self.lock = threading.Lock()
        self._versions = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args) -> None:
                pass

            def _answer(self, status: int, body: bytes = b"", headers: Dict[str, str] = None) -> None:
                self.send_response_only(status)  # its own Date header: the server's (movable) clock
                self.send_header("Date", formatdate(owner.now(), usegmt=True))
                self.send_header("Content-Length", str(len(body)))
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def _serve(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                with owner.lock:
                    owner.requests.append((self.command, self.path))
                    mode = owner.mode
                if mode == "down":
                    self.connection.close()
                    return
                if mode == "portal":
                    return self._answer(200, b"<html>Please sign in to the Wi-Fi</html>", {"Content-Type": "text/html"})
                if mode == "redirect":
                    return self._answer(302, b"", {"Location": "http://elsewhere.example/"})
                if mode == "busy":
                    return self._answer(503, b"", {"Retry-After": "7"})
                expected = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
                if mode == "auth" or self.headers.get("Authorization") != expected:
                    return self._answer(401, b"", {"WWW-Authenticate": 'Basic realm="test"'})
                path = unquote(self.path)
                if not path.startswith(owner.folder):
                    return self._answer(404)
                name = path[len(owner.folder):]
                with owner.lock:
                    self._act(name, body, mode)

            def _act(self, name: str, body: bytes, mode: str) -> None:
                if self.command == "MKCOL":
                    if owner.exists:
                        return self._answer(405)
                    owner.exists = True
                    return self._answer(201)
                if not owner.exists:
                    return self._answer(404)
                if self.command == "PROPFIND":
                    depth = self.headers.get("Depth", "infinity")
                    parts = [owner._entry(owner.folder, None)]
                    if depth != "0" and not name:
                        parts += [owner._entry(owner.folder + n, f) for n, f in sorted(owner.files.items())]
                    xml = '<?xml version="1.0"?><D:multistatus xmlns:D="DAV:">' + "".join(parts) + "</D:multistatus>"
                    return self._answer(207, xml.encode(), {"Content-Type": 'application/xml; charset="utf-8"'})
                if self.command == "PUT":
                    if mode == "full":
                        return self._answer(507)
                    owner._versions += 1
                    created = name not in owner.files
                    owner.files[name] = (body, owner.now(), owner._versions)
                    return self._answer(201 if created else 204)
                if self.command == "GET":
                    if name not in owner.files:
                        return self._answer(404)
                    return self._answer(200, owner.files[name][0], {"Content-Type": "application/octet-stream"})
                if self.command == "DELETE":
                    return self._answer(204 if owner.files.pop(name, None) is not None else 404)
                return self._answer(405)

            do_GET = do_PUT = do_DELETE = do_PROPFIND = do_MKCOL = _serve  # noqa: N815

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}{folder}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def now(self) -> float:
        return time.time() + self.skew

    def _entry(self, href: str, stored) -> str:
        if stored is None:
            return (f"<D:response><D:href>{escape(href)}</D:href><D:propstat><D:prop><D:resourcetype>"
                    "<D:collection/></D:resourcetype></D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>")
        content, modified, version = stored
        return (f"<D:response><D:href>{escape(href)}</D:href><D:propstat><D:prop>"
                f'<D:getetag>"v{version}"</D:getetag><D:getcontentlength>{len(content)}</D:getcontentlength>'
                f"<D:getlastmodified>{formatdate(modified, usegmt=True)}</D:getlastmodified></D:prop>"
                "<D:status>HTTP/1.1 200 OK</D:status></D:propstat>"
                "<D:propstat><D:prop><D:quota/></D:prop><D:status>HTTP/1.1 404 Not Found</D:status></D:propstat>"
                "</D:response>")

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


if __name__ == "__main__":
    server = WebDavServer(int(sys.argv[1]) if len(sys.argv) > 1 else 8899)
    print(f"WebDAV test server: {server.url}  user {USER!r}, password {PASSWORD!r}. Ctrl+C to stop.", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.close()
