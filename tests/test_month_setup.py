import unittest

from rotaplanner import availability, demand, editing, month_setup
from rotaplanner.model import Assignment, Regular, Settings

from . import helpers as h

REPLIES = (
    "Timestamp\tYour name\tMorning\tAfternoon\tEvening\tComment\n"
    "01/12/2026 10:00\tAna B.\tTue 1, Wed 2\t\tSun 6\tAway early December\n"
    "01/12/2026 11:00\tBen Cole\t\t15, 16\t\t\n"
    "01/12/2026 12:00\tAnna B\tMon 7\t\t\t\n"          # close to Ana B.: to check
    "01/12/2026 13:00\tNobody Here\tFri 4\t\t\t\n"
    "02/12/2026 09:00\tAna B.\tTue 1\t\t\tChanged my plans\n"  # sent again: replaces the first
)


class NewMonthTest(unittest.TestCase):
    def test_like_the_previous_month_in_one_step(self):
        catalog = h.catalog()
        catalog.regulars = [Regular("r", "ana", "lunch", "mon")]
        previous = h.month(catalog)
        previous.tasks[0].hours = 5  # December's own change
        previous.tasks.append(previous.tasks[1].__class__(id="xmas", area_id="kitchen", name="Xmas", only_dates=["2026-12-25"]))
        january = month_setup.new_month(catalog, previous, 2027, 1, h.people(), Settings())
        self.assertEqual(january.id, "2027-01")
        self.assertEqual(next(t for t in january.tasks if t.id == "lunch").hours, 5)
        self.assertNotIn("xmas", [t.id for t in january.tasks])  # belonged to December
        self.assertEqual(sorted(a.date for a in january.assignments),
                         ["2027-01-04", "2027-01-11", "2027-01-18", "2027-01-25"])
        january.tasks[0].hours = 9
        self.assertEqual(previous.tasks[0].hours, 5)  # a copy, not shared

    def test_next_month_after(self):
        self.assertEqual(month_setup.next_month_after(2026, 12), (2027, 1))
        self.assertEqual(month_setup.next_month_after(2026, 3), (2026, 4))


