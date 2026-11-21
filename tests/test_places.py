"""The meeting places: a shared folder and a WebDAV server (a pretend one, on
this computer), and what each says when something is wrong."""

import tempfile
import unittest
import unittest.mock
from pathlib import Path

from rotaplanner import places
from rotaplanner.places import FolderPlace, PlaceError, WebDavPlace

from .webdav_server import PASSWORD, USER, WebDavServer

A, B = "rota-c-0123456789ab.rpsync", "pen-c-ba9876543210.rpsync"


class AddressTest(unittest.TestCase):
    def test_addresses(self):
        self.assertEqual(places.webdav_address("https://app.koofr.net/dav/Koofr/Rota"),
                         ("https", "app.koofr.net", 443, "/dav/Koofr/Rota/"))
        self.assertEqual(places.webdav_address("example.org/dav/My%20Rota/")[3], "/dav/My%20Rota/")
        # A folder's name with a space or an accent, typed as it reads (Koofr's own suggestion has a space).
        self.assertEqual(places.webdav_address("https://app.koofr.net/dav/Koofr/Rota Planner")[3], "/dav/Koofr/Rota%20Planner/")
        self.assertEqual(places.webdav_address("https://example.org/dav/Été 2026/")[3], "/dav/%C3%89t%C3%A9%202026/")
        place = WebDavPlace("https://app.koofr.net/dav/Koofr/Rota Planner/", "me", "x")
        self.assertEqual(place.describe()["label"], "app.koofr.net/dav/Koofr/Rota Planner/")  # shown as it reads
        self.assertEqual(places.webdav_address("http://127.0.0.1:8899/rota/")[:3], ("http", "127.0.0.1", 8899))
        for bad in ("", "http://example.org/dav/", "https://user:pass@example.org/dav/", "https://example.org/dav/?x=1",
                    "https:///dav/", "https://example.org:port/"):
            with self.assertRaises(PlaceError, msg=bad):
                places.webdav_address(bad)

    def test_the_password_is_never_described(self):
        place = WebDavPlace("https://example.org/dav/", "me", "s3cret")
        self.assertNotIn("s3cret", str(place.describe()))
        self.assertEqual(place.describe()["url"], "https://example.org/dav/")


class FolderPlaceTest(unittest.TestCase):
    def test_gestures(self):
        with tempfile.TemporaryDirectory() as tmp:
            place = FolderPlace(tmp)
            place.check()
            place.write(A, b"one")
            (Path(tmp) / "notes.txt").write_text("not ours")
            listing = place.list()
            self.assertEqual(list(listing.files), [A])
            first = listing.files[A].signature
            self.assertEqual(place.read(A), b"one")
            place.write(A, b"three")
            self.assertNotEqual(place.list().files[A].signature, first)
            place.delete(A)
            place.delete(A)  # gone already: fine
            self.assertEqual(place.list().files, {})
            for gesture in (lambda: place.write("other.txt", b""), lambda: place.read("../x"), lambda: place.delete("x")):
                with self.assertRaises(PlaceError):
                    gesture()

    def test_only_plain_files_of_a_sane_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            place = FolderPlace(tmp)
            (Path(tmp) / "elsewhere").write_bytes(b"secret")
            (Path(tmp) / A).symlink_to(Path(tmp) / "elsewhere")
            with self.assertRaises(PlaceError):
                place.read(A)
            (Path(tmp) / B).write_bytes(b"x" * 100)
            with self.assertRaises(PlaceError):
                place.read(B, limit=50)
            self.assertEqual(place.read(B, limit=100), b"x" * 100)

    def test_a_folder_that_is_not_there(self):
        place = FolderPlace("/nowhere/at/all")
        for gesture in (place.list, place.check, lambda: place.write(A, b"x")):
            with self.assertRaises(PlaceError) as caught:
                gesture()
            self.assertEqual(caught.exception.kind, "folder")
        with self.assertRaises(PlaceError):
            FolderPlace("relative").check()


