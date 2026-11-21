"""Hours a week: the target, how older files with monthly targets are read,
and the figures and words every screen shows about them."""

import copy
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rotaplanner import demo, solver, transfer, view, workload
from rotaplanner import eligibility as rules
from rotaplanner.model import (
    WEEKS_A_MONTH, Assignment, NeverRule, Person, Settings, Task, TimeChange, week_start, weekly_from_monthly,
)
from rotaplanner.months import apply_regulars, create_month
from rotaplanner.schedule import Schedule
from rotaplanner.store import Store

from . import helpers as h


class TargetTest(unittest.TestCase):
    """The target is hours a week, on average; the month's figure is worked out from it."""

    def test_the_months_figure_comes_from_the_weekly_target(self):
        settings = Settings(default_target_hours_per_week=3.5)
        december = Schedule(h.month(), h.people(), settings)
        self.assertEqual((december.weeks_in_month(), december.week_target("ana")), (31 / 7, 3.5))
        self.assertEqual((december.target("ana"), december.usual_target()), (15.5, 15.5))  # 3.5 × 31 / 7
        february = Schedule(create_month(h.catalog(), 2027, 2), h.people(), settings)
        self.assertEqual((february.weeks_in_month(), february.target("ana"), february.usual_target()), (4.0, 14.0, 14.0))
        # Someone's own target, else the usual one.
        people = h.people()
        people[0].target_hours_per_week = 1.5
        people[1].target_hours_per_week = 0
        mine = Schedule(h.month(), people, settings)
        self.assertEqual((mine.week_target("ana"), mine.week_target("ben"), mine.week_target("cleo")), (1.5, 0, 3.5))
        self.assertAlmostEqual(mine.target("ana"), 1.5 * 31 / 7)
        self.assertEqual((mine.target("ben"), mine.target("cleo"), mine.usual_target()), (0, 15.5, 15.5))

    def test_time_away_is_not_counted_when_the_setting_says_so(self):
        month = h.month()
        h.away(month, "ana", *[f"2026-12-{d:02d}/{p}" for d in range(1, 16) for p in ("morning", "afternoon", "evening")])
        on = Schedule(month, h.people(), Settings(default_target_hours_per_week=3.5))
        self.assertAlmostEqual(on.weeks_counted("ana"), 16 / 7)     # 16 days here
        self.assertAlmostEqual(on.target("ana"), 3.5 * 16 / 7)
        self.assertEqual(on.week_target("ana"), 3.5)                 # the weekly target itself is never lowered
        self.assertEqual((on.weeks_counted("ben"), on.target("ben"), on.usual_target()), (31 / 7, 15.5, 15.5))
        off = Schedule(month, h.people(), Settings(default_target_hours_per_week=3.5, prorate_target=False))
        self.assertEqual((off.weeks_counted("ana"), off.target("ana"), off.week_target("ana")), (31 / 7, 15.5, 3.5))

    def test_a_monthly_target_said_as_hours_a_week(self):
        self.assertEqual(WEEKS_A_MONTH, 52 / 12)
        for monthly, weekly in ((6, 1.5), (10, 2.25), (12, 2.75), (15, 3.5), (20, 4.5), (0.5, 0.25), (15.0, 3.5),
                                (0, 0.0), (-2, 0.0)):
            self.assertEqual(weekly_from_monthly(monthly), weekly, monthly)
        for strange in (None, "15", "lots", True, False, float("nan"), float("inf"), [15], {"h": 15},
                        10 ** 400, -10 ** 400):   # a whole number no float can hold: not a target, and no error
            self.assertIsNone(weekly_from_monthly(strange), str(strange)[:20])

    def test_older_files_are_read_as_hours_a_week(self):
        older = {"id": "ana", "name": "Ana B.", "target_hours": 15, "max_hours": 20}
        given = copy.deepcopy(older)
        ana = Person.from_dict(given)
        self.assertEqual((ana.target_hours_per_week, ana.max_hours), (3.5, 20))   # "at most a month" stays as it is
        self.assertEqual(given, older)                                            # the dict given is never changed
        self.assertNotIn("target_hours", ana.to_dict())
        self.assertEqual(ana.to_dict()["target_hours_per_week"], 3.5)
        # The weekly key, when there, wins; also when it says "the usual target".
        self.assertEqual(Person.from_dict(dict(older, target_hours_per_week=2.0)).target_hours_per_week, 2.0)
        self.assertIsNone(Person.from_dict(dict(older, target_hours_per_week=None)).target_hours_per_week)
        # A value that is not a number: the usual target, and nothing raises.
        for strange in ("lots", None, True, [6], float("nan")):
            self.assertIsNone(Person.from_dict(dict(older, target_hours=strange)).target_hours_per_week, strange)
        self.assertIsNone(Person.from_dict({"id": "ben", "name": "Ben C."}).target_hours_per_week)

        settings = {"centre_name": "Demo centre", "default_target_hours": 12, "prorate_target": False}
        given = copy.deepcopy(settings)
        read = Settings.from_dict(given)
        self.assertEqual((read.default_target_hours_per_week, read.prorate_target), (2.75, False))
        self.assertEqual(given, settings)
        self.assertNotIn("default_target_hours", read.to_dict())
        self.assertEqual(read.to_dict()["default_target_hours_per_week"], 2.75)
        both = Settings.from_dict(dict(settings, default_target_hours_per_week=3.25))
        self.assertEqual(both.default_target_hours_per_week, 3.25)
        for strange in ("lots", None, True, float("inf")):
            self.assertEqual(Settings.from_dict(dict(settings, default_target_hours=strange)).default_target_hours_per_week,
                             Settings().default_target_hours_per_week, strange)
        self.assertEqual(Settings.from_dict({"default_target_hours": 0}).default_target_hours_per_week, 0.0)
        # An empty weekly key is never a usual target: the older key if there is one, else the default.
        given = {"default_target_hours_per_week": None, "default_target_hours": 15}
        self.assertEqual(Settings.from_dict(given).default_target_hours_per_week, 3.5)
        self.assertEqual(given, {"default_target_hours_per_week": None, "default_target_hours": 15})
        self.assertEqual(Settings.from_dict({"default_target_hours_per_week": None}).default_target_hours_per_week, 2.75)
        self.assertEqual(Settings().default_target_hours_per_week, 2.75)   # the former 12 h a month


def all_month(month, person_id):
    """Every part of every day of the month marked away in this person's reply."""
    h.away(month, person_id, *[f"{iso}/{part}" for iso in month.dates() for part in ("morning", "afternoon", "evening")])


