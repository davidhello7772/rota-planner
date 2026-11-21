import unittest

from rotaplanner import editing, people_import
from rotaplanner.model import Assignment, Regular, Settings

from . import helpers as h
from .xlsx_fixture import build_xlsx


class PersonTest(unittest.TestCase):
    def setUp(self):
        self.catalog = h.catalog()
        self.people = h.people()

    def test_create_with_generated_rota_name_and_validation(self):
        made = editing.save_person(self.people, self.catalog, {"first_name": "Ana", "last_name": "Bell", "skills": ["cook", "nope"]})
        self.assertEqual(made.name, "Ana Be.")  # "Ana B." is taken
        self.assertEqual(made.skills, ["cook"])
        with self.assertRaises(ValueError):
            editing.save_person(self.people, self.catalog, {"first_name": "X", "name": "ana b."})
        with self.assertRaises(ValueError):
            editing.save_person(self.people, self.catalog, {"first_name": "", "name": ""})
        with self.assertRaises(ValueError):
            editing.save_person(self.people, self.catalog, {"first_name": "Zed", "target_hours_per_week": "lots"})

    def test_the_target_is_hours_a_week(self):
        editing.save_person(self.people, self.catalog, {"id": "ana", "name": "Ana B.", "target_hours_per_week": "3.75"})
        self.assertEqual(self.people[0].target_hours_per_week, 3.75)
        editing.save_person(self.people, self.catalog, {"id": "ana", "name": "Ana B.", "target_hours_per_week": ""})
        self.assertIsNone(self.people[0].target_hours_per_week)   # empty: the usual target
        with self.assertRaisesRegex(ValueError, "^Target hours a week must be between 0 and 80.$"):
            editing.save_person(self.people, self.catalog, {"id": "ana", "name": "Ana B.", "target_hours_per_week": "80.5"})
        # A page loaded before the update would post the monthly key and silently empty the target: refused.
        self.people[0].target_hours_per_week = 2.5
        with self.assertRaisesRegex(ValueError, "^This page is older than the program: reload it, then save again.$"):
            editing.save_person(self.people, self.catalog, {"id": "ana", "name": "Ana C.", "target_hours": "10"})
        self.assertEqual((self.people[0].name, self.people[0].target_hours_per_week), ("Ana B.", 2.5))
        with self.assertRaisesRegex(ValueError, "^This page is older"):
            editing.save_person(self.people, self.catalog, {"first_name": "Zed", "target_hours": ""})
        self.assertNotIn("Zed", [p.first_name for p in self.people])

    def test_update_constraints(self):
        editing.save_person(self.people, self.catalog, {
            "id": "ana", "name": "Ana B.", "first_name": "Ana", "last_name": "Berg", "target_hours_per_week": "10",
            "max_tasks_per_day": 1, "max_shifts_per_week": "3", "never": [{"weekday": "mon", "period": "evening"}],
            "preferences": {"kitchen": 5, "ghost": 2}, "avoid_task_ids": ["night", "nope"],
        })
        ana = self.people[0]
        self.assertEqual((ana.target_hours_per_week, ana.max_tasks_per_day, ana.max_shifts_per_week), (10.0, 1, 3))
        self.assertIsInstance(ana.max_shifts_per_week, int)
        self.assertIsInstance(ana.max_tasks_per_day, int)
        self.assertEqual((ana.never[0].weekday, ana.never[0].period), ("mon", "evening"))
        self.assertEqual(ana.preferences, {"kitchen": 5})
        self.assertEqual(ana.avoid_task_ids, ["night"])
        with self.assertRaises(ValueError):
            editing.save_person(self.people, self.catalog, {"id": "ana", "first_name": "Ana", "never": [{"weekday": "xyz"}]})

    def test_counts_are_whole_numbers(self):
        def save(**limits):
            return editing.save_person(self.people, self.catalog, {"id": "ana", "first_name": "Ana", **limits})

        self.assertEqual(save(max_shifts_per_week=2.0).max_shifts_per_week, 2)
        self.assertIsNone(save(max_shifts_per_week="").max_shifts_per_week)  # empty: no limit
        for bad, words in (("1.5", "Shifts a week must be a whole number."),
                           (0, "Shifts a week must be between 1 and 50."),
                           (51, "Shifts a week must be between 1 and 50."),
                           ("two", "Shifts a week must be a number.")):
            with self.assertRaises(ValueError) as raised:
                save(max_shifts_per_week=bad)
            self.assertEqual(str(raised.exception), words)
        with self.assertRaises(ValueError) as raised:
            save(max_tasks_per_day="1.5")
        self.assertEqual(str(raised.exception), "Tasks a day must be a whole number.")

    def test_skill_matrix_toggle(self):
        editing.set_person_skill(self.people, self.catalog, "cleo", "cook", True)
        self.assertIn("cook", self.people[2].skills)
        editing.set_person_skill(self.people, self.catalog, "cleo", "cook", False)
        self.assertNotIn("cook", self.people[2].skills)


