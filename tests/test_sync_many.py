"""Several computers, through a shared folder or a (pretend) WebDAV server:
who has the newest version, working offline, a backup put back, the pen."""

import json
import random
import shutil
import tempfile
import time
import unittest
import unittest.mock
from datetime import date
from pathlib import Path

from rotaplanner import sync as sy
from rotaplanner.model import Assignment, PersonAvailability
from rotaplanner.store import Store
from rotaplanner.sync import PenError, Sync, SyncError

from . import helpers as h
from .webdav_server import PASSWORD, USER, WebDavServer

DECEMBER, PEOPLE = "months/2026-12.json", "people.json"


def fill(store: Store) -> None:
    store.save_catalog(h.catalog())
    store.save_people(h.people())
    store.save_month(h.month())


class RankTest(unittest.TestCase):
    def test_who_has_the_newest(self):
        rank = sy.rank
        same = rank(({"a": 1}, "h1"), {"b": ({"a": 1}, "h1")})
        self.assertEqual(same["state"], "same")
        self.assertEqual(rank(({"a": 2}, "h2"), {"b": ({"a": 1}, "h1")})["state"], "sent")
        self.assertEqual(rank(({"a": 2}, "h2"), {"b": ({"a": 1}, "h1")})["waiting"], ["b"])
        behind = rank(({"a": 1}, "h1"), {"b": ({"a": 1, "b": 1}, "h2"), "c": ({"a": 1}, "h1")})
        self.assertEqual((behind["state"], behind["source"]), ("get", "b"))
        # Two others changed it: one must be chosen, even by a computer that changed nothing.
        split = rank(({"a": 1}, "h1"), {"b": ({"a": 1, "b": 1}, "h2"), "c": ({"a": 1, "c": 1}, "h3")})
        self.assertEqual((split["state"], split["candidates"]), ("both", ["b", "c"]))
        mine_too = rank(({"a": 2}, "h4"), {"b": ({"a": 1, "b": 1}, "h2")})
        self.assertEqual((mine_too["state"], mine_too["candidates"]), ("both", ["", "b"]))
        # Not there on this computer, there elsewhere; and the other way round.
        self.assertEqual(rank(None, {"b": ({"b": 1}, "h1")})["state"], "get")
        self.assertEqual(rank(({"a": 1}, "h1"), {}, missing=["b"])["waiting"], ["b"])
        # The same history with another content must never pass for "in sync".
        self.assertEqual(rank(({"a": 1}, "h1"), {"b": ({"a": 1}, "h2")})["state"], "both")
        # The same content made separately is one version.
        self.assertEqual(rank(({"a": 1}, "h1"), {"b": ({"b": 1}, "h1")})["state"], "same")


class Computers(unittest.TestCase):
    """Three computers; ``kind`` says where they meet."""
    kind = "folder"
    names = ("Office", "Front desk", "Kitchen")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.today = date(2026, 11, 20)
        if self.kind == "webdav":
            self.server = WebDavServer()
            self.addCleanup(self.server.close)
            self.place = {"kind": "webdav", "url": self.server.url, "user": USER, "password": PASSWORD}
        else:
            (base / "shared").mkdir()
            self.place = str(base / "shared")
        self.stores, self.syncs = [], []
        for index, name in enumerate(self.names):
            store = Store(base / f"data{index}")
            store.root.mkdir()
            if index == 0:
                fill(store)
            one = Sync(store, today=lambda: self.today)
            if index == 0:
                self.code = one.start(name, self.place)
            else:
                one.join(name, self.place, self.code)
            self.stores.append(store)
            self.syncs.append(one)
        self.a, self.b, self.c = self.syncs

    def round(self, *who):
        """One round of the courier on each (all by default), twice so that all have seen all."""
        for _ in range(2):
            for one in who or self.syncs:
                one.exchange()

    def settle(self):
        """Everyone takes what needs no choice, until nothing moves."""
        for _ in range(6):
            self.round()
            if not any(one.apply_automatic()["taken"] for one in self.syncs):
                break
        self.round()

    def states(self, one):
        return {u["unit"]: u["state"] for u in one.status()["units"]}

    def note(self, index, text):
        people = self.stores[index].load_people()
        people[0].note = text
        self.stores[index].save_people(people)

    def lunch(self, index, person):
        month = self.stores[index].load_month("2026-12")
        month.assignments.append(Assignment("lunch", "2026-12-02", 0, person_id=person))
        self.stores[index].save_month(month)


