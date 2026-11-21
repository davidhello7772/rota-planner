"""Defects found in review, each kept from coming back."""

import io
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path

from rotaplanner import demand, editing, month_setup, transfer
from rotaplanner.app import App
from rotaplanner.model import Area, Assignment, PersonAvailability, Settings, to_minutes
from rotaplanner.schedule import Schedule
from rotaplanner.solver import Proposal
from rotaplanner.store import Store
from rotaplanner.view import still_valid
from rotaplanner.xlsx_read import Sheet, Cell

from . import helpers as h


class TwoMonthEventsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name))
        catalog = h.catalog()
        self.store.save_catalog(catalog)
        self.store.save_people(h.people())
        december = h.month(catalog)
        december.areas.append(Area("tent", "Tent"))  # only December has it
        self.store.save_month(december)
        self.store.save_month(month_setup.new_month(catalog, h.month(catalog), 2027, 1, h.people(), Settings()))
        self.app = App(self.store, today=date(2026, 12, 1))
        result = self.app.save_event("2026-12", {"event": {"name": "New Year", "start": "2026-12-30", "end": "2027-01-02"}})
        self.event_id = result["events"][0]["id"]
        for name, area in (("Desk", "tent"), ("Hot chocolate", "kitchen")):
            self.app.save_event_task("2026-12", {"task": {"event_id": self.event_id, "name": name, "area_id": area}})

    def tearDown(self):
        self.tmp.cleanup()

    def test_an_area_only_one_month_has_comes_with_the_event_tasks(self):
        january = self.app.month("2027-01")
        self.assertIn("tent", [a.id for a in january.areas])
        self.assertEqual(sorted(t.name for t in month_setup.event_tasks(january, self.event_id)), ["Desk", "Hot chocolate"])
        # Saving the event from January no longer removes December's tasks.
        self.app.save_event("2027-01", {"event": {"id": self.event_id, "name": "New Year!", "start": "2026-12-30", "end": "2027-01-02"}})
        self.assertEqual(len(month_setup.event_tasks(self.app.month("2026-12"), self.event_id)), 2)

    def test_undo_reaches_the_other_month_and_is_not_an_undo_step_there(self):
        self.app.save_event("2027-01", {"event": {"id": self.event_id, "name": "Renamed", "start": "2026-12-30", "end": "2027-01-02"}})
        self.assertEqual(self.app.month("2026-12").events[0].name, "Renamed")
        self.app.undo("2027-01", {})
        self.assertEqual(self.app.month("2026-12").events[0].name, "New Year")
        december_steps = len(self.app.history.stacks("2026-12")[0])
        self.app.save_event("2027-01", {"event": {"id": self.event_id, "name": "Again", "start": "2026-12-30", "end": "2027-01-02"}})
        self.assertEqual(len(self.app.history.stacks("2026-12")[0]), december_steps)

    # -- undo over two months ----------------------------------------------------

    def january_slot(self, day, task="lunch", person="ana"):
        return {"task_id": task, "date": f"2027-01-{day:02d}", "position": 0, "person_id": person, "force": True}

    def names(self):
        return [self.app.month(m).events[0].name if self.app.month(m).events else None for m in ("2026-12", "2027-01")]

    def placed(self, month_id, task_id):
        return [(a.date, a.person_id) for a in self.app.month(month_id).assignments if a.task_id == task_id]

    def hot_chocolate(self):
        return next(t.id for t in month_setup.event_tasks(self.app.month("2026-12"), self.event_id) if t.name == "Hot chocolate")

    def rename_from_december(self, name):
        self.app.save_event("2026-12", {"event": {"id": self.event_id, "name": name, "start": "2026-12-30", "end": "2027-01-02"}})

    def test_undoing_an_older_step_keeps_a_change_made_to_the_event_from_the_other_month(self):
        self.app.assign("2027-01", self.january_slot(5))  # a step in January, before the event changes
        self.rename_from_december("Renamed")
        self.assertEqual(self.names(), ["Renamed", "Renamed"])
        self.app.undo("2027-01", {})
        self.assertEqual(self.placed("2027-01", "lunch"), [])  # only that step is undone
        self.assertEqual(self.names(), ["Renamed", "Renamed"])  # not silently put back in either month
        self.assertEqual(self.store.load_month("2027-01").events[0].name, "Renamed")  # as saved
        self.app.redo("2027-01", {})
        self.assertEqual((self.placed("2027-01", "lunch"), self.names()), ([("2027-01-05", "ana")], ["Renamed", "Renamed"]))

    def test_going_back_several_steps_keeps_it_too(self):
        first = self.app.assign("2027-01", self.january_slot(5))["history"]["undo"]["id"]
        self.app.assign("2027-01", self.january_slot(6))
        self.rename_from_december("Renamed")
        self.app.assign("2027-01", self.january_slot(7))
        self.app.history_go("2027-01", {"to": first})
        self.assertEqual(self.placed("2027-01", "lunch"), [])
        self.assertEqual(self.names(), ["Renamed", "Renamed"])

    def test_undoing_a_step_that_changed_the_event_still_reaches_the_other_month(self):
        self.app.save_event("2027-01", {"event": {"id": self.event_id, "name": "From January", "start": "2026-12-30",
                                                  "end": "2027-01-02"}})
        self.rename_from_december("From December")
        self.app.undo("2027-01", {})  # that step did change the event: as it was before it, everywhere
        self.assertEqual(self.names(), ["New Year", "New Year"])

    def test_people_placed_in_the_other_month_come_back_with_the_event(self):
        task = self.hot_chocolate()
        self.app.assign("2027-01", self.january_slot(1, task=task))
        january = self.app.history.stacks("2027-01")
        self.app.delete_event("2026-12", {"id": self.event_id})
        self.assertEqual(self.placed("2027-01", task), [])
        self.app.undo("2026-12", {})
        self.assertEqual(self.placed("2027-01", task), [("2027-01-01", "ana")])
        self.assertEqual(self.placed("2027-01", task), [(a.date, a.person_id) for a in self.store.load_month("2027-01").assignments
                                                        if a.task_id == task])  # saved
        self.app.redo("2026-12", {})
        self.assertEqual(self.placed("2027-01", task), [])
        self.app.undo("2026-12", {})  # a second time too
        self.assertEqual(self.placed("2027-01", task), [("2027-01-01", "ana")])
        self.assertEqual(self.app.history.stacks("2027-01"), january)  # nothing recorded in January's own history

    def test_each_step_knows_the_events_it_changed(self):
        self.rename_from_december("Renamed")
        self.assertEqual(self.app.history.top("2026-12").linked, {("events", self.event_id)})
        self.app.assign("2026-12", {"task_id": "lunch", "date": "2026-12-02", "position": 0, "person_id": "ana"})
        self.assertEqual(self.app.history.top("2026-12").linked, frozenset())

    def test_going_back_several_steps_brings_them_back_too(self):
        task = self.hot_chocolate()
        self.app.assign("2027-01", self.january_slot(1, task=task))
        deleted = self.app.delete_event("2026-12", {"id": self.event_id})["history"]["undo"]["id"]
        self.app.assign("2026-12", {"task_id": "lunch", "date": "2026-12-02", "position": 0, "person_id": "ana"})
        self.app.history_go("2026-12", {"to": deleted})
        self.assertEqual(self.placed("2027-01", task), [("2027-01-01", "ana")])
        self.app.history_go("2026-12", {"to": self.app.history.stacks("2026-12")[1][0].id})  # redo both
        self.assertEqual(self.placed("2027-01", task), [])
        self.app.undo("2026-12", {})
        self.app.undo("2026-12", {})
        self.assertEqual(self.placed("2027-01", task), [("2027-01-01", "ana")])

    def test_an_older_step_undone_where_the_event_is_gone_leaves_its_people_to_the_other_month(self):
        task = self.hot_chocolate()
        self.app.assign("2027-01", self.january_slot(1, task=task))
        self.app.assign("2027-01", self.january_slot(5))
        self.app.delete_event("2026-12", {"id": self.event_id})
        self.app.undo("2027-01", {})  # the lunch on the 5th; the event stays deleted, in January too
        self.assertEqual((self.names(), self.placed("2027-01", task), self.placed("2027-01", "lunch")), ([None, None], [], []))
        self.app.undo("2026-12", {})  # the event comes back, with whoever was on it
        self.assertEqual(self.placed("2027-01", task), [("2027-01-01", "ana")])

    def test_people_placed_on_a_task_an_undo_took_away_come_back_with_the_redo(self):
        self.app.save_event_task("2026-12", {"task": {"event_id": self.event_id, "name": "Tea", "area_id": "kitchen"}})
        task = next(t.id for t in month_setup.event_tasks(self.app.month("2027-01"), self.event_id) if t.name == "Tea")
        self.app.undo("2026-12", {})
        self.app.redo("2026-12", {})
        self.app.assign("2027-01", self.january_slot(2, task=task))  # placed in January after the task came back
        self.app.undo("2026-12", {})
        self.assertEqual(self.placed("2027-01", task), [])
        self.app.redo("2026-12", {})
        self.assertEqual(self.placed("2027-01", task), [("2027-01-02", "ana")])

    def test_shortening_the_event_out_of_the_month_and_back(self):
        task = self.hot_chocolate()
        self.app.assign("2027-01", self.january_slot(1, task=task))
        self.app.save_event("2026-12", {"event": {"id": self.event_id, "name": "New Year", "start": "2026-12-30", "end": "2026-12-31"}})
        self.assertEqual(month_setup.event_tasks(self.app.month("2027-01"), self.event_id), [])
        self.app.undo("2026-12", {})
        self.assertEqual(self.placed("2027-01", task), [("2027-01-01", "ana")])

    def test_a_place_taken_since_is_not_taken_back(self):
        task = self.hot_chocolate()
        self.app.assign("2027-01", self.january_slot(1, task=task))
        self.app.save_event("2026-12", {"event": {"id": self.event_id, "name": "New Year", "start": "2026-12-30", "end": "2026-12-31"}})
        self.app.undo("2026-12", {})
        self.app.assign("2027-01", self.january_slot(1, task=task, person="ben"))  # Ben replaces Ana in January
        self.app.redo("2026-12", {})
        self.app.undo("2026-12", {})
        self.assertEqual(self.placed("2027-01", task), [("2027-01-01", "ben")])  # the latest, not Ana again

    def test_a_closure_undone_gives_back_the_regular_shifts_it_took_off(self):
        self.app.set_regular({"task_id": "lunch", "weekday": "fri", "person_id": "ana"})
        self.app.reapply_regulars("2027-01", {})
        self.assertIn(("2027-01-01", "ana"), self.placed("2027-01", "lunch"))
        self.app.save_marker("2026-12", {"marker": {"kind": "closed", "label": "Closed", "start": "2026-12-31",
                                                    "end": "2027-01-01"}})
        self.assertNotIn(("2027-01-01", "ana"), self.placed("2027-01", "lunch"))
        self.app.undo("2026-12", {})
        self.assertIn(("2027-01-01", "ana"), self.placed("2027-01", "lunch"))

    def test_a_closure_saved_from_the_other_month_keeps_its_areas(self):
        result = self.app.save_marker("2026-12", {"marker": {"kind": "closed", "label": "Tent down", "start": "2026-12-30",
                                                             "end": "2027-01-02", "area_ids": ["tent"]}})
        marker_id = result["markers"][0]["id"]
        self.app.save_marker("2027-01", {"marker": {"id": marker_id, "kind": "closed", "label": "Tent closed", "start": "2026-12-30",
                                                    "end": "2027-01-02", "area_ids": ["tent"]}})
        self.assertEqual(self.app.month("2026-12").markers[0].area_ids, ["tent"])  # not "the whole centre"


