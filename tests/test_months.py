import json
import random
import tempfile
import unittest
from pathlib import Path

from rotaplanner import checks, demo, month_setup, months, view
from rotaplanner import eligibility as rules
from rotaplanner.model import Assignment, Month, Regular, Settings
from rotaplanner.schedule import Schedule
from rotaplanner.store import Store, StoreError

from . import helpers as h


class AssignTest(unittest.TestCase):
    def setUp(self):
        self.month = h.month()
        self.people = h.people()
        self.settings = Settings()

    def assign(self, *args, **kwargs):
        return months.assign(self.month, self.people, self.settings, *args, **kwargs)

    def test_assign_replace_and_clear(self):
        self.assign("lunch", "2026-12-01", 0, person_id="ana")
        self.assign("lunch", "2026-12-01", 0, person_id="ben")
        self.assertEqual([a.person_id for a in self.month.assignments], ["ben"])
        self.assertTrue(months.clear(self.month, "lunch", "2026-12-01", 0))
        self.assertFalse(months.clear(self.month, "lunch", "2026-12-01", 0))

    def test_hard_rule_needs_force_and_keeps_the_reason(self):
        with self.assertRaises(months.RuleViolation) as raised:
            self.assign("lunch", "2026-12-01", 0, person_id="cleo")
        self.assertEqual(raised.exception.findings[0].rule, "not_trained")
        self.assertEqual(self.month.assignments, [])
        made = self.assign("lunch", "2026-12-01", 0, person_id="cleo", force=True, reason=" training ")
        self.assertEqual(made.forced_reason, "training")
        status = checks.assignment_status(h.schedule(self.month), made)
        self.assertEqual(status.state, checks.FORCED)

    def test_assign_anyway_someone_who_does_only_their_skills(self):
        self.people[0].skilled_tasks_only = True   # Ana: cook; the helper needs no skill
        with self.assertRaises(months.RuleViolation) as raised:
            self.assign("helper", "2026-12-05", 0, person_id="ana")
        self.assertEqual([f.rule for f in raised.exception.findings], ["skilled_only"])
        made = self.assign("helper", "2026-12-05", 0, person_id="ana", force=True)
        self.assertEqual(checks.assignment_status(h.schedule(self.month, self.people), made).state, checks.FORCED)
        self.assertEqual(self.assign("lunch", "2026-12-01", 0, person_id="ana").person_id, "ana")

    def test_failed_replacement_keeps_the_previous_person(self):
        self.assign("lunch", "2026-12-01", 0, person_id="ana")
        with self.assertRaises(months.RuleViolation):
            self.assign("lunch", "2026-12-01", 0, person_id="cleo")
        self.assertEqual([a.person_id for a in self.month.assignments], ["ana"])

    def test_guest_and_validation(self):
        made = self.assign("helper", "2026-12-05", 1, guest="  Marta   (volunteer) ")
        self.assertEqual(made.guest, "Marta (volunteer)")
        self.assertEqual(checks.assignment_status(h.schedule(self.month), made).state, checks.GUEST)
        for bad in (dict(task_id="nope"), dict(iso="2027-01-01"), dict(position=5)):
            args = {"task_id": "lunch", "iso": "2026-12-01", "position": 0, **bad}
            with self.assertRaises(ValueError):
                self.assign(args["task_id"], args["iso"], args["position"], person_id="ana")
        with self.assertRaises(ValueError):
            self.assign("lunch", "2026-12-01", 0)  # neither a person nor a guest

    def test_volunteer_whose_name_is_not_known_yet(self):
        made = self.assign("lunch", "2026-12-02", 0, guest_kind="volunteer")
        self.assertEqual((made.guest, made.guest_label), ("", "Volunteer"))
        self.assertEqual(checks.assignment_status(h.schedule(self.month), made).state, checks.GUEST)
        named = self.assign("lunch", "2026-12-02", 0, guest="Sam R.", guest_kind="volunteer")
        self.assertEqual((named.guest_label, len(self.month.assignments)), ("Sam R.", 1))
        with self.assertRaises(ValueError):
            self.assign("lunch", "2026-12-03", 0, guest_kind="friend")

    def test_a_short_note_on_a_place(self):
        self.assign("lunch", "2026-12-01", 0, person_id="ana")
        self.assertTrue(months.set_note(self.month, "lunch", "2026-12-01", 0, "  arrives   at 10 "))
        self.assertEqual(self.month.assignments[0].note, "arrives at 10")
        self.assertFalse(months.set_note(self.month, "lunch", "2026-12-01", 0, "arrives at 10"))  # nothing new
        self.assertEqual(len(months.set_note(self.month, "lunch", "2026-12-01", 0, "x" * 200) and self.month.assignments[0].note), 60)
        self.assign("lunch", "2026-12-01", 0, person_id="ben")
        self.assertEqual(self.month.assignments[0].note, "")  # the note went with the person
        with self.assertRaises(ValueError):
            months.set_note(self.month, "lunch", "2026-12-02", 0, "nobody there")

    def test_highlight_open_slot(self):
        self.assertTrue(months.set_wanted(self.month, self.people, self.settings, "lunch", "2026-12-01", 0, True))
        self.assertFalse(months.set_wanted(self.month, self.people, self.settings, "lunch", "2026-12-01", 0, True))
        result = view.month_view(self.month, h.catalog(), self.people, self.settings)
        self.assertTrue(result["cells"]["lunch|2026-12-01"]["slots"][0]["wanted"])
        self.assertEqual(result["summary"]["wanted"], 1)
        self.assign("lunch", "2026-12-01", 0, person_id="ana")  # filled: no longer counted
        self.assertEqual(view.month_view(self.month, h.catalog(), self.people, self.settings)["summary"]["wanted"], 0)
        with self.assertRaises(ValueError):
            months.set_wanted(self.month, self.people, self.settings, "lunch", "2027-01-01", 0, True)

    def test_headcount_by_hand_off_and_back(self):
        self.assign("helper", "2026-12-05", 1, person_id="ana")  # Saturday needs 2
        self.assertTrue(months.set_headcount(self.month, "helper", "2026-12-05", 1))
        self.assertEqual([a.position for a in self.month.assignments], [0])  # moved down, not hidden
        self.assertTrue(months.set_headcount(self.month, "lunch", "2026-12-02", 0))
        cell = view.month_view(self.month, h.catalog(), self.people, self.settings)["cells"]["lunch|2026-12-02"]
        self.assertEqual((cell["required"], cell["usual"], cell["adjusted"], cell["slots"]), (0, 1, True, []))
        self.assertTrue(months.set_headcount(self.month, "lunch", "2026-12-02", None))
        self.assertFalse(months.set_headcount(self.month, "lunch", "2026-12-02", None))
        self.assertNotIn("lunch", self.month.demand_overrides)
        with self.assertRaises(ValueError):
            months.set_headcount(self.month, "lunch", "2026-12-02", -1)

    def test_other_times_for_one_day(self):
        self.assertTrue(months.set_time(self.month, "lunch", "2026-12-25", {"start": "12.00", "end": "15:30", "label": "Christmas"}))
        change = self.month.time_changes["lunch|2026-12-25"]
        self.assertEqual((change.start, change.end, change.hours, change.label), ("12:00", "15:30", 3.5, "Christmas"))
        cell = view.month_view(self.month, h.catalog(), self.people, self.settings)["cells"]["lunch|2026-12-25"]
        self.assertEqual(cell["time"], "12:00–15:30 · Christmas")
        self.assertTrue(months.set_time(self.month, "lunch", "2026-12-25", None))
        self.assertFalse(months.set_time(self.month, "lunch", "2026-12-25", None))
        with self.assertRaises(ValueError):
            months.set_time(self.month, "lunch", "2026-12-25", {"start": "12:00"})

    def test_extra_position_is_flagged_not_required(self):
        made = self.assign("lunch", "2026-12-01", 1, person_id="ana")
        status = checks.assignment_status(h.schedule(self.month), made)
        self.assertIn("not_required", [f.rule for f in status.findings])
        self.assertIn("Beyond the 1 needed that day", [f.message for f in status.findings])

    def view(self):
        return view.month_view(self.month, h.catalog(), self.people, self.settings)

    def test_the_rota_says_how_many_people_each_task_needs(self):
        tasks = {t["id"]: t for area in self.view()["areas"] for t in area["tasks"]}
        self.assertEqual(tasks["lunch"]["need"], {"text": "1", "title": "Needs 1 person a day."})
        self.assertEqual(tasks["helper"]["need"]["text"], "2")
        months.set_headcount(self.month, "lunch", "2026-12-03", 2)
        cells = self.view()["cells"]
        self.assertEqual(cells["lunch|2026-12-03"]["need"],
                         {"count": 2, "title": "2 people needed this day (usually 1): set by hand."})
        self.assertIsNone(cells["lunch|2026-12-04"]["need"])
        months.set_headcount(self.month, "lunch", "2026-12-04", 0)   # no place left: "Not needed" says it
        self.assertIsNone(self.view()["cells"]["lunch|2026-12-04"]["need"])

    def test_a_place_beyond_what_the_day_needs(self):
        # One fewer needed with both places taken: nobody is hidden, the second place is beyond the need.
        months.set_headcount(self.month, "lunch", "2026-12-01", 2)
        self.assign("lunch", "2026-12-01", 0, person_id="ana")
        self.assign("lunch", "2026-12-01", 1, person_id="ben")
        self.assertNotIn("beyond", self.view()["cells"]["lunch|2026-12-01"]["slots"][1])
        months.set_headcount(self.month, "lunch", "2026-12-01", 1)
        cell = self.view()["cells"]["lunch|2026-12-01"]
        self.assertEqual([slot.get("beyond", False) for slot in cell["slots"]], [False, True])
        self.assertIn("Beyond the 1 needed that day", [f["message"] for f in cell["slots"][1]["findings"]])
        self.assertIsNone(cell["need"])   # back to its usual number
        months.set_headcount(self.month, "lunch", "2026-12-01", 0)
        cell = self.view()["cells"]["lunch|2026-12-01"]
        self.assertEqual([slot.get("beyond", False) for slot in cell["slots"]], [True, True])
        self.assertIn("Not needed that day", [f["message"] for f in cell["slots"][0]["findings"]])
        self.assertEqual(cell["need"], {"count": 0, "title": "Nobody needed this day (usually 1): set by hand."})

    def test_a_place_beyond_the_need_after_the_task_changed(self):
        # A Saturday needs two helpers; the month's task then asks for one: the second place is beyond.
        self.assign("helper", "2026-12-05", 0, person_id="ana")
        self.assign("helper", "2026-12-05", 1, person_id="cleo")
        next(t for t in self.month.tasks if t.id == "helper").headcount["sat"] = 1
        cell = self.view()["cells"]["helper|2026-12-05"]
        self.assertEqual([slot.get("beyond", False) for slot in cell["slots"]], [False, True])
        self.assertEqual(cell["slots"][1]["state"], checks.WARN)
        self.assertIsNone(cell["need"])   # the usual number has changed with it


