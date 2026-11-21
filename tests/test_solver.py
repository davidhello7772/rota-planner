import random
import unittest
from collections import Counter

from rotaplanner import checks, demo, months, solver
from rotaplanner import eligibility as rules
from rotaplanner.model import Assignment, Person, PersonAvailability, Regular, Settings, Task, week_start
from rotaplanner.schedule import Schedule

from . import helpers as h

FAST = 0.2  # seconds of improvement in tests


def demo_month(seed, adjust=None):
    """The invented demo centre for December 2026, regular week placed
    (after ``adjust(people)``, when given)."""
    rng = random.Random(seed)
    catalog = demo.build_catalog()
    people = demo.build_people(catalog, rng)
    catalog.regulars = demo.build_regulars(catalog, people, rng)
    month = months.create_month(catalog, 2026, 12)
    month.availability = demo.build_availability(people, 2026, 12, rng)
    if adjust:
        adjust(people)
    months.apply_regulars(month, catalog, people, Settings())
    return month, people


def one_task(hours=2, year=h.YEAR, number=h.MONTH):
    """A month with a single task, one person every day."""
    catalog = h.catalog()
    catalog.tasks = [Task("desk", "kitchen", "Desk", start="10:00", end=f"{10 + hours}:00", hours=hours,
                          headcount={"default": 1})]
    return months.create_month(catalog, year, number)


def accept(month, proposals):
    for p in proposals:
        existing = month.find_assignment(p.task_id, p.date, p.position)
        if existing:
            month.assignments.remove(existing)
        month.assignments.append(Assignment(p.task_id, p.date, p.position, person_id=p.person_id, source="auto"))


