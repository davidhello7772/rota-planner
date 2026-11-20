import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from rotaplanner import sync as sy
from rotaplanner.app import App
from rotaplanner.model import SCHEMA_VERSION, Assignment, PersonAvailability
from rotaplanner.store import Store
from rotaplanner.sync import Sync, SyncError

from . import helpers as h

DECEMBER = "months/2026-12.json"


def fill(store: Store) -> None:
    store.save_catalog(h.catalog())
    store.save_people(h.people())
    store.save_month(h.month())


class CryptoTest(unittest.TestCase):
    key = bytes(range(32))

    def test_round_trip_and_tampering(self):
        sealed = sy.seal(self.key, "c-000000000001", b"hello " * 1000)
        header, payload = sy.unseal(self.key, sealed)
        self.assertEqual((header["computer"], payload), ("c-000000000001", b"hello " * 1000))
        self.assertNotIn(b"hello", sealed)
        self.assertNotEqual(sealed, sy.seal(self.key, "c-000000000001", b"hello " * 1000))  # fresh nonce each time
        for broken in (sealed[:-1], sealed[: len(sealed) // 2], sealed[:60] + bytes([sealed[60] ^ 1]) + sealed[61:], b"", b"junk"):
            with self.assertRaises(SyncError) as caught:
                sy.unseal(self.key, broken)
            self.assertEqual(caught.exception.kind, "damaged")
        with self.assertRaises(SyncError) as caught:
            sy.unseal(bytes(32), sealed)
        self.assertEqual(caught.exception.kind, "other-code")

    def test_codes(self):
        code = sy.new_code()
        self.assertRegex(code, r"^([A-Z2-9]{4}-){4}[A-Z2-9]{4}$")
        self.assertEqual(sy.normalise_code(code.lower().replace("-", " ")), code.replace("-", ""))
        self.assertEqual(sy.derive_key(code), sy.derive_key(code.lower()))
        for bad in ("", "ABCD", "ABCD-EFGH-JKMN-PQRS-TUV0", "ABCD-EFGH-JKMN-PQRS-TUVW-X"):
            with self.assertRaises(SyncError):
                sy.normalise_code(bad)

    def test_versions(self):
        self.assertEqual(sy.compare({"a": 1}, {"a": 1}), "same")
        self.assertEqual(sy.compare({"a": 2}, {"a": 1}), "sent")
        self.assertEqual(sy.compare({"a": 1}, {"a": 1, "b": 1}), "get")
        self.assertEqual(sy.compare({"a": 2}, {"a": 1, "b": 1}), "both")
        self.assertEqual(sy.merged({"a": 2}, {"a": 1, "b": 1}), {"a": 2, "b": 1})


class TwoComputersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.shared = base / "shared"
        self.shared.mkdir()
        self.store_a, self.store_b = Store(base / "a"), Store(base / "b")
        self.store_a.root.mkdir()
        self.store_b.root.mkdir()
        fill(self.store_a)
        self.today = date(2026, 11, 20)
        self.a = Sync(self.store_a, today=lambda: self.today)
        code = self.a.start("Office", str(self.shared))
        self.b = Sync(self.store_b, today=lambda: self.today)
        self.b.join("Front desk", str(self.shared), code)
        self.code = code

    def tearDown(self):
        self.tmp.cleanup()

    def sync(self, side, choices=None):
        preview = side.preview()
        return side.apply(preview["plan"], choices or {})

    def states(self, side):
        return {u["unit"]: u["state"] for u in side.status()["units"]}

    def test_first_sync_to_an_empty_computer(self):
        self.assertEqual(self.states(self.b), {"catalog.json": "get", "people.json": "get", DECEMBER: "get"})
        self.assertEqual(self.a.status()["counts"], {"sent": 3, "get": 0, "both": 0})
        self.assertEqual(self.a.status()["other"]["name"], "Front desk")
        done = self.sync(self.b)
        self.assertEqual(set(done["taken"]), {"People", "Tasks & areas", "December 2026"})
        self.assertEqual(done["safety"].startswith("before-sync-"), True)
        self.assertEqual(len(self.store_b.load_people()), 4)
        self.assertEqual(self.store_b.load_month("2026-12").to_dict(), self.store_a.load_month("2026-12").to_dict())
        self.assertEqual(self.states(self.b), {})
        self.assertEqual(self.states(self.a), {})  # the other has taken it all

    def test_a_change_goes_one_way(self):
        self.sync(self.b)
        people = self.store_a.load_people()
        people[0].note = "Prefers mornings"
        self.store_a.save_people(people)
        self.assertEqual(self.states(self.a), {"people.json": "sent"})
        self.assertEqual(self.states(self.b), {"people.json": "get"})
        self.sync(self.b)
        self.assertEqual(self.store_b.load_people()[0].note, "Prefers mornings")
        self.assertEqual((self.states(self.a), self.states(self.b)), ({}, {}))

    def test_both_changed_the_choice_is_asked_once(self):
        self.sync(self.b)
        for store, person in ((self.store_a, "ana"), (self.store_b, "ben")):
            month = store.load_month("2026-12")
            month.assignments.append(Assignment("lunch", "2026-12-02", 0, person_id=person))
            store.save_month(month)
        self.a.publish()
        self.b.publish()  # done in the background while the program runs
        self.assertEqual(self.states(self.a), {DECEMBER: "both"})
        self.assertEqual(self.states(self.b), {DECEMBER: "both"})
        preview = self.b.preview()
        self.assertEqual(preview["items"][0]["difference"], "1 shift differ")
        with self.assertRaisesRegex(SyncError, "Choose which December 2026"):
            self.b.apply(preview["plan"], {})
        done = self.b.apply(preview["plan"], {DECEMBER: "mine"})
        self.assertEqual(done["kept"], ["December 2026"])
        self.assertEqual(self.states(self.a), {DECEMBER: "get"})  # not asked again on the other computer
        self.sync(self.a)
        lunch = [x.person_id for x in self.store_a.load_month("2026-12").assignments if x.date == "2026-12-02" and x.task_id == "lunch"]
        self.assertEqual(lunch, ["ben"])
        self.assertEqual((self.states(self.a), self.states(self.b)), ({}, {}))

    def test_both_choosing_at_the_same_moment_is_seen(self):
        self.sync(self.b)
        for store, person in ((self.store_a, "ana"), (self.store_b, "ben")):
            month = store.load_month("2026-12")
            month.assignments.append(Assignment("lunch", "2026-12-02", 0, person_id=person))
            store.save_month(month)
        self.a.publish()
        self.b.publish()
        # Each looks before the other has chosen, and each takes the other's version.
        preview_a, preview_b = self.a.preview(), self.b.preview()
        self.a.apply(preview_a["plan"], {DECEMBER: "theirs"})
        with self.assertRaisesRegex(SyncError, "look again"):  # A has moved since B looked
            self.b.apply(preview_b["plan"], {DECEMBER: "theirs"})
        self.assertEqual((self.states(self.a), self.states(self.b)), ({}, {}))  # B's version is now on both

    def test_copies_that_parted_unnoticed_are_never_called_in_sync(self):
        self.sync(self.b)
        month = self.store_b.load_month("2026-12")
        month.assignments.append(Assignment("lunch", "2026-12-02", 0, person_id="ben"))
        self.store_b.save_month(month)
        self.b.state["hashes"][DECEMBER] = sy.content_hash(month.to_dict())  # changed without a new version
        self.b._save()
        self.b.publish(force=True)
        self.assertEqual(self.states(self.a), {DECEMBER: "both"})

    def test_an_old_file_never_brings_old_data_back(self):
        self.sync(self.b)
        own = next(self.shared.glob(f"rota-{self.a.state['computer_id']}.rpsync"))
        old = own.read_bytes()
        people = self.store_a.load_people()
        people[0].note = "new"
        self.store_a.save_people(people)
        self.a.publish()
        self.sync(self.b)
        own.write_bytes(old)  # the folder service hands back an older copy
        self.assertEqual(self.states(self.b), {"people.json": "sent"})
        self.assertEqual(self.store_b.load_people()[0].note, "new")

    def test_something_changed_meanwhile(self):
        preview = self.b.preview()
        people = self.store_a.load_people()
        people[0].note = "changed after the preview"
        self.store_a.save_people(people)
        self.a.publish()
        with self.assertRaisesRegex(SyncError, "look again"):
            self.b.apply(preview["plan"], {})
        self.assertTrue(self.store_b.is_empty())

    def test_stray_files_are_ignored_and_the_folder_may_vanish(self):
        own = next(self.shared.glob(f"rota-{self.a.state['computer_id']}.rpsync"))
        shutil.copy(own, self.shared / f"rota-{self.a.state['computer_id']} (1).rpsync")
        (self.shared / "notes.txt").write_text("hello")
        self.assertEqual(self.b.status()["problem"], None)
        shutil.rmtree(self.shared)
        status = self.b.status()
        self.assertEqual(status["kind"], "folder")
        self.assertFalse(status["connection"]["online"])
        # What was last seen is kept: the coordinator can still look, and take it.
        self.assertEqual(len(self.b.preview()["items"]), 3)
        self.sync(self.b)
        self.assertEqual(len(self.store_b.load_people()), 4)

    def test_cut_file_is_still_arriving(self):
        own = next(self.shared.glob(f"rota-{self.a.state['computer_id']}.rpsync"))
        own.write_bytes(own.read_bytes()[:-100])
        self.assertEqual(self.b.status()["kind"], "damaged")

    def test_wrong_code(self):
        wrong = sy.new_code()
        d_store = Store(Path(self.tmp.name) / "d")
        d_store.root.mkdir()
        lone = Path(self.tmp.name) / "lone"
        lone.mkdir()
        shutil.copy(next(self.shared.glob("rota-*.rpsync")), lone)
        d = Sync(d_store)
        d.join("Wrong", str(lone), wrong)
        self.assertEqual(d.status()["kind"], "other-code")

    def test_newer_program_is_refused(self):
        payload = {"format": sy.SYNC_FORMAT, "schema_version": SCHEMA_VERSION + 1, "computer_id": self.a.state["computer_id"],
                   "name": "Office", "written": 0, "units": {}}
        own = self.shared / f"rota-{self.a.state['computer_id']}.rpsync"
        own.write_bytes(sy.seal(self.a._key, self.a.state["computer_id"], json.dumps(payload).encode()))
        status = self.b.status()
        self.assertIn("newer Rota Planner", status["problem"])

    def test_bad_content_changes_nothing(self):
        people = self.store_a.load_people()
        self.store_a.save_people(people)
        month = json.loads((self.store_a.root / DECEMBER).read_text())
        month["id"] = "2027-01"  # not the month it claims to be
        (self.store_a.root / DECEMBER).write_text(json.dumps(month))
        self.a.publish()
        with self.assertRaisesRegex(SyncError, "nothing was changed"):
            self.sync(self.b)
        self.assertTrue(self.store_b.is_empty())

    def test_comments_of_a_month_over_never_travel(self):
        month = self.store_a.load_month("2026-12")
        month.availability["ana"] = PersonAvailability(unavailable=[], comment="private words")
        self.store_a.save_month(month)
        self.a.publish()
        _, payload = sy.unseal(self.a._key, next(self.shared.glob(f"rota-{self.a.state['computer_id']}.rpsync")).read_bytes())
        self.assertIn(b"private words", payload)  # this month: the coordinator still needs them
        self.today = date(2027, 1, 2)
        self.a.publish(force=True)
        _, payload = sy.unseal(self.a._key, next(self.shared.glob(f"rota-{self.a.state['computer_id']}.rpsync")).read_bytes())
        self.assertNotIn(b"private words", payload)
        self.assertNotIn(b"private words", (self.shared / f"rota-{self.a.state['computer_id']}.rpsync").read_bytes())

    def test_dangling_shifts_are_reported(self):
        self.sync(self.b)
        month = self.store_b.load_month("2026-12")
        month.assignments.append(Assignment("lunch", "2026-12-03", 0, person_id="ghost"))
        self.store_b.save_month(month)
        self.assertEqual(len(sy.dangling({"people.json": {"people": []}, DECEMBER: month.to_dict()})), 1)

    def test_a_copied_data_folder_does_not_write_over(self):
        copy_root = Path(self.tmp.name) / "copy"
        shutil.copytree(self.store_a.root, copy_root)
        twin = Sync(Store(copy_root))
        people = twin.store.load_people()
        people[0].note = "from the twin"
        twin.store.save_people(people)
        original = self.a.state["computer_id"]
        twin.publish()
        # The copy takes a name of its own instead of writing over the original's file.
        self.assertNotEqual(twin.state["computer_id"], original)
        self.assertIn("copy of another computer", twin.status()["log"][0]["text"] + twin.status()["log"][-1]["text"])
        self.a.exchange()  # the original puts its own file back if the copy removed it
        _, payload = sy.unseal(self.a._key, (self.shared / f"rota-{original}.rpsync").read_bytes())
        self.assertNotIn(b"from the twin", payload)
        self.assertEqual(len(self.b.status()["others"]), 2)
        self.assertEqual(self.states(self.a)["people.json"], "get")  # and its change arrives like any other

    def test_no_second_pairing_over_the_first(self):
        with self.assertRaisesRegex(SyncError, "already set up"):
            self.b.start("Again", str(self.shared))
        self.assertEqual(len(list(self.shared.glob("rota-*.rpsync"))), 2)

    def test_stop(self):
        self.b.stop()
        self.assertFalse(self.b.configured)
        self.assertEqual(len(list(self.shared.glob("rota-*.rpsync"))), 1)
        self.assertEqual(self.a.status()["kind"], "alone")

    def test_setup_checks(self):
        store = Store(Path(self.tmp.name) / "e")
        store.root.mkdir()
        s = Sync(store)
        with self.assertRaises(SyncError):
            s.start("x", "relative/folder")
        with self.assertRaises(SyncError):
            s.start("x", str(Path(self.tmp.name) / "missing"))
        with self.assertRaises(SyncError):
            s.start("x", str(store.root))
        with self.assertRaises(SyncError):
            s.join("x", str(self.shared), "not a code")
        self.assertFalse(s.configured)
        self.assertEqual(json.loads(json.dumps(s.status())), {"configured": False})


class AppSyncTest(unittest.TestCase):
    def test_app_reloads_after_sync(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "shared").mkdir()
            store_a, store_b = Store(base / "a"), Store(base / "b")
            fill(store_a)
            fill(store_b)
            app_a, app_b = App(store_a), App(store_b)
            code = app_a.sync_setup({"action": "start", "name": "A", "folder": str(base / "shared")})["code"]
            joining = {"action": "join", "name": "B", "folder": str(base / "shared"), "code": code}
            with self.assertRaisesRegex(Exception, "already has data"):  # never over existing data without a word
                app_b.sync_setup(joining)
            app_b.sync_setup(dict(joining, confirmed=True))
            # Same data on both, made separately: identical content is simply in sync.
            self.assertEqual(app_b.sync_status()["counts"], {"sent": 0, "get": 0, "both": 0})
            app_a.save_person({"person": {"id": "ana", "first_name": "Ana", "last_name": "Berg", "note": "From A"}})
            app_a.sync.publish()
            preview = app_b.sync_preview({})
            done = app_b.sync_apply({"plan": preview["plan"]})
            self.assertEqual(done["taken"], ["People"])
            self.assertEqual(next(p for p in app_b.people if p.id == "ana").note, "From A")
            self.assertEqual(app_b.sync_code({})["code"], code)
            self.assertFalse((store_a.root / "sync.json").stat().st_mode & 0o077)


if __name__ == "__main__":
    unittest.main()
