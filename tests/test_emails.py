import json
import tempfile
import unittest
from pathlib import Path

from rotaplanner import availability, editing, emails, people_import
from rotaplanner.app import App
from rotaplanner.store import Store

from . import helpers as h

SALT = bytes(16)
ANA = "ana.berg@example.org"
BEN = "ben.cole@example.org"


def replies(*rows):
    header = "Timestamp\tEmail address\tName\tMorning\tAfternoon\tEvening\tComment"
    return "\n".join([header, *["\t".join(r) for r in rows]])


class FingerprintTest(unittest.TestCase):
    def test_one_way_and_stable(self):
        fp = emails.fingerprint("  Ana.Berg@Example.org ", SALT)
        self.assertEqual(fp, emails.fingerprint(ANA, SALT))
        self.assertTrue(emails.valid(fp))
        self.assertNotIn("ana", fp)
        self.assertNotEqual(fp, emails.fingerprint(ANA, b"\x01" * 16))  # another installation, another fingerprint
        self.assertEqual(emails.fingerprint("not an address", SALT), "")
        self.assertEqual(emails.fingerprint("", SALT), "")
        self.assertFalse(emails.valid("ana@example.org"))

    def test_an_address_belongs_to_one_person(self):
        people = h.people()
        fp = emails.fingerprint(ANA, SALT)
        self.assertTrue(emails.remember(people, "ana", fp, SALT))
        self.assertFalse(emails.remember(people, "ana", fp, SALT))
        self.assertTrue(emails.remember(people, "ben", fp, SALT))
        self.assertEqual([p.id for p in people if p.email_fp == fp], ["ben"])
        self.assertFalse(emails.remember(people, "ben", "made up", SALT))
        self.assertFalse(emails.remember(people, "nobody", fp, SALT))
        self.assertFalse(emails.remember(people, "ana", fp, b"\x02" * 16))  # made with another salt

    def test_public_record_hides_the_fingerprint(self):
        person = h.people()[0]
        person.email_fp = emails.fingerprint(ANA, SALT)
        shown = emails.public(person)
        self.assertNotIn("email_fp", shown)
        self.assertTrue(shown["has_email"])


class AvailabilityTest(unittest.TestCase):
    def setUp(self):
        self.month = h.month()
        self.people = h.people()
        self.people[0].email_fp = emails.fingerprint(ANA, SALT)

    def test_recognised_by_address_whatever_the_name(self):
        result = availability.preview(self.month, self.people, replies(
            ("1/11", ANA, "Anita", "Tue 1", "", "", ""),
            ("1/11", BEN, "Ben Cole", "", "Wed 2", "", "")), salt=SALT)
        ana, ben = result["rows"]
        self.assertEqual((ana["person_id"], ana["match"], ana["email_owner"]), ("ana", "exact", "ana"))
        self.assertEqual((ben["person_id"], ben["match"], ben["email_owner"]), ("ben", "exact", None))
        self.assertTrue(emails.valid(ben["email_fp"]))
        self.assertNotIn("example.org", json.dumps(result))  # the address is never sent back

    def test_what_to_remember_is_decided_here(self):
        self.people[1].email_fp = emails.fingerprint("old@example.org", SALT)
        rows = availability.preview(self.month, self.people, replies(
            ("1/11", ANA, "Ana Berg", "Tue 1", "", "", ""),
            ("1/11", BEN, "Ben Cole", "", "", "", ""),
            ("1/11", "cleo@example.org", "Cleo Dane", "", "", "", "")), salt=SALT)["rows"]
        self.assertEqual([(r["email_status"], r["remember_email"]) for r in rows],
                         [("recognised", False), ("changed", False), ("new", True)])

    def test_address_and_name_disagree(self):
        result = availability.preview(self.month, self.people, replies(("1/11", ANA, "Ben Cole", "Tue 1", "", "", "")), salt=SALT)
        row = result["rows"][0]
        self.assertEqual((row["match"], row["suggestions"]), ("close", ["ana", "ben"]))

    def test_reply_with_only_an_address(self):
        text = "Username\tMorning\tAfternoon\tEvening\n" + f"{ANA}\tTue 1\t\t"
        row = availability.preview(self.month, self.people, text, salt=SALT)["rows"][0]
        self.assertEqual((row["person_id"], row["match"]), ("ana", "exact"))

    def test_without_an_address_column_as_before(self):
        text = "Name\tMorning\tAfternoon\tEvening\nAna Berg\tTue 1\t\t"
        row = availability.preview(self.month, self.people, text, salt=SALT)["rows"][0]
        self.assertEqual((row["person_id"], row["email_fp"]), ("ana", ""))


