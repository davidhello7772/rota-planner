import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from rotaplanner.app import App
from rotaplanner.model import Assignment
from rotaplanner.months import create_month
from rotaplanner.server import TOKEN_HEADER, create_server, host_allowed, is_loopback
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
        cls.server = create_server(App(store), TOKEN, 0)
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

    def test_other_site_cannot_post(self):
        def post(origin):
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            conn.request("POST", "/api/settings/save", body=b'{"settings": {}}',
                         headers={"Host": f"127.0.0.1:{self.port}", TOKEN_HEADER: TOKEN, "Origin": origin,
                                  "Content-Type": "application/json"})
            status = conn.getresponse().status
            conn.close()
            return status
        self.assertEqual(post("http://evil.example"), 403)
        self.assertEqual(post(f"http://127.0.0.1:{self.port}"), 200)

    def test_listens_on_this_computer_only(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        for gone in ("/api/login", "/api/logout", "/api/access", "/api/access/network", "/api/access/password"):
            self.assertEqual(self.request("POST", gone, {})[0].status, 404, gone)

    def test_host_names(self):
        self.assertTrue(host_allowed("127.0.0.1:8765", 8765))
        self.assertTrue(host_allowed("LOCALHOST:8765", 8765))
        self.assertFalse(host_allowed("127.0.0.1:8766", 8765))
        self.assertFalse(host_allowed("127.0.0.1", 8765))
        self.assertFalse(host_allowed("192.168.1.5:8765", 8765))
        self.assertFalse(host_allowed("office-pc.local:8765", 8765))
        self.assertFalse(host_allowed("evil.example:8765", 8765))  # DNS rebinding
        self.assertTrue(is_loopback("127.0.0.1") and is_loopback("::1"))
        self.assertFalse(is_loopback("192.168.1.57") or is_loopback("not an address"))

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
        step = body["history"]["undo"]
        self.assertEqual(step["label"], "Cleo D. → Lunch cook, Thu 10 Dec (assigned anyway)")
        self.assertEqual(set(body["history"]), {"undo", "redo", "auto"})

        status, body = self.json("POST", "/api/months/2026-12/undo", {"expect": step["id"]})
        self.assertTrue(body["cells"]["lunch|2026-12-10"]["slots"][0]["open"])
        self.assertEqual(body["did"], {"text": f"Undone: {step['label']}", "back": step["id"], "back_label": "Redo"})
        self.assertEqual(body["history"]["redo"], step)
        status, body = self.json("POST", "/api/months/2026-12/redo")
        self.assertEqual(body["cells"]["lunch|2026-12-10"]["slots"][0]["person"], "cleo")
        status, listed = self.json("GET", "/api/months/2026-12/history")
        self.assertEqual((status, listed["steps"][0]["id"], listed["steps"][0]["count"]), (200, step["id"], 1))
        self.assertEqual(set(listed["steps"][0]), {"id", "label", "kind", "when", "count"})
        status, body = self.json("POST", "/api/months/2026-12/history/go", {"to": step["id"]})
        self.assertTrue(body["cells"]["lunch|2026-12-10"]["slots"][0]["open"])
        status, body = self.json("POST", "/api/months/2026-12/history/go", {"to": step["id"]})
        self.assertEqual(body["cells"]["lunch|2026-12-10"]["slots"][0]["person"], "cleo")
        status, body = self.json("POST", "/api/months/2026-12/undo", {"expect": -1})
        self.assertEqual(status, 409)
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
        status, body = self.json("POST", "/api/people/save", {"person": {"first_name": "X", "target_hours_per_week": "x"}})
        self.assertEqual((status, body["error"]), (400, "Target hours a week must be a number."))
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

    def test_figures_for_the_screens_come_from_the_program(self):
        setup = self.json("GET", "/api/setup")[1]
        self.assertIsInstance(setup["regular_load"], list)
        totals = self.json("GET", "/api/months/2026-12/setup")[1]["totals"]
        self.assertIn(totals["balance"], ("ok", "warn", "bad"))
        self.assertEqual(totals["hours_short"] > 0, totals["balance"] == "bad")
        report = self.json("GET", "/api/months/2026-12/check")[1]
        self.assertEqual(sorted(report["averages"]), ["evenings", "hours", "target", "weekends"])
        self.assertTrue(all({"ratio", "load", "uneven"} <= set(p) for p in report["balance"]))
        # Hours a week against the target, in words and for the bar: on the rota and in the list for a shift.
        words = {"week_text", "month_text", "month_detail", "week_hours", "ratio", "bar", "hours", "target"}
        people = self.json("GET", "/api/months/2026-12")[1]["people"]
        self.assertTrue(people and all(words | {"presence", "limits"} <= set(p) for p in people))
        self.assertTrue(all(p["week_text"].endswith(" h a week") and p["bar"] in ("", "over", "under") for p in people))
        candidates = self.json("GET", "/api/months/2026-12/candidates?task=lunch&date=2026-12-11&position=0")[1]["candidates"]
        self.assertTrue(candidates and all(words <= set(c) for c in candidates))
        # The hours of each week: every person's weeks aligned with the month's, every day in its week;
        # and each candidate's hours that week.
        month = self.json("GET", "/api/months/2026-12")[1]
        weeks = month["calendar_weeks"]
        self.assertTrue(weeks and all({"start", "label", "short", "title", "cut"} <= set(w) for w in weeks))
        self.assertTrue(all(len(p["by_week"]) == len(weeks) for p in month["people"]))
        self.assertTrue(all({"hours", "level", "far", "that_week_text"} <= set(w) for p in month["people"] for w in p["by_week"]))
        self.assertTrue(all(weeks[d["week"]]["start"] <= d["date"] for d in month["days"]))
        self.assertEqual(sorted({d["week"] for d in month["days"]}), list(range(len(weeks))))
        self.assertTrue(all({"that_week", "that_week_text", "free_week"} <= set(c) for c in candidates))
        self.assertTrue(all(c["free_week"] == (c["that_week_text"] == "Free that week") for c in candidates))

    def test_the_example_message_is_written_by_the_program_and_saves_nothing(self):
        before = self.json("GET", "/api/setup")[1]["settings"]
        status, body = self.json("POST", "/api/settings/message/preview",
                                 {"message_greeting": "  Dear   {name},", "message_closing": "See you   soon"})
        lines = body["text"].splitlines()
        self.assertEqual((status, lines[0], lines[-1]), (200, "Dear Ana,", "See you soon"))
        self.assertTrue(lines[2].startswith("Here are your shifts for "))
        self.assertEqual(len([line for line in lines if line.startswith("• ")]), 2)
        self.assertNotIn("Thank you", self.json("POST", "/api/settings/message/preview", {"message_closing": ""})[1]["text"])
        subject = self.json("POST", "/api/settings/message/preview", {"message_subject": "Rota {month} – {month}"})[1]["subject"]
        self.assertRegex(subject, r"^Rota \w+ \d{4} – \w+ \d{4}$")  # every {month}, as when sending
        self.assertTrue(self.json("POST", "/api/settings/message/preview", {"message_subject": "  "})[1]["subject"].startswith("Your shifts for "))
        self.assertEqual(self.json("GET", "/api/setup")[1]["settings"], before)

    def test_the_task_form_asks_the_program_about_its_times(self):
        ask = lambda start, end: self.json("POST", "/api/tasks/times", {"start": start, "end": end})[1]  # noqa: E731
        self.assertEqual(ask("14:00", "17:15"), {"hours": 3.25, "periods": ["afternoon"]})
        self.assertEqual(ask("22:00", "08:00"), {"hours": 10.0, "periods": ["evening"]})
        self.assertEqual(ask("09:00", ""), {"hours": None, "periods": None})  # nothing to say yet
        self.assertEqual(ask("", ""), {"hours": None, "periods": None})
        self.assertEqual(self.json("POST", "/api/tasks/times", {})[0], 200)

    def test_a_skill_is_renamed_and_deleted(self):
        skills = self.json("POST", "/api/skills/save", {"skill": {"name": "First aid"}, "people": ["ana"]})[1]
        skill = next(s for s in skills["catalog"]["skills"] if s["name"] == "First aid")
        renamed = self.json("POST", "/api/skills/save", {"skill": {"id": skill["id"], "name": "First aider"}})[1]
        self.assertIn("First aider", [s["name"] for s in renamed["catalog"]["skills"]])
        self.assertIn(skill["id"], next(p for p in renamed["people"] if p["id"] == "ana")["skills"])
        status, body = self.json("POST", "/api/skills/delete", {"id": skill["id"]})
        self.assertEqual(status, 200)
        self.assertNotIn(skill["id"], [s["id"] for s in body["catalog"]["skills"]])
        self.assertNotIn(skill["id"], next(p for p in body["people"] if p["id"] == "ana")["skills"])  # off everyone who had it
        status, body = self.json("POST", "/api/skills/delete", {"id": "cook"})  # a task still needs it
        self.assertEqual(status, 400)
        self.assertIn("needed for", body["error"])
        self.assertIn("cook", [s["id"] for s in self.json("GET", "/api/setup")[1]["catalog"]["skills"]])

    def test_areas_and_the_tasks_of_an_event_type_are_put_in_order(self):
        order = lambda scope: [a["id"] for a in self.json("GET", f"/api/config?scope={scope}")[1]["areas"]]  # noqa: E731
        self.assertEqual(order("typical"), ["kitchen", "security"])
        status, body = self.json("POST", "/api/areas/order", {"ids": ["security", "kitchen"], "scope": "typical"})
        self.assertEqual((status, [a["id"] for a in body["areas"]]), (200, ["security", "kitchen"]))
        self.assertEqual(order("2026-12"), ["kitchen", "security"])  # a month keeps its own order
        self.json("POST", "/api/areas/order", {"ids": ["kitchen", "security"], "scope": "typical"})
        self.assertEqual(order("typical"), ["kitchen", "security"])

        made = self.json("POST", "/api/event-types/save", {"type": {"name": "Open day"}})[1]
        type_id = next(t["id"] for t in made["event_types"] if t["name"] == "Open day")
        for name in ("Welcome desk", "Tea table", "Car park"):
            made = self.json("POST", "/api/event-types/tasks/save",
                             {"task": {"type_id": type_id, "name": name, "area_id": "kitchen", "people": 1}})[1]
        tasks = next(t for t in made["event_types"] if t["id"] == type_id)["tasks"]
        ids = [tasks[2]["id"], tasks[0]["id"], tasks[1]["id"]]
        status, body = self.json("POST", "/api/event-types/tasks/order", {"type_id": type_id, "ids": ids})
        self.assertEqual(status, 200)
        self.assertEqual([t["name"] for t in next(t for t in body["event_types"] if t["id"] == type_id)["tasks"]],
                         ["Car park", "Welcome desk", "Tea table"])
        self.json("POST", "/api/event-types/delete", {"id": type_id})

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
        self.assertIsNone(body["suggested"])   # all accepted: nothing more to say
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

    def test_prefill_says_what_stays_open_and_can_go_further(self):
        week = [f"2026-12-{d:02d}" for d in range(7, 14)]
        status, body = self.json("POST", "/api/months/2026-12/prefill", {"dates": week})
        self.assertEqual(status, 200)
        first = {p["key"] for p in body["proposals"]}
        suggested = body["suggested"]
        self.assertEqual((suggested["proposed"], suggested["left"]), (len(first), body["prefill"]["left_open"]))
        self.assertGreater(suggested["beyond"], 0)
        self.assertTrue(suggested["left_text"].startswith(f"{suggested['left']} shifts stay open: "))
        self.assertEqual(sum(r["count"] for r in suggested["reasons"]), suggested["left"])
        self.assertEqual(self.json("GET", "/api/months/2026-12")[1]["suggested"], suggested)

        status, body = self.json("POST", "/api/months/2026-12/prefill", {"beyond": True})
        self.assertEqual(status, 200)
        now = {p["key"] for p in body["proposals"]}
        self.assertTrue(first < now)                                   # the first ones stay, new ones are added
        self.assertEqual(body["prefill"]["proposed"], len(now) - len(first))
        self.assertEqual({p["date"] for p in body["proposals"]} - set(week), set())   # the same days
        self.assertEqual(body["suggested"]["beyond"], 0)

        status, body = self.json("POST", "/api/months/2026-12/proposals/discard", {})
        self.assertEqual((body["proposals"], body["suggested"]), ([], None))
        status, body = self.json("POST", "/api/months/2026-12/prefill", {"beyond": True})
        self.assertEqual(status, 409)

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



class TwoMonthsServed:
    """November and December 2026 in a fresh data folder, served for one test."""

    def serve(self, people, november, december):
        self.tmp = tempfile.TemporaryDirectory()
        store = Store(Path(self.tmp.name))
        store.save_catalog(h.catalog())
        store.save_people(people)
        store.save_month(november)
        store.save_month(december)
        self.server = create_server(App(store), TOKEN, 0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def json(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Host": f"127.0.0.1:{self.port}", TOKEN_HEADER: TOKEN, "Content-Type": "application/json"}
        conn.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=headers)
        response = conn.getresponse()
        data = json.loads(response.read())
        conn.close()
        return response.status, data


class MonthsNextDoorTest(TwoMonthsServed, unittest.TestCase):
    """A shift late on the last day of November shows as a conflict in December."""

    def setUp(self):
        november = create_month(h.catalog(), 2026, 11)
        november.assignments.append(Assignment("night", "2026-11-30", 0, person_id="ben"))  # 22:00–08:00
        december = h.month()
        december.assignments.append(Assignment("breakfast", "2026-12-01", 0, person_id="ben"))  # 07:00–09:00
        self.serve(h.people(), november, december)

    def breakfast(self):
        status, view = self.json("GET", "/api/months/2026-12")
        self.assertEqual(status, 200)
        return view["cells"]["breakfast|2026-12-01"]["slots"][0]

    def test_conflict_across_the_end_of_the_month(self):
        slot = self.breakfast()
        self.assertEqual(slot["state"], "conflict")
        self.assertTrue(any(f["message"].startswith("Overlaps Night guard") for f in slot["findings"]))
        status, november = self.json("POST", "/api/months/2026-11/clear", {"task_id": "night", "date": "2026-11-30", "position": 0})
        self.assertEqual(status, 200)
        self.assertNotIn("person", november["cells"]["night|2026-11-30"]["slots"][0])
        self.assertNotEqual(self.breakfast()["state"], "conflict")
        # Placing the night again is now refused: it would overlap the breakfast next door.
        status, refused = self.json("POST", "/api/months/2026-11/assign",
                                    {"task_id": "night", "date": "2026-11-30", "position": 0, "person_id": "ben"})
        self.assertEqual(status, 409)
        self.assertTrue(any(f["message"].startswith("Overlaps Breakfast") for f in refused["findings"]))


class WeeklyLimitNextDoorTest(TwoMonthsServed, unittest.TestCase):
    """Ana does at most one shift a week; the week of 1 December starts on Monday 30 November."""

    def setUp(self):
        people = h.people()
        people[0].max_shifts_per_week = 1
        self.serve(people, create_month(h.catalog(), 2026, 11), h.month())

    def assign_on_the_30th(self):
        status, _ = self.json("POST", "/api/months/2026-11/assign",
                              {"task_id": "lunch", "date": "2026-11-30", "position": 0, "person_id": "ana"})
        self.assertEqual(status, 200)

    def test_accepting_skips_a_suggestion_over_the_weekly_limit(self):
        status, body = self.json("POST", "/api/months/2026-12/prefill", {"dates": ["2026-12-02"], "area_id": "kitchen"})
        hers = [p["key"] for p in body["proposals"] if p["person_id"] == "ana"]
        self.assertEqual(len(hers), 1)  # one shift that week, as her card says
        self.assign_on_the_30th()  # meanwhile, in November
        status, body = self.json("POST", "/api/months/2026-12/proposals/accept", {})
        self.assertEqual(status, 200)
        self.assertEqual(body["skipped"], 1)
        task_id, iso, position = hers[0].split("|")
        self.assertNotIn("person", body["cells"][f"{task_id}|{iso}"]["slots"][int(position)])

    def test_an_exchange_over_the_weekly_limit_is_refused(self):
        self.assign_on_the_30th()
        status, _ = self.json("POST", "/api/months/2026-12/assign",
                              {"task_id": "supper", "date": "2026-12-02", "position": 0, "person_id": "ben"})
        self.assertEqual(status, 200)
        status, refused = self.json("POST", "/api/months/2026-12/swap", {
            "key": "lunch|2026-12-02|0", "person_id": "ben", "from": "supper|2026-12-02|0", "replacement": "ana"})
        self.assertEqual(status, 409)
        self.assertIn("Already has 1 shift that week (limit 1)", refused["error"])
        self.assertEqual(self.json("GET", "/api/months/2026-12")[1]["cells"]["supper|2026-12-02"]["slots"][0]["person"],
                         "ben")


if __name__ == "__main__":
    unittest.main()


class OtherComputerTest(unittest.TestCase):
    """A request from another computer is refused, even with the right token and host
    (as if the firewall were open and the server reachable)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        store = Store(Path(self.tmp.name))
        store.save_catalog(h.catalog())
        store.save_people(h.people())
        store.save_month(h.month())
        self.server = create_server(App(store), TOKEN, 0)
        base = self.server.RequestHandlerClass

        class FromElsewhere(base):
            @property
            def client(self):
                return "192.168.1.57"

        self.server.RequestHandlerClass = FromElsewhere
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def test_refused_before_anything_else(self):
        for method, path in (("GET", "/"), ("GET", "/api/bootstrap"), ("GET", f"/print/2026-12/month?t={TOKEN}"),
                             ("POST", "/api/settings/save")):
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            conn.request(method, path, body=b"{}" if method == "POST" else None,
                         headers={"Host": f"127.0.0.1:{self.port}", TOKEN_HEADER: TOKEN, "Content-Type": "application/json"})
            response = conn.getresponse()
            self.assertEqual((response.status, response.read()), (403, b"Not allowed."), path)
            conn.close()


class OldAccessFileTest(unittest.TestCase):
    def test_left_over_password_file_is_removed_at_start(self):
        from rotaplanner.__main__ import forget_old_access
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / "access.json"
            old.write_text('{"mode": "network", "allowed": ["192.168.1.57"], "password": {"hash": "x"}}')
            forget_old_access(Path(tmp))
            self.assertFalse(old.exists())
            forget_old_access(Path(tmp))  # nothing there: nothing happens