class DaysTest(unittest.TestCase):
    def setUp(self):
        self.month = h.month()

    def test_events_make_event_days(self):
        event = month_setup.save_event(self.month, {"name": "Retreat", "start": "2026-12-08", "end": "2026-12-09", "big": True})
        self.assertTrue(event.big)
        self.assertEqual(demand.required(self.month)[("helper", "2026-12-08")], 3)
        month_setup.save_event(self.month, {"id": event.id, "name": "Retreat", "start": "2026-12-10", "end": "2026-12-10"})
        self.assertNotIn(("helper", "2026-12-08"), demand.required(self.month))
        for bad in ({"name": "", "start": "2026-12-01"}, {"name": "X", "start": "2026-12-05", "end": "2026-12-01"},
                    {"name": "X", "start": "2027-02-01"}, {"name": "X", "start": "nope"}):
            with self.assertRaises(ValueError, msg=str(bad)):
                month_setup.save_event(self.month, bad)
        self.assertTrue(month_setup.delete_event(self.month, event.id))

    def test_closing_a_day_frees_the_regulars_but_not_choices_made_by_hand(self):
        self.month.assignments = [Assignment("lunch", "2026-12-25", 0, person_id="ana", source="regular"),
                                  Assignment("supper", "2026-12-25", 0, person_id="ben", source="manual"),
                                  Assignment("night", "2026-12-25", 0, person_id="cleo", source="regular")]
        month_setup.save_marker(self.month, {"kind": "closed", "label": "Christmas", "start": "2026-12-25",
                                             "keep_task_ids": ["night"]})
        self.assertEqual([a.person_id for a in self.month.assignments], ["ben", "cleo"])

    def test_closures_and_notes(self):
        marker = month_setup.save_marker(self.month, {"kind": "closed", "label": "Closed for Christmas",
                                                      "start": "2026-12-24", "end": "2026-12-26",
                                                      "keep_task_ids": ["night", "ghost"], "area_ids": ["ghost"]})
        self.assertEqual((marker.keep_task_ids, marker.area_ids), (["night"], []))
        needs = demand.required(self.month)
        self.assertNotIn(("lunch", "2026-12-25"), needs)
        self.assertIn(("night", "2026-12-25"), needs)
        note = month_setup.save_marker(self.month, {"kind": "note", "label": "Carols", "start": "2026-12-22",
                                                    "keep_task_ids": ["night"]})
        self.assertEqual(note.keep_task_ids, [])
        with self.assertRaises(ValueError):
            month_setup.save_marker(self.month, {"kind": "party", "label": "X", "start": "2026-12-22"})

    def test_day_type_by_hand(self):
        self.assertTrue(month_setup.set_day_type(self.month, "2026-12-02", "event"))
        self.assertEqual(self.month.day_type("2026-12-02"), "event")
        self.assertFalse(month_setup.set_day_type(self.month, "2026-12-02", "event"))
        self.assertTrue(month_setup.set_day_type(self.month, "2026-12-02", None))
        with self.assertRaises(ValueError):
            month_setup.set_day_type(self.month, "2026-12-02", "holiday")

    def test_overview(self):
        result = month_setup.overview(self.month, h.people(), Settings(default_target_hours=10))
        # Every day: lunch 4 h + supper 3 h + night 0 h + breakfast 2 h; weekends: 2 helpers × 3 h.
        self.assertEqual(result["hours_needed"], 31 * 9 + 8 * 6)
        self.assertEqual(result["hours_available"], 30)  # three active people × 10 h
        self.assertEqual(len(result["weeks"]), 5)
        self.assertEqual(result["days"]["2026-12-05"]["shifts"], 6)
        self.assertEqual((result["balance"], result["hours_short"]), ("bad", 327 - 30))

    def test_can_the_people_give_what_the_month_needs(self):
        # 327 h of work, three active people: just enough, only just, plenty, and nobody at all.
        def balance(target):
            result = month_setup.overview(self.month, h.people(), Settings(default_target_hours=target))
            return result["balance"], result["hours_short"]
        self.assertEqual(balance(108.9), ("bad", 1))    # 0.3 h short still says "1 h short", never "0 h"
        self.assertEqual(balance(109), ("warn", 0))     # exactly enough: nothing to spare
        self.assertEqual(balance(120), ("warn", 0))     # more than nine tenths of what people can give
        self.assertEqual(balance(125), ("ok", 0))
        self.assertEqual(month_setup.overview(self.month, [], Settings())["balance"], "bad")


class OverTwoMonthsTest(unittest.TestCase):
    def test_months_spanned(self):
        self.assertEqual(month_setup.months_spanned("2026-12-28", "2027-01-03"), ["2026-12", "2027-01"])
        self.assertEqual(month_setup.months_spanned("2026-12-01", "2026-12-03"), ["2026-12"])

    def test_copies_follow_the_event(self):
        december = h.month()
        january = month_setup.new_month(h.catalog(), december, 2027, 1, h.people(), Settings())
        event = month_setup.save_event(december, {"name": "Winter retreat", "start": "2026-12-28", "end": "2027-01-03"})
        self.assertTrue(month_setup.sync_item(january, "events", event.id, event))
        self.assertEqual(january.day_type("2027-01-02"), "event")
        self.assertFalse(month_setup.sync_item(january, "events", event.id, event))  # already the same
        event.end = "2026-12-31"  # moved back into December only
        self.assertTrue(month_setup.sync_item(january, "events", event.id, event))
        self.assertEqual(january.events, [])

    def test_a_new_month_takes_what_already_reaches_into_it(self):
        december = h.month()
        month_setup.save_marker(december, {"kind": "closed", "label": "Closed", "start": "2026-12-30", "end": "2027-01-02"})
        january = month_setup.new_month(h.catalog(), december, 2027, 1, h.people(), Settings())
        self.assertTrue(month_setup.take_linked_items(january, [december]))
        self.assertEqual([m.label for m in january.markers], ["Closed"])
        self.assertNotIn(("lunch", "2027-01-02"), demand.required(january))
        with self.assertRaises(ValueError):
            month_setup.save_event(december, {"name": "Too long", "start": "2026-12-01", "end": "2027-03-01"})