class SaltTest(unittest.TestCase):
    def test_salt_is_on_disk_at_once_and_never_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            store.save_people(h.people())
            salt = store.email_salt()
            self.assertEqual(json.loads((Path(tmp) / "people.json").read_text())["email_salt"], salt.hex())
            Store(Path(tmp)).save_people(h.people())  # a store that never loaded the people
            self.assertEqual(Store(Path(tmp)).email_salt(), salt)


class PeopleImportTest(unittest.TestCase):
    def test_addresses_become_fingerprints(self):
        people, catalog = h.people(), h.catalog()
        people[0].email_fp = emails.fingerprint("old@example.org", SALT)
        text = "\n".join(["First name\tLast name\tEmail", f"Ana\tBerg\t{ANA}", f"Ben\tCole\t{BEN}",
                          f"Zoe\tNew\tzoe@example.org", "Yan\tNew\tnot-an-address"])
        result = people_import.preview(people, catalog, text, salt=SALT)
        self.assertNotIn("example.org", json.dumps(result))
        ana, ben, zoe, yan = result["rows"]
        self.assertEqual((ana["status"], ana["email_status"]), ("exists", "changed"))
        self.assertEqual((ben["status"], ben["email_status"]), ("exists", "new"))
        self.assertEqual(zoe["status"], "new")
        self.assertTrue(yan["email_invalid"])

        ana["remember_email"], ben["remember_email"] = False, True  # keep Ana's known one, remember Ben's
        added = people_import.apply(people, catalog, [ana, ben, zoe, yan], SALT)
        self.assertEqual(people_import.remember_emails(people, [ana, ben, zoe, yan], SALT), 1)
        self.assertEqual([p.first_name for p in added], ["Zoe", "Yan"])
        self.assertEqual(people[0].email_fp, emails.fingerprint("old@example.org", SALT))
        self.assertEqual(people[1].email_fp, emails.fingerprint(BEN, SALT))
        self.assertEqual(added[0].email_fp, emails.fingerprint("zoe@example.org", SALT))
        self.assertEqual(added[1].email_fp, "")


class PersonCardTest(unittest.TestCase):
    def test_give_replace_and_forget(self):
        people, catalog = h.people(), h.catalog()
        editing.save_person(people, catalog, {"id": "ana", "first_name": "Ana", "last_name": "Berg", "email": ANA}, salt=SALT)
        self.assertEqual(people[0].email_fp, emails.fingerprint(ANA, SALT))
        with self.assertRaisesRegex(ValueError, "already known for Ana B."):
            editing.save_person(people, catalog, {"id": "ben", "first_name": "Ben", "last_name": "Cole", "email": ANA}, salt=SALT)
        with self.assertRaisesRegex(ValueError, "does not look right"):
            editing.save_person(people, catalog, {"id": "ben", "first_name": "Ben", "last_name": "Cole", "email": "ben@"}, salt=SALT)
        self.assertEqual(people[1].email_fp, "")  # nothing changed by the refused saves
        editing.save_person(people, catalog, {"id": "ana", "first_name": "Ana", "last_name": "Berg"}, salt=SALT)
        self.assertTrue(people[0].email_fp)  # saving without an address keeps the one known
        editing.save_person(people, catalog, {"id": "ana", "first_name": "Ana", "last_name": "Berg", "forget_email": True}, salt=SALT)
        self.assertEqual(people[0].email_fp, "")


class NothingKeptTest(unittest.TestCase):
    """End to end through App: no address in any file, nor in any answer."""

    def test_addresses_are_never_stored(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            store.save_catalog(h.catalog())
            store.save_people(h.people())
            store.save_month(h.month())
            app = App(store)
            answers = [app.save_person({"person": {"id": "ana", "first_name": "Ana", "last_name": "Berg", "email": ANA}})]
            preview = app.availability_preview("2026-12", {"text": replies(("1/11", BEN, "Ben Cole", "Tue 1", "", "", "Away"))})
            answers.append(preview)
            row = preview["rows"][0]
            row["remember_email"] = True
            done = app.availability_apply("2026-12", {"replies": [row]})
            answers.append(done)
            self.assertEqual(done["remembered"], 1)
            answers.append(app.setup())
            for answer in answers:
                self.assertNotIn("example.org", json.dumps(answer))
                self.assertNotIn("email_fp", json.dumps(answer.get("people", [])))
            for path in Path(tmp).rglob("*"):
                if path.is_file():
                    self.assertNotIn(b"example.org", path.read_bytes(), path.name)
            self.assertIn("email_salt", json.loads((Path(tmp) / "people.json").read_text()))
            # The salt is kept with the people: a restart recognises the same address.
            again = App(Store(Path(tmp)))
            preview = again.availability_preview("2026-12", {"text": replies(("1/11", ANA, "Someone", "Tue 1", "", "", ""))})
            self.assertEqual(preview["rows"][0]["person_id"], "ana")


if __name__ == "__main__":
    unittest.main()