class ThreeComputersTest(Computers):
    def test_everyone_gets_everything(self):
        self.settle()
        for store in self.stores:
            self.assertEqual(len(store.load_people()), 4)
        for one in self.syncs:
            self.assertEqual(self.states(one), {})
            self.assertEqual(one.status()["summary"][:2], ["In sync", "ok"])
            self.assertEqual(len(one.status()["others"]), 2)

    def test_a_change_reaches_all_by_itself(self):
        self.settle()
        self.note(1, "from the front desk")
        self.round()
        self.assertEqual(self.states(self.a), {PEOPLE: "get"})
        self.assertEqual(self.states(self.b), {PEOPLE: "sent"})
        status = self.b.status()
        self.assertEqual([c["state"] for c in status["others"]], ["behind", "behind"])
        self.assertIn(" and ", status["summary"][2])  # both are named as not having taken it yet
        self.assertEqual((status["me"]["text"], status["me"]["editing"]), ("Up to date", False))
        self.assertFalse(self.a.status()["needs_choice"])  # taken by itself: nothing to decide
        done = self.a.apply_automatic()
        self.assertEqual((done["taken"], done["from"]), (["People"], ["Front desk"]))
        self.assertIn("Took People from Front desk.", [e["text"] for e in self.a.status()["log"]])
        self.assertEqual(self.a.status()["applied"]["seq"], 1)  # what the open page watches to reload
        self.settle()
        self.assertEqual([s.load_people()[0].note for s in self.stores], ["from the front desk"] * 3)

    def test_two_changed_the_same_thing_and_the_third_may_choose(self):
        self.settle()
        self.lunch(0, "ana")
        self.lunch(1, "ben")
        self.settle()
        for one in self.syncs:
            self.assertEqual(self.states(one), {DECEMBER: "both"})
            self.assertTrue(one.status()["needs_choice"])
        self.assertEqual(self.c.apply_automatic()["taken"], [])  # never by itself
        preview = self.c.preview()
        choices = {c["name"]: c["id"] for c in preview["items"][0]["choices"]}
        self.assertEqual(sorted(choices), ["Front desk", "Office"])
        with self.assertRaisesRegex(SyncError, "Choose which December 2026"):
            self.c.apply(preview["plan"], {})
        done = self.c.apply(preview["plan"], {DECEMBER: choices["Front desk"]})
        self.assertTrue(done["safety"].startswith("before-"))
        self.settle()  # the choice wins everywhere, with no more question
        for one, store in zip(self.syncs, self.stores):
            self.assertEqual(self.states(one), {})
            lunch = [x.person_id for x in store.load_month("2026-12").assignments if x.date == "2026-12-02" and x.task_id == "lunch"]
            self.assertEqual(lunch, ["ben"])

    def test_a_work_changed_here_is_kept_apart_when_replaced(self):
        self.settle()
        self.lunch(0, "ana")
        self.lunch(1, "ben")
        self.round()
        preview = self.a.preview()
        done = self.a.apply(preview["plan"], {DECEMBER: "theirs"})
        self.assertTrue(done["safety"].startswith("before-choice-"))  # kept longer than the routine copies

    def test_random_histories_converge(self):
        rng = random.Random(7)
        self.settle()
        for step in range(40):
            who = rng.randrange(3)
            action = rng.choice(["note", "note", "lunch", "round", "auto", "auto"])
            if action == "note":
                self.note(who, f"note {step}")
            elif action == "lunch":
                self.lunch(who, rng.choice(["ana", "ben", "cleo"]))
            elif action == "round":
                self.syncs[who].exchange()
            else:
                self.syncs[who].exchange()
                self.syncs[who].apply_automatic()
        for _ in range(8):  # then: everyone looks, and whoever is asked keeps their own
            self.settle()
            asked = False
            for one in self.syncs:
                preview = one.preview()
                both = [i for i in preview["items"] if i["state"] == "both"]
                if both:
                    one.apply(preview["plan"], {i["unit"]: i["choices"][0]["id"] for i in both})
                    asked = True
            if not asked:
                break
        contents = [{name: json.loads((s.root / name).read_text()) for name in (PEOPLE, DECEMBER)} for s in self.stores]
        self.assertEqual(contents[0], contents[1])
        self.assertEqual(contents[1], contents[2])
        for one in self.syncs:
            self.assertEqual(self.states(one), {})

    def test_a_computer_that_left_is_forgotten(self):
        self.settle()
        gone = self.c.state["computer_id"]
        self.a.forget(gone)
        self.round(self.a, self.b)
        self.assertEqual(len(self.a.status()["others"]), 1)
        self.assertEqual(len(self.b.status()["others"]), 1)
        with self.assertRaises(SyncError):
            self.a.forget(self.a.state["computer_id"])
        self.c.exchange()  # still running after all: it simply comes back
        self.round(self.a)
        self.assertEqual(len(self.a.status()["others"]), 2)

    def test_the_month_ending_is_not_a_change(self):
        month = self.stores[0].load_month("2026-12")
        month.availability["ana"] = PersonAvailability(unavailable=[], comment="private words")
        self.stores[0].save_month(month)
        self.settle()
        self.today = date(2027, 1, 2)
        self.round()  # each computer stops sending the comments, on its own
        for one in self.syncs:
            self.assertEqual(self.states(one), {})
        _, payload = sy.unseal(self.a._key, (self.a.cache / sy.OUTBOX).read_bytes())
        self.assertNotIn(b"private words", payload)
        self.note(1, "after the new year")  # and real changes still travel
        self.settle()
        self.assertEqual(self.stores[2].load_people()[0].note, "after the new year")

    def test_a_backup_put_back_is_noticed(self):
        self.settle()
        backup = Path(self.tmp.name) / "backup"
        shutil.copytree(self.stores[0].root, backup)
        self.note(0, "written after the backup")
        self.settle()
        shutil.rmtree(self.stores[0].root)
        shutil.copytree(backup, self.stores[0].root)
        restored = Sync(self.stores[0], today=lambda: self.today)
        self.syncs[0] = self.a = restored
        self.note(0, "written on the old data")
        restored.exchange()
        status = restored.status()
        self.assertEqual((status["hold"], status["kind"]), ("restored", "restored"))
        self.assertEqual(restored.apply_automatic()["taken"], [])  # nothing moves until the coordinator says
        self.round(self.b, self.c)
        self.assertEqual(self.b.apply_automatic()["taken"], [])  # and the old data never passes for new
        self.assertEqual(self.stores[1].load_people()[0].note, "written after the backup")
        self.assertEqual(self.states(self.b), {PEOPLE: "both"})
        restored.resume("mine")
        self.settle()
        self.assertEqual([s.load_people()[0].note for s in self.stores], ["written on the old data"] * 3)

    def test_what_was_removed_here_is_not_brought_back_by_itself(self):
        self.settle()
        (self.stores[1].root / DECEMBER).unlink()
        self.round()
        self.assertEqual(self.states(self.b), {DECEMBER: "get"})
        self.assertEqual(self.b.apply_automatic()["taken"], [])
        preview = self.b.preview()
        self.b.apply(preview["plan"], {})  # asked for: it comes back
        self.assertTrue((self.stores[1].root / DECEMBER).exists())

    def test_the_courier_does_it_all_in_the_background(self):
        told = []
        self.b.on_incoming = lambda: told.append(self.b.apply_automatic()["taken"])
        self.b.start_background()
        self.addCleanup(self.b.goodbye)
        self.note(0, "seen by the courier")
        self.a.exchange()
        self.b.touch()
        for _ in range(100):
            if any("People" in taken for taken in told):
                break
            time.sleep(0.1)
        self.assertEqual(self.stores[1].load_people()[0].note, "seen by the courier")

    def test_a_folder_that_was_moved_leaves_no_ghost(self):
        self.settle()
        moved = Path(self.tmp.name) / "moved"
        shutil.move(str(self.stores[0].root), str(moved))  # as after reinstalling with the data brought over
        again = Sync(Store(moved), today=lambda: self.today)
        again.exchange()
        self.round(self.b, self.c)
        self.assertEqual(sorted(c["name"] for c in self.b.status()["others"]), ["Kitchen", "Office"])
        self.assertEqual(self.states(self.b), {})

    def test_automatic_taking_can_be_turned_off(self):
        self.b.set_auto(False)
        self.round()
        self.assertEqual(self.b.apply_automatic()["taken"], [])
        self.assertTrue(self.b.status()["needs_choice"])  # newer data waits for the coordinator
        self.assertEqual(len(self.c.apply_automatic()["taken"]), 3)


