import io
import unittest
from datetime import date

from rotaplanner.importers.legacy_workbook import (
    WorkbookImportError,
    base_task_name,
    import_workbook,
    parse_times,
)
from rotaplanner.editing import rota_name
from rotaplanner.xlsx_read import Workbook, XlsxError, column_index

from .xlsx_fixture import GREY, build_xlsx

DEC1 = 46357  # Excel day number of 1 December 2026
AJ, AL, AM = column_index("AJ"), column_index("AL"), column_index("AM")
SUNDAYS = (6, 13, 20, 27)


def col_of(day: int) -> int:
    return 4 + day  # column E is the 1st


def legacy_workbook() -> bytes:
    """A miniature of the centre's workbook, with invented people."""
    residents = {(1, c): h for c, h in enumerate(
        ["Name", "Last name", "Lay name", "Resident", "Phone number", "ACTIVE ON ROTA", "Name on Rota",
         "LEAD COOK", "CAFE SHIFT"], start=1)}
    people = [
        ("Ana", "Berg", "Anne", "YES", "0100", "YES", "Ana B.", "YES", "NO"),
        ("Ben", "Cole", "", "YES", "0200", "YES", "Ben C.", "NO", "YES"),
        ("Cleo", "Dane", "", "NO", "", "YES", "Cleo D.", "NO", "YES"),
        ("Dov", "Earl", "", "YES", "", "NO", "Dov E.", "NO", "NO"),
    ]
    for r, row in enumerate(people, start=2):
        residents.update({(r, c): v for c, v in enumerate(row, start=1) if v})

    month = {(2, col_of(d)): DEC1 + d - 1 for d in range(1, 32)}
    month[(3, 1)] = "Events"
    month[(3, col_of(11))] = "Retreat"
    month.update({
        (4, 1): "Kitchen", (4, 2): "Lead Cook", (4, 4): 4, (4, AJ): "9:00 - 13:00", (4, AL): "LEAD COOK", (4, AM): "MORNING",
        (5, 1): "Cafe", (5, 2): "Cafe shift - 1", (5, 4): 3, (5, AJ): "11:00 - 14:00", (5, AL): "CAFE SHIFT", (5, AM): "MORNING, AFTERNOON",
        (6, 1): "(Manager: Ana B.)", (6, 2): "Cafe shift - 2", (6, 4): 3, (6, AJ): "11:00 - 14:00", (6, AL): "CAFE SHIFT", (6, AM): "MORNING, AFTERNOON",
    })
    for day in range(1, 32):
        for row in (5, 6):
            needed = day in SUNDAYS or (day == 11 and row == 5)
            month[(row, col_of(day))] = (None, None if needed else GREY, False)
    month[(4, col_of(1))] = ("Ana B.", None, True)        # pre-filled by the regular week
    month[(4, col_of(2))] = "Unknown Person"
    month[(5, col_of(13))] = ("Ben C. / Cleo D.", None, False)

    weekly = {(3, 1): "Area", (3, 2): "Tasks", **{(3, 3 + i): d for i, d in enumerate(
        ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])},
        (4, 2): "Lead Cook", (4, 3): "Ana B.", (4, 9): "Ghost X."}
    replies = {(1, 1): "Name", (1, 2): "Morning", (1, 3): "Afternoon", (1, 4): "Evening", (1, 5): "Comment",
               (2, 1): "Ben C.", (2, 2): "Tue 1, Wed 2", (2, 4): "Sun 6", (2, 5): "Away for a few days",
               (3, 1): "Nobody Z.", (3, 2): "Thu 3"}
    preferences = {(1, 3): "Full name", (1, 4): "Helping in the kitchen", (1, 5): "Helping in the cafe",
                   (2, 3): "Ana Berg", (2, 4): 5, (2, 5): 2}
    return build_xlsx({
        "Info Residents": {"cells": preferences},
        "Weekly Assignments": {"cells": weekly},
        "Residents": {"cells": residents},
        "December 2026": {"cells": month, "merges": [f"O3:P3"]},  # 11–12 December
        "Resident unavailability from the form": {"cells": replies},
    })


class HelperTest(unittest.TestCase):
    def test_text_helpers(self):
        self.assertEqual(base_task_name("Cafe Morning shift - 3"), ("Cafe Morning shift", 3))
        self.assertEqual(base_task_name("Lunch Kitchen Assistant 2"), ("Lunch Kitchen Assistant", 2))
        self.assertEqual(base_task_name("Inspect public toilets 2pm"), ("Inspect public toilets 2pm", None))
        self.assertEqual(parse_times("22:00 - 08:00 (d+1)"), ("22:00", "08:00", ""))
        self.assertEqual(parse_times("9:00 - 13:00 10.00-15.00 (Sat-Sun)"), ("09:00", "13:00", "10.00-15.00 (Sat-Sun)"))
        self.assertEqual(parse_times("Done by 9am"), ("", "", "Done by 9am"))
        self.assertEqual(rota_name("Ada", "Berg", ["Ada B."]), "Ada Be.")

    def test_reader_values_fills_and_merges(self):
        book = Workbook(io.BytesIO(legacy_workbook()))
        sheet = book.sheet("December 2026")
        self.assertEqual(sheet.text(4, 2), "Lead Cook")
        self.assertEqual(sheet.number(4, 4), 4.0)
        self.assertEqual(sheet.cell(5, col_of(1)).fill, GREY)
        self.assertTrue(sheet.cell(4, col_of(1)).formula)
        self.assertEqual(sheet.merge_span(3, col_of(11)), 2)
        self.assertEqual(book.find_sheet("resident unavailability"), "Resident unavailability from the form")
        with self.assertRaises(XlsxError):
            Workbook(io.BytesIO(b"not a zip file"))