class RegularTest(unittest.TestCase):
    def test_regulars_fill_their_weekday_and_skip_absences(self):
        cat = h.catalog()
        cat.regulars = [Regular("r1", "ana", "lunch", "tue")]
        month = h.month(cat)
        h.away(month, "ana", "2026-12-08/morning")
        placed = months.apply_regulars(month, cat, h.people(), Settings())
        tuesdays = {a.date for a in month.assignments}
        self.assertEqual(placed, 4)
        self.assertEqual(tuesdays, {"2026-12-01", "2026-12-15", "2026-12-22", "2026-12-29"})
        cells = view.month_view(month, cat, h.people(), Settings())["cells"]
        self.assertEqual(cells["lunch|2026-12-08"]["slots"][0]["cover_for"], "Ana B.")

    def test_regulars_stop_at_the_weekly_limit(self):
        cat = h.catalog()
        cat.regulars = [Regular(f"r-{day}", "ana", "lunch", day) for day in ("mon", "wed", "fri")]
        ppl = h.people()
        ppl[0].max_shifts_per_week = 2
        month = h.month(cat)
        months.apply_regulars(month, cat, ppl, Settings())
        week = {a.date for a in month.assignments if "2026-12-07" <= a.date <= "2026-12-13"}
        self.assertEqual(week, {"2026-12-07", "2026-12-09"})  # Friday 11 stays open
        cells = view.month_view(month, cat, ppl, Settings())["cells"]
        self.assertEqual(cells["lunch|2026-12-11"]["slots"][0]["cover_for"], "Ana B.")

    def test_regulars_see_the_week_in_the_month_before(self):
        cat = h.catalog()
        cat.regulars = [Regular("r-tue", "ana", "lunch", "tue")]
        ppl = h.people()
        ppl[0].max_shifts_per_week = 1
        november = months.create_month(cat, 2026, 11)
        november.assignments.append(Assignment("supper", "2026-11-30", 0, person_id="ana"))  # a Monday
        month = h.month(cat)
        months.apply_regulars(month, cat, ppl, Settings(), around=[november])
        self.assertEqual(sorted(a.date for a in month.assignments),
                         ["2026-12-08", "2026-12-15", "2026-12-22", "2026-12-29"])  # Tuesday 1 left open


