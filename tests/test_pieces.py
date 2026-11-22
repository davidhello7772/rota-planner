"""A task of an event cut into several times (pieces): set up as one task,
each time a task of its own. All names are invented."""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from rotaplanner import demand, editing, month_setup
from rotaplanner.app import App
from rotaplanner.model import Assignment, Settings, Task
from rotaplanner.store import Store

from . import helpers as h

M = "2026-12"
THREE = [{"start": "14:00", "end": "17:00", "people": 2},
         {"start": "17:00", "end": "19:00", "people": 1},
         {"start": "19:00", "end": "20:00", "people": 1}]


def times_of(pieces):
    """The times of saved pieces, as the form sends them back (with their ids)."""
    return [{"id": p.id, "start": p.start, "end": p.end, "people": p.headcount.get("default", 1)} for p in pieces]


class PiecesTest(unittest.TestCase):
    def setUp(self):
        self.catalog = h.catalog()
        self.month = h.month(self.catalog)
        self.event = month_setup.save_event(self.month, {"name": "Course", "start": "2026-12-11", "end": "2026-12-13"})

    def save(self, **fields):
        data = {"event_id": self.event.id, "name": "Registration", "area_id": "kitchen", "event_days": "all", **fields}
        return month_setup.save_event_task(self.month, self.catalog, data)

    def test_three_times_make_three_tasks_side_by_side(self):
        before = [t.id for t in self.month.tasks]
        pieces = self.save(times=THREE, skill_id="cook", event_days="first")
        self.assertEqual(len(pieces), 3)
        self.assertEqual({(p.name, p.area_id, p.skill_id, p.event_days, p.event_id) for p in pieces},
                         {("Registration", "kitchen", "cook", "first", self.event.id)})
        self.assertEqual(len({p.piece_of for p in pieces}), 1)
        self.assertIsNotNone(pieces[0].piece_of)
        self.assertEqual([(p.start, p.end, p.hours, p.headcount) for p in pieces],
                         [("14:00", "17:00", 3.0, {"default": 2}), ("17:00", "19:00", 2.0, {"default": 1}),
                          ("19:00", "20:00", 1.0, {"default": 1})])
        self.assertEqual([p.periods for p in pieces], [["afternoon"], ["evening"], ["evening"]])
        self.assertEqual([t.id for t in self.month.tasks], before + [p.id for p in pieces])  # a new task: at the end
        needs = demand.required(self.month)
        self.assertEqual([needs.get((p.id, "2026-12-11")) for p in pieces], [2, 1, 1])  # each filled on its own

    def test_a_shared_field_changes_every_time_and_they_stay_in_place(self):
        pieces = self.save(times=THREE)
        self.save(name="Tea", event_days="all")  # another task after them
        ids = [t.id for t in self.month.tasks]
        again = self.save(id=pieces[0].id, name="Welcome desk", area_id="security", event_days="last",
                          on_month_rota=False, times=times_of(pieces))
        self.assertEqual([p.id for p in again], [p.id for p in pieces])  # the same tasks
        self.assertEqual({(p.name, p.area_id, p.event_days, p.on_month_rota) for p in again},
                         {("Welcome desk", "security", "last", False)})
        self.assertEqual([t.id for t in self.month.tasks], ids)  # nothing moved

    def test_a_time_added_in_the_middle_stays_with_the_others(self):
        pieces = self.save(times=THREE[:2])
        tea = self.save(name="Tea")[0]
        times = times_of(pieces)
        times[0]["end"] = "16:00"  # the first time made shorter to make room
        times.insert(1, {"start": "16:00", "end": "17:00", "people": 3})
        again = self.save(id=pieces[0].id, times=times)
        self.assertEqual([p.start for p in again], ["14:00", "16:00", "17:00"])
        order = [t.id for t in self.month.tasks]
        self.assertEqual(order[order.index(again[0].id):order.index(again[0].id) + 4], [p.id for p in again] + [tea.id])

    def test_removing_a_time_takes_off_what_was_on_it(self):
        pieces = self.save(times=THREE)
        last = pieces[2]
        self.month.assignments.append(Assignment(last.id, "2026-12-11", 0, person_id="ana"))
        self.month.assignments.append(Assignment(pieces[0].id, "2026-12-11", 0, person_id="ben"))
        self.month.wanted.append(f"{last.id}|2026-12-12|0")
        self.month.demand_overrides[last.id] = {"2026-12-13": 2}
        self.month.time_changes[f"{last.id}|2026-12-12"] = {"start": "19:30", "end": "20:00"}
        again = self.save(id=pieces[0].id, times=times_of(pieces)[:2])
        self.assertEqual([p.id for p in again], [p.id for p in pieces[:2]])
        self.assertNotIn(last.id, [t.id for t in self.month.tasks])
        self.assertEqual([a.person_id for a in self.month.assignments if a.task_id in {p.id for p in pieces}], ["ben"])
        self.assertEqual(self.month.wanted, [])
        self.assertNotIn(last.id, self.month.demand_overrides)
        self.assertEqual(self.month.time_changes, {})

    def test_back_to_one_time_is_a_task_in_one_piece(self):
        pieces = self.save(times=THREE)
        again = self.save(id=pieces[0].id, times=times_of(pieces)[:1], hours=2)
        self.assertEqual(len(again), 1)
        self.assertIsNone(again[0].piece_of)
        self.assertEqual(again[0].hours, 2)  # one time: the hours counted can differ from the times
        self.assertEqual(len(month_setup.event_tasks(self.month, self.event.id)), 1)

    def test_each_time_needs_a_start_and_an_end(self):
        tasks = [t.to_dict() for t in self.month.tasks]
        for times in ([THREE[0], {"start": "17:00", "end": "", "people": 1}],
                      [{"start": "", "end": "", "people": 1}, THREE[1]],
                      [{"start": "08:00", "end": "09:00", "people": 1}] * (editing.MAX_TIMES + 1),
                      [],
                      [{"start": "17:00", "end": "19:00", "people": 1}, {"start": "15:00", "end": "20:00", "people": 1}],
                      [{"id": ["not", "an", "id"], "start": "08:00", "end": "09:00", "people": "many"}]):
            with self.assertRaises(ValueError):
                self.save(times=times)
        with self.assertRaises(ValueError):  # the shared fields are checked before anything changes
            self.save(name="", times=THREE)
        self.assertEqual([t.to_dict() for t in self.month.tasks], tasks)

    def test_times_past_midnight_follow_one_another(self):
        pieces = self.save(times=[{"start": "22:00", "end": "00:00", "people": 1}, {"start": "00:00", "end": "02:00", "people": 1}])
        self.assertEqual([(p.start, p.end, p.hours) for p in pieces], [("22:00", "00:00", 2.0), ("00:00", "02:00", 2.0)])

    def test_without_times_a_page_changes_the_one_it_names_and_keeps_the_others(self):
        pieces = self.save(times=THREE)
        again = self.save(id=pieces[1].id, start="17:30", end="19:00", people=4)
        self.assertEqual([(p.id, p.start, p.headcount["default"]) for p in again],
                         [(pieces[0].id, "14:00", 2), (pieces[1].id, "17:30", 4), (pieces[2].id, "19:00", 1)])

    def test_removing_the_task_removes_every_time(self):
        pieces = self.save(times=THREE)
        self.month.assignments.append(Assignment(pieces[1].id, "2026-12-11", 0, person_id="ana"))
        self.assertTrue(month_setup.delete_event_task(self.month, pieces[1].id))
        self.assertEqual(month_setup.event_tasks(self.month, self.event.id), [])
        self.assertEqual(self.month.assignments, [])

    def test_an_old_task_without_the_field_loads(self):
        data = Task("t", "kitchen", "Tea").to_dict()
        del data["piece_of"]
        self.assertIsNone(Task.from_dict(data).piece_of)


