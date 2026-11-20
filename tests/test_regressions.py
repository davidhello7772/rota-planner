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
        december_steps = len(self.app._undo.get("2026-12", []))
        self.app.save_event("2027-01", {"event": {"id": self.event_id, "name": "Again", "start": "2026-12-30", "end": "2027-01-02"}})
        self.assertEqual(len(self.app._undo.get("2026-12", [])), december_steps)

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