class SmallerFixesTest(unittest.TestCase):
    def test_safety_copy_before_an_import_has_no_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            store.save_catalog(h.catalog())
            store.save_people(h.people())
            month = h.month()
            month.availability["ana"] = PersonAvailability(comment="Hospital on the 3rd")
            store.save_month(month)
            safety = transfer.replace_data(store, transfer.export_bytes(store))
            archive = zipfile.ZipFile(store.backups_dir / safety)
            self.assertNotIn(b"Hospital", b"".join(archive.read(n) for n in archive.namelist()))

    def test_a_suggestion_is_dropped_once_a_volunteer_took_the_place(self):
        month = h.month()
        proposal = Proposal("lunch", "2026-12-01", 0, "ana", why="least busy")
        self.assertEqual(len(still_valid(Schedule(month, h.people(), Settings()), [proposal])), 1)  # open: still good
        month.assignments.append(Assignment("lunch", "2026-12-01", 0, guest_kind="volunteer"))
        self.assertEqual(still_valid(Schedule(month, h.people(), Settings()), [proposal]), [])

    def test_large_numbers_from_a_spreadsheet_stay_whole(self):
        sheet = Sheet("People", cells={(1, 1): Cell(value=612345678.0), (1, 2): Cell(value=2.5)})
        self.assertEqual((sheet.text(1, 1), sheet.text(1, 2)), ("612345678", "2.5"))

    def test_other_files_in_the_months_folder_are_not_months(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            store.save_month(h.month())
            (store.months_dir / "2026-12 - copy.json").write_text("{}")
            self.assertEqual(store.list_months(), ["2026-12"])

    def test_minutes_must_be_00_to_59(self):
        self.assertIsNone(to_minutes("09:75"))
        with self.assertRaises(ValueError):
            editing.clock("9.75", "Start time")
        self.assertEqual(editing.clock("9.30", "Start time"), "09:30")

    def test_an_archived_area_needs_nobody(self):
        month = h.month()
        next(a for a in month.areas if a.id == "security").archived = True
        self.assertFalse([k for k in demand.required(month) if k[0] == "night"])

    def test_a_skill_still_used_by_a_month_cannot_be_deleted(self):
        catalog, month = h.catalog(), h.month()
        for task in catalog.tasks:
            if task.skill_id == "guard":
                task.skill_id = None
        with self.assertRaises(ValueError):
            editing.delete_skill(catalog, h.people(), "guard", [month])

    def test_names_on_the_rota_alone_each_make_a_person(self):
        from rotaplanner import people_import
        catalog, people = h.catalog(), h.people()
        rows = people_import.preview(people, catalog, text="Rota name\nZed Q.\nYan P.")["rows"]
        self.assertEqual([r["status"] for r in rows], ["new", "new"])
        self.assertEqual([p.first_name for p in people_import.apply(people, catalog, rows)], ["Zed", "Yan"])


class DaysPatternTest(unittest.TestCase):
    """Check › Balance › Days: where each person's shifts fall."""

    def test_always_the_same_weekday_and_too_many_in_a_row(self):
        from rotaplanner.report import check_report
        month = h.month()
        # Ana: every Tuesday lunch (5 Tuesdays in December 2026), plus 6 days in a row of breakfast.
        month.assignments = [Assignment("lunch", f"2026-12-{d:02d}", 0, person_id="ana") for d in (1, 8, 15, 22, 29)]
        month.assignments += [Assignment("breakfast", f"2026-12-{d:02d}", 0, person_id="ben") for d in range(14, 20)]
        report = check_report(month, h.people(), Settings(), [])
        ana = next(p for p in report["balance"] if p["id"] == "ana")
        ben = next(p for p in report["balance"] if p["id"] == "ben")
        self.assertEqual(ana["weekdays"], [0, 5, 0, 0, 0, 0, 0])
        self.assertTrue(any(n.startswith("Mostly Tuesdays (5 of 5)") for n in ana["notes"]))
        self.assertEqual(ben["days_in_a_row"], 6)
        self.assertIn("6 days in a row", ben["notes"])
        self.assertEqual(len(report["weeks"]), 5)
        self.assertEqual(sum(ben["weeks"]), 6)
        # What the screen sorts and colours by is counted here, not on the screen.
        self.assertEqual(ana["uneven"], 5 / 5 + len(ana["notes"]))  # all on one weekday, plus what is worth a look
        self.assertEqual(ben["uneven"], round(1 / 6 + len(ben["notes"]), 4))  # six different weekdays

    def test_hours_against_the_target_and_the_averages(self):
        from rotaplanner.report import check_report
        month = h.month()
        # Ana: 4 lunches of 4 h (16 h for a target of 12); Ben: one breakfast (2 h); Cleo: nothing.
        month.assignments = [Assignment("lunch", f"2026-12-{d:02d}", 0, person_id="ana") for d in (1, 2, 5, 6)]
        month.assignments.append(Assignment("breakfast", "2026-12-07", 0, person_id="ben"))
        report = check_report(month, h.people(), Settings(default_target_hours_per_week=h.a_week(12), prorate_target=False), [])
        rows = {p["id"]: p for p in report["balance"]}
        self.assertEqual([(rows[i]["ratio"], rows[i]["load"]) for i in ("ana", "ben", "cleo")],
                         [(round(16 / 12, 4), "over"), (round(2 / 12, 4), "under"), (0.0, "under")])
        self.assertEqual(report["averages"], {"hours": 6.0, "target": 12.0, "weekends": round(2 / 3, 1), "evenings": 0.0})
        nobody = check_report(h.month(), [], Settings(), [])
        self.assertEqual(nobody["averages"], {"hours": 0, "target": 0, "weekends": 0, "evenings": 0})
        # Without a target there is nothing to be over or under.
        people = h.people()
        people[0].target_hours_per_week = 0
        ana = next(p for p in check_report(month, people, Settings(), [])["balance"] if p["id"] == "ana")
        self.assertEqual(ana["ratio"], 0.0)


    def test_days_away_and_the_month_day_by_day(self):
        from rotaplanner.model import NeverRule
        from rotaplanner.report import check_report
        month = h.month()
        people = h.people()
        people[0].never = [NeverRule("mon", "evening")]  # Ana, on her card: never Monday evenings
        # Ana's reply: away all of Tue 1, and the morning of Tue 15 — where she has the lunch shift anyway.
        h.away(month, "ana", "2026-12-01/morning", "2026-12-01/afternoon", "2026-12-01/evening", "2026-12-15/morning")
        month.assignments = [Assignment("lunch", "2026-12-15", 0, person_id="ana"),
                             Assignment("supper", "2026-12-15", 0, person_id="ana"),
                             Assignment("lunch", "2026-12-16", 0, person_id="ana")]
        ana = next(p for p in check_report(month, people, Settings(), [])["balance"] if p["id"] == "ana")

        tuesday, monday, wednesday = ana["weekday_away"][1], ana["weekday_away"][0], ana["weekday_away"][2]
        self.assertEqual(tuesday["level"], "some")  # 2 of the 5 Tuesdays
        self.assertEqual(tuesday["text"], "Away: Tue 1 (all day), Tue 15 (morning)")
        # Every Monday evening, from her card: still free in the mornings, so not "all", and the
        # rule explains those days without listing each one.
        self.assertEqual(monday, {"level": "some", "text": "Never on Monday evenings (on their card)"})
        self.assertEqual(wednesday, {"level": "", "text": ""})
        self.assertEqual(ana["week_away"][0]["level"], "some")

        days = {d["date"]: d for d in ana["calendar"]}
        self.assertEqual(len(ana["calendar"]), 31)
        self.assertEqual((days["2026-12-15"]["count"], days["2026-12-15"]["away"], days["2026-12-15"]["clash"]), (2, "part", True))
        self.assertEqual(days["2026-12-15"]["text"],
                         "Tue 15: 2 tasks\n• Lunch cook 09:00–13:00\n• Supper cook 16:00–19:00\nAway morning — but has a task then")
        self.assertEqual((days["2026-12-16"]["count"], days["2026-12-16"]["away"], days["2026-12-16"]["clash"]), (1, "", False))
        self.assertEqual((days["2026-12-01"]["away"], days["2026-12-01"]["text"]), ("all", "Tue 1: no task\nAway all day"))
        self.assertEqual(days["2026-12-07"]["away"], "part")  # a Monday: the evening, from her card
        # "all" only when away the whole day, every one of those days.
        people[1].never = [NeverRule("fri", "any")]
        ben = next(p for p in check_report(month, people, Settings(), [])["balance"] if p["id"] == "ben")
        self.assertEqual(ben["weekday_away"][4], {"level": "all", "text": "Never on Friday (on their card)"})

    # -- Balance › Hours: hours a week, week by week ----------------------------

    def test_hours_a_week_and_each_week(self):
        from rotaplanner.report import check_report
        month = h.month()
        # Ana (3.5 h a week): a lunch (4 h) on Tue 1, then three in the week of Monday 14.
        month.assignments = [Assignment("lunch", f"2026-12-{d:02d}", 0, person_id="ana") for d in (1, 14, 15, 16)]
        # Ben: one breakfast (2 h) on Mon 21.
        month.assignments.append(Assignment("breakfast", "2026-12-21", 0, person_id="ben"))
        report = check_report(month, h.people(), Settings(default_target_hours_per_week=3.5), [])
        ana = next(p for p in report["balance"] if p["id"] == "ana")
        # The month's figures and words, from the program.
        self.assertEqual((ana["hours"], ana["target"], ana["week_target"]), (16.0, 15.5, 3.5))
        self.assertEqual(ana["week_hours"], round(16 / (31 / 7), 1))
        self.assertEqual(ana["week_text"], "3.6 / 3.5 h a week")
        self.assertEqual(ana["month_detail"], "16 of 15.5 h in December")
        self.assertEqual((ana["ratio"], ana["load"]), (round(16 / 15.5, 4), ""))   # under the 1.15 that stands out
        # One entry per week, Monday to Sunday, aligned with the report's calendar weeks.
        self.assertEqual([w["label"] for w in report["calendar_weeks"]], ["1–6", "7–13", "14–20", "21–27", "28–31"])
        self.assertEqual([w["hours"] for w in ana["by_week"]], [4.0, 0.0, 12.0, 0.0, 0.0])
        self.assertEqual([w["level"] for w in ana["by_week"]], [2, 0, 3, 0, 0])   # 4 h is above 3.5; 12 h far above
        self.assertEqual([w["far"] for w in ana["by_week"]], [False, False, True, False, False])
        self.assertEqual(ana["by_week"][2]["text"],
                         "Week of Monday 14 to Sunday 20 December: 12 h, more than twice their target (3.5 h a week)")
        # Every week is said in words on Check, even an empty one.
        self.assertEqual(ana["by_week"][1]["text"], "Week of Monday 7 to Sunday 13 December: 0 h")
        ben = next(p for p in report["balance"] if p["id"] == "ben")
        self.assertEqual([w["hours"] for w in ben["by_week"]], [0.0, 0.0, 0.0, 2.0, 0.0])
        self.assertEqual(ben["by_week"][3]["level"], 1)

    def test_a_week_shared_with_the_month_before_counts_whole(self):
        from rotaplanner.months import create_month
        from rotaplanner.report import check_report
        november = create_month(h.catalog(), 2026, 11)
        november.assignments = [Assignment("lunch", "2026-11-30", 0, person_id="ana")]   # Monday 30 November
        month = h.month()
        month.assignments = [Assignment("lunch", "2026-12-01", 0, person_id="ana")]
        settings = Settings(default_target_hours_per_week=4)   # 8 h that week: twice the target, not more
        report = check_report(month, h.people(), settings, [november], around=[november])
        ana = next(p for p in report["balance"] if p["id"] == "ana")
        self.assertEqual(report["calendar_weeks"][0]["start"], "2026-11-30")
        self.assertEqual(report["calendar_weeks"][0]["label"], "30 Nov–6 Dec")
        self.assertEqual(ana["by_week"][0]["hours"], 8.0)
        self.assertEqual(ana["by_week"][0]["text"],
                         "Week of Monday 30 November to Sunday 6 December: 8 h, 4 h of them in November")
        self.assertEqual(ana["hours"], 4.0)   # the month's own hours only
        # The Days view keeps the month's own weeks and counts: not the calendar weeks.
        self.assertEqual(report["weeks"][0], {"start": "2026-12-01", "label": "1–6"})
        self.assertEqual(ana["weeks"][0], 1)
        # Without November's rota, only December's days count, and the week says so.
        alone = check_report(month, h.people(), settings, [])
        ana = next(p for p in alone["balance"] if p["id"] == "ana")
        self.assertEqual((alone["calendar_weeks"][0]["label"], alone["calendar_weeks"][0]["cut"]), ("1–6", True))
        self.assertEqual(ana["by_week"][0]["hours"], 4.0)

    def test_the_earlier_months_as_hours_a_week(self):
        from rotaplanner.months import create_month
        from rotaplanner.report import check_report
        november, october = create_month(h.catalog(), 2026, 11), create_month(h.catalog(), 2026, 10)
        # Ana: three lunches (12 h) in November, 30 days: 2.8 h a week; nothing in October.
        november.assignments = [Assignment("lunch", f"2026-11-{d:02d}", 0, person_id="ana") for d in (3, 10, 17)]
        report = check_report(h.month(), h.people(), Settings(default_target_hours_per_week=3.5), [november, october])
        ana = next(p for p in report["balance"] if p["id"] == "ana")
        self.assertEqual(report["previous_titles"], ["November 2026", "October 2026"])
        self.assertEqual(report["previous_short"], ["Nov", "Oct"])
        self.assertEqual(ana["previous"], [12.0, 0.0])
        self.assertEqual(ana["previous_week"], [round(12 / (30 / 7), 1), 0.0])
        self.assertEqual(ana["previous_week"], [2.8, 0.0])

    def test_the_sentence_above_the_hours(self):
        from rotaplanner.report import check_report
        month = h.month()
        # As in test_hours_against_the_target_and_the_averages: Ana 16 h (2 weekend shifts), Ben 2 h, Cleo nothing.
        month.assignments = [Assignment("lunch", f"2026-12-{d:02d}", 0, person_id="ana") for d in (1, 2, 5, 6)]
        month.assignments.append(Assignment("breakfast", "2026-12-07", 0, person_id="ben"))
        people = h.people()
        people[2].target_hours_per_week = 1.5   # Cleo's own target
        # The means over the rows: (16 + 2 + 0) / 3 = 6 h, 6 / (31 / 7) = 1.35 h a week; targets (3.5 + 3.5 + 1.5) / 3.
        off = check_report(month, people, Settings(default_target_hours_per_week=3.5, prorate_target=False), [])
        self.assertEqual(off["balance_text"], "On average 1.4 h a week for a target of 2.83 h · 6 h each in December · "
                                              "0.7 weekend and 0 evening shifts each.")
        on = check_report(month, people, Settings(default_target_hours_per_week=3.5, prorate_target=True), [])
        self.assertEqual(on["balance_text"], off["balance_text"] + " The time someone is away is not counted in their average.")
        # The averages of before are kept as they were (their keys are pinned).
        self.assertEqual(sorted(on["averages"]), ["evenings", "hours", "target", "weekends"])
        # Nobody in the table: still a sentence, nothing divided by zero.
        nobody = check_report(h.month(), [], Settings(prorate_target=True), [])
        self.assertEqual(nobody["balance_text"], "Nobody to compare in December.")
        self.assertEqual((nobody["calendar_weeks"][0]["label"], nobody["previous_short"]), ("1–6", []))


class HardeningTest(unittest.TestCase):
    """From the security review."""

    def test_a_stray_cell_far_away_does_not_make_the_sheet_huge(self):
        from rotaplanner.xlsx_read import Workbook, XlsxError, split_ref
        from .xlsx_fixture import build_xlsx
        data = build_xlsx({"People": {"cells": {(1, 1): "Name", (2, 1): "Ana"}}})
        archive = zipfile.ZipFile(io.BytesIO(data))
        files = {n: archive.read(n) for n in archive.namelist()}
        name = next(n for n in files if n.startswith("xl/worksheets/"))
        files[name] = files[name].replace(b"</sheetData>", b'<row r="1000000"><c r="XFD1000000"><v>1</v></c></row></sheetData>')
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as rebuilt:
            for n, content in files.items():
                rebuilt.writestr(n, content)
        sheet = Workbook(io.BytesIO(out.getvalue())).sheet("People")
        self.assertEqual((sheet.max_row(), sheet.table()), (2, [["Name"], ["Ana"]]))
        with self.assertRaises(XlsxError):
            split_ref("A99999999999")

    def test_long_cells_in_a_people_list_are_cut(self):
        from rotaplanner import people_import
        rows = people_import.preview(h.people(), h.catalog(), text="Name\n" + "x" * 5000)["rows"]
        self.assertLessEqual(len(rows[0]["first_name"]), 80)

    def test_safety_copies_are_never_overwritten_and_old_ones_lose_their_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            store.save_catalog(h.catalog())
            store.save_people(h.people())
            month = h.month()
            month.availability["ana"] = PersonAvailability(comment="Hospital on the 3rd")
            store.save_month(month)
            data = transfer.export_bytes(store)
            first, second = transfer.replace_data(store, data), transfer.replace_data(store, data)
            self.assertNotEqual(first, second)
            # A copy made by an older version of the program, comments included:
            old = store.backups_dir / "before-import-20200101-000000.zip"
            month.availability["ana"] = PersonAvailability(comment="Hospital on the 3rd")
            store.save_month(month)
            old.write_bytes(transfer.export_bytes(store, include_comments=True))
            self.assertEqual(transfer.scrub_safety_copies(store), 1)
            archive = zipfile.ZipFile(old)
            self.assertNotIn(b"Hospital", b"".join(archive.read(n) for n in archive.namelist()))

    def test_an_import_that_would_not_open_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            store.save_catalog(h.catalog())
            store.save_people(h.people())
            store.save_month(h.month())
            archive = zipfile.ZipFile(io.BytesIO(transfer.export_bytes(store)))
            out = io.BytesIO()
            with zipfile.ZipFile(out, "w") as bad:
                for n in archive.namelist():
                    content = archive.read(n)
                    if n.startswith("months/"):
                        content = content.replace(b'"year": 2026', b'"year": "soon"')
                    bad.writestr(n, content)
            with self.assertRaises(transfer.TransferError):
                transfer.read_export(out.getvalue())

    def test_backups_of_a_month_that_is_gone_lose_their_comments_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            store.save_catalog(h.catalog())
            store.save_people(h.people())
            store.backups_dir.mkdir(parents=True)
            month = h.month()
            month.availability["ana"] = PersonAvailability(comment="Hospital on the 3rd")
            (store.backups_dir / "2026-12.20261201-100000.json").write_text(__import__("json").dumps(month.to_dict()))
            App(store, today=date(2027, 1, 2))
            self.assertNotIn("Hospital", (store.backups_dir / "2026-12.20261201-100000.json").read_text())


if __name__ == "__main__":
    unittest.main()