class FiguresTest(unittest.TestCase):
    """What the screens show about someone's hours against their target, all made here."""

    def setUp(self):
        self.month = h.month()
        # Ana: two lunches (4 h) and two suppers (3 h): 14 h in December.
        self.month.assignments = [Assignment("lunch", "2026-12-01", 0, person_id="ana"),
                                  Assignment("lunch", "2026-12-02", 0, person_id="ana"),
                                  Assignment("supper", "2026-12-03", 0, person_id="ana"),
                                  Assignment("supper", "2026-12-04", 0, person_id="ana"),
                                  Assignment("breakfast", "2026-12-05", 0, person_id="ben")]
        self.settings = Settings(default_target_hours_per_week=3.5)

    def test_figures_and_words(self):
        s = Schedule(self.month, h.people(), self.settings)
        self.assertEqual(workload.summary(s, "ana"), {
            "hours": 14.0, "target": 15.5, "week_hours": 3.2, "week_target": 3.5, "ratio": 0.9032, "bar": "",
            "week_text": "3.2 / 3.5 h a week", "month_text": "14 h in December", "month_detail": "14 of 15.5 h in December",
        })
        self.assertEqual(workload.reason(s, "ana", "2026-12-04"), "3.2 of 3.5 h a week · 14 h that week · 14 h in December")
        ben = workload.summary(s, "ben")   # 2 h: below half of the target
        self.assertEqual((ben["week_text"], ben["bar"], ben["month_detail"]),
                         ("0.5 / 3.5 h a week", "under", "2 of 15.5 h in December"))
        self.assertEqual(workload.summary(s, "cleo")["week_text"], "0 / 3.5 h a week")
        self.assertAlmostEqual(s.week_average("ana"), 14 * 7 / 31)
        self.assertAlmostEqual(s.week_average("ana", hours=7), 7 * 7 / 31)
        # Past the target: amber; exactly on it, not.
        self.month.assignments.append(Assignment("lunch", "2026-12-08", 0, person_id="ana"))
        self.assertEqual(workload.summary(Schedule(self.month, h.people(), self.settings), "ana")["bar"], "over")
        february = create_month(h.catalog(), 2027, 2)   # 3.5 h a week makes exactly 14 h in 28 days
        february.assignments = [Assignment("lunch", f"2027-02-0{d}", 0, person_id="ana") for d in (1, 2, 3)] \
            + [Assignment("breakfast", "2027-02-04", 0, person_id="ana")]
        on_target = workload.summary(Schedule(february, h.people(), self.settings), "ana")
        self.assertEqual((on_target["ratio"], on_target["bar"], on_target["week_text"], on_target["month_text"]),
                         (1.0, "", "3.5 / 3.5 h a week", "14 h in February"))

    def test_numbers_as_people_write_them(self):
        self.assertEqual([workload.number(v) for v in (3.25, 14.0, 2.75, 15.5, 0, 14.254)],
                         ["3.25", "14", "2.75", "15.5", "0", "14.25"])
        self.assertEqual([workload.number(v, 1) for v in (3.16, -0.001, -0.0, 4.04)], ["3.2", "0", "0", "4"])

    def test_a_target_of_zero(self):
        people = h.people()
        people[0].target_hours_per_week = 0
        s = Schedule(self.month, people, self.settings)
        ana = workload.summary(s, "ana")
        self.assertEqual((ana["ratio"], ana["bar"], ana["target"], ana["week_text"], ana["month_detail"]),
                         (0.0, "under", 0.0, "3.2 / 0 h a week", "14 h in December"))
        self.assertEqual(workload.over_target_text(s, "ana", 18), "Over target: would reach 4.1 h a week")

    def test_away_all_month(self):
        all_month(self.month, "ana")   # her shifts were placed before her reply: she keeps her 14 h
        for around in ((), [create_month(h.catalog(), 2026, 11)]):
            s = Schedule(self.month, h.people(), self.settings, around)
            self.assertEqual((s.weeks_counted("ana"), s.target("ana"), s.week_average("ana")), (0.0, 0.0, 0.0))
            ana = workload.summary(s, "ana")
            self.assertEqual((ana["week_text"], ana["week_hours"], ana["ratio"], ana["bar"], ana["month_detail"]),
                             ("Away all month", 0.0, 0.0, "under", "14 h in December"))   # no figure to be "of"
            self.assertEqual(workload.reason(s, "ana", "2026-12-01"), "Away all month · 14 h that week · 14 h in December")
            self.assertEqual(workload.over_target_text(s, "ana", 18), "Over target: away all month")
            self.assertEqual(workload.week_change(s, "ana", 14, 10), "away all month")
        # Without the setting, the time away counts: an average like anyone's.
        counted = Schedule(self.month, h.people(), Settings(default_target_hours_per_week=3.5, prorate_target=False))
        self.assertEqual(workload.summary(counted, "ana")["week_text"], "3.2 / 3.5 h a week")
        # Nobody's hours at all, away all month: nothing to say, nothing raises.
        empty = h.month()
        all_month(empty, "ben")
        self.assertEqual(workload.summary(h.schedule(empty), "ben")["week_text"], "Away all month")

    def test_the_month_view_and_an_import_of_such_a_month(self):
        all_month(self.month, "ana")
        people = h.people()
        people[1].target_hours_per_week = 0
        shown = view.month_view(self.month, h.catalog(), people, self.settings)
        ana = next(p for p in shown["people"] if p["id"] == "ana")
        self.assertEqual((ana["week_text"], ana["hours"], ana["presence"]), ("Away all month", 14.0, 0.0))
        self.assertEqual(next(p for p in shown["people"] if p["id"] == "ben")["bar"], "under")
        candidates = view.candidates_view(self.month, people, self.settings, "lunch", "2026-12-09", 0)["candidates"]
        self.assertEqual(next(c for c in candidates if c["id"] == "ana")["week_text"], "Away all month")
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder))
            store.save_catalog(h.catalog())
            store.save_people(people)
            store.save_settings(self.settings)
            store.save_month(self.month)
            documents = transfer.checked(transfer._documents(store))
        self.assertIn("months/2026-12.json", documents)



def index(schedule):
    """The hours a week a schedule keeps, without the weeks that came back to nothing."""
    return {key: round(value, 6) for key, value in schedule._week_hours.items() if abs(value) > 1e-9}


def lunch(iso, person_id="ana"):
    return Assignment("lunch", iso, 0, person_id=person_id)