class WebDavTest(Computers):
    kind = "webdav"

    def test_through_a_server(self):
        self.settle()
        self.note(0, "over the internet")
        self.settle()
        self.assertEqual([s.load_people()[0].note for s in self.stores], ["over the internet"] * 3)
        self.assertTrue(all(name.endswith(".rpsync") for name in self.server.files))
        self.assertNotIn(b"over the internet", b"".join(f[0] for f in self.server.files.values()))  # sealed
        status = self.a.status()
        self.assertEqual((status["place"]["kind"], status["place"]["has_password"]), ("webdav", True))
        self.assertNotIn(PASSWORD, json.dumps(status))

    def test_the_screens_never_wait_for_the_server(self):
        self.settle()
        self.server.requests.clear()
        self.a.status()
        self.a.preview()
        self.assertEqual(self.server.requests, [])

    def test_offline_then_back(self):
        self.settle()
        self.server.mode = "down"
        self.note(0, "written offline")
        self.assertFalse(self.a.exchange()["ok"])
        status = self.a.status()
        self.assertEqual(status["summary"][0], "Offline · changes waiting")
        self.assertEqual((status["icon"], status["connection"]["online"]), ("offline", False))
        self.assertIn("Connection lost", status["log"][0]["text"])
        self.a.exchange()
        self.assertEqual(len([e for e in self.a.status()["log"] if "Connection lost" in e["text"]]), 1)  # said once
        self.note(1, "meanwhile, elsewhere")  # the others cannot be reached either
        self.server.mode = "ok"
        self.assertTrue(self.a.exchange()["sent"])
        self.assertIn("Back online", self.a.status()["log"][0]["text"])
        self.round()
        self.assertEqual(self.states(self.a), {PEOPLE: "both"})  # both wrote while apart: asked, nothing lost
        self.assertEqual(self.a.apply_automatic()["taken"], [])

    def test_a_refused_password_is_said_and_not_hammered(self):
        self.settle()
        self.server.mode = "auth"
        self.a.exchange()
        status = self.a.status()
        self.assertEqual((status["kind"], status["summary"][:2]), ("auth", ["Needs attention", "bad"]))
        self.assertIn("app password", status["problem"])
        self.assertEqual((status["to_fix"], status["passing"]), (True, False))  # for the coordinator, in Settings
        self.server.mode = "down"
        self.a.exchange()
        status = self.a.status()
        self.assertEqual((status["to_fix"], status["passing"]), (False, True))  # passes by itself

    def test_a_sign_in_page_is_not_taken_for_an_empty_server(self):
        self.settle()
        self.server.mode = "portal"
        self.a.exchange()
        self.assertEqual(len(self.a.status()["others"]), 2)  # nobody "left"
        self.assertEqual(self.a.status()["kind"], "offline")

    def test_the_place_can_be_tested_and_changed(self):
        with self.assertRaisesRegex(SyncError, "refuses the user name"):
            self.a.test_place(dict(self.place, password="wrong"))
        self.assertEqual(self.a.test_place(dict(self.place, password=""))["kind"], "webdav")  # the one kept
        other = WebDavServer()
        self.addCleanup(other.close)
        for one in self.syncs:
            one.change_place({"kind": "webdav", "url": other.url, "user": USER, "password": PASSWORD})
        self.settle()
        self.assertEqual(len(other.files), 3)
        self.assertEqual(self.server.files, {})  # nothing left behind
        self.assertEqual(self.states(self.c), {})

    def test_stop_removes_this_computers_files(self):
        self.settle()
        self.assertTrue(self.c.stop())
        self.assertEqual(len(self.server.files), 2)
        self.assertFalse((self.stores[2].root / "sync.json").exists())
        self.assertEqual(len(self.stores[2].load_people()), 4)  # the data stays