class LegacyImportTest(unittest.TestCase):
    def setUp(self):
        self.result = import_workbook(io.BytesIO(legacy_workbook()), anonymous=False)
        self.month = self.result.months[0]
        self.tasks = {t.name: t for t in self.result.catalog.tasks}

    def test_people_and_skills(self):
        people = {p.name: p for p in self.result.people}
        self.assertEqual(len(people), 4)
        self.assertEqual(self.result.summary["active"], 3)
        self.assertEqual([s.name for s in self.result.catalog.skills], ["Lead cook", "Cafe shift"])
        self.assertEqual(people["Ana B."].skills, ["skill-lead-cook"])
        self.assertFalse(people["Cleo D."].resident)
        self.assertEqual(people["Ana B."].preferences, {"area-kitchen": 5, "area-cafe": 2})

    def test_tasks_and_headcounts_from_grey_cells(self):
        cafe = self.tasks["Cafe shift"]
        self.assertEqual(cafe.headcount, {"sun": 2, "event": 1})
        self.assertEqual((cafe.start, cafe.end, cafe.hours), ("11:00", "14:00", 3.0))
        self.assertEqual(cafe.periods, ["morning", "afternoon"])
        self.assertEqual(self.tasks["Lead Cook"].headcount, {"default": 1})
        self.assertEqual([(e.name, e.start, e.end) for e in self.month.events], [("Retreat", "2026-12-11", "2026-12-12")])
        self.assertEqual(self.result.summary["months"][0]["required"], 31 + 4 * 2 + 1)
        # The retreat also covers the 12th, when the cafe needed nobody: kept as an exception.
        self.assertEqual(self.month.demand_overrides, {cafe.id: {"2026-12-12": 0}})

    def test_assignments_guests_and_regulars(self):
        by_slot = {(a.task_id, a.date, a.position): a for a in self.month.assignments}
        lead, cafe = self.tasks["Lead Cook"].id, self.tasks["Cafe shift"].id
        self.assertEqual(by_slot[(lead, "2026-12-01", 0)].source, "regular")
        self.assertEqual(by_slot[(lead, "2026-12-02", 0)].guest, "Unknown Person")
        self.assertEqual({by_slot[(cafe, "2026-12-13", p)].person_id for p in (0, 1)}, {"person-002", "person-003"})
        regulars = self.result.catalog.regulars
        self.assertEqual([(r.person_id, r.task_id, r.weekday) for r in regulars], [("person-001", lead, "mon")])
        self.assertTrue(any("Regular week" in w for w in self.result.warnings))

    def test_availability(self):
        reply = self.month.availability["person-002"]
        self.assertEqual(reply.unavailable, ["2026-12-01/morning", "2026-12-02/morning", "2026-12-06/evening"])
        self.assertEqual(reply.comment, "Away for a few days")
        self.assertEqual(self.result.summary["availability_replies"], 1)

    def test_anonymous_import_keeps_structure_drops_identities(self):
        anonymous = import_workbook(io.BytesIO(legacy_workbook()), anonymous=True, seed=3)
        real = {"Ana", "Ben", "Cleo", "Dov", "Berg", "Cole", "Dane", "Earl"}
        for person in anonymous.people:
            self.assertNotIn(person.first_name, real)
            self.assertNotIn(person.last_name, real)
            self.assertEqual((person.phone, person.lay_name), ("", ""))
        month = anonymous.months[0]
        self.assertEqual(len(month.assignments), len(self.month.assignments))
        self.assertIn("Guest 1", {a.guest for a in month.assignments})
        self.assertEqual(month.availability["person-002"].comment, "")
        self.assertEqual({a.manager for a in anonymous.catalog.areas}, {""})
        self.assertEqual(anonymous.people[0].skills, ["skill-lead-cook"])

    def test_skill_named_after_the_task(self):
        from rotaplanner.importers.legacy_workbook import _Importer
        importer = _Importer(None, False, None)
        for name in ("LEAD COOK LUNCH", "CAFE SHIFT", "SHOP SHIFT", "RECEPTION", "RECEPTION STEWARD",
                     "GARDEN HOSTING", "GARDEN REGISTRATION", "GARDEN SECURITY"):
            importer.skill_ids[name.lower()] = name
        self.assertEqual(importer.skill_named_after("Stewards", "RECEPTION"), "RECEPTION STEWARD")
        self.assertEqual(importer.skill_named_after("Garden Registration", "RECEPTION"), "GARDEN REGISTRATION")
        self.assertIsNone(importer.skill_named_after("Cafe Lunch shift", "CAFE SHIFT"))  # sheet's skill fits
        self.assertIsNone(importer.skill_named_after("Garden", "GARDEN SECURITY"))
        self.assertIsNone(importer.skill_named_after("Meet and Greet", "RECEPTION"))

    def test_not_a_rota_workbook(self):
        with self.assertRaises(WorkbookImportError):
            import_workbook(io.BytesIO(build_xlsx({"Sheet1": {"cells": {(1, 1): "x"}}})))
        with self.assertRaises(WorkbookImportError):
            import_workbook(io.BytesIO(b"garbage"))


if __name__ == "__main__":
    unittest.main()