class MonthInPreparationTest(unittest.TestCase):
    def test_next_month_first_then_this_month(self):
        from datetime import date
        from rotaplanner.app import month_in_preparation
        ids = ["2026-11", "2026-12"]
        self.assertEqual(month_in_preparation(ids, date(2026, 11, 10)), "2026-12")
        self.assertEqual(month_in_preparation(ids, date(2026, 12, 10)), "2026-12")
        self.assertEqual(month_in_preparation(["2026-12", "2027-01"], date(2026, 12, 31)), "2027-01")
        self.assertEqual(month_in_preparation(ids, date(2027, 3, 1)), "2026-12")
        self.assertIsNone(month_in_preparation([], date(2027, 3, 1)))


class ConsistencyTest(unittest.TestCase):
    """What the picker calls a clean choice must never show up as a conflict."""

    def test_clean_choices_never_become_conflicts(self):
        for seed in range(5):
            rng = random.Random(seed)
            cat = demo.build_catalog()
            ppl = demo.build_people(cat, rng)
            settings = Settings()
            month = months.create_month(cat, 2026, 12)
            month.availability = demo.build_availability(ppl, 2026, 12, rng)
            schedule = Schedule(month, ppl, settings)
            slots = schedule.open_slots()
            rng.shuffle(slots)
            for task_id, iso, position in slots[:250]:
                best = rules.candidates(schedule, schedule.tasks[task_id], iso)[0]
                if best.group == rules.BLOCKED:
                    continue
                assignment = Assignment(task_id, iso, position, person_id=best.person.id)
                month.assignments.append(assignment)
                schedule.add(assignment)
            states = {s.state for s in checks.all_statuses(Schedule(month, ppl, settings)).values()}
            self.assertNotIn(checks.CONFLICT, states, f"seed {seed}")