class WebDavPlaceTest(unittest.TestCase):
    def setUp(self):
        self.server = WebDavServer()
        self.addCleanup(self.server.close)
        self.place = WebDavPlace(self.server.url, USER, PASSWORD, timeout=3)

    def kind(self, gesture):
        with self.assertRaises(PlaceError) as caught:
            gesture()
        return caught.exception.kind

    def test_gestures(self):
        self.place.check()
        self.assertEqual(self.server.files, {})  # the trial file is gone
        self.place.write(A, b"one")
        self.place.write(B, b"pen")
        self.server.files["notes.txt"] = (b"not ours", 0.0, 99)
        listing = self.place.list()
        self.assertEqual(sorted(listing.files), sorted([A, B]))
        self.assertEqual(listing.files[A].size, 3)
        self.assertAlmostEqual(listing.now, self.server.now(), delta=5)
        self.assertAlmostEqual(listing.files[A].modified, self.server.now(), delta=5)
        first = listing.files[A].signature
        self.place.write(A, b"three")
        self.assertNotEqual(self.place.list().files[A].signature, first)
        self.assertEqual(self.place.read(A), b"three")
        self.place.delete(A)
        self.place.delete(A)
        self.assertEqual(list(self.place.list().files), [B])

    def test_the_folder_is_made_when_missing(self):
        self.server.exists = False
        self.assertEqual(self.kind(self.place.list), "missing")
        self.place.check()
        self.assertTrue(self.server.exists)

    def test_the_servers_clock_is_used(self):
        self.server.skew = 3600
        self.place.write(A, b"x")
        listing = self.place.list()
        self.assertAlmostEqual(listing.now - listing.files[A].modified, 0, delta=5)

    def test_what_is_said_when_something_is_wrong(self):
        self.place.write(A, b"x")
        for mode, kind in (("auth", "auth"), ("portal", "offline"), ("redirect", "moved"), ("busy", "busy"), ("down", "offline")):
            self.server.mode = mode
            self.assertEqual(self.kind(self.place.list), kind, mode)
        self.server.mode = "full"
        self.assertEqual(self.kind(lambda: self.place.write(A, b"y")), "full")
        self.server.mode = "busy"
        with self.assertRaises(PlaceError) as caught:
            self.place.list()
        self.assertEqual(caught.exception.retry_after, 7)
        self.server.mode = "ok"
        self.assertEqual(self.kind(lambda: WebDavPlace(self.server.url, USER, "wrong", timeout=3).list()), "auth")
        self.assertEqual(self.place.read(A), b"x")  # nothing was lost meanwhile

    def test_a_server_that_dribbles_is_cut_off(self):
        import socket
        import threading
        import time as clock
        slow = socket.socket()
        slow.bind(("127.0.0.1", 0))
        slow.listen(1)
        self.addCleanup(slow.close)

        def dribble():
            connection, _ = slow.accept()
            connection.recv(65536)
            try:
                connection.sendall(b"HTTP/1.1 207 Multi-Status\r\nContent-Length: 100000\r\n\r\n")
                for _ in range(200):
                    connection.sendall(b"<")
                    clock.sleep(0.1)
            except OSError:
                pass
            connection.close()

        threading.Thread(target=dribble, daemon=True).start()
        place = WebDavPlace(f"http://127.0.0.1:{slow.getsockname()[1]}/rota/", USER, PASSWORD, timeout=0.5)
        started = clock.time()
        self.assertEqual(self.kind(place.list), "offline")
        self.assertLess(clock.time() - started, 5)  # the whole answer has a time limit, not each piece

    def test_a_file_locked_for_a_moment_is_asked_for_again(self):
        with unittest.mock.patch.object(places, "LOCKED_WAITS", (0.0, 0.0)):
            self.server.locked = 2  # another computer reads it just then
            self.place.write(A, b"x")
            self.server.locked = 2
            self.assertEqual(self.place.read(A), b"x")
            self.server.locked = 3  # still in use after the pauses: busy for now, not broken
            with self.assertRaises(PlaceError) as caught:
                self.place.write(A, b"y")
        self.assertEqual(caught.exception.kind, "busy")
        self.assertIn("in use by another computer", str(caught.exception))
        self.assertEqual(self.place.read(A), b"x")

    def test_odd_waiting_times_are_ignored(self):
        error = self.place._refused(503, {"Retry-After": "nan"}, "x")
        self.assertEqual((error.kind, error.retry_after), ("busy", 0.0))
        self.assertEqual(self.place._refused(503, {"Retry-After": "1e999"}, "x").retry_after, 0.0)

    def test_a_redirect_is_never_followed(self):
        self.server.mode = "redirect"
        self.assertEqual(self.kind(lambda: self.place.write(A, b"secret")), "moved")
        self.assertTrue(all("elsewhere" not in path for _, path in self.server.requests))

    def test_no_server_at_all(self):
        self.server.close()
        self.assertEqual(self.kind(self.place.list), "offline")

    def test_answers_that_cannot_be_trusted(self):
        for body in (b"<!DOCTYPE x [<!ENTITY a 'b'>]><x/>", b"<not closed"):
            with self.assertRaises(PlaceError):
                places._parse_listing(body, "/rota/")
        full_url = (b'<d:multistatus xmlns:d="DAV:"><d:response><d:href>https://h.example/rota/rota-c-0123456789ab.rpsync</d:href>'
                    b"<d:propstat><d:prop><d:getcontentlength>5</d:getcontentlength></d:prop><d:status>HTTP/1.1 200 OK</d:status>"
                    b"</d:propstat></d:response><d:response><d:href>/rota/..%2Fevil.rpsync</d:href></d:response></d:multistatus>")
        self.assertEqual(list(places._parse_listing(full_url, "/rota/")), [A])  # names from any form of address, ours only

    def test_made_from_settings(self):
        made = places.make({"kind": "webdav", "url": self.server.url, "user": USER, "password": PASSWORD})
        self.assertIsInstance(made, WebDavPlace)
        self.assertIsInstance(places.make({"kind": "folder", "folder": "/tmp"}), FolderPlace)
        with self.assertRaises(PlaceError):
            places.make({"kind": "ftp"})


if __name__ == "__main__":
    unittest.main()