class PenTest(Computers):
    kind = "webdav"

    def setUp(self):
        super().setUp()
        self.settle()

    def test_one_edits_and_the_others_look(self):
        self.assertFalse(self.a.ensure_pen())
        self.assertTrue(self.a.pen()["mine"])
        self.round(self.b)
        self.assertEqual(self.b.pen()["text"][:17], "Office is editing")
        with self.assertRaises(PenError) as caught:
            self.b.ensure_pen()
        self.assertEqual((caught.exception.kind, caught.exception.info["name"]), ("held", "Office"))
        self.a.release_pen()
        self.assertFalse(self.b.ensure_pen())
        self.assertTrue(self.b.pen()["mine"])
        self.round(self.a)
        with self.assertRaises(PenError):
            self.a.ensure_pen()

    def test_two_asking_at_the_same_moment(self):
        # B's note reaches the server while A is still asking: only one may hold the pen.
        self.b.state["pen"] = {"since": self.b._now_there()}
        self.server.files[f"pen-{self.b.state['computer_id']}.rpsync"] = (self.b._pen_note(False), self.server.now() - 1, 9999)
        with self.assertRaises(PenError):
            self.a.take_pen()
        self.assertFalse(self.a.pen()["mine"])
        self.assertNotIn(f"pen-{self.a.state['computer_id']}.rpsync", self.server.files)

    def test_newer_data_is_taken_before_editing(self):
        self.note(1, "newer")
        self.b.exchange()
        self.a.on_incoming = self.a.apply_automatic
        self.assertTrue(self.a.ensure_pen())  # the screen is told: what it showed may have changed
        self.assertEqual(self.stores[0].load_people()[0].note, "newer")

    def test_the_holder_goes_away_and_another_takes_over(self):
        self.a.ensure_pen()
        self.round(self.b)
        self.server.skew = sy.PEN_STALE_SECONDS + 60  # no word from A for a while
        self.b.exchange()
        self.assertTrue(self.b.pen()["stale"])
        with self.assertRaises(PenError) as caught:
            self.b.ensure_pen()
        self.assertEqual(caught.exception.kind, "stale")  # never silently
        self.b.take_pen(force=True)
        self.assertTrue(self.b.pen()["mine"])
        self.a.exchange()  # A comes back: it no longer holds the pen, and is told why
        self.assertFalse(self.a.pen()["mine"])
        self.assertIn("took over", self.a.pen()["lost"])

    def test_the_pen_is_held_by_saying_so_again(self):
        self.a.ensure_pen()
        name = f"pen-{self.a.state['computer_id']}.rpsync"
        first = self.server.files[name][2]
        self.a.state["pen"]["beat"] = time.time() - sy.PEN_BEAT_SECONDS - 1
        self.a.exchange()
        self.assertNotEqual(self.server.files[name][2], first)
        self.a._last_edit = time.time() - sy.PEN_IDLE_SECONDS - 1  # a long while without a change
        self.a.exchange()
        self.assertNotIn(name, self.server.files)
        self.assertFalse(self.a.pen()["mine"])

    def test_no_connection_no_pen_unless_decided(self):
        self.server.mode = "down"
        with self.assertRaises(PenError) as caught:
            self.a.ensure_pen()
        self.assertEqual(caught.exception.kind, "offline")
        self.a.work_offline(True)
        self.assertFalse(self.a.ensure_pen())
        self.assertTrue(self.a.pen()["working_offline"])
        self.note(0, "offline, knowingly")
        self.server.mode = "ok"
        self.settle()
        self.assertEqual(self.stores[1].load_people()[0].note, "offline, knowingly")

    def test_the_holder_keeps_working_without_connection(self):
        self.a.ensure_pen()
        self.server.mode = "down"
        self.assertFalse(self.a.ensure_pen())  # still theirs
        self.note(0, "while disconnected")
        self.a.exchange()
        self.server.mode = "ok"
        self.settle()
        self.assertEqual(self.stores[2].load_people()[0].note, "while disconnected")

    def test_alone_no_pen_is_needed(self):
        store = Store(Path(self.tmp.name) / "alone")
        store.root.mkdir()
        fill(store)
        lone_server = WebDavServer()
        self.addCleanup(lone_server.close)
        lone = Sync(store)
        lone.start("Only one", {"kind": "webdav", "url": lone_server.url, "user": USER, "password": PASSWORD})
        self.assertFalse(lone.ensure_pen())
        self.assertFalse(lone.pen()["mine"])
        self.assertEqual(list(lone_server.files), [f"rota-{lone.state['computer_id']}.rpsync"])