class ViewTest(unittest.TestCase):
    def test_labels_are_unique(self):
        ppl = h.people()
        ppl[1].first_name, ppl[1].last_name = "Ann", "Bell"  # clashes with Ana Berg: "AB"
        labels = view.person_labels(ppl)
        initials = [label["initials"] for label in labels.values()]
        self.assertEqual(len(initials), len(set(initials)))

    def test_month_view_is_plain_json(self):
        month = h.month()
        months.assign(month, h.people(), Settings(), "lunch", "2026-12-01", 0, person_id="ana")
        result = view.month_view(month, h.catalog(), h.people(), Settings())
        json.dumps(result)
        self.assertEqual(result["cells"]["lunch|2026-12-01"]["slots"][0]["state"], checks.OK)
        self.assertEqual(result["summary"]["filled"], 1)
        self.assertEqual(len(result["days"]), 31)
        self.assertEqual({t["pieces"] for a in result["areas"] for t in a["tasks"]}, {None})

    def test_month_view_sends_the_times_of_a_task_cut_into_several(self):
        month = h.month()
        event = month_setup.save_event(month, {"name": "Course", "start": "2026-12-11", "end": "2026-12-12"})
        month_setup.save_event_task(month, h.catalog(), {
            "event_id": event.id, "name": "Registration", "area_id": "kitchen",
            "times": [{"start": "14:00", "end": "17:00", "people": 2}, {"start": "17:00", "end": "19:00", "people": 1}]})
        result = view.month_view(month, h.catalog(), h.people(), Settings())
        cut = [t for a in result["areas"] for t in a["tasks"] if t["name"] == "Registration"]
        self.assertEqual([t["time"] for t in cut], ["14:00–17:00", "17:00–19:00"])
        self.assertIsNotNone(cut[0]["pieces"])
        self.assertEqual(cut[0]["pieces"], cut[1]["pieces"])