class WeekHoursTest(unittest.TestCase):
    """Hours per calendar week (Monday to Sunday), kept by the schedule."""

    def setUp(self):
        self.settings = Settings(default_target_hours_per_week=3.5)
        self.november = create_month(h.catalog(), 2026, 11)
        self.november.assignments = [lunch("2026-11-30"), lunch("2026-11-23")]   # Monday 30: December's first week
        self.december = h.month()
        self.december.assignments = [Assignment("supper", "2026-12-01", 0, person_id="ana")]

    def test_a_week_is_whole_across_two_months(self):
        both = Schedule(self.december, h.people(), self.settings, [self.november])
        self.assertEqual((both.hours_in_week("ana", "2026-12-01"), both.hours_in_week("ana", "2026-11-30")), (7.0, 7.0))
        self.assertEqual((both.hours("ana"), both.shifts_in_week("ana", "2026-12-06")), (3.0, 2))  # the month: its own
        self.assertEqual(both.hours_in_week("ana", "2026-12-07"), 0.0)
        alone = Schedule(self.december, h.people(), self.settings)
        self.assertEqual((alone.hours_in_week("ana", "2026-12-01"), alone.hours("ana")), (3.0, 3.0))
        self.assertEqual(alone.hours_in_week("nobody", "2026-12-01"), 0.0)

    def test_the_other_months_own_task(self):
        # November's lunch lasts longer: its own hours count; once it no longer has the task, nothing.
        self.november.tasks = [t for t in self.november.tasks if t.id != "lunch"] \
            + [Task("lunch", "kitchen", "Lunch cook", start="09:00", end="15:00", hours=6, headcount={"default": 1})]
        longer = Schedule(self.december, h.people(), self.settings, [self.november])
        self.assertEqual(longer.hours_in_week("ana", "2026-12-01"), 9.0)
        self.november.tasks = [t for t in self.november.tasks if t.id != "lunch"]
        gone = Schedule(self.december, h.people(), self.settings, [self.november])
        self.assertEqual((gone.hours_in_week("ana", "2026-12-01"), gone.shifts_in_week("ana", "2026-12-01")), (3.0, 1))

    def test_add_then_remove_leaves_it_as_it_was(self):
        s = Schedule(self.december, h.people(), self.settings, [self.november])
        before = index(s)
        extra = [lunch("2026-12-02"), Assignment("helper", "2026-12-05", 1, person_id="ana"), lunch("2026-12-31", "ben")]
        for assignment in extra:
            s.add(assignment)
        self.assertEqual((s.hours_in_week("ana", "2026-12-03"), s.hours_in_week("ben", "2026-12-28")), (14.0, 4.0))
        for assignment in extra:
            s.remove(assignment)
        self.assertEqual(index(s), before)

    def test_after_suggestions_the_index_is_that_of_a_fresh_schedule(self):
        rng = random.Random(0)
        catalog = demo.build_catalog()
        people = demo.build_people(catalog, rng)
        catalog.regulars = demo.build_regulars(catalog, people, rng)
        november, december = create_month(catalog, 2026, 11), create_month(catalog, 2026, 12)
        apply_regulars(november, catalog, people, Settings())
        apply_regulars(december, catalog, people, Settings(), around=[november])
        made = []

        class Kept(Schedule):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                made.append(self)

        with mock.patch.object(solver, "Schedule", Kept):
            proposals, _, _ = solver.prefill(december, people, Settings(), seconds=0.2, around=[november])
        self.assertGreater(len(proposals), 50)
        used = made[0]   # the solver's own, after thousands of tries and undos
        december.assignments = used.all_assignments()
        fresh = Schedule(december, people, Settings(), [november])
        self.assertEqual(index(used), index(fresh))
        self.assertTrue(any(key[1] == "2026-11-30" for key in index(fresh)))   # November's Monday is in it

    def test_the_weeks_of_a_month(self):
        alone = Schedule(self.december, h.people(), self.settings)
        self.assertEqual([len(days) for days in alone.calendar_weeks()], [6, 7, 7, 7, 4])
        self.assertEqual(alone.calendar_weeks()[0][0], "2026-12-01")
        weeks = workload.weeks(alone)
        self.assertEqual([w["label"] for w in weeks], ["1–6", "7–13", "14–20", "21–27", "28–31"])
        self.assertEqual([w["short"] for w in weeks], ["1–6", "7–13", "14–20", "21–27", "28–31"])
        self.assertEqual([w["cut"] for w in weeks], [True, False, False, False, True])
        self.assertEqual([w["start"] for w in weeks], ["2026-11-30", "2026-12-07", "2026-12-14", "2026-12-21", "2026-12-28"])
        self.assertEqual(weeks[1]["title"], "Monday 7 to Sunday 13 December")
        self.assertEqual(weeks[0]["title"], "Tuesday 1 to Sunday 6 December. The week began in November, which has no "
                                            "rota yet: those days are not counted.")
        self.assertEqual(weeks[4]["title"], "Monday 28 to Thursday 31 December. The week goes on in January, which has "
                                            "no rota yet: those days are not counted.")

        january = create_month(h.catalog(), 2027, 1)
        both = workload.weeks(Schedule(self.december, h.people(), self.settings, [self.november, january]))
        self.assertEqual(both[0], {"start": "2026-11-30", "label": "30 Nov–6 Dec", "short": "30–6",
                                   "title": "Monday 30 November to Sunday 6 December", "cut": False})
        self.assertEqual((both[4]["label"], both[4]["short"], both[4]["title"], both[4]["cut"]),
                         ("28 Dec–3 Jan", "28–3", "Monday 28 December to Sunday 3 January", False))
        self.assertEqual([w["cut"] for w in both], [False] * 5)
        before = workload.weeks(Schedule(self.december, h.people(), self.settings, [self.november]))
        self.assertEqual(([w["cut"] for w in before], before[4]["label"]), ([False, False, False, False, True], "28–31"))

        # February 2027: four whole weeks, Monday 1 to Sunday 28, with or without the months next door.
        february = create_month(h.catalog(), 2027, 2)
        january.assignments = [lunch("2027-01-31")]   # the Sunday before: a week February does not touch
        for around in ((), [january, create_month(h.catalog(), 2027, 3)]):
            s = Schedule(february, h.people(), self.settings, around)
            weeks = workload.weeks(s)
            self.assertEqual([w["label"] for w in weeks], ["1–7", "8–14", "15–21", "22–28"])
            self.assertEqual([w["cut"] for w in weeks], [False] * 4)
            self.assertEqual(len(workload.by_week(s, "ana")), 4)

    def test_a_week_far_above_the_target(self):
        catalog = h.catalog()
        catalog.tasks.append(Task("long", "kitchen", "Long day", start="08:00", end="17:00", hours=9,
                                  headcount={"default": 1}))
        month = h.month(catalog)
        month.assignments = [
            lunch("2026-12-01"), Assignment("breakfast", "2026-12-02", 0, person_id="ana"),   # 6 h
            lunch("2026-12-07"), Assignment("supper", "2026-12-08", 0, person_id="ana"),      # 7 h: exactly twice
            lunch("2026-12-14"), lunch("2026-12-15"),                                         # 8 h in two shifts
            Assignment("long", "2026-12-21", 0, person_id="ana"),                             # 9 h in one shift
            Assignment("breakfast", "2026-12-28", 0, person_id="ana"),                        # 2 h
        ]
        weeks = workload.by_week(Schedule(month, h.people(), self.settings), "ana")
        self.assertEqual([(w["hours"], w["level"], w["far"]) for w in weeks],
                         [(6.0, 2, False), (7.0, 2, False), (8.0, 3, True), (9.0, 2, False), (2.0, 1, False)])
        self.assertEqual(["text" in w for w in weeks], [False, False, True, False, False])
        self.assertEqual(weeks[2]["text"], "Week of Monday 14 to Sunday 20 December: 8 h, more than twice their target "
                                           "(3.5 h a week)")
        self.assertEqual(weeks[2]["that_week_text"], "8 h that week")
        # One line under the person's weeks says what the mark means; nobody marked, no line.
        s = Schedule(month, h.people(), self.settings)
        self.assertEqual(workload.far_note(s, "ana"), "Marked: more than twice their target (3.5 h a week).")
        self.assertEqual((workload.far_note(s, "ana", weeks), workload.far_note(s, "cleo")),
                         ("Marked: more than twice their target (3.5 h a week).", ""))
        shown = view.month_view(month, catalog, h.people(), self.settings)["people"]
        self.assertEqual([p["far_note"] != "" for p in shown if p["id"] in ("ana", "ben")], [True, False])
        self.assertEqual(workload.by_week(Schedule(month, h.people(), self.settings), "cleo")[0],
                         {"hours": 0.0, "level": 0, "far": False, "that_week_text": "Free that week"})
        # A target of 0: never far; any hours are "up to the target".
        people = h.people()
        people[0].target_hours_per_week = 0
        zero = workload.by_week(Schedule(month, people, self.settings), "ana")
        self.assertEqual([(w["level"], w["far"]) for w in zero], [(1, False)] * 5)
        # Every week in words (for Check), the part of another month said apart; a cut week by its days that count.
        detailed = workload.by_week(Schedule(month, h.people(), self.settings, [self.november]), "ana", detailed=True)
        self.assertEqual([w["text"] for w in detailed], [
            "Week of Monday 30 November to Sunday 6 December: 10 h, 4 h of them in November, more than twice their "
            "target (3.5 h a week)",
            "Week of Monday 7 to Sunday 13 December: 7 h",
            "Week of Monday 14 to Sunday 20 December: 8 h, more than twice their target (3.5 h a week)",
            "Week of Monday 21 to Sunday 27 December: 9 h",
            "Week of Monday 28 to Thursday 31 December: 2 h",
        ])
        self.assertEqual(workload.by_week(Schedule(month, h.people(), self.settings), "cleo", detailed=True)[0]["text"],
                         "Week of Tuesday 1 to Sunday 6 December: 0 h")

    def test_that_week(self):
        month = h.month()
        month.assignments = [Assignment("breakfast", "2026-12-08", 0, person_id="ana"),
                             Assignment("night", "2026-12-15", 0, person_id="ben")]   # a night counts 0 h
        s = Schedule(month, h.people(), self.settings)
        self.assertEqual(workload.that_week(s, "ana", "2026-12-13"),
                         {"that_week": 2.0, "that_week_text": "2 h that week", "free_week": False})
        self.assertEqual(workload.that_week(s, "ana", "2026-12-14"),
                         {"that_week": 0.0, "that_week_text": "Free that week", "free_week": True})
        self.assertEqual(workload.that_week(s, "ben", "2026-12-15"),
                         {"that_week": 0.0, "that_week_text": "0 h that week", "free_week": False})
        # A suggestion's own line, with the hours of the week of its shift.
        self.assertEqual(workload.reason(s, "ana", "2026-12-08"), "0.5 of 3.5 h a week · 2 h that week · 2 h in December")

    def test_the_month_view_names_the_weeks(self):
        self.december.assignments.append(Assignment("breakfast", "2026-12-14", 0, person_id="ben"))
        shown = view.month_view(self.december, h.catalog(), h.people(), self.settings, around=[self.november])
        weeks = shown["calendar_weeks"]
        self.assertEqual([w["short"] for w in weeks], ["30–6", "7–13", "14–20", "21–27", "28–31"])
        self.assertTrue(all(len(p["by_week"]) == len(weeks) for p in shown["people"]))
        self.assertTrue(all(weeks[day["week"]]["start"] == week_start(day["date"]) for day in shown["days"]))
        self.assertEqual([d["week"] for d in shown["days"]][:8], [0] * 6 + [1] * 2)
        ana = next(p for p in shown["people"] if p["id"] == "ana")
        self.assertEqual(ana["by_week"][0], {"hours": 7.0, "level": 2, "far": False, "that_week_text": "7 h that week"})
        candidates = view.candidates_view(self.december, h.people(), self.settings, "lunch", "2026-12-15", 0,
                                          around=[self.november])["candidates"]
        by_id = {c["id"]: c for c in candidates}
        # The list for a shift: the hours of the shift's week, and of the month, with this shift's 4 h.
        self.assertEqual(by_id["ben"]["load"]["week"]["figures"], [["was", "2 → "], ["after-over", "6"], ["of", " of 3.5 h"]])
        self.assertEqual(by_id["ana"]["load"]["week"]["figures"], [["was", "0 → "], ["after-over", "4"], ["of", " of 3.5 h"]])
        self.assertEqual(by_id["ana"]["load"]["month"]["figures"], [["was", "3 → "], ["after", "7"], ["of", " of 15.5 h"]])
        self.assertTrue(all(not {"that_week", "that_week_text", "free_week"} & set(c) for c in candidates))
        # On the first week, shared with November: whole, and Ana already holds the supper of the 1st (3 h):
        # she is the person on the shift, above the list, with this shift drawn lighter.
        first = view.candidates_view(self.december, h.people(), self.settings, "supper", "2026-12-01", 0,
                                     around=[self.november])
        self.assertNotIn("ana", [c["id"] for c in first["candidates"]])
        ana = first["current_person"]["load"]
        self.assertEqual((ana["week"]["figures"], ana["month"]["figures"]),
                         ([["after-over", "7"], ["of", " of 3.5 h"]], [["after", "3"], ["of", " of 15.5 h"]]))
        self.assertEqual(ana["week"]["segments"], [["now", 0.0, 66.67], ["now-over", 66.67, 76.19],
                                                   ["add-over", 76.19, 100.0]])
        self.assertEqual(ana["month"]["segments"], [["add", 0.0, 12.9]])
        self.assertEqual(ana["title"], "Week of Monday 30 November to Sunday 6 December: 7 h, 3 h of them this shift, "
                                       "for a target of 3.5 h a week. December: 3 h, 3 h of them this shift, of 15.5 h.")


