"""The control window and what it stands on: one copy per data folder (the
lock and the wake-up door), starting and stopping the server, the drawing,
and the window itself when this computer has a screen."""

import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from rotaplanner import instance as inst
from rotaplanner import paint, sync
from rotaplanner.access import Access
from rotaplanner.app import App
from rotaplanner.service import FAILED, RUNNING, STOPPED, Service
from rotaplanner.store import Store

from . import helpers as h

PROJECT = Path(__file__).resolve().parent.parent


def tiny_store(folder: Path) -> Store:
    store = Store(folder)
    store.save_catalog(h.catalog())
    store.save_people(h.people())
    store.save_month(h.month())
    return store


class InstanceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_only_one_holds_the_folder(self):
        first, second = inst.Instance(self.root), inst.Instance(self.root)
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())
        first.release()
        self.assertTrue(second.acquire())
        second.release()

    def test_a_note_left_by_a_killed_program_is_replaced_at_once(self):
        (self.root / inst.INFO_FILE).write_text(json.dumps({"pid": 1, "port": 9999, "door": 9, "secret": "old"}))
        holder = inst.Instance(self.root)
        self.assertTrue(holder.acquire())
        note = inst.running_note(self.root)
        self.assertIsNone(note["port"])
        self.assertNotEqual(note["secret"], "old")
        self.assertEqual((self.root / inst.INFO_FILE).stat().st_mode & 0o077, 0)
        holder.release()
        self.assertIsNone(inst.running_note(self.root))

    def test_wake_needs_the_secret(self):
        holder = inst.Instance(self.root)
        self.assertTrue(holder.acquire())
        woken = []
        holder.on_wake = lambda: woken.append(True)
        holder.open_door()
        holder.serving(8765)
        self.assertEqual(inst.running_note(self.root)["port"], 8765)
        door = inst.running_note(self.root)["door"]
        for wrong in (b"show not-the-secret\n", "show caf\u00e9\n".encode()):
            with socket.create_connection(("127.0.0.1", door), timeout=2) as connection:
                connection.sendall(wrong)
        self.assertTrue(inst.wake(self.root))
        for _ in range(50):
            if woken:
                break
            time.sleep(0.02)
        self.assertEqual(woken, [True])  # once: the wrong secret did nothing
        holder.release()
        self.assertFalse(inst.wake(self.root))

    def test_a_second_start_uses_the_first(self):
        tiny_store(self.root)
        first = subprocess.Popen([sys.executable, "run.py", "--data", str(self.root), "--port", "0", "--no-browser"],
                                 cwd=PROJECT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(first.wait)
        self.addCleanup(first.terminate)
        for _ in range(150):  # up to 30 s, never forever
            if isinstance((inst.running_note(self.root) or {}).get("port"), int) or first.poll() is not None:
                break
            time.sleep(0.2)
        self.assertIsNone(first.poll(), "the first program stopped")
        self.assertIsInstance(inst.running_note(self.root)["port"], int)
        second = subprocess.run([sys.executable, "run.py", "--data", str(self.root), "--no-browser"],
                                cwd=PROJECT, capture_output=True, text=True, timeout=30)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("already running", second.stdout)
        reset = subprocess.run([sys.executable, "run.py", "--data", str(self.root), "--reset-access", "--no-browser"],
                               cwd=PROJECT, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(reset.returncode, 0)
        self.assertIn("close it first", reset.stdout)
        first.terminate()  # as the system does at log-out: the program ends cleanly
        first.wait(timeout=10)
        self.assertFalse((self.root / inst.INFO_FILE).exists())
        self.assertFalse((self.root / ".running").exists())


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.access = Access(root)
        self.service = Service(App(tiny_store(root)), self.access, "token", 0)
        self.states = []
        self.service.listeners.append(self.states.append)
        self.addCleanup(self.service.close)
        self.addCleanup(self.tmp.cleanup)

    def get(self, port, path="/", token=None):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        headers = {"Host": f"127.0.0.1:{port}"}
        if token:
            headers["X-Rota-Token"] = token
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        status, body = response.status, response.read()
        connection.close()
        return status, body

    def test_start_stop_start_on_the_same_port(self):
        self.assertTrue(self.service.start())
        port = self.service.port
        self.assertEqual(self.get(port)[0], 200)
        self.assertEqual(self.get(port, "/api/bootstrap", "token")[0], 200)
        self.assertEqual(self.service.url(), f"http://127.0.0.1:{port}/?t=token")
        self.assertEqual(self.service.plain_url(), f"http://127.0.0.1:{port}/")
        self.service.stop()
        self.assertEqual(self.service.state, STOPPED)
        self.assertEqual(self.service.url(), "")
        # Stopped, the address is kept (nobody else can take it), and nothing but "stopped" is said.
        status, body = self.get(port, "/api/bootstrap", "token")
        self.assertEqual(status, 503)
        self.assertIn(b"stopped", body)
        self.assertNotIn(b"Ana", body)
        self.assertTrue(self.service.start())
        self.assertEqual(self.service.port, port)
        self.assertEqual(self.get(port, "/api/bootstrap", "token")[0], 200)
        self.assertEqual(self.states, ["starting", "running", "stopping", "stopped", "starting", "running"])
        self.service.close()
        with self.assertRaises(OSError):
            self.get(port)

    def test_a_new_port_gets_a_new_token_and_ends_sessions(self):
        renewed = []
        self.service.renew_token = lambda: renewed.append(True) or "fresh"
        self.access.set_password("", "a-long-password")
        self.service.start()
        port = self.service.port
        session = self.access.login("127.0.0.1", "a-long-password")
        self.assertTrue(self.access.check_session(session, "127.0.0.1"))
        self.service.close()  # as when the address changes (Settings › Access)
        squatter = socket.socket()
        squatter.bind(("127.0.0.1", port))  # meanwhile, something else took the port
        self.addCleanup(squatter.close)
        self.service.preferred = port
        self.assertTrue(self.service.start())
        self.assertNotEqual(self.service.port, port)
        self.assertEqual((renewed, self.service.token), ([True], "fresh"))
        self.assertFalse(self.access.check_session(session, "127.0.0.1"))

    def test_with_a_password_the_address_holds_no_token(self):
        self.access.set_password("", "a-long-password")
        self.service.start()
        self.assertNotIn("token", self.service.url())

    def test_a_port_in_use_is_reported(self):
        self.service.start()
        busy = Service(self.service.app, Access(Path(self.tmp.name)), "token", self.service.port)
        import rotaplanner.service as svc
        original = svc.first_free_server
        svc.first_free_server = lambda *a, **k: (_ for _ in ()).throw(OSError("No free port"))
        try:
            self.assertFalse(busy.start())
        finally:
            svc.first_free_server = original
        self.assertEqual(busy.state, FAILED)
        self.assertIn("No free port", busy.problem)

    def test_listening_again_when_access_changes(self):
        self.service.start()
        port = self.service.port
        self.access.on_listen_change()
        for _ in range(100):
            if self.states.count("running") == 2:
                break
            time.sleep(0.02)
        self.assertEqual(self.states.count("running"), 2)
        self.assertEqual(self.service.port, port)
        self.assertEqual(self.service.state, RUNNING)


class PaletteTest(unittest.TestCase):
    def test_colours_come_from_the_pages(self):
        from rotaplanner.window import palettes
        colours = palettes()
        self.assertEqual(colours["light"]["accent"], "#4056d6")
        self.assertEqual(colours["dark"]["bg"], "#0f1217")
        for palette in colours.values():
            for name in ("bg", "surface", "line", "line_strong", "text", "text_2", "text_3", "accent",
                         "accent_hover", "accent_press", "on_accent", "chip", "chip_hover", "ok", "ok_weak",
                         "warn", "warn_weak", "bad", "bad_weak", "idle"):
                self.assertRegex(palette[name], r"^#[0-9a-f]{6}$", name)


class PaintTest(unittest.TestCase):
    def test_rounded_box(self):
        rows = paint.rounded_box(40, 20, 8, paint.rgb("#4056d6"), paint.rgb("#ffffff"))
        self.assertEqual((len(rows), len(rows[0])), (20, 40))
        self.assertEqual(rows[0][0], (255, 255, 255))      # the corner is cut away
        self.assertEqual(rows[10][20], paint.rgb("#4056d6"))
        self.assertNotIn(rows[1][3], ((255, 255, 255), paint.rgb("#4056d6")))  # a soft edge

    def test_outlined_box_and_light(self):
        rows = paint.rounded_box(30, 30, 6, paint.rgb("#ffffff"), paint.rgb("#000000"), paint.rgb("#ff0000"), 2)
        self.assertEqual(rows[15][0], (255, 0, 0))
        self.assertEqual(rows[15][15], (255, 255, 255))
        light = paint.light(20, (0, 255, 0), (0, 0, 255), (255, 255, 255))
        self.assertEqual(light[10][10], (0, 255, 0))
        self.assertEqual(light[0][0], (255, 255, 255))

    def test_icon_is_a_png(self):
        data = paint.icon_png(32)
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(data[16:24], (32).to_bytes(4, "big") * 2)


class SyncWordsTest(unittest.TestCase):
    def test_words(self):
        base = {"configured": True, "other": {"name": "office", "written": 1000.0},
                "counts": {"sent": 0, "get": 0, "both": 0}, "problem": None, "kind": None}
        self.assertEqual(sync.summary({"configured": False})[0], "Not set up")
        self.assertEqual(sync.summary(base, now=1000.0 + 300), ["In sync", "ok", "Same data as office (its file: 5 min ago)."])
        self.assertEqual(sync.summary(dict(base, counts={"sent": 0, "get": 2, "both": 0}))[:2], ["2 to get", "warn"])
        self.assertEqual(sync.summary(dict(base, counts={"sent": 0, "get": 2, "both": 1}))[:2], ["Both changed · 1", "bad"])
        self.assertEqual(sync.summary(dict(base, problem="Refused", kind="auth"))[:2], ["Sync: look", "bad"])
        self.assertEqual(sync.summary(dict(base, problem="Folder gone", kind="folder"))[:2], ["Offline", ""])
        self.assertEqual(sync.summary(dict(base, problem="No answer", kind="offline", pending=True))[0],
                         "Offline · changes waiting")
        three = dict(base, others=[{"id": "x"}, {"id": "y"}])
        self.assertEqual(sync.summary(three)[2], "Same data on all 3 computers.")
        self.assertEqual(sync.summary(dict(base, counts={"sent": 3, "get": 0, "both": 0}))[0], "3 sent")


def screen_available() -> bool:
    try:
        import tkinter
    except ImportError:
        return False
    if sys.platform != "darwin" and os.name != "nt" and not os.environ.get("DISPLAY") \
            and not os.environ.get("WAYLAND_DISPLAY"):
        return False
    try:
        root = tkinter.Tk()
        root.destroy()
        return tkinter.TkVersion >= 8.6
    except tkinter.TclError:
        return False


@unittest.skipUnless(screen_available(), "needs Tk 8.6 and a screen")
class WindowTest(unittest.TestCase):
    def setUp(self):
        import tkinter
        from rotaplanner import window
        os.environ["ROTA_PLANNER_THEME"] = "light"
        self.addCleanup(os.environ.pop, "ROTA_PLANNER_THEME", None)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.service = Service(App(tiny_store(root)), Access(root), "token", 0)
        self.addCleanup(self.service.stop)
        self.opened = []
        self.tk = tkinter.Tk(className="RotaPlanner")
        self.window = window.Window(self.tk, self.service, root, demo=False,
                                    open_browser=lambda: self.opened.append(True))
        self.addCleanup(self.close_window)

    def close_window(self):
        import tkinter
        self.window.dispose()
        try:
            self.tk.destroy()
        except tkinter.TclError:
            pass  # already closed by the test

    def settle(self, until, seconds=5.0):
        end = time.time() + seconds
        while time.time() < end:
            self.tk.update()
            if until():
                return True
            time.sleep(0.02)
        return False

    def text(self, name):
        return self.window.widgets[name].cget("text")

    def test_states_and_buttons(self):
        self.window.refresh()
        self.assertEqual(self.text("state"), "Stopped")
        self.assertEqual(self.window.widgets["toggle"].text, "Start")
        self.window.widgets["toggle"]._fire()
        self.assertTrue(self.settle(lambda: self.text("state") == "Running"))
        self.assertEqual(self.text("address"), self.service.plain_url())
        self.assertEqual(self.window.widgets["toggle"].text, "Stop")
        self.window.widgets["open"]._fire()
        self.assertEqual(self.opened, [True])
        self.window.copy()
        self.assertEqual(self.tk.clipboard_get(), self.service.plain_url())  # never the token
        self.window.widgets["toggle"]._fire()
        self.assertTrue(self.settle(lambda: self.text("state") == "Stopped"))
        self.window.widgets["open"]._fire()  # Open when stopped: starts, then opens
        self.assertTrue(self.settle(lambda: self.opened == [True, True] and self.text("state") == "Running"))

    def test_details_and_dark(self):
        self.window.facts["sync"] = {"configured": False}
        self.window.dirty = True
        self.settle(lambda: not self.window.dirty)
        shown = [row[0] for row in self.window.widgets["detail_rows_shown"]]
        self.assertEqual(shown, ["Who can use it", "Other computers", "Data"])
        self.window.facts["dark"] = True
        self.assertTrue(self.settle(lambda: self.window.c["bg"] == "#0f1217"))
        self.assertEqual(self.tk.cget("bg"), "#0f1217")

    def test_closing_stops_the_server(self):
        self.service.start()
        self.window.close()
        self.assertEqual(self.service.state, STOPPED)
        self.assertIsNone(self.service._server)  # the address is given up when the program ends

    def test_the_system_asks_to_end(self):
        self.service.start()
        self.window.request_close()  # what the SIGTERM handler does
        self.settle(lambda: self.window.closing)
        self.assertTrue(self.window.closing)
        self.assertIsNone(self.service._server)

    def test_short_path(self):
        from rotaplanner.window import short_path
        self.assertEqual(short_path(Path.home() / "Rota Planner" / "data"), os.path.join("~", "Rota Planner", "data"))
        self.assertEqual(short_path(Path("/a/very/long/folder/name/that/goes/on/data")), os.path.join("…", "on", "data"))


if __name__ == "__main__":
    unittest.main()
