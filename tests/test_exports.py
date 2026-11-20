import io
import unittest
from datetime import date

from rotaplanner import month_setup
from rotaplanner.exporters import documents, personal, versions, xlsx_rota
from rotaplanner.exporters.sheet import GUEST, OPEN, PERSON, WANTED, build_sheet
from rotaplanner.model import Assignment, Person, PrintTemplate, Settings
from rotaplanner.xlsx_read import Workbook

from . import helpers as h

WHOLE = PrintTemplate(id="t", name="Whole rota")


class PrintedTest(unittest.TestCase):
    def setUp(self):
        self.catalog = h.catalog()
        self.month = h.month(self.catalog)
        self.people = h.people()
        self.month.assignments = [
            Assignment("lunch", "2026-12-01", 0, person_id="ana"),
            Assignment("lunch", "2026-12-02", 0, guest="Marta"),
            Assignment("lunch", "2026-12-03", 0, guest_kind="volunteer"),
        ]
        self.month.wanted = ["lunch|2026-12-04|0"]
        month_setup.save_marker(self.month, {"kind": "closed", "label": "Closed for Christmas", "start": "2026-12-25",
                                             "keep_task_ids": ["night"]})

    def sheet(self):
        return build_sheet(self.month, self.people, Settings())

    def html(self, document):
        document.stamp = "Draft · printed 1 Dec 2026"
        return documents.page(document, standalone=True)

    def test_what_each_cell_shows(self):
        lunch = next(r for a in self.sheet().areas for r in a.rows if r.task.id == "lunch")
        kinds = {iso: [(p.kind, p.text) for p in lunch.cells[iso].places] for iso in
                 ("2026-12-01", "2026-12-02", "2026-12-03", "2026-12-04", "2026-12-05")}
        self.assertEqual(kinds, {
            "2026-12-01": [(PERSON, "Ana B.")], "2026-12-02": [(GUEST, "Marta")],
            "2026-12-03": [(GUEST, "Volunteer")], "2026-12-04": [(WANTED, "")], "2026-12-05": [(OPEN, "")]})
        self.assertEqual(lunch.cells["2026-12-25"].closed, "Closed for Christmas")
        self.assertEqual(lunch.cells["2026-12-25"].places, [])

    def test_monthly_rota(self):
        html = self.html(documents.monthly(self.sheet(), WHOLE))
        self.assertIn("Rota — December 2026", html)
        self.assertIn("Draft · printed 1 Dec 2026", html)
        self.assertIn('class="cell signup"', html)  # a highlighted place colours its whole cell…
        self.assertNotIn("Sign up</div>", html)       # …without words
        self.assertNotIn("dotted", html.split("<body")[1])
        self.assertIn("Closed for Christmas", html)
        self.assertIn("@page a3l", html)  # the styles travel inside a file made for PDF
        halves = self.html(documents.monthly(self.sheet(), PrintTemplate(id="h", name="Halves", size="a4x2")))
        self.assertIn("part 2 of 2", halves)

    def test_templates_choose_areas_paper_and_event_tasks(self):
        retreat = month_setup.save_event(self.month, {"name": "Retreat", "start": "2026-12-11", "end": "2026-12-13",
                                                      "tasks_on_main_rota": False})
        month_setup.save_event_task(self.month, self.catalog, {"event_id": retreat.id, "name": "Hot chocolate",
                                                               "area_id": "kitchen", "start": "20:30", "end": "21:00"})
        kitchen = PrintTemplate(id="k", name="Kitchen", area_ids=["kitchen"])
        html = self.html(documents.monthly(self.sheet(), kitchen))
        self.assertIn("Lunch cook", html)
        self.assertNotIn("Night guard", html)  # Security is not chosen
        self.assertNotIn("Hot chocolate", html)  # the event keeps it on its own rota
        everything = PrintTemplate(id="a", name="All", event_tasks="all")
        self.assertIn("Hot chocolate", self.html(documents.monthly(self.sheet(), everything)))

    def test_tasks_again_at_the_end_and_day_numbers_repeated(self):
        plain = self.html(documents.monthly(self.sheet(), WHOLE))
        both = self.html(documents.monthly(self.sheet(), PrintTemplate(id="b", name="Both", tasks_at_end=True, repeat_days=True)))
        self.assertEqual(plain.count('class="task end"'), 0)
        self.assertEqual(both.count('class="task end"'), both.count('class="task"'))  # every task on both sides
        self.assertIn('class="area-row with-days"', both)
        self.assertNotIn("<tfoot>", both)  # the area lines carry the days: none at the bottom

    def test_posters_on_a3_sheets(self):
        a2 = documents.poster_layout("a2", "a3_sheets")
        self.assertEqual((a2["page"], a2["cols"], a2["rows"], a2["w"], a2["h"]), ("a3pt", 2, 1, 564, 404))
        a1 = documents.poster_layout("a1", "a3_sheets")
        self.assertEqual((a1["page"], a1["cols"], a1["rows"], a1["w"], a1["h"]), ("a3lt", 2, 2, 810, 552))
        self.assertEqual(documents.poster_layout("a1", "one_page")["page"], "a1l")
        html = self.html(documents.monthly(self.sheet(), PrintTemplate(id="p", name="Poster", size="a1")))
        self.assertIn('data-cols="2" data-rows="2"', html)

    def test_names_are_escaped(self):
        self.people.append(Person("eve", "<b>Eve</b>", skills=["cook"]))
        self.month.assignments.append(Assignment("supper", "2026-12-01", 0, person_id="eve"))
        html = self.html(documents.monthly(self.sheet(), WHOLE))
        self.assertIn("&lt;b&gt;Eve&lt;/b&gt;", html)
        self.assertNotIn("<b>Eve</b>", html)

    def test_event_tasks_kept_off_the_monthly_rota_and_sharing_a_row(self):
        retreat = month_setup.save_event(self.month, {"name": "Retreat", "start": "2026-12-11", "end": "2026-12-13"})
        course = month_setup.save_event(self.month, {"name": "Course", "start": "2026-12-18", "end": "2026-12-19"})
        for event in (retreat, course):
            month_setup.save_event_task(self.month, self.catalog, {"event_id": event.id, "name": "Hot chocolate",
                                                                   "area_id": "kitchen", "start": "20:30", "end": "21:00",
                                                                   "event_days": "first"})
        month_setup.save_event_task(self.month, self.catalog, {"event_id": retreat.id, "name": "Registration desk",
                                                               "area_id": "kitchen", "on_month_rota": False})
        monthly = self.html(documents.monthly(self.sheet(), WHOLE))
        self.assertEqual(monthly.count("<span>Hot chocolate</span>"), 1)
        self.assertNotIn("Registration desk", monthly)
        event = self.html(documents.event_rota("Retreat", retreat.start, retreat.end,
                                               [(self.sheet(), retreat.dates())]))
        self.assertIn("Registration desk", event)
        self.assertIn("Friday 11 December", event)

    def test_an_event_over_two_months_is_printed_whole(self):
        event = month_setup.save_event(self.month, {"name": "New Year retreat", "start": "2026-12-30", "end": "2027-01-02"})
        january = month_setup.new_month(self.catalog, self.month, 2027, 1, self.people, Settings())
        month_setup.take_linked_items(january, [self.month])
        parts = [(self.sheet(), ["2026-12-30", "2026-12-31"]),
                 (build_sheet(january, self.people, Settings()), ["2027-01-01", "2027-01-02"])]
        html = self.html(documents.event_rota(event.name, event.start, event.end, parts))
        for day in ("Wednesday 30 December", "Thursday 31 December", "Friday 1 January", "Saturday 2 January"):
            self.assertIn(day, html)
        alone = self.html(documents.event_rota(event.name, event.start, event.end, parts[:1], missing=["January 2027"]))
        self.assertIn("Not planned yet: the days in January 2027.", alone)

    def test_weekly_sheets_and_sign_up_sheet(self):
        weekly = self.html(documents.weekly(self.sheet(), area_ids=["kitchen"]))
        self.assertIn("Kitchen — week of Tuesday 1 December", weekly)
        self.assertNotIn("Night guard", weekly)
        signup = self.html(documents.open_shifts(self.sheet(), only_wanted=True))
        self.assertIn("Friday 4 December", signup)
        self.assertNotIn("Saturday 5 December", signup)  # open, but not highlighted