class EventTypesTest(unittest.TestCase):
    def setUp(self):
        self.catalog = h.catalog()

    def test_a_type_and_its_tasks_are_edited_on_their_own(self):
        retreat = editing.save_event_type(self.catalog, {"name": " Retreat "})
        self.assertEqual((retreat.name, retreat.big, retreat.tasks_on_main_rota), ("Retreat", False, True))
        festival = editing.save_event_type(self.catalog, {"name": "Festival", "big": True})
        self.assertFalse(festival.tasks_on_main_rota)  # big events: on their own rota unless told otherwise
        with self.assertRaises(ValueError):
            editing.save_event_type(self.catalog, {"name": "retreat"})  # the name is taken
        task = editing.save_type_task(self.catalog, {"type_id": retreat.id, "name": "Hot chocolate", "area_id": "kitchen",
                                                     "start": "20:30", "end": "21:00", "people": 2, "event_days": "first"})
        self.assertEqual((task.headcount, task.event_days, task.periods, task.on_month_rota),
                         ({"default": 2}, "first", ["evening"], None))
        self.assertEqual([t.name for t in retreat.tasks], ["Hot chocolate"])
        self.assertNotIn(task.id, [t.id for t in self.catalog.tasks])  # not a usual task
        with self.assertRaises(ValueError):
            editing.save_type_task(self.catalog, {"type_id": retreat.id, "name": "X", "area_id": "kitchen", "event_days": "sometimes"})
        self.assertTrue(editing.delete_type_task(self.catalog, retreat.id, task.id))
        self.assertTrue(editing.delete_event_type(self.catalog, festival.id))
        self.assertEqual([t.name for t in self.catalog.event_types], ["Retreat"])


