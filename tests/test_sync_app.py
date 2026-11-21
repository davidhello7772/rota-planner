"""Synchronisation as the program and the browser live it: changes taken by
themselves, the page told to reload, and no change without the pen."""

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from rotaplanner.app import App
from rotaplanner.server import PEN_FREE, TOKEN_HEADER, create_server
from rotaplanner.store import Store

from . import helpers as h
from .webdav_server import PASSWORD, USER, WebDavServer

TOKEN = "test-token"


class TwoPrograms(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dav = WebDavServer()
        self.addCleanup(self.dav.close)
        place = {"kind": "webdav", "url": self.dav.url, "user": USER, "password": PASSWORD}
        base = Path(self.tmp.name)
        store_a, store_b = Store(base / "a"), Store(base / "b")
        store_a.save_catalog(h.catalog())
        store_a.save_people(h.people())
        store_a.save_month(h.month())
        store_b.root.mkdir()
        self.a, self.b = App(store_a), App(store_b)
        code = self.a.sync_setup({"action": "start", "name": "Office", "place": place})["code"]
        answer = self.b.sync_setup({"action": "join", "name": "Front desk", "place": place, "code": code})
        self.joined = answer
        self.servers = {}
        for name, app in (("a", self.a), ("b", self.b)):
            server = create_server(app, TOKEN, 0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            self.servers[name] = server.server_address[1]

    def call(self, who, method, path, body=None):
        port = self.servers[who]
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
        headers = {"Host": f"127.0.0.1:{port}", TOKEN_HEADER: TOKEN}
        payload = json.dumps(body).encode() if body is not None else None
        if payload is not None:
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        data = json.loads(response.read() or b"{}")
        connection.close()
        return response.status, data

    def rounds(self):
        for app in (self.a, self.b, self.a, self.b):
            app.sync.exchange()
            app.sync.deliver()

    def test_a_new_computer_receives_everything_when_it_joins(self):
        self.assertEqual(len(self.b.people), 4)
        self.assertEqual([m["id"] for m in self.joined["bootstrap"]["months"]], ["2026-12"])
        self.assertFalse(self.joined["bootstrap"]["empty"])
        self.assertNotIn(PASSWORD, json.dumps(self.joined))

    def test_an_empty_computer_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(App(Store(Path(tmp))).bootstrap()["empty"])
        self.assertFalse(self.a.bootstrap()["empty"])

    def test_changes_arrive_by_themselves_and_the_page_is_told(self):
        before = self.call("b", "GET", "/api/sync/status")[1]["applied"]["seq"]
        self.assertEqual(self.call("a", "POST", "/api/people/save",
                                   {"person": {"id": "ana", "first_name": "Ana", "last_name": "Berg", "note": "From the office"}})[0], 200)
        self.rounds()
        status = self.call("b", "GET", "/api/sync/status")[1]
        self.assertEqual(status["applied"]["seq"], before + 1)
        self.assertIn("People", status["applied"]["text"])
        self.assertEqual(next(p for p in self.b.people if p.id == "ana").note, "From the office")
        self.assertEqual(status["summary"][0], "In sync")

    def test_only_the_month_replaced_loses_its_undo(self):
        self.rounds()
        self.b.history.record("2026-12", "One", "change", {"x": 1})
        self.b.history.record("2026-11", "Two", "change", {"y": 1})
        self.b._after_taking({"replaced": ["months/2026-12.json"], "replaced_months": ["2026-12"]})
        self.assertEqual(self.b.history.stacks("2026-12"), ([], []))
        self.assertEqual([s.label for s in self.b.history.stacks("2026-11")[0]], ["Two"])
        # The list of recent changes says why it is shorter.
        self.assertIn("updated from another computer", self.b.history.note("2026-12"))
        self.assertEqual(self.b.history.note("2026-11"), "Kept until Rota Planner is closed.")

    def test_no_change_without_the_pen(self):
        change = {"person": {"id": "ben", "first_name": "Ben", "last_name": "Cole", "note": "x"}}
        self.rounds()  # the office has seen that another computer joined
        self.assertEqual(self.call("a", "POST", "/api/people/save", change)[0], 200)  # took the pen on the way
        self.assertTrue(self.call("a", "GET", "/api/sync/status")[1]["pen"]["mine"])
        self.rounds()
        status, answer = self.call("b", "POST", "/api/people/save", change)
        self.assertEqual((status, answer["kind"], answer["pen"]["name"]), (423, "held", "Office"))
        self.assertIn("Office is editing", answer["error"])
        self.assertEqual(self.call("b", "GET", "/api/months/2026-12")[0], 200)  # looking is always possible
        self.assertEqual(self.call("b", "POST", "/api/sync/preview", {})[0], 200)
        self.assertEqual(self.call("a", "POST", "/api/sync/pen", {"action": "release"})[1]["pen"]["mine"], False)
        self.rounds()
        self.assertEqual(self.call("b", "POST", "/api/people/save", change)[0], 200)

    def test_newer_data_taken_on_the_way_asks_to_look_again(self):
        self.a.sync.work_offline(True)  # A changes without the pen (as after a cut connection)
        self.call("a", "POST", "/api/people/save", {"person": {"id": "ana", "first_name": "Ana", "last_name": "Berg", "note": "newer"}})
        self.a.sync.exchange()
        status, answer = self.call("b", "POST", "/api/people/save",
                                   {"person": {"id": "ana", "first_name": "Ana", "last_name": "Berg", "note": "from an old form"}})
        self.assertEqual((status, answer.get("updated")), (409, True))
        self.assertEqual(next(p for p in self.b.people if p.id == "ana").note, "newer")  # not overwritten

    def test_working_offline_is_a_decision(self):
        self.rounds()
        self.dav.mode = "down"
        change = {"person": {"id": "ben", "first_name": "Ben", "last_name": "Cole", "note": "offline"}}
        status, answer = self.call("b", "POST", "/api/people/save", change)
        self.assertEqual((status, answer["kind"]), (423, "offline"))
        self.assertTrue(self.call("b", "POST", "/api/sync/pen", {"action": "offline"})[1]["pen"]["working_offline"])
        self.assertEqual(self.call("b", "POST", "/api/people/save", change)[0], 200)

    def test_asking_to_edit_from_the_screen(self):
        self.rounds()
        change = {"person": {"id": "ana", "first_name": "Ana", "last_name": "Berg", "note": "Left open"}}
        self.assertEqual(self.call("a", "POST", "/api/people/save", change)[0], 200)  # A edits, then is forgotten
        self.rounds()
        self.a.sync._last_edit = 0  # its last change is fully written
        answer = self.call("b", "POST", "/api/sync/pen", {"action": "ask"})[1]
        self.assertTrue(answer["pen"]["asking"])
        self.a.sync.exchange()  # A's courier: sends, hands over
        self.b.sync.exchange()
        self.b.sync.deliver()
        self.b.sync.follow_up()  # B's courier: takes the changes, then the pen
        status = self.call("b", "GET", "/api/sync/status")[1]
        self.assertTrue(status["pen"]["mine"])
        self.assertEqual(next(p for p in self.b.people if p.id == "ana").note, "Left open")
        self.assertEqual(self.call("b", "POST", "/api/people/save", change)[0], 200)
        notice = self.call("a", "GET", "/api/sync/status")[1]["pen"]["notice"]
        self.assertIn("Front desk took over", notice["text"])
        self.assertIsNone(self.call("a", "POST", "/api/sync/pen", {"action": "release"})[1]["pen"]["notice"])

    def test_which_requests_need_the_pen(self):
        for path in ("/api/sync/apply", "/api/sync/pen", "/api/mail/test", "/api/data/export",
                     "/api/settings/message/preview", "/api/tasks/times",
                     "/api/months/2026-12/availability/preview", "/api/months/2026-12/export"):
            self.assertIsNotNone(PEN_FREE.fullmatch(path), path)
        for path in ("/api/people/save", "/api/months/2026-12/assign", "/api/months/2026-12/undo",
                     "/api/months/2026-12/redo", "/api/months/2026-12/history/go",
                     "/api/data/import/apply", "/api/months/create", "/api/months/2026-12/mail/send"):
            self.assertIsNone(PEN_FREE.fullmatch(path), path)

    def test_choosing_between_versions_needs_the_pen(self):
        self.rounds()
        self.call("a", "POST", "/api/people/save", {"person": {"id": "ana", "first_name": "Ana", "last_name": "Berg", "note": "x"}})
        self.rounds()  # the office holds the pen
        status, answer = self.call("b", "POST", "/api/sync/apply", {"plan": "whatever", "choices": {"people.json": "mine"}})
        self.assertEqual((status, answer["kind"]), (423, "held"))
        status, answer = self.call("b", "POST", "/api/sync/apply", {"plan": "whatever", "choices": {}})
        self.assertEqual(status, 400)  # no choice: no pen needed (here simply an out-of-date plan)

    def test_keeping_a_restored_backup_needs_the_pen(self):
        self.rounds()
        self.call("a", "POST", "/api/people/save", {"person": {"id": "ana", "first_name": "Ana", "last_name": "Berg", "note": "x"}})
        self.rounds()  # the office holds the pen
        self.b.sync.state["hold"] = "restored"
        status, answer = self.call("b", "POST", "/api/sync/options", {"resume": "mine"})
        self.assertEqual((status, answer["kind"]), (423, "held"))
        self.assertEqual(self.b.sync.state["hold"], "restored")  # nothing decided
        self.call("a", "POST", "/api/sync/pen", {"action": "release"})
        self.rounds()
        # Nobody edits: kept, without first taking the others' newer data (which is what was not chosen).
        self.assertEqual(self.call("b", "POST", "/api/sync/options", {"resume": "mine"})[0], 200)
        self.assertEqual(self.b.sync.state["hold"], "")

    def test_the_screen_is_told_to_only_look(self):
        self.rounds()
        self.assertFalse(self.call("b", "GET", "/api/sync/status")[1]["pen"]["read_only"])  # nobody edits
        self.call("a", "POST", "/api/people/save", {"person": {"id": "ana", "first_name": "Ana", "last_name": "Berg", "note": "x"}})
        self.rounds()
        pen = self.call("b", "GET", "/api/sync/status")[1]["pen"]
        self.assertTrue(pen["read_only"])
        self.assertIn("Ask to edit", pen["how"])
        self.assertFalse(self.call("a", "GET", "/api/sync/status")[1]["pen"]["read_only"])  # the one editing

    def test_test_the_connection(self):
        status, answer = self.call("a", "POST", "/api/sync/test", {"place": {"kind": "webdav", "url": self.dav.url, "user": USER, "password": "wrong"}})
        self.assertEqual(status, 400)
        self.assertIn("refuses the user name", answer["error"])
        status, answer = self.call("a", "POST", "/api/sync/test", {"place": {"kind": "webdav", "url": self.dav.url, "user": USER, "password": ""}})
        self.assertEqual((status, answer["ok"]), (200, True))
        self.assertNotIn(PASSWORD, json.dumps(answer))


if __name__ == "__main__":
    unittest.main()
