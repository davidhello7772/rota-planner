import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from rotaplanner.app import App
from rotaplanner.model import PersonAvailability
from rotaplanner.store import Store

from . import helpers as h

COMMENT = "Hospital appointment on the 3rd"


class CommentsAreNotKeptTest(unittest.TestCase):
    """Availability comments may say something about someone's health: once
    deleted they must be gone from the month, its backups and its undo history."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name))
        month = h.month()
        month.availability["ana"] = PersonAvailability(unavailable=["2026-12-03/morning"], comment=COMMENT)
        self.store.save_catalog(h.catalog())
        self.store.save_people(h.people())
        self.store.save_month(month)
        self.store.save_month(month)  # the second save keeps the first as a backup

    def tearDown(self):
        self.tmp.cleanup()

    def everything_on_disk(self) -> str:
        return "".join(p.read_text(encoding="utf-8") for p in Path(self.tmp.name).rglob("*.json"))

    def test_deleting_the_comments_removes_every_copy(self):
        app = App(self.store, today=date(2026, 12, 10))
        self.assertIn(COMMENT, self.everything_on_disk())
        app.availability_comment("2026-12", {"person_id": "ana", "done": True})  # an undo step holding the comment
        result = app.forget_comments("2026-12")
        self.assertEqual(result["forgotten"], 1)
        self.assertNotIn(COMMENT, self.everything_on_disk())
        self.assertNotIn(COMMENT, json.dumps(app._undo.get("2026-12", [])))
        self.assertEqual(app.month("2026-12").availability["ana"].unavailable, ["2026-12-03/morning"])  # days away stay

    def test_comments_go_once_the_month_is_over(self):
        App(self.store, today=date(2026, 12, 31))
        self.assertIn(COMMENT, self.everything_on_disk())
        App(self.store, today=date(2027, 1, 1))
        self.assertNotIn(COMMENT, self.everything_on_disk())


if __name__ == "__main__":
    unittest.main()