class PrintTemplatesTest(unittest.TestCase):
    def test_save_duplicate_delete(self):
        settings, catalog = Settings(), h.catalog()
        self.assertEqual([t.size for t in settings.print_templates], ["a3", "a2", "a1"])  # ready to use
        made = editing.save_print_template(settings, catalog, {"name": "Kitchen", "size": "a2", "area_ids": ["kitchen", "nope"],
                                                                "show_times": False, "event_tasks": "none"})
        self.assertEqual((made.area_ids, made.show_times, made.output), (["kitchen"], False, "a3_sheets"))
        every = editing.save_print_template(settings, catalog, {"id": made.id, "name": "Kitchen", "area_ids": ["kitchen", "security"]})
        self.assertEqual(every.area_ids, [])  # every area ticked: new areas will be in too
        with self.assertRaises(ValueError):
            editing.save_print_template(settings, catalog, {"name": "X", "size": "a0"})
        copy_ = editing.duplicate_print_template(settings, made.id)
        self.assertEqual(copy_.name, "Kitchen (copy)")
        self.assertTrue(editing.delete_print_template(settings, made.id))
        self.assertEqual(len(settings.print_templates), 4)


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.catalog = h.catalog()
        self.people = h.people()

    def test_skills(self):
        skill = editing.save_skill(self.catalog, {"name": "First aid"})
        with self.assertRaises(ValueError):
            editing.save_skill(self.catalog, {"name": "first AID"})
        editing.delete_skill(self.catalog, self.people, skill.id)
        with self.assertRaises(ValueError):  # still needed by the cooks
            editing.delete_skill(self.catalog, self.people, "cook")

    def test_task_form(self):
        task = editing.save_task(self.catalog, self.catalog, {
            "name": "Garden", "area_id": "kitchen", "start": "9.30", "end": "11:00", "periods": ["morning"],
            "headcount": {"sat": "2", "event": 3, "mon": 0},
        })
        self.assertEqual((task.start, task.end, task.hours), ("09:30", "11:00", 1.5))
        self.assertEqual(task.headcount, {"sat": 2, "event": 3})
        night = editing.save_task(self.catalog, self.catalog, {"name": "Night", "area_id": "kitchen", "start": "22:00", "end": "06:00"})
        self.assertEqual(night.hours, 8.0)
        long = editing.save_task(self.catalog, self.catalog, {"name": "Long", "area_id": "kitchen",
                                                              "periods": ["afternoon", "morning", "nonsense"]})
        self.assertEqual(long.periods, ["morning", "afternoon"])  # day order
        self.assertEqual(editing.parse_periods(["morning", "anytime"]), ["anytime"])
        with self.assertRaises(ValueError):
            editing.parse_periods([])
        for bad in ({"area_id": "kitchen"}, {"name": "X", "area_id": "nope"},
                    {"name": "X", "area_id": "kitchen", "start": "25:00", "end": "26:00"},
                    {"name": "X", "area_id": "kitchen", "start": "09:00"},
                    {"name": "X", "area_id": "kitchen", "headcount": {"someday": 1}}):
            with self.assertRaises(ValueError, msg=str(bad)):
                editing.save_task(self.catalog, self.catalog, bad)

    def test_hours_a_week_from_the_regular_week(self):
        self.assertEqual(editing.regular_load(self.catalog), [])
        for task, weekday, person, frequency in (("lunch", "tue", "ana", "weekly"), ("lunch", "wed", "ana", "odd"),
                                                 ("supper", "mon", "ben", "1st"), ("breakfast", "mon", "ben", "weekly"),
                                                 ("night", "fri", "cleo", "weekly")):
            editing.set_regular(self.catalog, self.people, {"task_id": task, "weekday": weekday, "person_id": person,
                                                            "frequency": frequency})
        # Ana: 4 h every week + 4 h every other week; Ben: 3 h once a month (12 a year) + 2 h every week.
        self.assertEqual(editing.regular_load(self.catalog), [
            {"person_id": "ana", "shifts": 2, "hours": 6.0},
            {"person_id": "ben", "shifts": 2, "hours": 2.7},  # (3 × 12 + 2 × 52) ÷ 52 weeks
            {"person_id": "cleo", "shifts": 1, "hours": 0},  # the night guard counts no hours
        ])
        self.catalog.tasks = [t for t in self.catalog.tasks if t.id != "lunch"]  # a regular left on a task that is gone
        self.assertEqual([e["person_id"] for e in editing.regular_load(self.catalog)], ["ben", "cleo"])

    def test_words_around_a_schedule_message(self):
        self.assertEqual(editing.message_words("  Dear   {name} ,\n", 120), "Dear {name} ,")
        self.assertEqual(len(editing.message_words("x" * 500, editing.CLOSING_LENGTH)), 200)
        self.assertEqual(editing.message_words(None, 10), "")

    def test_duplicate_reorder_and_month_configuration(self):
        clone = editing.duplicate_task(self.catalog, "lunch")
        self.assertEqual(self.catalog.tasks[1].id, clone.id)
        editing.reorder(self.catalog.tasks, ["night", "lunch"])
        self.assertEqual([t.id for t in self.catalog.tasks[:2]], ["night", "lunch"])

        # A month has its own configuration: editing it leaves the typical month alone.
        month = h.month()
        editing.save_task(month, self.catalog, {"id": "lunch", "name": "Lunch cook", "area_id": "kitchen", "hours": 5,
                                                "headcount": {"default": 2}})
        self.assertEqual(next(t for t in month.tasks if t.id == "lunch").hours, 5)
        self.assertEqual(next(t for t in self.catalog.tasks if t.id == "lunch").hours, 4)

    def test_use_another_configuration(self):
        month = h.month()
        month.assignments = [Assignment("lunch", "2026-12-01", 0, person_id="ana"),
                             Assignment("night", "2026-12-01", 0, person_id="ben")]
        month.wanted = ["night|2026-12-02|0"]
        month.demand_overrides = {"night": {"2026-12-03": 0}}
        other = h.month()
        other.tasks = [t for t in other.tasks if t.id != "night"]
        removed = editing.use_configuration(month, other)
        self.assertEqual(removed, 1)
        self.assertEqual([a.task_id for a in month.assignments], ["lunch"])
        self.assertEqual((month.wanted, month.demand_overrides), ([], {}))
        self.assertIsNot(month.tasks[0], other.tasks[0])  # a copy, not shared

    def test_regulars(self):
        editing.set_regular(self.catalog, self.people, {"task_id": "lunch", "weekday": "tue", "person_id": "ana", "frequency": "odd"})
        editing.set_regular(self.catalog, self.people, {"task_id": "lunch", "weekday": "tue", "person_id": "ben"})
        self.assertEqual([(r.person_id, r.frequency) for r in self.catalog.regulars], [("ben", "weekly")])
        editing.set_regular(self.catalog, self.people, {"task_id": "lunch", "weekday": "tue", "person_id": ""})
        self.assertEqual(self.catalog.regulars, [])
        with self.assertRaises(ValueError):
            editing.set_regular(self.catalog, self.people, {"task_id": "lunch", "weekday": "tue", "person_id": "ana",
                                                            "start": "2026-12-10", "end": "2026-12-01"})

    def test_regular_frequencies(self):
        def days(frequency):
            regular = Regular("r", "ana", "lunch", "tue", frequency=frequency)
            return [d for d in range(1, 32) if regular.applies(f"2026-12-{d:02d}")]
        self.assertEqual(days("weekly"), [1, 8, 15, 22, 29])
        self.assertEqual(days("1st"), [1])
        self.assertEqual(days("last"), [29])
        self.assertEqual(sorted(days("odd") + days("even")), [1, 8, 15, 22, 29])
        bounded = Regular("r", "ana", "lunch", "tue", start="2026-12-05", end="2026-12-20")
        self.assertEqual([d for d in range(1, 32) if bounded.applies(f"2026-12-{d:02d}")], [8, 15])

    def test_settings(self):
        settings = editing.save_settings(Settings(), {"centre_name": " Centre ", "default_target_hours_per_week": "14"})
        self.assertEqual((settings.centre_name, settings.default_target_hours_per_week), ("Centre", 14.0))
        # Saving only the words of the schedule message keeps the centre's name and the hours.
        settings = editing.save_settings(settings, {"message_greeting": "Hi {name},"})
        self.assertEqual((settings.centre_name, settings.default_target_hours_per_week, settings.message_greeting),
                         ("Centre", 14.0, "Hi {name},"))

    def test_the_usual_target_is_hours_a_week(self):
        settings = editing.save_settings(Settings(), {"default_target_hours_per_week": "3.25"})
        self.assertEqual(settings.default_target_hours_per_week, 3.25)
        with self.assertRaisesRegex(ValueError, "^Usual target hours a week must be between 0 and 80.$"):
            editing.save_settings(settings, {"default_target_hours_per_week": "81"})
        # A page loaded before the update still sends the monthly key: refused, nothing changed.
        with self.assertRaisesRegex(ValueError, "^This page is older than the program: reload it, then save again.$"):
            editing.save_settings(settings, {"centre_name": "Other", "default_target_hours": "15"})
        self.assertEqual((settings.centre_name, settings.default_target_hours_per_week), ("", 3.25))
        editing.save_settings(settings, {"default_target_hours": "15", "default_target_hours_per_week": "3.5"})
        self.assertEqual(settings.default_target_hours_per_week, 3.5)