class EventTasksTest(unittest.TestCase):
    def setUp(self):
        self.catalog = h.catalog()
        self.month = h.month(self.catalog)
        self.retreat = month_setup.save_event(self.month, {"name": "Retreat", "start": "2026-12-11", "end": "2026-12-13"})

    def add(self, **fields):
        data = {"event_id": self.retreat.id, "name": "Hot chocolate", "area_id": "kitchen",
                "start": "20:30", "end": "21:00", "event_days": "first", **fields}
        return month_setup.save_event_task(self.month, self.catalog, data)

    def test_needed_only_on_the_chosen_days_of_its_event(self):
        task = self.add(people=2)
        needs = demand.required(self.month)
        self.assertEqual([d for (t, d) in needs if t == task.id], ["2026-12-11"])
        self.assertEqual(needs[(task.id, "2026-12-11")], 2)
        self.assertEqual(task.periods, ["evening"])
        self.assertIsNone(task.on_month_rota)  # no answer of its own: it follows its event
        self.retreat.start, self.retreat.end = "2026-12-18", "2026-12-20"  # the event moves: its tasks follow
        self.assertEqual([d for (t, d) in demand.required(self.month) if t == task.id], ["2026-12-18"])

    def test_the_event_says_whether_its_tasks_are_on_the_main_rota(self):
        chocolate = self.add()
        desk = self.add(name="Registration", on_month_rota=False)  # an exception: event rota only
        self.assertTrue(self.retreat.tasks_on_main_rota)  # not a big event: on the main rota
        self.assertEqual([self.month.on_main_rota(t) for t in (chocolate, desk)], [True, False])
        month_setup.save_event(self.month, {"id": self.retreat.id, "name": "Retreat", "start": self.retreat.start,
                                            "end": self.retreat.end, "tasks_on_main_rota": False})
        self.assertFalse(self.month.on_main_rota(chocolate))
        self.assertTrue(self.month.on_main_rota(self.month.tasks[0]))  # usual tasks always are
        big = month_setup.save_event(self.month, {"name": "Festival", "start": "2026-12-20", "end": "2026-12-21", "big": True})
        self.assertFalse(big.tasks_on_main_rota)  # big events keep their tasks on their own rota, unless told otherwise

    def test_removing_the_event_removes_its_tasks_and_placements(self):
        task = self.add()
        self.month.assignments.append(Assignment(task.id, "2026-12-11", 0, person_id="ana"))
        month_setup.delete_event(self.month, self.retreat.id)
        self.assertNotIn(task.id, [t.id for t in self.month.tasks])
        self.assertEqual([a for a in self.month.assignments if a.task_id == task.id], [])

    def test_an_event_type_gives_its_tasks_to_the_next_event(self):
        self.add()
        self.add(name="Registration", event_days="all", start="", end="", on_month_rota=False)
        retreat = month_setup.type_from_event(self.month, self.retreat.id, "Retreat")
        self.assertEqual([t.name for t in retreat.tasks], ["Hot chocolate", "Registration"])
        other = month_setup.save_event(self.month, {"name": "Weekend course", "start": "2026-12-18", "end": "2026-12-19",
                                                    "type_id": retreat.id})
        self.assertEqual(other.type_id, retreat.id)
        self.assertEqual(month_setup.add_type_tasks(self.month, other.id, retreat), 2)
        self.assertEqual(month_setup.add_type_tasks(self.month, other.id, retreat), 0)  # already there
        copies = month_setup.event_tasks(self.month, other.id)
        self.assertEqual([(t.event_days, t.on_month_rota) for t in copies], [("first", None), ("all", False)])

    def test_new_months_and_other_configurations_leave_event_tasks_alone(self):
        task = self.add()
        january = month_setup.new_month(self.catalog, self.month, 2027, 1, h.people(), Settings())
        self.assertNotIn(task.id, [t.id for t in january.tasks])
        editing.use_configuration(self.month, self.catalog)
        self.assertIn(task.id, [t.id for t in self.month.tasks])

    def test_event_tasks_follow_into_the_next_month(self):
        self.retreat.start, self.retreat.end = "2026-12-30", "2027-01-02"
        task = self.add(event_days="last")
        january = month_setup.new_month(self.catalog, self.month, 2027, 1, h.people(), Settings())
        self.assertTrue(month_setup.take_linked_items(january, [self.month]))
        self.assertEqual([d for (t, d) in demand.required(january) if t == task.id], ["2027-01-02"])
        self.assertEqual([d for (t, d) in demand.required(self.month) if t == task.id], [])
        month_setup.delete_event_task(self.month, task.id)
        self.assertTrue(month_setup.sync_item(january, "events", self.retreat.id, self.retreat, []))
        self.assertEqual(month_setup.event_tasks(january, self.retreat.id), [])

    def test_parts_of_the_day_from_the_times(self):
        self.assertEqual(editing.periods_for_times("11:00", "15:00"), ["morning", "afternoon"])
        self.assertEqual(editing.periods_for_times("12:00", "14:00"), ["morning"])
        self.assertEqual(editing.periods_for_times("16:00", "19:00"), ["afternoon", "evening"])
        self.assertEqual(editing.periods_for_times("", ""), ["anytime"])
        self.assertEqual(editing.periods_for_times("09:00", ""), ["anytime"])
        self.assertEqual(editing.periods_for_times("soon", "later"), ["anytime"])

    def test_a_part_of_the_day_counts_from_half_an_hour(self):
        parts = editing.periods_for_times
        self.assertEqual(parts("14:00", "17:15"), ["afternoon"])               # a quarter of an hour is not an evening
        self.assertEqual(parts("14:00", "17:30"), ["afternoon", "evening"])    # half an hour is
        self.assertEqual(parts("13:31", "16:00"), ["afternoon"])
        self.assertEqual(parts("13:30", "16:00"), ["morning", "afternoon"])
        self.assertEqual(parts("09:00", "18:00"), ["morning", "afternoon", "evening"])
        # Too short to count anywhere: the part that holds most of it (the earlier one when equal).
        self.assertEqual(parts("09:00", "09:15"), ["morning"])
        self.assertEqual(parts("13:45", "14:20"), ["afternoon"])
        self.assertEqual(parts("13:50", "14:10"), ["morning"])
        # Past midnight is evening, not the next morning.
        self.assertEqual(parts("22:00", "08:00"), ["evening"])
        self.assertEqual(parts("16:00", "01:00"), ["afternoon", "evening"])

    def test_an_event_task_gets_its_parts_by_the_same_rule(self):
        catalog = h.catalog()
        task = editing.save_event_task(catalog, catalog, {"name": "Tea table", "area_id": "kitchen", "people": 1,
                                                          "start": "14:00", "end": "17:15"})
        self.assertEqual(task.periods, ["afternoon"])


