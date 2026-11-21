"""Hours a week: the target, how older files with monthly targets are read,
and the figures and words every screen shows about them."""

import copy
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rotaplanner import demo, solver, transfer, view, workload
from rotaplanner.model import WEEKS_A_MONTH, Assignment, Person, Settings, Task, week_start, weekly_from_monthly
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
        self.assertEqual((by_id["ben"]["that_week_text"], by_id["ben"]["free_week"], by_id["ben"]["that_week"]),
                         ("2 h that week", False, 2.0))
        self.assertEqual((by_id["ana"]["that_week_text"], by_id["ana"]["free_week"]), ("Free that week", True))


if __name__ == "__main__":
    unittest.main()
