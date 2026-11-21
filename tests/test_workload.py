"""Hours a week: the target, how older files with monthly targets are read,
and the figures and words every screen shows about them."""

import copy
import tempfile
import unittest
from pathlib import Path

from rotaplanner import transfer, view, workload
from rotaplanner.model import WEEKS_A_MONTH, Assignment, Person, Settings, weekly_from_monthly
from rotaplanner.months import create_month
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
        self.assertEqual(workload.reason(s, "ana", "2026-12-04"), "3.2 of 3.5 h a week · 14 h in December")
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
            self.assertEqual(workload.reason(s, "ana", "2026-12-01"), "Away all month · 14 h in December")
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


if __name__ == "__main__":
    unittest.main()
