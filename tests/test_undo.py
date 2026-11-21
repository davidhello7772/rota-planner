"""Undo with names, through the program as the screens use it (on a temporary data folder)."""

import json
import tempfile
import time
import unittest
from datetime import date
from http import HTTPStatus
from pathlib import Path

from rotaplanner.app import ApiError, App
from rotaplanner.history import unpack
from rotaplanner.solver import Proposal
from rotaplanner.store import Store

from . import helpers as h

M = "2026-12"
COMMENT = "Dentist on the 3rd, please"


class UndoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name))
        self.store.save_catalog(h.catalog())
        self.store.save_people(h.people())
        self.store.save_month(h.month())
        self.app = App(self.store, today=date(2026, 12, 1))

    def top(self):
        return self.app.history.top(M)

    def label(self):
        return self.top().label

    def slot(self, task, day, position=0):
        return {"task_id": task, "date": f"2026-12-{day:02d}", "position": position}

    # -- names ------------------------------------------------------------------

    def test_every_change_has_its_name(self):
        app, seen = self.app, []

        def expect(text, kind="change"):
            self.assertEqual((self.label(), self.top().kind), (text, kind))
            seen.append(self.top().id)

        # The rota, one shift at a time.
        app.set_regular({"task_id": "lunch", "weekday": "mon", "person_id": "ana"})
        result = app.reapply_regulars(M, {})
        expect("Regular week applied: 4 shifts filled")  # the Mondays of December
        self.assertEqual(len(result["days"]), 31)
        app.assign(M, {**self.slot("lunch", 1), "person_id": "ana"})
        expect("Ana B. → Lunch cook, Tue 1 Dec")
        app.assign(M, {**self.slot("lunch", 1), "person_id": "ben"})
        expect("Ben C. → Lunch cook, Tue 1 Dec, instead of Ana B.")
        app.assign(M, {**self.slot("lunch", 2), "person_id": "cleo", "force": True, "reason": "agreed"})
        expect("Cleo D. → Lunch cook, Wed 2 Dec (assigned anyway)")
        app.assign(M, {**self.slot("helper", 2), "guest_kind": "volunteer"})
        expect("A volunteer → Kitchen helper, Wed 2 Dec")
        app.assign(M, {**self.slot("helper", 3), "guest_kind": "guest"})
        expect("A guest → Kitchen helper, Thu 3 Dec")
        app.assign(M, {**self.slot("helper", 4), "guest": "Zed Quill"})
        expect("Zed Quill → Kitchen helper, Fri 4 Dec")
        app.clear(M, self.slot("lunch", 1))
        expect("Lunch cook, Tue 1 Dec left open (was Ben C.)")
        app.set_note(M, {**self.slot("lunch", 2), "note": "Arrives at ten"})
        expect("Note saved: Lunch cook, Wed 2 Dec")
        self.assertNotIn("ten", self.label())
        app.set_note(M, {**self.slot("lunch", 2), "note": ""})
        expect("Note removed: Lunch cook, Wed 2 Dec")
        app.highlight(M, {**self.slot("supper", 3), "on": True})
        expect("Highlighted to fill: Supper cook, Thu 3 Dec")
        app.highlight(M, {**self.slot("supper", 3), "on": False})
        expect("Highlight removed: Supper cook, Thu 3 Dec")
        app.headcount(M, {"task_id": "supper", "date": "2026-12-03", "count": 2})
        expect("Supper cook, Thu 3 Dec: 2 needed")
        app.headcount(M, {"task_id": "supper", "date": "2026-12-03", "count": 0})
        expect("Supper cook, Thu 3 Dec: not needed that day")
        app.headcount(M, {"task_id": "supper", "date": "2026-12-03", "count": None})
        expect("Supper cook, Thu 3 Dec: back to the usual number")
        app.set_time(M, {"task_id": "supper", "date": "2026-12-03", "start": "17:00", "end": "20:00"})
        expect("Supper cook, Thu 3 Dec: time changed for that day")
        app.set_time(M, {"task_id": "supper", "date": "2026-12-03", "clear": True})
        expect("Supper cook, Thu 3 Dec: back to the usual time")
        app.assign(M, {**self.slot("lunch", 8), "person_id": "ana"})
        app.swap(M, {"key": "supper|2026-12-08|0", "person_id": "ana", "from": "lunch|2026-12-08|0", "replacement": "ben"})
        expect("Exchange: Ana B. → Supper cook, Tue 8 Dec")

        # Suggestions accepted, and moves.
        proposed = app.prefill(M, {"dates": ["2026-12-20"]})["prefill"]["proposed"]
        self.assertGreater(proposed, 1)
        app.accept_proposals(M, {})
        expect(f"{proposed} suggestions accepted", "auto")
        app.assign(M, {**self.slot("supper", 22), "person_id": "ana"})
        app._proposals[M] = [Proposal("supper", "2026-12-22", 0, "ben", replaces="ana")]
        app.accept_proposals(M, {})
        expect("1 move accepted", "auto")

        # Set up: events, closures, days.
        event = app.save_event(M, {"event": {"name": "Big week", "start": "2026-12-14", "end": "2026-12-16"}})["events"][0]
        expect("Event added: Big week")
        app.save_event(M, {"event": {"id": event["id"], "name": "Big week!", "start": "2026-12-14", "end": "2026-12-16"}})
        expect("Event changed: Big week!")
        setup = app.save_event_task(M, {"task": {"event_id": event["id"], "name": "Desk", "area_id": "kitchen"}})
        expect("Event task added: Desk (Big week!)")
        desk = next(t for e in setup["events"] for t in e["tasks"] if t["name"] == "Desk")
        app.save_event_task(M, {"task": {"id": desk["id"], "event_id": event["id"], "name": "Front desk", "area_id": "kitchen"}})
        expect("Event task changed: Front desk (Big week!)")
        app.save_event_type({"type": {"name": "Retreat"}})
        type_id = app.catalog.event_types[0].id
        app.save_type_task({"task": {"type_id": type_id, "name": "Tea", "area_id": "kitchen"}})
        app.add_type_tasks(M, {"event_id": event["id"], "type_id": type_id})
        expect("1 task of “Retreat” added to Big week!")
        app.save_event_as_type(M, {"event_id": event["id"], "name": "Festival"})
        expect("Big week! set as a “Festival” event")
        app.delete_event_task(M, {"id": desk["id"]})
        expect("Event task removed: Front desk (Big week!)")
        app.delete_event(M, {"id": event["id"]})
        expect("Event removed: Big week!")
        marker = app.save_marker(M, {"marker": {"kind": "closed", "label": "Painting", "start": "2026-12-24",
                                                "end": "2026-12-24"}})["markers"][0]
        expect("Closure added: Painting")
        app.save_marker(M, {"marker": {"id": marker["id"], "kind": "closed", "label": "Painting the hall",
                                       "start": "2026-12-24", "end": "2026-12-25"}})
        expect("Closure changed: Painting the hall")
        app.delete_marker(M, {"id": marker["id"]})
        expect("Closure removed: Painting the hall")
        app.save_marker(M, {"marker": {"kind": "offrota", "label": "Concert", "start": "2026-12-26", "end": "2026-12-26"}})
        expect("Off-rota event added: Concert")
        app.save_marker(M, {"marker": {"kind": "note", "label": "Bring chairs", "start": "2026-12-27", "end": "2026-12-27"}})
        expect("Note added: Bring chairs")
        app.set_day_type(M, {"date": "2026-12-29", "day_type": "event"})
        expect("Tue 29 Dec set as an event day")
        app.set_day_type(M, {"date": "2026-12-29", "day_type": "normal"})
        expect("Tue 29 Dec set as a normal day")
        app.set_day_type(M, {"date": "2026-12-29", "day_type": None})
        expect("Tue 29 Dec: back to what the events say")

        # Availability: never the comment, and no name for a comment marked.
        result = app.availability_apply(M, {"replies": [
            {"person_id": "ana", "unavailable": ["2026-12-07/morning"], "comment": COMMENT},
            {"person_id": "ben", "unavailable": [], "comment": ""}]})
        expect("2 replies saved")
        self.assertEqual(result["id"], M)
        self.assertTrue(result["history"]["undo"])
        app.availability_set(M, {"person_id": "ben", "unavailable": ["2026-12-05/evening"]})
        expect("Availability changed: Ben C.")
        app.availability_comment(M, {"person_id": "ana", "done": True})
        expect("Comment marked as read")
        app.availability_comment(M, {"person_id": "ana", "done": False})
        expect("Comment marked as not read")
        app.free_regulars(M, {})
        expect("1 regular shift freed: people away")

        # The month's own tasks and areas (Tasks & Areas, that month).
        config = app.save_area({"scope": M, "area": {"name": "Garden"}})
        expect("Area added: Garden")
        self.assertTrue(config["history"]["undo"])
        garden = next(a for a in config["areas"] if a["name"] == "Garden")
        app.save_area({"scope": M, "area": {"id": garden["id"], "name": "Garden", "color": "green"}})
        expect("Area changed: Garden")
        app.order_areas({"scope": M, "ids": [garden["id"], "kitchen", "security"]})
        expect("Areas put in another order")
        config = app.save_task({"scope": M, "task": {"name": "Weeding", "area_id": garden["id"]}})
        expect("Task added: Weeding")
        weeding = next(t for t in config["tasks"] if t["name"] == "Weeding")
        app.save_task({"scope": M, "task": {**weeding, "hours": 2}})
        expect("Task changed: Weeding")
        config = app.duplicate_task({"scope": M, "id": weeding["id"]})
        copy = next(t for t in config["tasks"] if t["name"] != "Weeding" and t["area_id"] == garden["id"])
        expect(f"Task duplicated: {copy['name']}")
        app.order_tasks({"scope": M, "ids": [copy["id"], weeding["id"]]})
        expect("Tasks put in another order")
        result = app.use_config({"scope": M, "source": "typical"})
        expect("Tasks and areas taken from Typical month" + (
            f" · {result['removed']} placement{'s' if result['removed'] != 1 else ''} taken off" if result["removed"] else ""))
        self.assertIsNone(app.config("typical")["history"])

        # Versions.
        app.publish_version(M, {"audience": "manager"})
        expect("Version 1 given to the manager", "version")
        app.publish_version(M, {"audience": "residents"})
        expect("Version 2 published for the residents", "version")

        undo, _ = app.history.stacks(M)
        self.assertEqual(len(seen), len(set(seen)))
        for step in undo:
            self.assertNotEqual(step.label, "Change")
            self.assertNotIn("Dentist", step.label)

    def test_a_label_that_fails_never_undoes_the_change(self):
        def broken():
            raise RuntimeError("no")
        self.app._change(M, lambda m: m.wanted.append("lunch|2026-12-01|0") or True, label=broken)
        self.assertEqual(self.label(), "Change")
        self.assertIn("lunch|2026-12-01|0", self.app.month(M).wanted)

    def test_a_change_that_changes_nothing_records_and_saves_nothing(self):
        app = self.app
        app.assign(M, {**self.slot("lunch", 1), "person_id": "ana"})
        app.set_note(M, {**self.slot("lunch", 1), "note": "Arrives at ten"})
        saves = []
        original = self.store.save_month
        self.store.save_month = lambda month: saves.append(month.id) or original(month)
        before = app.history.stacks(M)
        app.clear(M, self.slot("lunch", 2))                                  # already open
        app.set_note(M, {**self.slot("lunch", 1), "note": "Arrives at ten"})  # the same note
        app.highlight(M, {**self.slot("supper", 3), "on": False})            # not highlighted
        app.set_day_type(M, {"date": "2026-12-29", "day_type": None})        # nothing set by hand
        app.availability_set(M, {"person_id": "cleo", "unavailable": []})
        app.availability_set(M, {"person_id": "cleo", "unavailable": []})   # the same again
        self.assertEqual(saves, [M])  # only the first availability_set changed something
        self.assertEqual(len(app.history.stacks(M)[0]), len(before[0]) + 1)
        # Saving an event's window unchanged records no step either.
        event = app.save_event(M, {"event": {"name": "Calm", "start": "2026-12-10", "end": "2026-12-11"}})["events"][0]
        steps = len(app.history.stacks(M)[0])
        app.save_event(M, {"event": {**event}})
        self.assertEqual(len(app.history.stacks(M)[0]), steps)

    # -- undo, redo, going back ---------------------------------------------------

    def five_changes(self):
        """Five changes; returns the month (as stored) before each and after the last, and the steps."""
        states, steps = [self.app.month(M).to_dict()], []
        for day in range(1, 6):
            self.app.assign(M, {**self.slot("lunch", day), "person_id": "ana" if day % 2 else "ben"})
            states.append(self.app.month(M).to_dict())
            steps.append(self.top())
        return states, steps

    def test_what_the_screen_is_told(self):
        states, steps = self.five_changes()
        result = self.app.undo(M, {})
        self.assertEqual(result["did"], {"text": "Undone: Ana B. → Lunch cook, Sat 5 Dec", "back": steps[4].id,
                                         "back_label": "Redo"})
        self.assertEqual(result["history"]["redo"], {"id": steps[4].id, "label": "Ana B. → Lunch cook, Sat 5 Dec"})
        self.assertEqual(result["history"]["undo"]["id"], steps[3].id)
        result = self.app.history_go(M, {"to": result["did"]["back"]})  # the message's Redo
        self.assertEqual(result["did"], {"text": "Redone: Ana B. → Lunch cook, Sat 5 Dec", "back": steps[4].id,
                                         "back_label": "Undo"})
        self.assertEqual(self.app.month(M).to_dict(), states[5])
        result = self.app.history_go(M, {"to": steps[2].id})
        self.assertEqual(result["did"], {"text": "Went back 3 changes, to before “Ana B. → Lunch cook, Thu 3 Dec”",
                                         "back": steps[4].id, "back_label": "Redo them"})
        self.assertEqual(self.app.month(M).to_dict(), states[2])
        result = self.app.history_go(M, {"to": result["did"]["back"]})
        self.assertEqual(result["did"], {"text": "3 changes redone", "back": steps[2].id, "back_label": "Undo them"})
        self.assertEqual(self.app.month(M).to_dict(), states[5])
        self.app.history_go(M, {"to": result["did"]["back"]})
        self.assertEqual(self.app.month(M).to_dict(), states[2])

    def test_going_back_several_steps_is_stepping(self):
        states, steps = self.five_changes()
        self.app.history_go(M, {"to": steps[1].id})
        self.assertEqual(self.app.month(M).to_dict(), states[1])
        self.assertEqual(self.app.store.load_month(M).to_dict(), states[1])  # saved
        for n in (2, 3, 4, 5):
            self.app.redo(M, {})
            self.assertEqual(self.app.month(M).to_dict(), states[n])
        for n in (4, 3):
            self.app.undo(M, {})
            self.assertEqual(self.app.month(M).to_dict(), states[n])
        self.app.history_go(M, {"to": steps[4].id})  # in the redo list: up to and including it
        self.assertEqual(self.app.month(M).to_dict(), states[5])
        self.assertIsNone(self.app.history.top(M, redo=True))

    def test_refusals(self):
        _, steps = self.five_changes()
        for call, body, text in ((self.app.undo, {"expect": steps[3].id},
                                  "Something else was changed since. Open Recent changes to choose where to go back to."),
                                 (self.app.redo, {}, "Nothing to redo."),
                                 (self.app.history_go, {"to": 12345}, "This list is out of date: open it again.")):
            before = self.app.month(M).to_dict()
            with self.assertRaises(ApiError) as caught:
                call(M, body)
            self.assertEqual((caught.exception.status, caught.exception.payload["error"]), (HTTPStatus.CONFLICT, text))
            self.assertEqual(self.app.month(M).to_dict(), before)  # nothing moved
            self.assertEqual(self.top().id, steps[4].id)
        self.app.undo(M, {"expect": steps[4].id})
        self.assertEqual(self.top().id, steps[3].id)
        self.app.history_go(M, {"to": steps[0].id})
        with self.assertRaises(ApiError) as caught:
            self.app.undo(M, {})
        self.assertEqual(caught.exception.payload["error"], "Nothing to undo.")

    def test_the_list(self):
        _, steps = self.five_changes()
        self.app.undo(M, {})
        self.app.undo(M, {})
        listed = self.app.history_list(M)
        self.assertEqual(listed["title"], "Recent changes · December 2026")
        self.assertEqual([(s["id"], s["count"]) for s in listed["steps"]],
                         [(steps[2].id, 1), (steps[1].id, 2), (steps[0].id, 3)])
        self.assertEqual([(s["id"], s["count"]) for s in listed["undone"]], [(steps[4].id, 2), (steps[3].id, 1)])
        self.assertEqual(listed["steps"][0]["label"], "Ana B. → Lunch cook, Thu 3 Dec")
        self.assertEqual({s["when"] for s in listed["steps"] + listed["undone"]}, {"just now"})
        self.assertEqual(listed["note"], "Kept until Rota Planner is closed.")
        self.app.history._undo[M][0].at = time.time() - 3600 * 3
        self.assertEqual(self.app.history_list(M)["steps"][-1]["when"], "3 h ago")
        self.app.history.drop(M)
        listed = self.app.history_list(M)
        self.assertEqual((listed["steps"], listed["undone"]), ([], []))
        self.assertEqual(listed["empty"], "Nothing to undo in December 2026 yet. "
                                          "What you change appears here until Rota Planner is closed.")

    # -- suggestions ----------------------------------------------------------------

    def test_undoing_accepted_suggestions_shows_them_again(self):
        app = self.app
        for person in app.people:
            if person.id != "ana":
                person.active = False  # too few people: some shifts stay open, and the Assistant says why
        week = [f"2026-12-{d:02d}" for d in range(7, 14)]
        suggested = app.prefill(M, {"dates": week})
        self.assertTrue(suggested["proposals"] and suggested["suggested"])
        keys = {p["key"] for p in suggested["proposals"]}
        app.accept_proposals(M, {})
        after = app.month_view(M)
        self.assertEqual((after["proposals"], after["suggested"]), ([], None))
        self.assertEqual(after["history"]["auto"]["text"], f"{len(keys)} suggestions accepted.")

        result = app.undo(M, {})
        self.assertEqual(result["did"]["text"], f"Undone: {len(keys)} suggestions accepted. They are shown dashed again.")
        self.assertEqual({p["key"] for p in result["proposals"]}, keys)
        self.assertEqual(result["suggested"], suggested["suggested"])  # with what stays open, and why
        app.prefill(M, {"beyond": True})  # "Suggest for these too" works again
        app.prefill(M, {"dates": week})

        app.accept_proposals(M, {})
        app.undo(M, {})
        result = app.redo(M, {})
        self.assertEqual((result["proposals"], result["suggested"]), ([], None))  # gone again
        with self.assertRaises(ApiError):
            app.prefill(M, {"beyond": True})

    def test_the_assistant_offers_to_undo_accepted_suggestions(self):
        app = self.app
        app.prefill(M, {"dates": ["2026-12-20"]})
        app.accept_proposals(M, {})
        accepted = self.top()
        auto = app.month_view(M)["history"]["auto"]
        self.assertEqual(auto, {"id": accepted.id, "text": f"{accepted.label}.", "button": "Undo"})
        app.highlight(M, {**self.slot("supper", 3), "on": True})
        self.assertEqual(app.month_view(M)["history"]["auto"],
                         {"id": accepted.id, "text": f"{accepted.label}, then 1 more change.", "button": "Undo all 2"})
        app.highlight(M, {**self.slot("supper", 3), "on": False})
        app.highlight(M, {**self.slot("supper", 3), "on": True})
        self.assertEqual(app.month_view(M)["history"]["auto"]["text"], f"{accepted.label}, then 3 more changes.")
        self.assertEqual(app.month_view(M)["history"]["auto"]["button"], "Undo all 4")
        for n in range(17):
            app.highlight(M, {**self.slot("supper", 3), "on": n % 2 == 1})  # each one a change
        self.assertEqual(app.month_view(M)["history"]["auto"]["button"], "Undo all 21")
        app.highlight(M, {**self.slot("supper", 3), "on": True})
        self.assertIsNone(app.month_view(M)["history"]["auto"])  # 21 changes after it: no longer offered
        app.history_go(M, {"to": accepted.id})
        self.assertTrue(app.month_view(M)["proposals"])

    # -- what must not move, or not be kept ----------------------------------------

    def test_who_was_emailed_stays_known_across_a_jump(self):
        _, steps = self.five_changes()
        self.app.month(M).mailed["ana"] = 1
        self.app.history_go(M, {"to": steps[1].id})
        self.assertEqual(self.app.month(M).mailed, {"ana": 1})
        self.app.history_go(M, {"to": steps[4].id})
        self.assertEqual(self.app.month(M).mailed, {"ana": 1})

    def test_deleted_comments_are_gone_from_every_step(self):
        app = self.app
        app.availability_apply(M, {"replies": [{"person_id": "ana", "unavailable": [], "comment": COMMENT}]})
        app.availability_comment(M, {"person_id": "ana", "done": True})
        app.assign(M, {**self.slot("lunch", 1), "person_id": "ben"})
        app.undo(M, {})
        app.forget_comments(M)
        undo, redo = app.history.stacks(M)
        self.assertTrue(undo and redo)
        for step in undo + redo:
            self.assertNotIn("Dentist", json.dumps(unpack(step.snapshot)))
            self.assertNotIn("Dentist", step.label)
        app.history_go(M, {"to": undo[0].id})  # still works after
        self.assertNotIn("Dentist", json.dumps(app.month(M).to_dict()))

    def test_no_new_file_in_the_data_folder(self):
        def files():
            root = Path(self.tmp.name)
            return {p.relative_to(root).as_posix() for p in root.rglob("*")
                    if p.is_file() and not p.relative_to(root).as_posix().startswith("backups/")}
        before = files()
        _, steps = self.five_changes()
        self.app.undo(M, {})
        self.app.history_go(M, {"to": steps[0].id})
        self.app.history_list(M)
        self.app.history_go(M, {"to": steps[4].id})
        self.assertEqual(files(), before)


if __name__ == "__main__":
    unittest.main()