class ChoiceTest(unittest.TestCase):
    """The list for a shift: the shift's week and the month, so far and with this shift, against the target."""

    def setUp(self):
        self.settings = Settings(default_target_hours_per_week=3.5)
        self.month = h.month()
        # Ana: 4 h the week before, 2 h in the week of the shift (Monday 7 to Sunday 13), 3 h the week after.
        self.month.assignments = [lunch("2026-12-01"), Assignment("breakfast", "2026-12-08", 0, person_id="ana"),
                                  Assignment("supper", "2026-12-15", 0, person_id="ana")]

    def schedule(self, people=None, around=()):
        return Schedule(self.month, people or h.people(), self.settings, around)

    def test_the_two_lines(self):
        load = workload.choice(self.schedule(), "ana", "2026-12-10", 4)   # a lunch on Thursday 10: 4 h
        self.assertEqual(load["week"], {
            "label": "Week",
            "segments": [["now", 0.0, 38.1], ["add", 38.1, 66.67], ["add-over", 66.67, 100.0]],   # 6 h is cut at 5.25 h
            "target_at": 66.67,
            "figures": [["was", "2 → "], ["after-over", "6"], ["of", " of 3.5 h"]],
        })
        self.assertEqual(load["month"], {
            "label": "Dec",
            "segments": [["now", 0.0, 38.71], ["add", 38.71, 55.91]],   # 9 → 13 of 15.5 h: below the target
            "target_at": 66.67,
            "figures": [["was", "9 → "], ["after", "13"], ["of", " of 15.5 h"]],
        })
        self.assertEqual(load["title"], "Week of Monday 7 to Sunday 13 December: 2 h so far, 6 h with this shift, for a "
                                        "target of 3.5 h a week. December: 9 h so far, 13 h with this shift, of 15.5 h.")

    def test_the_months_target_is_shown_as_precisely_as_the_hours(self):
        # Rounded to one decimal, 9.96 h would read "10" next to an amber "10", and 23.25 h "23.2"
        # next to a plain "23.25": the colour and the numbers would disagree.
        people = h.people()
        people[0].target_hours_per_week = 2.25            # 9.96 h in December
        month = self.schedule(people)                     # Ana: 9 h this month
        self.assertEqual(workload.choice(month, "ana", "2026-12-10", 1)["month"]["figures"],
                         [["was", "9 → "], ["after-over", "10"], ["of", " of 9.96 h"]])
        people[0].target_hours_per_week = 5.25            # exactly 23.25 h in December
        figures = workload.choice(self.schedule(people), "ana", "2026-12-10", 14.25)["month"]["figures"]
        self.assertEqual(figures, [["was", "9 → "], ["after", "23.25"], ["of", " of 23.25 h"]])

    def test_a_week_cut_to_one_day_is_named_by_that_day(self):
        november = create_month(h.catalog(), 2026, 11)    # begins on a Sunday, ends on a Monday
        weeks = workload.weeks(Schedule(november, h.people(), self.settings))
        self.assertEqual((weeks[0]["title"].split(".")[0], weeks[-1]["title"].split(".")[0]),
                         ("Sunday 1 November", "Monday 30 November"))

    def test_a_shift_that_adds_nothing(self):
        # A task counted 0 h (the night): no arrow, nothing light.
        s = self.schedule()
        for added in (0, 0.0):
            load = workload.choice(s, "ana", "2026-12-08", added)
            self.assertEqual(load["week"]["figures"], [["after", "2"], ["of", " of 3.5 h"]])
            self.assertEqual(load["week"]["segments"], [["now", 0.0, 38.1]])
            self.assertEqual(load["month"]["figures"], [["after", "9"], ["of", " of 15.5 h"]])
            self.assertFalse(any(kind.startswith("add") for line in ("week", "month")
                                 for kind, _, _ in load[line]["segments"]))
            self.assertEqual(load["title"], "Week of Monday 7 to Sunday 13 December: 2 h so far, for a target of 3.5 h "
                                            "a week. December: 9 h so far, of 15.5 h.")
        # The holder of the place is above the list, with their total; the night guard's 0 h add nothing to anyone.
        self.month.assignments.append(Assignment("night", "2026-12-09", 0, person_id="ben"))
        shown = view.candidates_view(self.month, h.people(), self.settings, "breakfast", "2026-12-08", 0)
        self.assertNotIn("ana", [c["id"] for c in shown["candidates"]])
        self.assertEqual(shown["current_person"]["load"]["week"]["figures"], [["after", "2"], ["of", " of 3.5 h"]])
        self.assertEqual(next(c for c in shown["candidates"] if c["id"] == "ben")["load"]["week"]["figures"],
                         [["was", "0 → "], ["after", "2"], ["of", " of 3.5 h"]])
        night = view.candidates_view(self.month, h.people(), self.settings, "night", "2026-12-10", 0)["candidates"]
        self.assertTrue(all(c["load"]["week"]["figures"][0][0] != "was" for c in night))

    def test_past_the_target(self):
        self.month.assignments.append(lunch("2026-12-09"))   # 6 h in the week: already past 3.5 h
        load = workload.choice(self.schedule(), "ana", "2026-12-11", 2)
        self.assertEqual(load["week"]["segments"], [["now", 0.0, 66.67], ["now-over", 66.67, 100.0]])   # 8 h: cut
        self.assertEqual(load["week"]["figures"], [["was", "6 → "], ["after-over", "8"], ["of", " of 3.5 h"]])
        self.month.assignments[-1] = Assignment("breakfast", "2026-12-09", 0, person_id="ana")   # 4 h in the week
        self.month.assignments += [lunch("2026-12-02"), lunch("2026-12-03")]                       # 19 h in December
        load = workload.choice(self.schedule(), "ana", "2026-12-10", 3)   # 4 → 7 of 3.5 h a week
        self.assertEqual(load["week"]["segments"], [["now", 0.0, 66.67], ["now-over", 66.67, 76.19],
                                                    ["add-over", 76.19, 100.0]])
        self.assertEqual(load["week"]["figures"], [["was", "4 → "], ["after-over", "7"], ["of", " of 3.5 h"]])
        # The month: 19 h, past 15.5 h; with 3 h more, 22 h.
        self.assertEqual(load["month"]["segments"], [["now", 0.0, 66.67], ["now-over", 66.67, 81.72],
                                                     ["add-over", 81.72, 94.62]])
        self.assertEqual(load["month"]["figures"], [["was", "19 → "], ["after-over", "22"], ["of", " of 15.5 h"]])
        # Exactly on the target is not past it.
        on = workload.choice(self.schedule(), "cleo", "2026-12-10", 3.5)
        self.assertEqual(on["week"]["segments"], [["add", 0.0, 66.67]])
        self.assertEqual(on["week"]["figures"], [["was", "0 → "], ["after", "3.5"], ["of", " of 3.5 h"]])

    def test_far_past_the_target_is_cut_at_the_end(self):
        self.month.assignments += [lunch(f"2026-12-{d:02d}") for d in (2, 3, 4, 5, 6)]   # 24 h in the first week
        load = workload.choice(self.schedule(), "ana", "2026-12-04", 4)
        self.assertEqual(load["week"]["segments"], [["now", 0.0, 66.67], ["now-over", 66.67, 100.0]])
        self.assertTrue(all(0 <= start < end <= 100 for line in ("week", "month")
                            for _, start, end in load[line]["segments"]))

    def test_a_target_of_zero(self):
        people = h.people()
        people[0].target_hours_per_week = 0
        load = workload.choice(self.schedule(people), "ana", "2026-12-10", 4)
        # No line; from 0 to the hours with the shift; all of it past the target.
        self.assertEqual(load["week"], {"label": "Week", "segments": [["now-over", 0.0, 33.33], ["add-over", 33.33, 100.0]],
                                        "target_at": None,
                                        "figures": [["was", "2 → "], ["after-over", "6"], ["of", " h"]]})
        self.assertEqual(load["month"]["figures"], [["was", "9 → "], ["after-over", "13"], ["of", " h"]])
        self.assertTrue(load["title"].endswith("for a target of 0 h a week. December: 9 h so far, 13 h with this shift, "
                                               "for a target of 0 h."))
        # Nobody's hours, nothing added: an empty bar, nothing divided by zero.
        cleo = workload.choice(Schedule(self.month, [Person("cleo", "Cleo D.", target_hours_per_week=0)], self.settings),
                               "cleo", "2026-12-10", 0)
        self.assertEqual((cleo["week"]["segments"], cleo["week"]["figures"]), ([], [["after", "0"], ["of", " h"]]))

    def test_away_all_month_and_a_cut_week(self):
        all_month(self.month, "ana")
        for around in ((), [create_month(h.catalog(), 2026, 11)]):
            load = workload.choice(self.schedule(around=around), "ana", "2026-12-01", 4)
            self.assertEqual(load["month"]["target_at"], None)   # the month's figure: 0 when away all month
            self.assertEqual(load["week"]["target_at"], 66.67)   # the weekly target is never lowered
            self.assertIn("December: 9 h so far, 13 h with this shift; away all month, so no target.", load["title"])
        cut = workload.choice(self.schedule(), "ana", "2026-12-01", 4)["title"]
        self.assertTrue(cut.startswith("Week of Tuesday 1 to Sunday 6 December: 4 h so far, 8 h with this shift, for "
                                       "a target of 3.5 h a week. The week began in November, which has no rota yet: "
                                       "those days are not counted. December:"))
        candidates = view.candidates_view(self.month, h.people(), self.settings, "lunch", "2026-12-09", 0)["candidates"]
        self.assertEqual(next(c for c in candidates if c["id"] == "ana")["away"]["text"], "Away 1–31 Dec")

    def test_the_person_on_the_shift(self):
        # Ana holds the breakfast of Tuesday 8 (2 h): so far without it, then the shift lighter; the total, no arrow.
        load = workload.choice(self.schedule(), "ana", "2026-12-08", 2, held=True)
        self.assertEqual(load["week"], {
            "label": "Week",
            "segments": [["add", 0.0, 38.1]],
            "target_at": 66.67,
            "figures": [["after", "2"], ["of", " of 3.5 h"]],
        })
        self.assertEqual(load["month"]["segments"], [["now", 0.0, 30.11], ["add", 30.11, 38.71]])
        self.assertEqual(load["month"]["figures"], [["after", "9"], ["of", " of 15.5 h"]])
        self.assertEqual(load["title"], "Week of Monday 7 to Sunday 13 December: 2 h, 2 h of them this shift, for a "
                                        "target of 3.5 h a week. December: 9 h, 2 h of them this shift, of 15.5 h.")
        # Past the target with the shift: amber total, the shift's part amber too.
        self.month.assignments.append(lunch("2026-12-09"))   # 6 h in the week, 13 h in December
        load = workload.choice(self.schedule(), "ana", "2026-12-08", 2, held=True)
        self.assertEqual(load["week"]["segments"], [["now", 0.0, 66.67], ["now-over", 66.67, 76.19],
                                                    ["add-over", 76.19, 100.0]])
        self.assertEqual(load["week"]["figures"], [["after-over", "6"], ["of", " of 3.5 h"]])
        self.assertEqual(load["month"]["figures"], [["after", "13"], ["of", " of 15.5 h"]])
        self.assertTrue(load["title"].startswith("Week of Monday 7 to Sunday 13 December: 6 h, 2 h of them this shift, "))
        # On a cut week (November has no rota): the same words as for anyone.
        cut = workload.choice(self.schedule(), "ana", "2026-12-01", 4, held=True)
        self.assertEqual(cut["week"]["figures"], [["after-over", "4"], ["of", " of 3.5 h"]])
        self.assertTrue(cut["title"].startswith("Week of Tuesday 1 to Sunday 6 December: 4 h, 4 h of them this shift, "
                                                "for a target of 3.5 h a week. The week began in November, which has "
                                                "no rota yet: those days are not counted. December: 13 h, 4 h of them "
                                                "this shift, of 15.5 h."))

    def test_the_person_on_a_shift_of_no_hours_or_with_no_target(self):
        # A night counted 0 h: nothing light, and the title does not speak of the shift.
        self.month.assignments.append(Assignment("night", "2026-12-09", 0, person_id="ana"))
        night = workload.choice(self.schedule(), "ana", "2026-12-09", 0, held=True)
        self.assertEqual((night["week"]["segments"], night["week"]["figures"]),
                         ([["now", 0.0, 38.1]], [["after", "2"], ["of", " of 3.5 h"]]))
        self.assertEqual(night["title"], "Week of Monday 7 to Sunday 13 December: 2 h, for a target of 3.5 h a week. "
                                         "December: 9 h, of 15.5 h.")
        # A target of 0: no line, all of it past the target, nothing divided by zero.
        people = h.people()
        people[0].target_hours_per_week = 0
        load = workload.choice(self.schedule(people), "ana", "2026-12-08", 2, held=True)
        self.assertEqual(load["week"], {"label": "Week", "segments": [["add-over", 0.0, 100.0]], "target_at": None,
                                        "figures": [["after-over", "2"], ["of", " h"]]})
        self.assertEqual(load["month"]["segments"], [["now-over", 0.0, 77.78], ["add-over", 77.78, 100.0]])
        self.assertEqual(load["month"]["figures"], [["after-over", "9"], ["of", " h"]])
        self.assertTrue(load["title"].endswith("for a target of 0 h a week. December: 9 h, 2 h of them this shift, "
                                               "for a target of 0 h."))
        zero = workload.choice(Schedule(self.month, [Person("cleo", "Cleo D.", target_hours_per_week=0)], self.settings),
                               "cleo", "2026-12-09", 0, held=True)
        self.assertEqual((zero["week"]["segments"], zero["week"]["figures"]), ([], [["after", "0"], ["of", " h"]]))
        # Hours that do not add up (more for the shift than they have): never below 0.
        odd = workload.choice(self.schedule(), "cleo", "2026-12-08", 2, held=True)
        self.assertEqual(odd["week"]["figures"], [["after", "2"], ["of", " of 3.5 h"]])

    def test_the_list_leaves_the_over_target_note_to_the_bars(self):
        self.month.assignments += [lunch(f"2026-12-{d:02d}") for d in (2, 3, 4)]   # 21 h: past 15.5 h
        candidates = view.candidates_view(self.month, h.people(), self.settings, "lunch", "2026-12-21", 0)["candidates"]
        ana = next(c for c in candidates if c["id"] == "ana")
        self.assertFalse(any("Over target" in f["message"] for c in candidates for f in c["findings"]))
        self.assertEqual(ana["load"]["month"]["figures"][1], ["after-over", "25"])
        s = self.schedule()   # it stays a rule everywhere else
        self.assertIn("over_target", [f.rule for f in rules.evaluate(rules.Placement(s, s.people["ana"],
                                                                                         s.task_on("lunch", "2026-12-21"),
                                                                                         "2026-12-21"))])