class OlderFilesTest(unittest.TestCase):
    def test_format_2_asked_each_task_whether_it_was_on_the_monthly_rota(self):
        from rotaplanner.model import Month
        from rotaplanner.store import migrate
        document = {
            "schema_version": 2, "id": "2026-12", "year": 2026, "month": 12,
            "events": [{"id": "e1", "name": "Festival", "start": "2026-12-04", "end": "2026-12-06", "big": True}],
            "tasks": [
                {"id": "usual", "area_id": "kitchen", "name": "Lunch", "on_month_rota": True},
                {"id": "a", "area_id": "kitchen", "name": "Desk", "event_id": "e1", "on_month_rota": False},
                {"id": "b", "area_id": "kitchen", "name": "Car park", "event_id": "e1", "on_month_rota": False},
                {"id": "c", "area_id": "kitchen", "name": "Book stall", "event_id": "e1", "on_month_rota": True},
            ],
        }
        month = Month.from_dict(migrate(document))
        self.assertFalse(month.events[0].tasks_on_main_rota)  # what most of its tasks said
        self.assertEqual([t.on_month_rota for t in month.tasks], [None, None, None, True])  # one exception kept
        self.assertEqual([month.on_main_rota(t) for t in month.tasks], [True, False, False, True])

    def test_format_4_people_have_no_weekly_limit_and_older_programs_refuse_format_5(self):
        import json
        import tempfile
        from pathlib import Path

        from rotaplanner.model import SCHEMA_VERSION, Person
        from rotaplanner.store import Store, StoreError, migrate
        self.assertEqual(SCHEMA_VERSION, 5)
        document = {"schema_version": 4, "people": [{"id": "ana", "name": "Ana B.", "max_tasks_per_day": 1}]}
        migrated = migrate(json.loads(json.dumps(document)))
        self.assertEqual(migrated, dict(document, schema_version=5))  # nothing else changes
        person = Person.from_dict(migrated["people"][0])
        self.assertIsNone(person.max_shifts_per_week)
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            ana = h.people()[0]
            ana.max_shifts_per_week = 2
            store.save_people([ana])
            saved = json.loads((Path(tmp) / "people.json").read_text())
            self.assertEqual(saved["schema_version"], 5)
            self.assertEqual(saved["people"][0]["max_shifts_per_week"], 2)
            self.assertEqual(store.load_people()[0].max_shifts_per_week, 2)
        # What a program still at format 4 does with it: refuse, rather than drop the limit when saving.
        import rotaplanner.store as store_module
        original = store_module.SCHEMA_VERSION
        store_module.SCHEMA_VERSION = 4
        try:
            with self.assertRaises(StoreError):
                migrate(dict(saved))
        finally:
            store_module.SCHEMA_VERSION = original


