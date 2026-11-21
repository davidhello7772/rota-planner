import unittest

from rotaplanner import demand
from rotaplanner import eligibility as rules
from rotaplanner.model import (
    ANYTIME, Assignment, Event, Marker, NeverRule, Settings, Task, TimeChange, week_dates, week_start,
)
from rotaplanner.months import create_month
from rotaplanner.schedule import Schedule

from . import helpers as h


def findings_for(schedule, person_id, task_id, iso):
    person = schedule.people[person_id]
    task = schedule.task_on(task_id, iso)
    return {f.rule: f.severity for f in rules.evaluate(rules.Placement(schedule, person, task, iso))}


class DemandTest(unittest.TestCase):
    def test_weekday_default_and_event_headcounts(self):
        month = h.month()
        needs = demand.required(month)
        self.assertEqual(needs[("lunch", "2026-12-01")], 1)       # default
        self.assertNotIn(("helper", "2026-12-01"), needs)          # Tuesday: nobody
        self.assertEqual(needs[("helper", "2026-12-05")], 2)       # Saturday
        month.events.append(Event("e", "Retreat", "2026-12-08", "2026-12-08"))
        self.assertEqual(demand.required(month)[("helper", "2026-12-08")], 3)

    def test_closure_cancels_tasks_except_the_kept_ones(self):
        month = h.month()
        month.markers.append(Marker("m", "closed", "Closed", "2026-12-02", "2026-12-03", keep_task_ids=["night"]))
        needs = demand.required(month)
        self.assertNotIn(("lunch", "2026-12-02"), needs)
        self.assertIn(("night", "2026-12-02"), needs)
        self.assertIn(("lunch", "2026-12-04"), needs)

    def test_area_closure_and_day_override(self):
        month = h.month()
        month.markers.append(Marker("m", "closed", "Kitchen works", "2026-12-02", "2026-12-02", area_ids=["kitchen"]))
        month.demand_overrides = {"supper": {"2026-12-02": 2}}
        needs = demand.required(month)
        self.assertNotIn(("lunch", "2026-12-02"), needs)
        self.assertIn(("night", "2026-12-02"), needs)
        self.assertEqual(needs[("supper", "2026-12-02")], 2)  # typed by hand: wins over closure


