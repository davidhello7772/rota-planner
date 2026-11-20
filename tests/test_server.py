import base64
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from rotaplanner.access import Access
from rotaplanner.app import App
from rotaplanner.server import TOKEN_HEADER, create_server
from rotaplanner.store import Store

from . import helpers as h

TOKEN = "test-token"


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        store = Store(Path(cls.tmp.name))
        store.save_catalog(h.catalog())
        store.save_people(h.people())
        store.save_month(h.month())
        cls.server = create_server(App(store), Access(Path(cls.tmp.name)), TOKEN, 0)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def request(self, method, path, body=None, token=TOKEN, host=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if token:
            headers[TOKEN_HEADER] = token
        payload = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        if payload is not None:
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=payload, headers=headers)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response, data

    def json(self, method, path, body=None):
        response, data = self.request(method, path, body)
        return response.status, json.loads(data)

    def test_page_is_served_with_a_strict_policy(self):
        response, data = self.request("GET", "/", token=None)
        self.assertEqual(response.status, 200)
        self.assertIn(b"Rota Planner", data)
        self.assertIn("default-src 'self'", response.getheader("Content-Security-Policy"))
        self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")

    def test_print_pages_need_the_token_in_their_address(self):
        response, _ = self.request("GET", "/print/2026-12/month", token=None)
        self.assertEqual(response.status, 403)
        response, data = self.request("GET", f"/print/2026-12/month?paper=a4&t={TOKEN}", token=None)
        self.assertEqual(response.status, 200)
        self.assertIn("Rota — December 2026".encode(), data)
        self.assertIn(b'href="/css/print.css"', data)
        self.assertIn("default-src 'self'", response.getheader("Content-Security-Policy"))
        response, _ = self.request("GET", f"/print/2026-12/nothing?t={TOKEN}", token=None)
        self.assertEqual(response.status, 404)
        response, _ = self.request("GET", f"/print/2026-12/event?event=nope&t={TOKEN}", token=None)
        self.assertEqual(response.status, 400)

    def test_phase_of_the_rota_through_the_api(self):
        status, month = self.json("GET", "/api/months/2026-12")
        self.assertEqual((status, month["phase"]["state"]), (200, "draft"))
        status, info = self.json("POST", "/api/months/2026-12/versions/publish", {"audience": "manager"})
        self.assertEqual((status, info["phase"]["state"], info["versions"][-1]["audience"]), (200, "checking", "manager"))
        self.assertEqual(self.json("POST", "/api/months/2026-12/versions/publish", {"audience": "the world"})[0], 400)
        status, month = self.json("POST", "/api/months/2026-12/undo", {})  # giving a version can be undone
        self.assertEqual(month["phase"]["state"], "draft")

    def test_everyones_schedule_message_at_once(self):
        status, result = self.json("GET", "/api/months/2026-12/messages")
        self.assertEqual(status, 200)
        self.assertEqual([p["name"] for p in result["people"]], ["Ana B.", "Ben C.", "Cleo D."])  # active people
        self.assertTrue(result["people"][0]["message"].startswith("Hello Ana,"))

    def test_api_needs_the_token_and_the_right_host(self):
        self.assertEqual(self.request("GET", "/api/bootstrap", token=None)[0].status, 403)
        self.assertEqual(self.request("GET", "/api/bootstrap", token="wrong")[0].status, 403)
        self.assertEqual(self.request("GET", "/api/bootstrap", host="evil.example:80")[0].status, 403)
        status, body = self.json("GET", "/api/bootstrap")
        self.assertEqual(status, 200)
        self.assertEqual(body["current"], "2026-12")

    def test_no_file_outside_the_web_folder(self):
        for path in ("/../server.py", "/js/../../server.py", "/%2e%2e/server.py", "/..%2fserver.py", "/css/missing.css"):
            self.assertEqual(self.request("GET", path, token=None)[0].status, 404, path)

    def test_bad_requests(self):
        self.assertEqual(self.request("POST", "/api/months/2026-12/assign", raw=b"{not json")[0].status, 400)
        self.assertEqual(self.request("POST", "/api/months/2026-12/assign", raw=b"[1]")[0].status, 400)
        self.assertEqual(self.request("GET", "/api/months/2026-12/unknown")[0].status, 404)
        self.assertEqual(self.request("GET", "/api/months/2031-01")[0].status, 400)
        self.assertEqual(self.request("POST", "/api/months/2026-12/assign", body={"task_id": "lunch"})[0].status, 400)

    def test_assign_conflict_force_undo_redo(self):
        slot = {"task_id": "lunch", "date": "2026-12-10", "position": 0}
        status, body = self.json("POST", "/api/months/2026-12/assign", {**slot, "person_id": "cleo"})
        self.assertEqual(status, 409)
        self.assertEqual(body["findings"][0]["message"], "Not trained for this task")

        status, body = self.json("POST", "/api/months/2026-12/assign", {**slot, "person_id": "cleo", "force": True, "reason": "ok"})
        self.assertEqual(status, 200)
        self.assertEqual(body["cells"]["lunch|2026-12-10"]["slots"][0]["state"], "forced")
        self.assertTrue(body["history"]["undo"])

        status, body = self.json("POST", "/api/months/2026-12/undo")
        self.assertTrue(body["cells"]["lunch|2026-12-10"]["slots"][0]["open"])
        status, body = self.json("POST", "/api/months/2026-12/redo")
        self.assertEqual(body["cells"]["lunch|2026-12-10"]["slots"][0]["person"], "cleo")
        self.json("POST", "/api/months/2026-12/clear", slot)

    def test_highlight_and_headcount(self):
        slot = {"task_id": "lunch", "date": "2026-12-12", "position": 0}
        status, body = self.json("POST", "/api/months/2026-12/highlight", {**slot, "on": True})
        self.assertEqual(status, 200)
        self.assertTrue(body["cells"]["lunch|2026-12-12"]["slots"][0]["wanted"])
        status, body = self.json("POST", "/api/months/2026-12/headcount", {"task_id": "lunch", "date": "2026-12-12", "count": 0})
        self.assertEqual(body["cells"]["lunch|2026-12-12"]["required"], 0)
        status, body = self.json("POST", "/api/months/2026-12/headcount", {"task_id": "lunch", "date": "2026-12-12", "count": None})
        self.assertEqual(body["cells"]["lunch|2026-12-12"]["required"], 1)
        self.assertEqual(self.request("POST", "/api/months/2026-12/headcount", {"task_id": "lunch", "date": "2026-12-12", "count": "x"})[0].status, 400)
        self.json("POST", "/api/months/2026-12/highlight", {**slot, "on": False})

    def test_setup_routes(self):
        status, body = self.json("GET", "/api/setup")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["people"]), 4)
        status, body = self.json("POST", "/api/people/save", {"person": {"first_name": "Eva", "last_name": "Fox"}})
        self.assertIn("Eva F.", [p["name"] for p in body["people"]])
        status, body = self.json("POST", "/api/people/save", {"person": {"first_name": "X", "target_hours": "x"}})
        self.assertEqual((status, body["error"]), (400, "Target hours must be a number."))
        status, body = self.json("POST", "/api/tasks/save", {"task": {"name": "Garden", "area_id": "kitchen",
                                                                      "headcount": {"sun": 1}}, "scope": "2026-12"})
        garden = next(t for t in body["tasks"] if t["name"] == "Garden")
        month = self.json("GET", "/api/months/2026-12")[1]
        self.assertIn(f"{garden['id']}|2026-12-06", month["cells"])  # in December only…
        typical = self.json("GET", "/api/config?scope=typical")[1]
        self.assertNotIn("Garden", [t["name"] for t in typical["tasks"]])  # …not in the typical month
        self.assertTrue(month["history"]["undo"])  # and undoable from the rota
        status, body = self.json("POST", "/api/regulars/set", {"task_id": "lunch", "weekday": "mon", "person_id": "ana"})
        self.assertEqual(len(body["catalog"]["regulars"]), 1)
        status, body = self.json("POST", "/api/people/import/preview", {"text": "Ana Berg\nNew Person"})
        self.assertEqual([r["status"] for r in body["rows"]], ["exists", "new"])

    def test_templates_and_using_a_configuration(self):
        status, body = self.json("POST", "/api/templates/save", {"scope": "typical", "name": "Quiet month"})
        template = body["templates"][-1]
        self.assertEqual((template["name"], template["tasks"]), ("Quiet month", 5))
        status, body = self.json("POST", "/api/config/use", {"scope": "2026-12", "source": f"template:{template['id']}"})
        self.assertEqual((status, len(body["tasks"]), body["removed"]), (200, 5, 0))
        self.assertEqual(self.json("POST", "/api/config/use", {"scope": "typical", "source": "typical"})[0], 400)
        self.assertEqual(self.json("POST", "/api/config/use", {"scope": "2026-12", "source": "template:nope"})[0], 400)
        self.json("POST", "/api/templates/delete", {"id": template["id"]})
        self.assertEqual(self.json("GET", "/api/config?scope=typical")[1]["templates"], [])

    def test_workbook_import_preview_is_anonymous_and_changes_nothing(self):
        from .test_import import legacy_workbook
        encoded = base64.b64encode(legacy_workbook()).decode()
        status, body = self.json("POST", "/api/import/workbook", {"file": encoded, "anonymous": True})
        self.assertEqual(status, 200)
        self.assertEqual((body["summary"]["people"], body["applied"]), (4, False))
        self.assertEqual(len(self.json("GET", "/api/setup")[1]["people"]) >= 4, True)
        status, body = self.json("POST", "/api/import/workbook", {"file": "!!!not base64"})
        self.assertEqual(status, 400)

    def test_create_and_prepare_a_month(self):
        info = self.json("GET", "/api/new-month")[1]
        self.assertEqual((info["choices"][0]["id"], info["default_source"]), ("2027-01", "month:2026-12"))
        status, body = self.json("POST", "/api/months/create", {"year": 2027, "month": 1, "source": info["default_source"]})
        self.assertEqual((status, body["id"]), (200, "2027-01"))
        self.assertIn("2027-01", [m["id"] for m in body["bootstrap"]["months"]])
        self.assertEqual(self.json("POST", "/api/months/create", {"year": 2027, "month": 1})[0], 409)
        status, setup = self.json("POST", "/api/months/2027-01/events/save",
                                  {"event": {"name": "Retreat", "start": "2027-01-08", "end": "2027-01-10"}})
        self.assertEqual(setup["events"][0]["name"], "Retreat")
        self.assertEqual(next(d for d in setup["days"] if d["date"] == "2027-01-08")["day_type"], "event")
        self.assertTrue(setup["history"]["undo"])
        status, body = self.json("POST", "/api/months/2027-01/availability/preview",
                                 {"text": "Name,Morning,Afternoon,Evening\nAna B.,Mon 4,,\n"})
        self.assertEqual(body["rows"][0]["unavailable"], ["2027-01-04/morning"])
        status, body = self.json("POST", "/api/months/2027-01/availability/apply", {"replies": body["rows"]})
        self.assertEqual(body["saved"], 1)
        self.assertTrue(next(p for p in body["people"] if p["id"] == "ana")["replied"])

    def test_prefill_accept_discard_and_undo(self):
        status, body = self.json("POST", "/api/months/2026-12/prefill", {"dates": ["2026-12-20"]})
        proposed = body["prefill"]["proposed"]
        self.assertGreater(proposed, 0)
        self.assertEqual({p["date"] for p in body["proposals"]}, {"2026-12-20"})
        self.assertTrue(any("proposal" in s for s in body["cells"]["lunch|2026-12-20"]["slots"]))
        status, body = self.json("POST", "/api/months/2026-12/proposals/accept", {})
        self.assertEqual((body["accepted"], body["skipped"], body["proposals"]), (proposed, 0, []))
        self.assertFalse(body["cells"]["lunch|2026-12-20"]["slots"][0].get("open"))
        status, body = self.json("POST", "/api/months/2026-12/undo")
        self.assertTrue(body["cells"]["lunch|2026-12-20"]["slots"][0]["open"])
        self.json("POST", "/api/months/2026-12/prefill", {"dates": ["2026-12-20"]})
        status, body = self.json("POST", "/api/months/2026-12/proposals/discard", {})
        self.assertEqual(body["proposals"], [])
        self.assertEqual(self.json("GET", "/api/months/2026-12/shortages")[0], 200)
        status, report = self.json("GET", "/api/months/2026-12/check")
        self.assertEqual(status, 200)
        self.assertEqual({p["name"] for p in report["balance"]} >= {"Ana B.", "Ben C."}, True)
        self.assertTrue(report["open"])

    def test_swap_is_all_or_nothing(self):
        self.json("POST", "/api/months/2026-12/assign", {"task_id": "supper", "date": "2026-12-22", "position": 0, "person_id": "ben"})
        status, body = self.json("POST", "/api/months/2026-12/swap", {
            "key": "lunch|2026-12-22|0", "person_id": "ben", "from": "supper|2026-12-22|0", "replacement": "ana"})
        self.assertEqual(status, 200)
        self.assertEqual(body["cells"]["lunch|2026-12-22"]["slots"][0]["person"], "ben")
        self.assertEqual(body["cells"]["supper|2026-12-22"]["slots"][0]["person"], "ana")
        status, body = self.json("POST", "/api/months/2026-12/swap", {   # Cleo cannot cook: nothing changes
            "key": "lunch|2026-12-23|0", "person_id": "cleo", "from": "supper|2026-12-22|0", "replacement": "ben"})
        self.assertEqual(status, 409)
        month = self.json("GET", "/api/months/2026-12")[1]
        self.assertEqual(month["cells"]["supper|2026-12-22"]["slots"][0]["person"], "ana")

    def test_event_over_two_months(self):
        status, setup = self.json("POST", "/api/months/2026-12/events/save",
                                  {"event": {"name": "New Year retreat", "start": "2026-12-30", "end": "2027-02-02"}})
        self.assertEqual(status, 200)
        event_id = setup["events"][-1]["id"]
        missing = [m["id"] for m in setup["missing_months"]]
        self.assertIn("2027-02", missing)
        if "2027-01" not in [m["id"] for m in self.json("GET", "/api/bootstrap")[1]["months"]]:
            self.json("POST", "/api/months/create", {"year": 2027, "month": 1, "source": "month:2026-12"})
        january = self.json("GET", "/api/months/2027-01/setup")[1]
        self.assertIn(event_id, [e["id"] for e in january["events"]])  # came with the new month
        self.assertEqual(next(d for d in january["days"] if d["date"] == "2027-01-02")["day_type"], "event")
        # Changing it in January changes December's copy; deleting it removes both.
        self.json("POST", "/api/months/2027-01/events/save",
                  {"event": {"id": event_id, "name": "Retreat", "start": "2026-12-31", "end": "2027-01-02"}})
        december = self.json("GET", "/api/months/2026-12/setup")[1]
        self.assertEqual(next(e for e in december["events"] if e["id"] == event_id)["start"], "2026-12-31")
        self.json("POST", "/api/months/2026-12/events/delete", {"id": event_id})
        self.assertNotIn(event_id, [e["id"] for e in self.json("GET", "/api/months/2027-01/setup")[1]["events"]])

    def test_candidates_and_eligible(self):
        status, body = self.json("GET", "/api/months/2026-12/candidates?task=lunch&date=2026-12-11&position=0")
        self.assertEqual(status, 200)
        self.assertEqual([c["id"] for c in body["candidates"] if c["group"] == "best"], ["ana", "ben"])
        status, body = self.json("GET", "/api/months/2026-12/people/cleo/eligible")
        self.assertTrue(all(key.split("|")[0] in ("night", "helper", "breakfast") for key in body["slots"]))


if __name__ == "__main__":
    unittest.main()