class TypePiecesTest(unittest.TestCase):
    def setUp(self):
        self.catalog = h.catalog()
        self.month = h.month(self.catalog)
        self.type = editing.save_event_type(self.catalog, {"name": "Course"})

    def test_a_type_task_in_three_times_reaches_each_event_as_its_own(self):
        pieces = editing.save_type_task(self.catalog, {"type_id": self.type.id, "name": "Registration",
                                                       "area_id": "kitchen", "times": THREE})
        editing.save_type_task(self.catalog, {"type_id": self.type.id, "name": "Tea", "area_id": "kitchen"})
        self.assertEqual(len(self.type.tasks), 4)
        self.assertEqual(len({p.piece_of for p in pieces}), 1)
        first = month_setup.save_event(self.month, {"name": "Course 1", "start": "2026-12-04", "end": "2026-12-06"})
        second = month_setup.save_event(self.month, {"name": "Course 2", "start": "2026-12-18", "end": "2026-12-20"})
        self.assertEqual(month_setup.add_type_tasks(self.month, first.id, self.type), (2, 0))  # a task in 3 times counts once
        self.assertEqual(month_setup.add_type_tasks(self.month, second.id, self.type), (2, 0))
        groups = []
        for event in (first, second):
            copies = [t for t in month_setup.event_tasks(self.month, event.id) if t.name == "Registration"]
            self.assertEqual([(t.start, t.end, t.headcount["default"]) for t in copies],
                             [("14:00", "17:00", 2), ("17:00", "19:00", 1), ("19:00", "20:00", 1)])
            self.assertEqual(len({t.piece_of for t in copies}), 1)
            groups.append(copies[0].piece_of)
        self.assertNotIn(pieces[0].piece_of, groups)  # a copy is not the type's own task
        self.assertNotEqual(groups[0], groups[1])  # nor the other event's
        # Changing one event's times leaves the other alone.
        one = [t for t in month_setup.event_tasks(self.month, first.id) if t.name == "Registration"]
        month_setup.save_event_task(self.month, self.catalog, {"event_id": first.id, "id": one[0].id, "name": "Registration",
                                                               "area_id": "kitchen", "times": times_of(one)[:1]})
        self.assertEqual(len([t for t in month_setup.event_tasks(self.month, second.id) if t.name == "Registration"]), 3)
        self.assertEqual(month_setup.add_type_tasks(self.month, first.id, self.type), (0, 0))  # it has a Registration

    def test_saving_an_event_as_a_type_keeps_its_times(self):
        event = month_setup.save_event(self.month, {"name": "Course", "start": "2026-12-04", "end": "2026-12-06"})
        pieces = month_setup.save_event_task(self.month, self.catalog, {"event_id": event.id, "name": "Registration",
                                                                        "area_id": "kitchen", "times": THREE})
        made = month_setup.type_from_event(self.month, event.id, "Course")
        self.assertEqual([(t.start, t.end) for t in made.tasks], [(p.start, p.end) for p in pieces])
        self.assertEqual(len({t.piece_of for t in made.tasks}), 1)
        self.assertNotEqual(made.tasks[0].piece_of, pieces[0].piece_of)
        self.assertIsNone(made.tasks[0].event_id)

    def test_order_and_removal_keep_the_times_together(self):
        tea = editing.save_type_task(self.catalog, {"type_id": self.type.id, "name": "Tea", "area_id": "kitchen"})[0]
        pieces = editing.save_type_task(self.catalog, {"type_id": self.type.id, "name": "Registration",
                                                       "area_id": "kitchen", "times": THREE})
        editing.order_type_tasks(self.catalog, self.type.id, [pieces[0].id, tea.id])  # the screen names the first time only
        self.assertEqual([t.id for t in self.type.tasks], [p.id for p in pieces] + [tea.id])
        self.assertEqual(editing.keep_pieces_together([pieces[1], tea, pieces[0], pieces[2]]),
                         [pieces[1], pieces[0], pieces[2], tea])
        self.assertTrue(editing.delete_type_task(self.catalog, self.type.id, pieces[2].id))
        self.assertEqual([t.id for t in self.type.tasks], [tea.id])


class AppPiecesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name))
        self.store.save_catalog(h.catalog())
        self.store.save_people(h.people())
        self.store.save_month(h.month())
        self.app = App(self.store, today=date(2026, 12, 1))
        result = self.app.save_event(M, {"event": {"name": "Course", "start": "2026-12-11", "end": "2026-12-13"}})
        self.event_id = result["events"][0]["id"]

    def entry(self, setup, name="Registration"):
        event = next(e for e in setup["events"] if e["id"] == self.event_id)
        return next(t for t in event["tasks"] if t["name"] == name)

    def test_saved_undone_and_sent_as_one_task(self):
        before = self.app.month(M).to_dict()
        setup = self.app.save_event_task(M, {"task": {"event_id": self.event_id, "name": "Registration",
                                                      "area_id": "kitchen", "times": THREE}})
        self.assertEqual(self.app.history.top(M).label, "Event task added: Registration (Course)")
        event = next(e for e in setup["events"] if e["id"] == self.event_id)
        self.assertEqual([t["name"] for t in event["tasks"]], ["Registration"])  # one entry
        entry = event["tasks"][0]
        self.assertEqual([(t["time"], t["people"], t["hours"], t["placed"]) for t in entry["times"]],
                         [("14:00–17:00", 2, 3.0, 0), ("17:00–19:00", 1, 2.0, 0), ("19:00–20:00", 1, 1.0, 0)])
        self.assertEqual(entry["id"], entry["times"][0]["id"])
        self.assertIn("Registration 17:00–19:00", [t["name"] for t in setup["tasks"]])  # told apart in lists
        after = self.app.month(M).to_dict()
        self.app.undo(M, {})
        self.assertEqual(self.app.month(M).to_dict(), before)
        self.app.redo(M, {})
        self.assertEqual(self.app.month(M).to_dict(), after)

        # Someone on the last time: the screen is told, to warn before it goes.
        last = entry["times"][2]["id"]
        self.app.assign(M, {"task_id": last, "date": "2026-12-11", "position": 0, "person_id": "ana"})
        entry = self.entry(self.app.month_setup(M))
        self.assertEqual([t["placed"] for t in entry["times"]], [0, 0, 1])
        times = [{k: t[k] for k in ("id", "start", "end", "people")} for t in entry["times"][:2]]
        setup = self.app.save_event_task(M, {"task": {"id": entry["id"], "event_id": self.event_id, "name": "Registration",
                                                      "area_id": "kitchen", "times": times}})
        self.assertEqual(self.app.history.top(M).label, "Event task changed: Registration (Course)")
        self.assertEqual(len(self.entry(setup)["times"]), 2)
        self.assertEqual([a for a in self.app.month(M).assignments if a.task_id == last], [])
        self.app.undo(M, {})  # Undo puts them back
        self.assertEqual([a.person_id for a in self.app.month(M).assignments if a.task_id == last], ["ana"])

    def test_one_time_is_a_list_of_one(self):
        setup = self.app.save_event_task(M, {"task": {"event_id": self.event_id, "name": "Tea", "area_id": "kitchen",
                                                      "start": "16:00", "end": "17:00", "people": 2}})
        entry = self.entry(setup, "Tea")
        self.assertEqual([(t["id"], t["time"], t["people"]) for t in entry["times"]], [(entry["id"], "16:00–17:00", 2)])
        self.assertIn("Tea", [t["name"] for t in setup["tasks"]])

    def test_a_type_sends_its_task_in_several_times_as_one(self):
        self.app.save_event_type({"type": {"name": "Course"}})
        type_id = self.app.catalog.event_types[0].id
        setup = self.app.save_type_task({"task": {"type_id": type_id, "name": "Registration", "area_id": "kitchen",
                                                  "times": THREE}})
        kind = next(t for t in setup["event_types"] if t["id"] == type_id)
        self.assertEqual([t["name"] for t in kind["tasks"]], ["Registration"])
        self.assertEqual([t["placed"] for t in kind["tasks"][0]["times"]], [0, 0, 0])

    def test_an_event_over_two_months_takes_the_times_along(self):
        self.store.save_month(month_setup.new_month(h.catalog(), h.month(), 2027, 1, h.people(), Settings()))
        app = App(self.store, today=date(2026, 12, 1))
        result = app.save_event(M, {"event": {"name": "New Year", "start": "2026-12-30", "end": "2027-01-02"}})
        event_id = next(e["id"] for e in result["events"] if e["name"] == "New Year")
        app.save_event_task(M, {"task": {"event_id": event_id, "name": "Tea", "area_id": "kitchen"}})
        app.save_event_task(M, {"task": {"event_id": event_id, "name": "Registration", "area_id": "kitchen", "times": THREE}})
        december = month_setup.event_tasks(app.month(M), event_id)
        january = month_setup.event_tasks(app.month("2027-01"), event_id)
        self.assertEqual([(t.id, t.piece_of, t.start) for t in january], [(t.id, t.piece_of, t.start) for t in december])
        # A time added in the middle reaches January in its place, next to the others.
        pieces = [t for t in december if t.piece_of]
        times = times_of(pieces)
        times[0]["end"] = "16:00"
        times.insert(1, {"start": "16:00", "end": "17:00", "people": 1})
        app.save_event_task(M, {"task": {"id": pieces[0].id, "event_id": event_id, "name": "Registration",
                                         "area_id": "kitchen", "times": times}})
        january = app.month("2027-01").tasks
        starts = [t.start for t in january if t.piece_of]
        self.assertEqual(starts, ["14:00", "16:00", "17:00", "19:00"])
        at = [i for i, t in enumerate(january) if t.piece_of]
        self.assertEqual(at, list(range(at[0], at[0] + 4)))  # side by side


if __name__ == "__main__":
    unittest.main()