class RuleTest(unittest.TestCase):
    def setUp(self):
        self.month = h.month()

    def add(self, task_id, iso, person_id, position=0):
        self.month.assignments.append(Assignment(task_id, iso, position, person_id=person_id))

    def test_clean_placement_has_no_findings(self):
        s = h.schedule(self.month)
        self.month.availability = {}
        found = findings_for(s, "ana", "lunch", "2026-12-01")
        self.assertEqual({k: v for k, v in found.items() if v != rules.NOTE}, {})

    def test_inactive_untrained_and_excluded_are_blocked(self):
        ppl = h.people()
        ppl[0].avoid_task_ids = ["supper"]
        s = h.schedule(self.month, ppl)
        self.assertEqual(findings_for(s, "dov", "lunch", "2026-12-01")["inactive"], rules.BLOCK)
        self.assertEqual(findings_for(s, "cleo", "lunch", "2026-12-01")["not_trained"], rules.BLOCK)
        self.assertEqual(findings_for(s, "ana", "supper", "2026-12-01")["excluded"], rules.BLOCK)
        self.assertNotIn("not_trained", findings_for(s, "cleo", "helper", "2026-12-05"))  # anyone

    def test_unavailable_only_for_that_period(self):
        h.away(self.month, "ana", "2026-12-01/morning")
        s = h.schedule(self.month)
        self.assertEqual(findings_for(s, "ana", "lunch", "2026-12-01")["unavailable"], rules.BLOCK)
        self.assertNotIn("unavailable", findings_for(s, "ana", "supper", "2026-12-01"))

    def test_anytime_task_only_blocked_when_away_all_day(self):
        cat = h.catalog()
        cat.tasks.append(Task("toilets", "kitchen", "Toilet check", periods=[ANYTIME], hours=0.5,
                              headcount={"default": 1}))
        month = h.month(cat)
        h.away(month, "ana", "2026-12-01/morning", "2026-12-01/afternoon")
        h.away(month, "ben", *(f"2026-12-01/{p}" for p in ("morning", "afternoon", "evening")))
        s = h.schedule(month)
        self.assertEqual(findings_for(s, "ana", "toilets", "2026-12-01")["partly_away"], rules.NOTE)
        self.assertEqual(findings_for(s, "ben", "toilets", "2026-12-01")["unavailable"], rules.BLOCK)
        self.assertEqual(s.tasks["toilets"].time_text(), "Any time")
        ppl = h.people()
        ppl[2].never = [NeverRule("tue", "any")]
        self.assertEqual(findings_for(h.schedule(month, ppl), "cleo", "toilets", "2026-12-01")["unavailable"], rules.BLOCK)

    def test_task_over_two_parts_of_the_day(self):
        cat = h.catalog()
        cat.tasks.append(Task("long", "kitchen", "Long shift", periods=["morning", "afternoon"], start="11:00", end="15:00",
                              hours=4, headcount={"default": 1}))
        month = h.month(cat)
        h.away(month, "ana", "2026-12-01/afternoon")
        s = h.schedule(month)
        self.assertEqual(findings_for(s, "ana", "long", "2026-12-01")["unavailable"], rules.BLOCK)
        self.assertNotIn("unavailable", findings_for(s, "ana", "long", "2026-12-02"))
        ppl = h.people()
        ppl[1].never = [NeverRule("tue", "afternoon")]
        self.assertEqual(findings_for(h.schedule(month, ppl), "ben", "long", "2026-12-01")["never"], rules.BLOCK)

    def test_never_rule(self):
        ppl = h.people()
        ppl[0].never = [NeverRule("tue", "morning")]
        s = h.schedule(self.month, ppl)
        self.assertEqual(findings_for(s, "ana", "lunch", "2026-12-01")["never"], rules.BLOCK)  # Tuesday
        self.assertNotIn("never", findings_for(s, "ana", "lunch", "2026-12-02"))

    def test_overlap_same_day_and_past_midnight(self):
        self.add("lunch", "2026-12-05", "ben")
        self.add("night", "2026-12-06", "ben")
        s = h.schedule(self.month)
        self.assertEqual(findings_for(s, "ben", "helper", "2026-12-05")["overlap"], rules.BLOCK)
        self.assertNotIn("overlap", findings_for(s, "ben", "supper", "2026-12-05"))
        # Night guard 22:00 on the 6th runs into breakfast at 07:00 on the 7th.
        self.assertEqual(findings_for(s, "ben", "breakfast", "2026-12-07")["overlap"], rules.BLOCK)
        self.assertNotIn("overlap", findings_for(s, "ben", "breakfast", "2026-12-08"))

    def test_a_day_with_other_times(self):
        # Usually 09:00–13:00, so lunch and supper (16:00–19:00) never clash…
        self.add("supper", "2026-12-25", "ben")
        s = h.schedule(self.month)
        self.assertNotIn("overlap", findings_for(s, "ben", "lunch", "2026-12-25"))
        # …but a late Christmas meal at 15:00–18:00 does.
        self.month.time_changes["lunch|2026-12-25"] = TimeChange("15:00", "18:00", 3, "Christmas meal")
        s = h.schedule(self.month)
        self.assertEqual(s.task_on("lunch", "2026-12-25").time_text(), "15:00–18:00 · Christmas meal")
        self.assertEqual(findings_for(s, "ben", "lunch", "2026-12-25")["overlap"], rules.BLOCK)
        self.assertNotIn("overlap", findings_for(s, "ben", "lunch", "2026-12-24"))
        self.add("lunch", "2026-12-25", "ana")
        self.assertEqual(h.schedule(self.month).hours("ana"), 3)

    def test_double_booking_same_task(self):
        self.add("helper", "2026-12-05", "ana")
        s = h.schedule(self.month)
        self.assertEqual(findings_for(s, "ana", "helper", "2026-12-05")["double_booked"], rules.BLOCK)

    def test_daily_limits(self):
        self.add("lunch", "2026-12-01", "ana")
        s = h.schedule(self.month)
        # Centre default of 4 h a day: a warning only.
        self.assertEqual(findings_for(s, "ana", "supper", "2026-12-01")["daily_hours"], rules.WARN)
        ppl = h.people()
        ppl[0].max_hours_per_day = 5
        ppl[0].max_tasks_per_day = 1
        s = h.schedule(self.month, ppl)
        found = findings_for(s, "ana", "supper", "2026-12-01")
        self.assertEqual(found["daily_hours"], rules.BLOCK)
        self.assertEqual(found["daily_tasks"], rules.BLOCK)

    def test_monthly_ceiling_is_hard_target_is_a_note(self):
        for day in range(1, 4):
            self.add("lunch", f"2026-12-0{day}", "ana")
        s = h.schedule(self.month, settings=Settings(default_target_hours=10))
        self.assertEqual(findings_for(s, "ana", "lunch", "2026-12-04")["over_target"], rules.NOTE)
        ppl = h.people()
        ppl[0].max_hours = 14
        s = h.schedule(self.month, ppl)
        self.assertEqual(findings_for(s, "ana", "lunch", "2026-12-04")["monthly_hours"], rules.BLOCK)

    def test_presence_scales_the_target(self):
        keys = [f"2026-12-{d:02d}/{p}" for d in range(1, 16) for p in ("morning", "afternoon", "evening")]
        h.away(self.month, "ana", *keys)
        s = h.schedule(self.month, settings=Settings(default_target_hours=12))
        self.assertAlmostEqual(s.target("ana"), 12 * 16 / 31)
        self.assertEqual(s.target("ben"), 12)

    def test_candidates_are_ranked_clean_first_then_least_loaded(self):
        self.add("supper", "2026-12-02", "ben")
        s = h.schedule(self.month)
        ranked = rules.candidates(s, s.tasks["lunch"], "2026-12-03")
        self.assertEqual([c.person.id for c in ranked if c.group == rules.BEST], ["ana", "ben"])
        self.assertEqual(ranked[-1].group, rules.BLOCKED)