class PrefillTest(unittest.TestCase):
    def test_never_breaks_a_rule_and_fills_what_it_can(self):
        for seed in range(3):
            month, people = demo_month(seed)
            proposals, left = solver.prefill(month, people, Settings(), seed=seed, seconds=FAST)
            accept(month, proposals)
            states = {s.state for s in checks.all_statuses(Schedule(month, people, Settings())).values()}
            self.assertNotIn(checks.CONFLICT, states, f"seed {seed}")
            self.assertLessEqual(len(left), 5, f"seed {seed}")

    def test_work_is_shared_in_proportion_to_each_target(self):
        month = one_task(hours=2)
        people = [Person("full", "Full A.", target_hours=12), Person("half", "Half B.", target_hours=6)]
        proposals, left = solver.prefill(month, people, Settings(), seconds=5)
        self.assertEqual(left, [])
        share = Counter(p.person_id for p in proposals)["half"] / len(proposals)
        self.assertTrue(0.27 <= share <= 0.40, share)   # ideal: a third

    def test_shifts_are_spread_over_the_weeks(self):
        month = one_task(hours=2, year=2027, number=2)   # February 2027: four weeks, Monday to Sunday
        people = [Person(f"p{n}", f"Person {n}.") for n in range(7)]
        proposals, left = solver.prefill(month, people, Settings(), seconds=5)
        self.assertEqual((len(proposals), left), (28, []))
        weeks = Counter((p.person_id, week_start(p.date)) for p in proposals)
        self.assertLessEqual(max(weeks.values()), 2)

    def test_limits_on_the_cards_hold(self):
        def adjust(people):
            active = [p for p in people if p.active and not p.archived]
            for n, person in enumerate(active[:24]):
                person.max_shifts_per_week = 1 + n % 2
                if n % 3 == 0:
                    person.max_tasks_per_day = 1

        month, people = demo_month(0, adjust)
        limited = {p.id: p for p in people}

        def assert_within_limits(label):
            weeks = Counter((a.person_id, week_start(a.date)) for a in month.assignments if a.person_id)
            days = Counter((a.person_id, a.date) for a in month.assignments if a.person_id)
            for (person_id, _), count in weeks.items():
                limit = limited[person_id].max_shifts_per_week if person_id in limited else None
                self.assertTrue(limit is None or count <= limit, f"{label}: {person_id} has {count} in a week")
            for (person_id, _), count in days.items():
                limit = limited[person_id].max_tasks_per_day if person_id in limited else None
                self.assertTrue(limit is None or count <= limit, f"{label}: {person_id} has {count} in a day")
            states = {s.state for s in checks.all_statuses(Schedule(month, people, Settings())).values()}
            self.assertNotIn(checks.CONFLICT, states, label)

        assert_within_limits("regular week")
        proposals, _ = solver.prefill(month, people, Settings(), seconds=FAST)
        accept(month, proposals)
        assert_within_limits("prefill")
        accept(month, solver.rebalance(month, people, Settings(default_target_hours=6)))
        assert_within_limits("rebalance")

    def test_the_same_input_gives_the_same_suggestions(self):
        month, people = demo_month(1)
        week = {f"2026-12-{d:02d}" for d in range(7, 14)}
        first, _ = solver.prefill(month, people, Settings(), dates=week, seed=3, seconds=30)
        again, _ = solver.prefill(month, people, Settings(), dates=week, seed=3, seconds=30)
        self.assertTrue(first)
        self.assertEqual([p.to_dict() for p in first], [p.to_dict() for p in again])

    def test_options_list_what_the_picker_lists_outside_blocked(self):
        month, people = demo_month(2)
        people[0].active = False
        people[1].archived = True
        schedule = Schedule(month, people, Settings())
        filled = next(a for a in month.assignments if a.person_id)
        for slot, ignore in [(s, None) for s in schedule.open_slots()[:40]] + [(filled.slot, filled)]:
            picker = [c for c in rules.candidates(schedule, schedule.tasks[slot[0]], slot[1], ignore=ignore)
                      if c.group != rules.BLOCKED]
            options = solver._options(schedule, slot, ignore=ignore)
            self.assertEqual([(c.person.id, c.group, c.cost, c.findings) for c in options],
                             [(c.person.id, c.group, c.cost, c.findings) for c in picker])
            self.assertNotIn(people[0].id, [c.person.id for c in options])
            self.assertNotIn(people[1].id, [c.person.id for c in options])

    def test_a_suggestion_says_what_to_know(self):
        month = one_task(hours=5)
        proposals, _ = solver.prefill(month, [Person("ana", "Ana B.")], Settings(), dates={"2026-12-01"},
                                      seconds=FAST)
        self.assertEqual(proposals[0].why, "5 h of 12 h this month · Would reach 5 h that day (usual max 4 h)"
                                           " · has not replied to the availability form")

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

    def test_repair_frees_someone_held_by_the_weekly_limit(self):
        # Ana, the only cook, may do one shift a week; she was put on Monday's
        # helper shift. Repair gives that to Ben so that she can cook on Wednesday.
        catalog = h.catalog()
        catalog.tasks = [
            Task("helper", "kitchen", "Helper", start="10:00", end="12:00", hours=2, headcount={"default": 1}),
            Task("lunch", "kitchen", "Cook", skill_id="cook", start="11:00", end="13:00", hours=2, headcount={"default": 1}),
        ]
        month = h.month(catalog)
        people = [Person("ana", "Ana B.", skills=["cook"], max_shifts_per_week=1), Person("ben", "Ben C.")]
        schedule = Schedule(month, people, Settings())
        placed = {}
        solver._place(schedule, placed, ("helper", "2026-12-07", 0), "ana")
        self.assertTrue(solver._repair(schedule, placed, ("lunch", "2026-12-09", 0)))
        self.assertEqual({s: a.person_id for s, a in placed.items()},
                         {("helper", "2026-12-07", 0): "ben", ("lunch", "2026-12-09", 0): "ana"})


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

    def test_people_already_full_add_no_capacity(self):
        month = h.month()
        month.assignments = [Assignment("breakfast", "2026-12-07", 0, person_id="ana"),
                             Assignment("supper", "2026-12-07", 0, person_id="ana"),
                             Assignment("breakfast", "2026-12-08", 0, person_id="ben"),
                             Assignment("night", "2026-12-09", 0, person_id="ben")]
        people = h.people()
        people[1].max_shifts_per_week = 2
        schedule = Schedule(month, people, Settings())
        ana, ben, cleo = people[:3]
        self.assertEqual(solver._capacity(schedule, ana, "2026-12-07"), 0)     # two shifts that day
        self.assertEqual(solver._capacity(schedule, ana, "2026-12-08"), 2)
        self.assertEqual(solver._capacity(schedule, ben, "2026-12-10"), 0)     # at the weekly limit
        self.assertEqual(solver._capacity(schedule, ben, "2026-12-14"), 2)     # the next week
        cleo.max_tasks_per_day, ben.max_shifts_per_week = 1, 3
        self.assertEqual(solver._capacity(schedule, cleo, "2026-12-10"), 1)
        self.assertEqual(solver._capacity(schedule, ben, "2026-12-10"), 1)


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

    def test_an_idea_can_free_someone_held_by_the_weekly_limit(self):
        month = h.month()
        people = h.people()
        people[0].max_shifts_per_week = 1                               # Ana: one shift a week
        month.assignments = [Assignment("lunch", "2026-12-07", 0, person_id="ana")]
        h.away(month, "ben", "2026-12-09/morning")                      # the other cook is away on Wednesday
        found = solver.ideas(month, people, Settings(), [], "lunch", "2026-12-09", 0)
        self.assertEqual(found[0]["kind"], "swap")
        self.assertEqual(found[0]["text"], "Move Ana B. here from Lunch cook on Mon 7; Ben C. can take it.")
        self.assertEqual(found[0]["action"], {"person_id": "ana", "from": "lunch|2026-12-07|0", "replacement": "ben"})


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
