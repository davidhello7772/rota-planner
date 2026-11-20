import base64
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from rotaplanner import transfer
from rotaplanner.app import ApiError, App
from rotaplanner.model import Month, PersonAvailability
from rotaplanner.store import Store

from . import helpers as h


class TransferTest(unittest.TestCase):
    """All the data in one file, to carry to another computer, and back."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.here = self.store("here")
        month = h.month()
        month.availability["ana"] = PersonAvailability(unavailable=["2026-12-03/morning"], comment="Doctor on the 3rd")
        self.here.save_month(month)
        self.here.save_month(Month.from_dict({**month.to_dict(), "id": "2027-01", "year": 2027, "month": 1,
                                               "assignments": [], "availability": {}}))

    def tearDown(self):
        self.tmp.cleanup()

    def store(self, name):
        store = Store(Path(self.tmp.name) / name)
        store.save_catalog(h.catalog())
        store.save_people(h.people())
        return store

    def test_round_trip_leaves_comments_out_unless_asked(self):
        data = transfer.export_bytes(self.here)
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        self.assertIn("months/2026-12.json", names)
        self.assertFalse(any(n.startswith("backups") or "token" in n for n in names))
        self.assertNotIn(b"Doctor", b"".join(zipfile.ZipFile(io.BytesIO(data)).read(n) for n in names))

        there = self.store("there")
        info = transfer.summary(there, data)
        self.assertEqual((info["people"], info["months"], info["here"]["months"]), (4, ["2026-12", "2027-01"], []))
        safety = transfer.replace_data(there, data)
        self.assertTrue((there.backups_dir / safety).exists())
        self.assertEqual(there.list_months(), ["2026-12", "2027-01"])
        moved = there.load_month("2026-12")
        self.assertEqual((moved.availability["ana"].unavailable, moved.availability["ana"].comment), (["2026-12-03/morning"], ""))

        with_comments = transfer.export_bytes(self.here, include_comments=True)
        transfer.replace_data(there, with_comments)
        self.assertEqual(there.load_month("2026-12").availability["ana"].comment, "Doctor on the 3rd")

    def test_refuses_what_is_not_an_export(self):
        bad = io.BytesIO()
        with zipfile.ZipFile(bad, "w") as archive:
            archive.writestr("rota-planner.json", '{"format": 1}')
            archive.writestr("catalog.json", "{}")
            archive.writestr("people.json", '{"people": []}')
            archive.writestr("../../evil.json", "{}")
        for data in (b"not a zip", bad.getvalue()):
            with self.assertRaises(transfer.TransferError):
                transfer.read_export(data)
        self.assertEqual(self.here.list_months(), ["2026-12", "2027-01"])  # nothing touched

    def test_the_program_reloads_what_was_imported(self):
        app = App(self.store("there"))
        data = transfer.export_bytes(self.here)
        encoded = base64.b64encode(data).decode()
        self.assertEqual(app.import_preview({"file": encoded})["months"], ["2026-12", "2027-01"])
        result = app.import_apply({"file": encoded})
        self.assertEqual([m["id"] for m in result["bootstrap"]["months"]], ["2026-12", "2027-01"])
        with self.assertRaises(ApiError):
            app.import_apply({"file": base64.b64encode(b"nope").decode()})


if __name__ == "__main__":
    unittest.main()