class AwayTest(unittest.TestCase):
    """Time away in the month, from the reply to the availability form, for the list for a shift."""

    def away(self, *keys):
        month = h.month()
        h.away(month, "ana", *keys)
        return workload.away(h.schedule(month), "ana")

    @staticmethod
    def days(first, last, parts=("morning", "afternoon", "evening")):
        return [f"2026-12-{d:02d}/{p}" for d in range(first, last + 1) for p in parts]

    def test_whole_days(self):
        self.assertEqual(self.away(*self.days(14, 20)),
                         {"level": "all", "text": "Away 14–20 Dec", "title": "Away all day 14–20 December"})
        self.assertEqual(self.away(*self.days(5, 5)),
                         {"level": "all", "text": "Away 5 Dec", "title": "Away all day 5 December"})
        self.assertEqual(self.away(*self.days(1, 4), *self.days(14, 20))["text"], "Away 1–4 and 14–20 Dec")

    def test_parts_of_days(self):
        self.assertEqual(self.away("2026-12-09/evening"),
                         {"level": "some", "text": "Away part of 9 Dec", "title": "Away the evening of 9 December"})
        self.assertEqual(self.away("2026-12-09/evening", "2026-12-12/morning", "2026-12-12/afternoon"),
                         {"level": "some", "text": "Away part of 2 days",   # several runs: counted, the title says which
                          "title": "Away the evening of 9 December; away the morning and afternoon of 12 December"})
        self.assertEqual(self.away(*self.days(9, 11, ("evening",))),
                         {"level": "some", "text": "Away part of 9–11 Dec", "title": "Away the evening of 9–11 December"})

    def test_both(self):
        self.assertEqual(self.away(*self.days(1, 4), "2026-12-09/evening"),
                         {"level": "all", "text": "Away 1–4 Dec, part of 9 Dec",
                          "title": "Away all day 1–4 December; away the evening of 9 December"})
        self.assertEqual(self.away(*self.days(1, 4), "2026-12-09/evening", "2026-12-17/evening")["text"],
                         "Away 1–4 Dec, part of 2 days")

    def test_many_days_are_counted(self):
        away = self.away(*self.days(1, 4), *self.days(10, 11), *self.days(20, 22), "2026-12-15/evening",
                         "2026-12-17/morning", "2026-12-28/morning")
        self.assertEqual((away["level"], away["text"]), ("all", "Away 9 days, part of 3 more"))
        self.assertEqual(away["title"], "Away all day 1–4 December; away all day 10–11 December; away the evening of "
                                        "15 December; away the morning of 17 December; away all day 20–22 December; "
                                        "away the morning of 28 December")
        only_parts = self.away(*[f"2026-12-{d:02d}/evening" for d in (1, 3, 5, 7, 9, 11)])
        self.assertEqual((only_parts["level"], only_parts["text"]), ("some", "Away part of 6 days"))
        self.assertEqual(self.away(*[f"2026-12-{d:02d}/{p}" for d in (1, 3, 5, 7) for p in ("morning", "afternoon",
                                                                                           "evening")])["text"],
                         "Away 4 days")

    def test_never_away(self):
        self.assertEqual(workload.away(h.schedule(h.month()), "ana"), {"level": "", "text": "", "title": ""})
        self.assertEqual(workload.away(h.schedule(h.month()), "nobody"), {"level": "", "text": "", "title": ""})
        # A "never" rule of the card is not time away in the month: the list says it as a reason.
        people = h.people()
        people[0].never = [NeverRule("mon", "any")]
        self.assertEqual(workload.away(h.schedule(h.month(), people), "ana")["level"], "")

    def test_away_all_month(self):
        month = h.month()
        all_month(month, "ana")
        for settings in (Settings(), Settings(prorate_target=False), Settings(default_target_hours_per_week=0)):
            s = Schedule(month, h.people(), settings)
            self.assertEqual(workload.away(s, "ana"),
                             {"level": "all", "text": "Away 1–31 Dec", "title": "Away all day 1–31 December"})
            for added in (0, 4):
                load = workload.choice(s, "ana", "2026-12-31", added)
                self.assertEqual(len(load["week"]["figures"]) + (0 if added else 1), 3)