class HandedOutTest(unittest.TestCase):
    """Versions, personal rotas, calendar files and the Excel file."""

    def setUp(self):
        self.catalog = h.catalog()
        self.month = h.month(self.catalog)
        self.people = h.people()
        self.settings = Settings()
        self.month.assignments = [
            Assignment("lunch", "2026-12-01", 0, person_id="ana"),
            Assignment("night", "2026-12-01", 0, person_id="ben"),
            Assignment("supper", "2026-12-02", 0, person_id="ana"),
        ]

    def test_versions_and_changes_since(self):
        self.assertEqual(versions.stamp(self.month, date(2026, 11, 20), False), "Draft · printed 20 Nov 2026")
        self.assertEqual(versions.changes(self.month, self.people, self.settings), [])
        first = versions.publish(self.month, self.people, self.settings, today=date(2026, 11, 20))
        self.assertEqual((first.number, first.places["lunch|2026-12-01|0"], first.places["lunch|2026-12-02|0"]), (1, "Ana B.", ""))
        self.assertEqual(versions.changes(self.month, self.people, self.settings), [])
        self.month.assignments[0].person_id = "ben"                                       # someone else
        self.month.assignments.append(Assignment("lunch", "2026-12-02", 0, person_id="ana"))  # an open shift filled
        found = versions.changes(self.month, self.people, self.settings)
        self.assertEqual([(c["key"], c["before"], c["after"]) for c in found],
                         [("lunch|2026-12-01|0", "Ana B.", "Ben C."), ("lunch|2026-12-02|0", "", "Ana B.")])
        printed = build_sheet(self.month, self.people, self.settings)
        self.assertEqual(printed.changes, 2)
        self.assertEqual(versions.stamp(self.month, date(2026, 11, 25), True),
                         "Version 1 · 20 Nov 2026 · ● changed since, printed 25 Nov 2026")
        html = documents.page(documents.monthly(printed, WHOLE), standalone=True)
        self.assertIn('class="p changed"', html)
        self.assertIn("changed since version 1", html)

    def test_phases_draft_manager_corrections_published_adjusted(self):
        def phase():
            found = versions.phase(self.month, self.people, self.settings)
            return found["state"], found["short"], [s["status"] for s in found["steps"]]

        self.assertEqual(phase(), ("draft", "Draft", ["current", "todo", "todo", "todo"]))
        first = versions.publish(self.month, self.people, self.settings, today=date(2026, 11, 20), audience="manager")
        self.assertEqual(first.audience, "manager")
        self.assertEqual(phase(), ("checking", "With the manager · v1", ["done", "current", "todo", "todo"]))
        self.assertEqual(versions.stamp(self.month, date(2026, 11, 21), False), "Version 1 to check · 20 Nov 2026")
        self.month.assignments[0].person_id = "ben"  # the manager asked for a change
        self.assertEqual(phase(), ("corrections", "Corrections · 1 change", ["done", "done", "current", "todo"]))
        versions.publish(self.month, self.people, self.settings, today=date(2026, 11, 22), audience="manager")  # checked again
        self.assertEqual(phase()[0], "checking")
        versions.publish(self.month, self.people, self.settings, today=date(2026, 11, 25))
        self.assertEqual(phase(), ("published", "Published · v3", ["done", "done", "done", "current"]))
        self.assertEqual(versions.reference(self.month).number, 3)
        self.assertEqual(versions.stamp(self.month, date(2026, 11, 25), False), "Version 3 · 25 Nov 2026")
        self.month.assignments.append(Assignment("lunch", "2026-12-02", 0, person_id="ana"))  # after printing
        found = versions.phase(self.month, self.people, self.settings)
        self.assertEqual((found["state"], found["short"], found["tone"]), ("adjusted", "Published · 1 change", "warn"))
        self.assertEqual([s["status"] for s in found["steps"]], ["done", "done", "done", "current"])
        self.assertEqual((found["version"], found["next_number"], found["changes"]), (3, 4, 1))
        # Given to the manager after it was published: what is on the wall stays the reference.
        versions.publish(self.month, self.people, self.settings, today=date(2026, 11, 27), audience="manager")
        found = versions.phase(self.month, self.people, self.settings)
        self.assertEqual((found["state"], found["version"], found["changes"], found["next_number"]), ("adjusted", 3, 1, 5))
        self.assertIn("Version 4 was given to the manager on 27 Nov.", found["text"])
        self.assertEqual(versions.stamp(self.month, date(2026, 11, 28), True),
                         "Version 3 · 25 Nov 2026 · ● changed since, printed 28 Nov 2026")
        self.assertEqual(build_sheet(self.month, self.people, self.settings).changes, 1)
        with self.assertRaises(ValueError):
            versions.publish(self.month, self.people, self.settings, audience="everyone")

    def test_a_check_without_corrections_shows_them_skipped(self):
        versions.publish(self.month, self.people, self.settings, today=date(2026, 11, 20), audience="manager")
        versions.publish(self.month, self.people, self.settings, today=date(2026, 11, 21))
        found = versions.phase(self.month, self.people, self.settings)
        self.assertEqual([s["status"] for s in found["steps"]], ["done", "done", "skipped", "current"])

    def test_published_without_a_manager_check(self):
        versions.publish(self.month, self.people, self.settings, today=date(2026, 11, 25))
        found = versions.phase(self.month, self.people, self.settings)
        self.assertEqual([s["status"] for s in found["steps"]], ["done", "skipped", "skipped", "current"])

    def test_versions_made_before_audiences_were_for_everyone(self):
        from rotaplanner.model import Version
        old = Version.from_dict({"number": 1, "published": "2026-11-20", "places": {}})
        self.assertEqual(old.audience, "residents")

    def test_personal_rota_message_and_calendar(self):
        shifts = personal.shifts_of(self.month, self.people, self.settings)
        self.assertEqual([(s.date, s.task, s.time) for s in shifts["ana"]],
                         [("2026-12-01", "Lunch cook", "09:00–13:00"), ("2026-12-02", "Supper cook", "16:00–19:00")])
        ana = self.people[0]
        text = personal.message(self.month, ana, shifts["ana"], "Demo centre")
        self.assertIn("Hello Ana,", text)
        self.assertIn("• Tue 1 Dec · 09:00–13:00 · Lunch cook", text)
        self.month.assignments[0].note = "arrives at 10"
        noted = personal.shifts_of(self.month, self.people, self.settings)["ana"]
        self.assertIn("Lunch cook — arrives at 10", personal.message(self.month, ana, noted))
        self.assertIn('<small class="note">arrives at 10</small>',
                      documents.page(documents.monthly(build_sheet(self.month, self.people, self.settings), WHOLE), standalone=True))
        self.month.assignments[0].note = ""
        self.assertNotIn("in all", text)  # no total of hours in the message
        self.assertTrue(text.endswith("Supper cook\n\nThank you!"))
        own = personal.message(self.month, ana, shifts["ana"], greeting="Dear {name},", closing="")
        self.assertTrue(own.startswith("Dear Ana,\n"))
        self.assertTrue(own.endswith("Supper cook"))
        html = documents.page(personal.personal_rotas(self.month, self.people, shifts), standalone=True)
        self.assertIn("Ana B.", html)
        self.assertNotIn("Cleo D.", html)  # no shift: not printed

        ics = personal.calendar(self.month, self.people[1], shifts["ben"], "Demo centre; hall")
        self.assertTrue(ics.startswith("BEGIN:VCALENDAR\r\n"))
        self.assertIn("DTSTART:20261201T220000\r\nDTEND:20261202T080000", ics)  # runs past midnight
        self.assertIn("LOCATION:Demo centre\\; hall", ics)
        self.assertTrue(all(len(line.encode()) <= 75 for line in ics.split("\r\n")))

    def test_excel_file_reads_back(self):
        month_setup.save_marker(self.month, {"kind": "closed", "label": "Closed", "start": "2026-12-25"})
        self.month.wanted = ["lunch|2026-12-03|0"]
        data = xlsx_rota.workbook(build_sheet(self.month, self.people, self.settings), "Draft")
        book = Workbook(io.BytesIO(data))
        page = book.sheet("December 2026")
        self.assertEqual([page.text(3, c) for c in range(1, 7)], ["Area", "Task", "Time", "Hours", "Tue 1", "Wed 2"])
        self.assertEqual((page.text(4, 1), page.text(5, 2), page.text(5, 5)), ("Kitchen", "Lunch cook", "Ana B."))
        self.assertEqual(page.text(5, 7), "")  # coloured, no words
        self.assertEqual(page.cell(5, 7).fill, "FFFFE98A")
        helper = next(r for r in page.rows() if page.text(r, 2) == "Kitchen helper")
        self.assertEqual(page.cell(helper, 5).fill, "FFD7DAE1")  # a weekday: nobody needed, grey


if __name__ == "__main__":
    unittest.main()