class PeopleImportTest(unittest.TestCase):
    def setUp(self):
        self.catalog = h.catalog()
        self.people = h.people()

    def test_pasted_names(self):
        result = people_import.preview(self.people, self.catalog, text="Ana Berg\nAnna Berg\n  Zoe Quinn  \n\nPia")
        self.assertEqual([r["status"] for r in result["rows"]], ["exists", "duplicate", "new", "new"])
        self.assertEqual(result["rows"][2]["name"], "Zoe Q.")
        added = people_import.apply(self.people, self.catalog, [r for r in result["rows"] if r["status"] == "new"])
        self.assertEqual([p.name for p in added], ["Zoe Q.", "Pia"])

    def test_csv_with_skills_and_xlsx(self):
        text = "First name;Last name;Phone;Cook;Resident\nRay;Moss;0700;yes;no\n"
        row = people_import.preview(self.people, self.catalog, text=text)["rows"][0]
        self.assertEqual((row["first_name"], row["last_name"], row["skills"], row["resident"]),
                         ("Ray", "Moss", ["cook"], False))
        data = build_xlsx({"People": {"cells": {(1, 1): "Name", (1, 2): "Surname", (2, 1): "Lia", (2, 2): "Tor"}}})
        row = people_import.preview(self.people, self.catalog, file_bytes=data, filename="list.xlsx")["rows"][0]
        self.assertEqual((row["first_name"], row["last_name"], row["status"]), ("Lia", "Tor", "new"))
        with self.assertRaises(ValueError):
            people_import.preview(self.people, self.catalog, file_bytes=b"junk", filename="list.xlsx")


if __name__ == "__main__":
    unittest.main()