class StoreTest(unittest.TestCase):
    def test_round_trip_backup_and_guards(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            month = h.month()
            month.assignments.append(Assignment("lunch", "2026-12-01", 0, person_id="ana", forced_reason=""))
            store.save_month(month)
            store.save_month(month)
            loaded = store.load_month("2026-12")
            self.assertEqual(loaded.to_dict(), month.to_dict())
            self.assertIsInstance(loaded.assignments[0], Assignment)
            self.assertEqual(len(list((Path(tmp) / "backups").glob("2026-12.*.json"))), 1)
            for bad in ("../people", "2026-1", "2026-12/../../x"):
                with self.assertRaises(StoreError):
                    store.load_month(bad)
            path = Path(tmp) / "months" / "2026-12.json"
            document = json.loads(path.read_text())
            document["schema_version"] = 999
            path.write_text(json.dumps(document))
            with self.assertRaises(StoreError):
                store.load_month("2026-12")

    def test_files_in_the_first_format_are_upgraded(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            month = h.month()
            document = month.to_dict()
            for task in document["tasks"]:
                task["period"] = task.pop("periods")[0]  # how format 1 stored it
            document["schema_version"] = 1
            (Path(tmp) / "months").mkdir()
            (Path(tmp) / "months" / "2026-12.json").write_text(json.dumps(document))
            loaded = store.load_month("2026-12")
            self.assertEqual([t.periods for t in loaded.tasks][:3], [["morning"], ["morning"], ["afternoon"]])

    def test_demo_data_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            demo.build_demo(store)
            ppl = store.load_people()
            self.assertEqual(len(ppl), demo.POPULATION)
            self.assertEqual(sum(p.active for p in ppl), demo.ACTIVE)
            for person in ppl:
                self.assertIn(person.first_name, demo.FIRST_NAMES)
                self.assertIn(person.last_name, demo.LAST_NAMES)
            month = store.load_month("2026-12")
            self.assertIsInstance(month, Month)
            states = [s.state for s in checks.all_statuses(Schedule(month, ppl, store.load_settings())).values()]
            self.assertEqual(states.count(checks.CONFLICT), 1)  # the one shown on purpose
            self.assertEqual(states.count(checks.FORCED), 1)


if __name__ == "__main__":
    unittest.main()