class HandOverTest(Computers):
    """"Ask to edit": the computer editing sends its changes and hands over by itself."""
    kind = "webdav"

    def setUp(self):
        super().setUp()
        self.settle()
        self.a.ensure_pen()
        self.a._last_edit = time.time() - sy.GROUP_SECONDS - 1  # its last change is fully written
        self.round(self.b)

    def test_asking_the_one_editing_to_hand_over(self):
        self.note(0, "made before the hand-over")  # not sent yet
        status = self.b.ask_pen()
        self.assertTrue(status["asking"])
        self.assertIn("Asking Office", status["text"])
        self.assertIn("take over in 5 min", status["hint"])
        self.assertTrue(self.a.pen()["mine"])  # A holds it until its next round
        self.a.exchange()  # which sends its change, then lets go
        self.assertFalse(self.a.pen()["mine"])
        self.assertIn("Front desk took over the editing", self.a.pen()["notice"]["text"])
        self.assertNotIn(f"pen-{self.a.state['computer_id']}.rpsync", self.server.files)
        with self.assertRaises(PenError) as caught:
            self.a.take_pen()  # nobody on A can snatch it back before B has it
        self.assertEqual(caught.exception.kind, "handing")
        self.b.on_incoming = self.b.apply_automatic
        self.b.exchange()
        self.b.follow_up()
        self.assertTrue(self.b.pen()["mine"])
        self.assertFalse(self.b.pen()["asking"])
        self.assertEqual(self.stores[1].load_people()[0].note, "made before the hand-over")
        self.round(self.a)
        self.assertEqual(self.a.pen()["name"], "Front desk")
        with self.assertRaises(PenError):
            self.a.ensure_pen()

    def test_the_changes_are_taken_even_when_taking_by_itself_is_off(self):
        self.b.set_auto(False)
        self.note(0, "newer")
        self.b.ask_pen()
        self.a.exchange()
        self.b.on_incoming = self.b.apply_automatic
        self.b.exchange()
        self.b.follow_up()
        self.assertTrue(self.b.pen()["mine"])
        self.assertEqual(self.stores[1].load_people()[0].note, "newer")

    def test_a_change_let_through_a_moment_ago_waits_for_the_next_round(self):
        self.b.ask_pen()
        self.a._last_edit = time.time()  # a save is still being written
        self.a.exchange()
        self.assertTrue(self.a.pen()["mine"])
        self.a._last_edit = time.time() - sy.GROUP_SECONDS - 1
        self.a.exchange()
        self.assertFalse(self.a.pen()["mine"])

    def test_closing_either_computer(self):
        self.b.ask_pen()
        self.b.goodbye()  # the asker closes: its request goes with it
        self.assertNotIn(f"pen-{self.b.state['computer_id']}.rpsync", self.server.files)
        self.a.exchange()
        self.assertTrue(self.a.pen()["mine"])
        self.c.ask_pen()
        self.a.goodbye()  # the holder closes: the asker simply takes the pen
        self.c.exchange()
        self.c.follow_up()
        self.assertTrue(self.c.pen()["mine"])

    def test_who_asked_first_is_first_in_line(self):
        self.b.ask_pen()
        self.a.release_pen()
        self.round(self.c)
        with self.assertRaises(PenError) as caught:
            self.c.ensure_pen()
        self.assertIn("Front desk asked for the editing first", str(caught.exception))
        self.b.exchange()
        self.b.follow_up()
        self.assertTrue(self.b.pen()["mine"])

    def test_asking_and_changing_ones_mind(self):
        self.b.ask_pen()
        name = f"pen-{self.b.state['computer_id']}.rpsync"
        self.assertIn(name, self.server.files)
        self.b.release_pen()  # "Cancel"
        self.assertNotIn(name, self.server.files)
        self.assertFalse(self.b.pen()["asking"])
        self.a.exchange()
        self.assertTrue(self.a.pen()["mine"])

    def test_asking_when_nobody_edits(self):
        self.a.release_pen()
        self.round(self.b)
        self.assertTrue(self.b.ask_pen()["mine"])  # simply taken

    def test_asking_a_computer_gone_quiet(self):
        self.server.skew = sy.PEN_STALE_SECONDS + 60
        with self.assertRaises(PenError) as caught:
            self.b.ask_pen()
        self.assertEqual(caught.exception.kind, "stale")  # "Take over…", knowingly

    def test_the_asked_computer_goes_quiet_while_asked(self):
        self.b.ask_pen()
        self.server.skew = sy.PEN_STALE_SECONDS + 60
        self.b.exchange()
        self.b.follow_up()
        pen = self.b.pen()
        self.assertTrue(pen["asking"] and pen["stale"])
        self.assertIn("does not answer", pen["text"])
        self.assertFalse(pen["mine"])  # never taken without the coordinator's word

    def test_a_computer_that_never_answers(self):
        self.b.ask_pen()
        self.b.state["pen"]["asked"] = time.time() - sy.ASK_GIVE_UP_SECONDS - 1
        with unittest.mock.patch.object(self.a, "_hand_over"):  # an older version: never hands over
            self.a.exchange()
        self.b.exchange()
        self.b.follow_up()
        pen = self.b.pen()
        self.assertFalse(pen["asking"] or pen["mine"])
        self.assertIn("did not hand over", pen["notice"]["text"])
        self.b.release_pen()  # "OK"
        self.assertIsNone(self.b.pen()["notice"])

    def test_a_request_only_counts_from_a_real_recent_note(self):
        me, them = self.a.state["computer_id"], self.b.state["computer_id"]
        name = f"pen-{them}.rpsync"
        forged = json.dumps({"kind": "pen", "computer_id": them, "name": "x", "holding": False,
                             "since": 0, "beat": self.server.now(), "asking": me}).encode()
        self.server.files[name] = (sy.seal(sy.derive_key(sy.new_code()), them, forged), self.server.now(), 999)
        self.a.exchange()
        self.assertTrue(self.a.pen()["mine"])  # sealed with another code: means nothing
        self.b.state["pen"] = {"asking": me, "name": "Office"}
        self.server.files[name] = (self.b._pen_note(False), self.server.now(), 998)
        self.server.skew = sy.PEN_STALE_SECONDS + 60  # an old request, put back
        self.a.state["pen"]["beat"] = 0
        self.a.exchange()
        self.assertTrue(self.a.pen()["mine"])


