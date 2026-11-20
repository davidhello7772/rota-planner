import random
import unittest

from rotaplanner import checks, demo, months, solver
from rotaplanner import eligibility as rules
from rotaplanner.model import Assignment, Person, PersonAvailability, Regular, Settings, Task
from rotaplanner.schedule import Schedule

from . import helpers as h

FAST = 0.2  # seconds of improvement in tests


def accept(month, proposals):
    for p in proposals:
        existing = month.find_assignment(p.task_id, p.date, p.position)
        if existing:
            month.assignments.remove(existing)
        month.assignments.append(Assignment(p.task_id, p.date, p.position, person_id=p.person_id, source="auto"))


class PrefillTest(unittest.TestCase):
    def test_never_breaks_a_rule_and_fills_what_it_can(self):
        for seed in range(3):
            rng = random.Random(seed)
            catalog = demo.build_catalog()
            people = demo.build_people(catalog, rng)
            catalog.regulars = demo.build_regulars(catalog, people, rng)
            month = months.create_month(catalog, 2026, 12)
            month.availability = demo.build_availability(people, 2026, 12, rng)
            months.apply_regulars(month, catalog, people, Settings())
            proposals, left = solver.prefill(month, people, Settings(), seed=seed, seconds=FAST)
            accept(month, proposals)
            states = {s.state for s in checks.all_statuses(Schedule(month, people, Settings())).values()}
            self.assertNotIn(checks.CONFLICT, states, f"seed {seed}")
            self.assertLessEqual(len(left), 5, f"seed {seed}")

    def test_scope_by_days_and_areas(self):
        month = h.month()
        proposals, _ = solver.prefill(month, h.people(), Settings(), dates={"2026-12-05"}, seconds=FAST)
        self.assertEqual({p.date for p in proposals}, {"2026-12-05"})
        proposals, _ = solver.prefill(month, h.people(), Settings(), area_ids={"security"}, seconds=FAST)
        self.assertEqual({p.task_id for p in proposals}, {"night"})

    def test_essential_tasks_come_first_when_people_are_short(self):
        catalog = h.catalog()
        catalog.tasks = [
            Task("nice", "kitchen", "Nice to have", start="09:00", end="12:00", hours=3, headcount={"default": 1}, priority="optional"),
            Task("must", "kitchen", "Essential", start="10:00", end="13:00", hours=3, headcount={"default": 1}, priority="critical"),
        ]
        month = h.month(catalog)
        people = [Person("solo", "Solo A.")]
        proposals, left = solver.prefill(month, people, Settings(), dates={"2026-12-01"}, seconds=FAST)
        self.assertEqual([p.task_id for p in proposals], ["must"])
        self.assertEqual([s[0] for s in left], ["nice"])

    def test_repair_frees_the_only_trained_person(self):
        # Ana is the only cook; Ben can do the helper shift. If Ana were put on
        # the helper shift first, the cook shift would stay open: repair swaps them.
        catalog = h.catalog()
        catalog.tasks = [
            Task("helper", "kitchen", "Helper", start="10:00", end="12:00", hours=2, headcount={"default": 1}, priority="critical"),
            Task("lunch", "kitchen", "Cook", skill_id="cook", start="11:00", end="13:00", hours=2, headcount={"default": 1}),
        ]
        month = h.month(catalog)
        people = [Person("ana", "Ana B.", skills=["cook"]), Person("ben", "Ben C.")]
        schedule = Schedule(month, people, Settings())
        placed = {}
        solver._place(schedule, placed, ("helper", "2026-12-01", 0), "ana")
        self.assertTrue(solver._repair(schedule, placed, ("lunch", "2026-12-01", 0)))
        self.assertEqual({s[0]: a.person_id for s, a in placed.items()}, {"helper": "ben", "lunch": "ana"})