if __name__ == "__main__":
    unittest.main()


class MonthsNextDoorTest(unittest.TestCase):
    """Rules look a few days into the months next door; those shifts never count in this month."""

    def setUp(self):
        self.november = create_month(h.catalog(), 2026, 11)
        self.night = Assignment("night", "2026-11-30", 0, person_id="ben")  # 22:00–08:00, a Monday
        self.november.assignments.append(self.night)
        self.december = h.month()

    def schedule(self, around=()):
        return Schedule(self.december, h.people(), Settings(), around)

    def test_weeks_run_monday_to_sunday(self):
        self.assertEqual(week_start("2026-12-01"), "2026-11-30")
        self.assertEqual(week_start("2026-11-30"), "2026-11-30")
        self.assertEqual(week_start("2026-12-06"), "2026-11-30")
        self.assertEqual(week_dates("2026-12-03"), ["2026-11-30", "2026-12-01", "2026-12-02", "2026-12-03",
                                                     "2026-12-04", "2026-12-05", "2026-12-06"])

    def test_a_night_on_the_last_day_blocks_a_morning_on_the_first(self):
        self.assertNotIn("overlap", findings_for(self.schedule(), "ben", "breakfast", "2026-12-01"))
        s = self.schedule([self.november])
        self.assertEqual(findings_for(s, "ben", "breakfast", "2026-12-01")["overlap"], rules.BLOCK)
        self.assertNotIn("overlap", findings_for(s, "ben", "lunch", "2026-12-01"))  # 09:00, after the night
        self.assertNotIn("overlap", findings_for(s, "cleo", "breakfast", "2026-12-01"))
        # And the other way round: November sees a breakfast already on 1 December.
        self.november.assignments.remove(self.night)
        self.december.assignments.append(Assignment("breakfast", "2026-12-01", 0, person_id="ben"))
        november = Schedule(self.november, h.people(), Settings(), [self.december])
        self.assertEqual(findings_for(november, "ben", "night", "2026-11-30")["overlap"], rules.BLOCK)

    def test_the_month_next_door_keeps_its_own_tasks_and_times(self):
        # A shorter night there, on that day only: no clash.
        self.november.time_changes["night|2026-11-30"] = TimeChange("20:00", "23:00", 0, "")
        self.assertNotIn("overlap", findings_for(self.schedule([self.november]), "ben", "breakfast", "2026-12-01"))
        del self.november.time_changes["night|2026-11-30"]
        # The same task id with other usual times next door.
        next(t for t in self.november.tasks if t.id == "night").end = "23:30"
        s = self.schedule([self.november])
        self.assertNotIn("overlap", findings_for(s, "ben", "breakfast", "2026-12-01"))
        self.assertEqual(s.task_on("night", "2026-11-30").end, "23:30")
        self.assertEqual(s.task_on("night", "2026-12-01").end, "08:00")
        # A task no longer in November: its shift is not known, so it does not clash.
        self.november.tasks = [t for t in self.november.tasks if t.id != "night"]
        s = self.schedule([self.november])
        self.assertFalse(s.knows(self.night))
        self.assertNotIn("overlap", findings_for(s, "ben", "breakfast", "2026-12-01"))
        self.assertEqual(s.shifts_in_week("ben", "2026-12-01"), 0)

    def test_nearby_shifts_never_count_in_the_month(self):
        self.december.assignments.append(Assignment("lunch", "2026-12-05", 0, person_id="ben"))  # a Saturday
        self.november.assignments.append(Assignment("helper", "2026-11-29", 0, person_id="ben"))  # a Sunday
        ana_lunch = Assignment("lunch", "2026-11-30", 0, person_id="ana")
        self.november.assignments.append(ana_lunch)
        self.november.time_changes["lunch|2026-11-30"] = TimeChange("09:00", "14:00", 5, "")
        alone, together = self.schedule(), self.schedule([self.november])
        for person_id in ("ana", "ben", "cleo"):
            self.assertEqual(together.hours(person_id), alone.hours(person_id))
            self.assertEqual(together.weekends(person_id), alone.weekends(person_id))
            self.assertEqual(together.evenings(person_id), alone.evenings(person_id))
        self.assertEqual(len(together.all_assignments()), 1)
        self.assertEqual(together.open_slots(), alone.open_slots())
        self.assertEqual(together.on_day("ben", "2026-11-30"), [self.night])
        self.assertEqual(alone.on_day("ben", "2026-11-30"), [])
        self.assertEqual(together.hours_of(ana_lunch), 5)  # as November has it that day
        self.assertEqual(together.hours("ana"), 0)
        self.assertEqual(together.dates, alone.dates)

    def test_shifts_in_week_count_across_the_two_months(self):
        s = self.schedule([self.november])
        self.assertEqual(s.shifts_in_week("ben", "2026-12-01"), 1)  # the night of Monday 30 November
        self.assertEqual(s.shifts_in_week("ben", "2026-12-06"), 1)
        self.assertEqual(s.shifts_in_week("ben", "2026-12-07"), 0)
        self.assertEqual(self.schedule().shifts_in_week("ben", "2026-12-01"), 0)
        lunch = Assignment("lunch", "2026-12-03", 0, person_id="ben")
        s.add(lunch)
        self.assertEqual(s.shifts_in_week("ben", "2026-12-01"), 2)
        self.assertEqual(s.shifts_in_week("ana", "2026-12-01"), 0)
        s.remove(lunch)
        self.assertEqual(s.shifts_in_week("ben", "2026-12-01"), 1)
        # Only the nearby days: an earlier November week is not this month's business.
        self.november.assignments.append(Assignment("lunch", "2026-11-23", 0, person_id="ben"))
        s = self.schedule([self.november])
        self.assertEqual(s.shifts_in_week("ben", "2026-11-23"), 0)
        self.assertEqual(s.on_day("ben", "2026-11-23"), [])