class SomeoneWritesAtThePlaceTest(Computers):
    """Someone who can write files on the server, but does not have the pairing
    code, must not be able to stop the computers working, nor fill them up."""
    kind = "webdav"

    def setUp(self):
        super().setUp()
        self.settle()
        self.id_a, self.id_b = self.a.state["computer_id"], self.b.state["computer_id"]

    def put(self, name, content):
        self.server.files[name] = (content, self.server.now(), 90000 + len(self.server.files))

    def test_a_data_file_copied_as_a_pen_note_holds_nothing(self):
        self.put(f"pen-{self.id_b}.rpsync", self.server.files[f"rota-{self.id_b}.rpsync"][0])
        self.a.exchange()
        self.assertIsNone(self.a.pen()["holder"])
        self.assertFalse(self.a.ensure_pen())
        self.assertTrue(self.a.pen()["mine"])

    def test_an_old_note_put_back_revives_nothing(self):
        self.b.ensure_pen()
        note = self.server.files[f"pen-{self.id_b}.rpsync"][0]
        self.b.release_pen()
        self.server.skew = sy.PEN_STALE_SECONDS + 60  # later…
        self.put(f"pen-{self.id_b}.rpsync", note)  # … the captured "I hold it" is put back
        self.a.exchange()
        self.assertIsNone(self.a.pen()["holder"] if not self.a.pen()["stale"] else None)  # nobody is editing now
        with self.assertRaises(PenError) as caught:  # at worst it looks like someone who went away…
            self.a.ensure_pen()
        self.assertEqual(caught.exception.kind, "stale")
        self.a.take_pen(force=True)  # … and one click takes over
        self.assertTrue(self.a.pen()["mine"])
        self.b.exchange()  # its owner clears away a note it did not write now
        self.assertNotIn(f"pen-{self.id_b}.rpsync", self.server.files)

    def test_junk_notes_do_not_take_the_pen_away(self):
        self.a.ensure_pen()
        del self.server.files[f"pen-{self.id_a}.rpsync"]
        self.put("pen-c-0000000000ff.rpsync", b"junk")
        self.server.skew = sy.PEN_STALE_SECONDS + 60
        self.a.exchange()
        self.assertTrue(self.a.pen()["mine"])
        self.assertIn(f"pen-{self.id_a}.rpsync", self.server.files)  # said again

    def test_another_computers_file_under_this_ones_name_changes_nothing(self):
        self.a.ensure_pen()
        self.put(f"rota-{self.id_a}.rpsync", self.server.files[f"rota-{self.id_b}.rpsync"][0])
        self.a.exchange()
        self.assertEqual(self.a.state["computer_id"], self.id_a)  # no new identity
        self.assertTrue(self.a.pen()["mine"])
        _, payload = sy.unseal(self.a._key, self.server.files[f"rota-{self.id_a}.rpsync"][0])
        self.assertEqual(json.loads(payload)["computer_id"], self.id_a)  # simply written over

    def test_junk_is_never_kept_and_only_so_much_is_looked_at(self):
        for index in range(200):
            self.put(f"rota-c-{index:012x}.rpsync", b"x" * 5000)
        self.put("rota-c-ffffffffffff.rpsync", b"y" * (sy.PEN_BYTES + 1))
        self.put("pen-c-ffffffffffff.rpsync", b"y" * (sy.PEN_BYTES + 1))
        requests = len(self.server.requests)
        self.a.exchange()
        kept = sorted(path.name for path in self.a.cache.iterdir())
        self.assertEqual(len(kept), 3)  # the two real computers' files, and this one's ready to leave
        self.assertLessEqual(len(self.server.requests) - requests, 2 + sy.MAX_COMPUTERS * 2 + 4)
        status = self.a.status()
        self.assertLessEqual(len(status["others"]), sy.MAX_COMPUTERS)
        self.assertEqual(sorted(c["name"] for c in status["others"] if c["state"] == "ok"), ["Front desk", "Kitchen"])
        self.note(1, "still works")
        self.settle()
        self.assertEqual(self.stores[0].load_people()[0].note, "still works")

    def test_a_cut_or_foreign_file_is_said_but_not_kept(self):
        cut = self.server.files[f"rota-{self.id_b}.rpsync"][0][:-50]
        self.put(f"rota-{self.id_b}.rpsync", cut)
        self.a.exchange()
        self.assertFalse((self.a.cache / f"rota-{self.id_b}.rpsync").exists())
        self.assertEqual([c["state"] for c in self.a.status()["others"] if c["name"] == "Another computer"], ["problem"])
        self.b.exchange(force=True)  # the real one writes it again: all is well
        self.a.exchange()
        self.assertEqual(self.states(self.a), {})