class DayTest(unittest.TestCase):
    """The strip of a person's day in the list for a shift: this shift, their other shifts, the parts away.

    The tiny set's tasks run from 07:00 (breakfast) to past midnight (the night guard): the window is
    07:00 to midnight, 1020 minutes, so 09:00 is 11.76 %, 13:00 35.29 %, 14:00 41.18 %, 17:00 58.82 %."""

    MARKS = [41.18, 58.82]
    LUNCH = [11.76, 35.29]   # 09:00–13:00

    def setUp(self):
        self.month = h.month()

    def put(self, task_id, iso, person_id="ana"):
        assignment = Assignment(task_id, iso, 0, person_id=person_id)
        self.month.assignments.append(assignment)
        return assignment

    def day(self, person_id="ana", task_id="lunch", iso="2026-12-09", around=(), ignore=None):
        schedule = Schedule(self.month, h.people(), Settings(), around)
        if ignore is not None:
            ignore = schedule.at((ignore.task_id, ignore.date, ignore.position))
        return workload.day(schedule, person_id, task_id, iso, workload.day_window(schedule), ignore=ignore)

    def test_the_window(self):
        def window(month):
            return workload.day_window(Schedule(month, h.people(), Settings()))
        self.assertEqual(window(self.month), (7 * 60, 24 * 60))   # the night guard runs past midnight
        day_tasks = h.month()
        day_tasks.tasks = [t for t in day_tasks.tasks if t.id != "night"]
        next(t for t in day_tasks.tasks if t.id == "supper").end = "18:45"         # rounded up to the hour
        next(t for t in day_tasks.tasks if t.id == "breakfast").start = "06:15"    # rounded down
        self.assertEqual(window(day_tasks), (6 * 60, 19 * 60))
        day_tasks.time_changes["lunch|2026-12-24"] = TimeChange("05:30", "09:00", 3.5)   # a day of the month counts
        self.assertEqual(window(day_tasks), (5 * 60, 19 * 60))
        untimed = h.month()
        for task in untimed.tasks:
            task.start = task.end = ""
        self.assertEqual(window(untimed), (6 * 60, 22 * 60))   # no task with times
        untimed.tasks = []
        self.assertEqual(window(untimed), (6 * 60, 22 * 60))

    def test_a_free_day(self):
        self.assertEqual(self.day(), {
            "slot": self.LUNCH, "segments": [], "marks": self.MARKS,
            "title": "Wednesday 9 December, from 07:00 to midnight. This shift: 09:00–13:00. Nothing else that day."})

    def test_other_shifts_before_and_after(self):
        self.put("supper", "2026-12-09")
        self.put("breakfast", "2026-12-09")   # ends at 09:00 as the lunch begins: not at the same time
        self.put("supper", "2026-12-10")      # another day
        day = self.day()
        self.assertEqual(day["segments"], [["busy", 0.0, 11.76], ["busy", 52.94, 70.59]])
        self.assertEqual(day["title"], "Wednesday 9 December, from 07:00 to midnight. This shift: 09:00–13:00. "
                                       "Already: Breakfast 07:00–09:00, Supper cook 16:00–19:00.")

    def test_a_shift_at_the_same_time(self):
        self.put("helper", "2026-12-09")
        self.put("supper", "2026-12-09")
        day = self.day()
        self.assertEqual(day["segments"], [["busy", 52.94, 70.59], ["clash", 17.65, 35.29]])   # red drawn last
        self.assertTrue(day["title"].endswith("Already: Kitchen helper 10:00–13:00 (at the same time), "
                                              "Supper cook 16:00–19:00."))

    def test_the_night_before_runs_into_the_morning(self):
        self.put("night", "2026-12-08", "ben")
        day = self.day("ben")
        self.assertEqual(day["segments"], [["busy", 0.0, 5.88]])   # 07:00–08:00 of it in the window
        self.assertTrue(day["title"].endswith("Already: Night guard from Tuesday 22:00 to 08:00."))
        day = self.day("ben", "breakfast")   # 07:00–09:00: at the same time
        self.assertEqual((day["slot"], day["segments"]), ([0.0, 11.76], [["clash", 0.0, 5.88]]))
        self.assertTrue(day["title"].endswith("Night guard from Tuesday 22:00 to 08:00 (at the same time)."))
        day = self.day("ben", "night", "2026-12-09")   # this shift runs past midnight: cut at the window's end
        self.assertEqual(day["slot"], [88.24, 100.0])
        self.assertTrue(day["title"].endswith("This shift: 22:00–08:00. Already: Night guard from Tuesday 22:00 to "
                                              "08:00."))

    def test_tasks_without_times(self):
        self.month.tasks += [Task("laundry", "kitchen", "Laundry", periods=["anytime"]),
                             Task("garden", "kitchen", "Garden", periods=["morning", "afternoon"]),
                             Task("bins", "kitchen", "Bins", periods=["morning", "evening"])]
        laundry = self.put("laundry", "2026-12-09")
        self.put("garden", "2026-12-09")
        self.put("bins", "2026-12-09")
        self.put("supper", "2026-12-09")
        day = self.day()
        self.assertEqual(day["segments"], [["loose", 0.0, 41.18], ["loose", 58.82, 100.0],   # bins: two parts
                                           ["loose", 0.0, 58.82], ["loose", 0.0, 100.0], ["busy", 52.94, 70.59]])
        self.assertTrue(day["title"].endswith("Already: Supper cook 16:00–19:00, Bins (in the morning and evening), "
                                              "Garden (in the morning and afternoon), Laundry (any time)."))
        # This shift without times: the parts of the day it is for, "any time" the whole window; never at the same time.
        self.assertEqual(self.day("ben", "laundry")["slot"], [0.0, 100.0])
        self.assertIn("This shift: any time. Nothing else that day.", self.day("ben", "laundry")["title"])
        self.assertEqual(self.day("ben", "garden")["slot"], [0.0, 58.82])
        self.assertIn("This shift: in the morning and afternoon.", self.day("ben", "garden")["title"])
        # Ana on the laundry: her own place is the outline, the bins and the garden are under it.
        self.assertEqual([kind for kind, *_ in self.day("ana", "laundry", ignore=laundry)["segments"]],
                         ["loose"] * 3 + ["busy"])

    def test_away(self):
        h.away(self.month, "ana", "2026-12-09/evening", "2026-12-10/morning")
        day = self.day()
        self.assertEqual(day["segments"], [["away", 58.82, 100.0]])
        self.assertTrue(day["title"].endswith("Nothing else that day. Away in the evening."))
        h.away(self.month, "ana", "2026-12-09/morning", "2026-12-09/afternoon")
        self.put("supper", "2026-12-09")
        day = self.day()
        self.assertEqual(day["segments"], [["away", 0.0, 58.82], ["busy", 52.94, 70.59]])   # away first, one piece
        self.assertTrue(day["title"].endswith("Supper cook 16:00–19:00. Away in the morning and afternoon."))
        h.away(self.month, "ana", "2026-12-09/morning", "2026-12-09/afternoon", "2026-12-09/evening")
        day = self.day()
        self.assertEqual(day["segments"][0], ["away", 0.0, 100.0])
        self.assertTrue(day["title"].endswith(" Away all day."))

    def test_the_person_on_the_shift(self):
        place = self.put("lunch", "2026-12-09")
        self.put("supper", "2026-12-09")
        day = self.day(ignore=place)
        self.assertEqual((day["slot"], day["segments"]), (self.LUNCH, [["busy", 52.94, 70.59]]))
        self.assertTrue(day["title"].endswith("This shift: 09:00–13:00. Already: Supper cook 16:00–19:00."))
        # In the list: their own shift is the outline, not another shift.
        shown = view.candidates_view(self.month, h.people(), Settings(), "lunch", "2026-12-09", 0)
        self.assertEqual(shown["current_person"]["day"], day)
        self.assertEqual(next(c for c in shown["candidates"] if c["id"] == "ben")["day"]["segments"], [])

    def test_a_time_change_on_that_day(self):
        self.month.time_changes["lunch|2026-12-09"] = TimeChange("09:30", "13:30", 4.0)
        self.month.time_changes["supper|2026-12-09"] = TimeChange("15:45", "18:45", 3.0)
        self.month.time_changes["supper|2026-12-10"] = TimeChange("09:00", "10:00", 1.0)   # another day
        self.put("supper", "2026-12-09")
        day = self.day()
        self.assertEqual((day["slot"], day["segments"]), ([14.71, 38.24], [["busy", 51.47, 69.12]]))
        self.assertEqual(day["title"], "Wednesday 9 December, from 07:00 to midnight. This shift: 09:30–13:30. "
                                       "Already: Supper cook 15:45–18:45.")

    def test_the_day_before_in_the_month_before(self):
        november = create_month(h.catalog(), 2026, 11)
        november.assignments.append(Assignment("night", "2026-11-30", 0, person_id="ben"))
        self.assertEqual(self.day("ben", iso="2026-12-01")["segments"], [])   # without the month before: not seen
        day = self.day("ben", iso="2026-12-01", around=[november])
        self.assertEqual(day["segments"], [["busy", 0.0, 5.88]])
        self.assertEqual(day["title"], "Tuesday 1 December, from 07:00 to midnight. This shift: 09:00–13:00. "
                                       "Already: Night guard from Monday 22:00 to 08:00.")
        november.time_changes["night|2026-11-30"] = TimeChange("21:00", "09:30", 0.0)   # November's own times
        day = self.day("ben", iso="2026-12-01", around=[november])
        self.assertEqual(day["segments"], [["clash", 0.0, 14.71]])
        self.assertTrue(day["title"].endswith("Night guard from Monday 21:00 to 09:30 (at the same time)."))
        november.tasks = [t for t in november.tasks if t.id != "night"]   # a task its month no longer has
        self.assertEqual(self.day("ben", iso="2026-12-01", around=[november])["segments"], [])

    def test_nothing_raises_on_an_odd_window(self):
        schedule = Schedule(self.month, h.people(), Settings())
        for window in ((0, 0), (600, 600), (900, 300)):
            day = workload.day(schedule, "ana", "lunch", "2026-12-09", window)
            self.assertEqual(day["slot"], [18.75, 43.75])   # 06:00 to 22:00 instead
        day = workload.day(schedule, "ana", "lunch", "2026-12-09", (0, 24 * 60))
        self.assertEqual((day["slot"], day["marks"]), ([37.5, 54.17], [58.33, 70.83]))
        self.assertTrue(day["title"].startswith("Wednesday 9 December, from midnight to midnight."))


if __name__ == "__main__":
    unittest.main()