class AvailabilityTest(unittest.TestCase):
    def setUp(self):
        self.month = h.month()
        self.people = h.people()

    def test_parse_days(self):
        self.assertEqual(availability.parse_days("Tue 1, Wed 2, 15th, 40", 2026, 12), ([1, 2, 15], 0))
        self.assertEqual(availability.parse_days("Mon 1", 2026, 12), ([1], 1))  # 1 December is a Tuesday

    def test_preview_matches_names_and_keeps_the_latest_reply(self):
        result = availability.preview(self.month, self.people, text=REPLIES)
        rows = result["rows"]
        self.assertEqual([(r["person_id"], r["match"], r["replaced"]) for r in rows], [
            ("ana", "exact", True), ("ben", "exact", False), ("ana", "close", True),
            (None, "none", False), ("ana", "exact", False)])
        self.assertEqual(rows[1]["unavailable"], ["2026-12-15/afternoon", "2026-12-16/afternoon"])
        self.assertEqual(result["counts"]["unknown"], 1)
        with self.assertRaises(ValueError):
            availability.preview(self.month, self.people, text="just\nsome words")

    def test_apply_set_and_comments(self):
        rows = availability.preview(self.month, self.people, text=REPLIES)["rows"]
        kept = [r for r in rows if r["person_id"] and not r["replaced"]]
        self.assertEqual(availability.apply(self.month, self.people, kept), 2)
        self.assertEqual(self.month.availability["ana"].unavailable, ["2026-12-01/morning"])
        self.assertEqual(self.month.availability["ana"].comment, "Changed my plans")
        availability.set_person(self.month, self.people, "cleo", ["2026-12-03/evening", "2027-01-01/evening", "x"])
        self.assertEqual(self.month.availability["cleo"].unavailable, ["2026-12-03/evening"])
        self.assertTrue(availability.mark_comment(self.month, "ana", True))
        summary = availability.summary(self.month, self.people)
        self.assertEqual([p["replied"] for p in summary["people"]], [True, True, True])  # Dov is not active

    def test_free_regulars_who_are_away(self):
        self.month.assignments = [Assignment("lunch", "2026-12-01", 0, person_id="ana", source="regular"),
                                  Assignment("lunch", "2026-12-02", 0, person_id="ana", source="regular"),
                                  Assignment("supper", "2026-12-01", 0, person_id="ben", source="manual")]
        h.away(self.month, "ana", "2026-12-01/morning")
        h.away(self.month, "ben", "2026-12-01/afternoon")
        self.assertEqual(availability.free_away_regulars(self.month, self.people, Settings()), 1)
        self.assertEqual([(a.person_id, a.date) for a in self.month.assignments],
                         [("ana", "2026-12-02"), ("ben", "2026-12-01")])  # manual ones stay, shown as conflicts


if __name__ == "__main__":
    unittest.main()