class FolderPenTest(PenTest):
    """The same pen through a shared folder (the clock is this computer's)."""
    kind = "folder"

    def test_two_asking_at_the_same_moment(self):
        self.b.state["pen"] = {"since": self.b._now_there()}
        (Path(self.place) / f"pen-{self.b.state['computer_id']}.rpsync").write_bytes(self.b._pen_note(False))
        time.sleep(0.02)
        with self.assertRaises(PenError):
            self.a.take_pen()
        self.assertFalse(self.a.pen()["mine"])

    def test_the_holder_goes_away_and_another_takes_over(self):
        self.skipTest("needs a clock that can be moved: tried with the server")

    def test_the_pen_is_held_by_saying_so_again(self):
        self.skipTest("tried with the server")

    def test_no_connection_no_pen_unless_decided(self):
        shared = Path(self.place)
        shared.rename(shared.with_name("unplugged"))
        with self.assertRaises(PenError) as caught:
            self.a.ensure_pen()
        self.assertEqual(caught.exception.kind, "offline")
        self.a.work_offline(True)
        self.assertFalse(self.a.ensure_pen())

    def test_the_holder_keeps_working_without_connection(self):
        self.skipTest("tried with the server")

    def test_alone_no_pen_is_needed(self):
        self.skipTest("tried with the server")


del Computers  # only its children are test cases

if __name__ == "__main__":
    unittest.main()
