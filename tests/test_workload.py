"""Hours a week: the target, and how older files with monthly targets are read."""

import copy
import unittest

from rotaplanner.model import WEEKS_A_MONTH, Person, Settings, weekly_from_monthly
from rotaplanner.months import create_month
from rotaplanner.schedule import Schedule

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


if __name__ == "__main__":
    unittest.main()