class RebalanceTest(unittest.TestCase):
    def test_moves_from_busy_to_idle_but_leaves_decided_shifts(self):
        month = h.month()
        busy = [Assignment("lunch", f"2026-12-{d:02d}", 0, person_id="ana") for d in range(1, 9)]
        busy[0].source = "regular"
        busy[1].locked = True
        busy[2].forced_reason = ""
        month.assignments = busy
        moves = solver.rebalance(month, h.people(), Settings(default_target_hours=10))
        self.assertTrue(moves)
        self.assertEqual({m.replaces for m in moves}, {"ana"})
        self.assertEqual({m.person_id for m in moves}, {"ben"})
        self.assertNotIn(busy[0].date, [m.date for m in moves])
        self.assertLessEqual(len(moves), 1)  # each person moves once at most per suggestion


class ShortageTest(unittest.TestCase):
    def test_day_with_more_shifts_than_people(self):
        month = h.month()
        # Both cooks away on the 1st: lunch and supper cannot be filled.
        for cook in ("ana", "ben"):
            h.away(month, cook, "2026-12-01/morning", "2026-12-01/afternoon")
        found = solver.shortages(month, h.people(), Settings(), {"cook": "Cook"})
        day = next(item for item in found if item.get("date") == "2026-12-01")
        self.assertEqual((day["open"], day["nobody"]), (4, 2))
        self.assertLessEqual(day["fillable"], 2)
        self.assertNotIn("2026-12-02", [item.get("date") for item in found])  # two shifts a day each is enough there


class IdeasTest(unittest.TestCase):
    def test_ideas_for_a_shift_nobody_can_take(self):
        catalog = h.catalog()
        month = h.month(catalog)
        people = h.people()
        # Ana (cook) is on breakfast 07:00-09:00 and lunch overlaps nothing… make lunch clash with it.
        month.time_changes = {}
        month.tasks[4].start, month.tasks[4].end = "08:00", "10:00"   # breakfast overlaps lunch (09:00-13:00)
        month.assignments = [Assignment("breakfast", "2026-12-01", 0, person_id="ana"),
                             Assignment("supper", "2026-12-01", 0, person_id="ben")]
        month.availability = {"ben": PersonAvailability(unavailable=["2026-12-01/morning"], comment="I could cook lunch on the 1st"),
                              "cleo": PersonAvailability()}
        catalog.regulars = [Regular("r", "ben", "supper", "tue")]
        found = solver.ideas(month, people, Settings(), catalog.regulars, "lunch", "2026-12-01", 0)
        kinds = [i["kind"] for i in found]
        self.assertEqual(kinds[0], "swap")
        self.assertEqual(found[0]["action"]["person_id"], "ana")
        self.assertIn("comment", kinds)
        self.assertIn("away", kinds)
        self.assertEqual(kinds[-1], "need")


class MentionsTest(unittest.TestCase):
    def test_a_comment_naming_a_day_only_counts_for_that_day(self):
        self.assertTrue(solver._mentions("I can cook lunch on Monday 7th", "2026-12-07", "Lead cook lunch"))
        self.assertFalse(solver._mentions("I can cook lunch on Monday 7th", "2026-12-30", "Lead cook lunch"))
        self.assertTrue(solver._mentions("Happy to cook lunch any day", "2026-12-30", "Lead cook lunch"))
        self.assertFalse(solver._mentions("Away visiting family", "2026-12-30", "Lead cook lunch"))


class CostTest(unittest.TestCase):
    def test_weekends_are_shared(self):
        month = h.month()
        month.assignments = [Assignment("helper", d, 0, person_id="ana") for d in ("2026-12-05", "2026-12-06", "2026-12-12")]
        schedule = Schedule(month, h.people(), Settings(default_target_hours=100))
        ranked = rules.candidates(schedule, schedule.tasks["helper"], "2026-12-13")
        best = [c.person.id for c in ranked if c.group == rules.BEST]
        self.assertLess(best.index("ben"), best.index("ana"))


if __name__ == "__main__":
    unittest.main()
