"""The history of a month's rota: named steps, packed, in memory only."""

import unittest

from rotaplanner.availability import scrub_comments
from rotaplanner.history import KEPT_NOTE, History, pack, unpack

M = "2026-12"


def state(n, comment=""):
    """A stand-in for a stored month: its number tells the states apart."""
    return {"n": n, "availability": {"ana": {"comment": comment, "comment_done": False}}}


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.history = History(limit=5)

    def made(self, *labels):
        """Steps from state 0 to state len(labels), one per label."""
        return [self.history.record(M, label, "change", state(n)) for n, label in enumerate(labels)]

    def test_pack_round_trip(self):
        document = {"id": M, "names": ["Ana B.", "Zoë"], "nested": {"a": [1, 2.5, None, True]}}
        blob = pack(document)
        self.assertIsInstance(blob, bytes)
        self.assertEqual(unpack(blob), document)
        self.assertNotIn(b"Ana", blob)  # compressed, not readable as text

    def test_order_labels_and_ids(self):
        steps = self.made("First", "Second", "Third")
        undo, redo = self.history.stacks(M)
        self.assertEqual([s.label for s in undo], ["First", "Second", "Third"])
        self.assertEqual(redo, [])
        self.assertEqual(self.history.top(M).label, "Third")
        self.assertLess(steps[0].id, steps[1].id)
        other = self.history.record("2027-01", "Elsewhere", "change", state(9))
        self.assertGreater(other.id, steps[2].id)  # ids grow across the whole program
        self.assertIsNone(self.history.top("2027-02"))

    def test_a_new_record_empties_redo(self):
        self.made("First", "Second")
        self.history.move(M, "undo", 1, state(2), None)
        self.assertEqual(len(self.history.stacks(M)[1]), 1)
        self.history.record(M, "Third", "change", state(1))
        self.assertEqual(self.history.stacks(M)[1], [])

    def test_the_limit_and_its_note(self):
        self.assertEqual(self.history.note(M), KEPT_NOTE)
        self.made(*[f"Step {n}" for n in range(5)])
        self.assertEqual(self.history.note(M), KEPT_NOTE)
        self.history.record(M, "Step 5", "change", state(5))
        undo = self.history.stacks(M)[0]
        self.assertEqual([s.label for s in undo], [f"Step {n}" for n in range(1, 6)])
        self.assertEqual(self.history.note(M), "Only the last 5 changes can be undone.")

    def test_undo_and_redo_one_step(self):
        steps = self.made("First", "Second")
        crossed = self.history.move(M, "undo", 1, state(2), None)
        self.assertEqual((crossed.id, crossed.label, unpack(crossed.snapshot)["n"]), (steps[1].id, "Second", 1))
        undo, redo = self.history.stacks(M)
        self.assertEqual([s.id for s in undo], [steps[0].id])
        self.assertEqual((redo[0].id, redo[0].label, unpack(redo[0].snapshot)["n"]), (steps[1].id, "Second", 2))
        crossed = self.history.move(M, "redo", 1, state(1), None)
        self.assertEqual(unpack(crossed.snapshot)["n"], 2)
        self.assertEqual(unpack(self.history.top(M).snapshot)["n"], 1)
        self.assertEqual(self.history.stacks(M)[1], [])

    def test_moving_k_steps_is_k_single_moves(self):
        def run(single):
            history = History()
            for n in range(5):
                history.record(M, f"Step {n}", "auto" if n == 2 else "change", state(n),
                               proposals=(f"waiting {n}",) if n == 2 else None)
            current, pending = state(5), ("now",)
            if single:
                for _ in range(4):
                    step = history.move(M, "undo", 1, current, pending)
                    current = unpack(step.snapshot)
                    pending = step.proposals if step.proposals is not None else pending
            else:
                step = history.move(M, "undo", 4, current, pending)
                current = unpack(step.snapshot)
                pending = step.proposals if step.proposals is not None else pending
            seen = [(s.id, s.label, s.kind, unpack(s.snapshot), s.proposals) for stack in history.stacks(M) for s in stack]
            return current, pending, seen

        self.assertEqual(run(True), run(False))
        current, pending, _ = run(False)
        self.assertEqual((current["n"], pending), (1, ("waiting 2",)))

    def test_suggestions_go_back_and_forth(self):
        self.history.record(M, "3 suggestions accepted", "auto", state(0), proposals=("three waiting",))
        step = self.history.move(M, "undo", 1, state(1), ("none left",))
        self.assertEqual(step.proposals, ("three waiting",))  # shown again
        step = self.history.move(M, "redo", 1, state(0), ("three waiting",))
        self.assertEqual(step.proposals, ("none left",))  # gone again
        self.history.record(M, "Plain", "change", state(1))
        step = self.history.move(M, "undo", 1, state(2), ("none left",))
        self.assertIsNone(step.proposals)  # a plain step leaves the suggestions alone

    def test_find(self):
        steps = self.made("A", "B", "C", "D")
        self.history.move(M, "undo", 1, state(4), None)
        self.assertEqual(self.history.find(M, steps[3].id), ("redo", 1))
        self.assertEqual(self.history.find(M, steps[2].id), ("undo", 1))
        self.assertEqual(self.history.find(M, steps[0].id), ("undo", 3))
        self.assertEqual(self.history.find(M, str(steps[1].id)), ("undo", 2))  # as sent by the screen
        with self.assertRaises(KeyError):
            self.history.find(M, 999)
        with self.assertRaises(KeyError):
            self.history.find("2027-01", steps[0].id)
        with self.assertRaises(KeyError):
            self.history.move(M, "undo", 4, state(3), None)  # only three to undo

    def test_scrub_reaches_both_lists(self):
        for n in range(3):
            self.history.record(M, f"Step {n}", "change", state(n, comment="Private words"))
        self.history.move(M, "undo", 1, state(3, comment="Private words"), None)
        self.history.scrub(M, scrub_comments)
        undo, redo = self.history.stacks(M)
        self.assertEqual(len(undo) + len(redo), 3)
        for step in undo + redo:
            self.assertEqual(unpack(step.snapshot)["availability"]["ana"]["comment"], "")

    def test_drop_with_its_note(self):
        self.made("A")
        self.history.record("2027-01", "B", "change", state(0))
        self.history.drop(M, note="Updated elsewhere.")
        self.assertEqual(self.history.stacks(M), ([], []))
        self.assertEqual(self.history.note(M), "Updated elsewhere.")
        self.assertEqual(len(self.history.stacks("2027-01")[0]), 1)
        self.assertEqual(self.history.note("2027-01"), KEPT_NOTE)
        self.history.drop()
        self.assertEqual(self.history.stacks("2027-01"), ([], []))
        self.assertEqual(self.history.note(M), KEPT_NOTE)


if __name__ == "__main__":
    unittest.main()
