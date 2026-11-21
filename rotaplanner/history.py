"""What can be undone in each month: named steps, kept in memory only.

Each month has its own history (a month is the document, and the unit of
sync): a list of steps that can be undone, and of steps undone that can be
done again. A step holds the month as it was on the other side of that step,
packed (zlib of compact JSON: about 10 KB instead of 300 KB in memory), and
the suggestions that were waiting in that state, when it changed them.

Nothing here is ever written to disk, logged or synchronised: the history ends
when the program closes. Its labels say what was done in plain words; they
never hold the text of an availability comment, of a note or an address.
Pure: no I/O. ``App`` holds its lock whenever it touches a ``History``.
"""

from __future__ import annotations

import itertools
import json
import time
import zlib
from dataclasses import dataclass, replace
from typing import Any, Callable, Dict, List, Optional, Tuple

Json = Dict[str, Any]

CHANGE, AUTO, VERSION = "change", "auto", "version"
"""Kinds of step: a change by hand, suggestions accepted, a version given out."""

KEPT_NOTE = "Kept until Rota Planner is closed."


def pack(document: Json) -> bytes:
    return zlib.compress(json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 1)


def unpack(blob: bytes) -> Json:
    return json.loads(zlib.decompress(blob).decode("utf-8"))


@dataclass
class Step:
    id: int            # grows across the whole program: a higher id is a later change
    label: str         # what was done, in plain words
    kind: str          # CHANGE | AUTO (suggestions accepted) | VERSION
    at: float          # when the change was made (time.time())
    snapshot: bytes    # the packed month this step puts back when it is crossed
    proposals: Optional[Any] = None
    """The suggestions waiting in the state this step puts back (as ``App`` keeps
    them), when the step changed them; ``None``: crossing it leaves them alone."""


class History:
    """The undo and redo lists of every month. Replaces whole-month snapshots kept as dicts."""

    def __init__(self, limit: int = 200) -> None:
        self.limit = limit
        self._undo: Dict[str, List[Step]] = {}
        self._redo: Dict[str, List[Step]] = {}
        self._notes: Dict[str, str] = {}
        self._ids = itertools.count(1)

    def record(self, month_id: str, label: str, kind: str, before: Json, proposals: Optional[Any] = None) -> Step:
        """A change was made to ``month_id``; ``before`` is the month as it was. Empties the redo list."""
        step = Step(next(self._ids), label, kind, time.time(), pack(before), proposals)
        undo = self._undo.setdefault(month_id, [])
        undo.append(step)
        if len(undo) > self.limit:
            del undo[:-self.limit]
            self._notes.setdefault(month_id, f"Only the last {self.limit} changes can be undone.")
        self._redo[month_id] = []
        return step

    def top(self, month_id: str, redo: bool = False) -> Optional[Step]:
        stack = (self._redo if redo else self._undo).get(month_id)
        return stack[-1] if stack else None

    def stacks(self, month_id: str) -> Tuple[List[Step], List[Step]]:
        """(undo, redo), each oldest first: the last of each is the next to cross."""
        return list(self._undo.get(month_id, [])), list(self._redo.get(month_id, []))

    def find(self, month_id: str, step_id: Any) -> Tuple[str, int]:
        """Where a step is: ("undo", n) to go back to before it, ("redo", n) to redo up to
        and including it, n being how many steps that crosses. KeyError if it is not there."""
        for direction, stacks in (("undo", self._undo), ("redo", self._redo)):
            stack = stacks.get(month_id, [])
            for depth, step in enumerate(reversed(stack), start=1):
                if str(step.id) == str(step_id):
                    return direction, depth
        raise KeyError(step_id)

    def move(self, month_id: str, direction: str, count: int, current: Json, pending: Any) -> Step:
        """Cross ``count`` steps ("undo" or "redo"): each goes to the other list, holding
        the state just left (the first one ``current``) and, when the step carried
        suggestions, those waiting in that state (the first one ``pending``).

        Returns the last step crossed, whose snapshot the caller puts back, once. Its
        ``proposals`` are the suggestions to put back when any step crossed carried
        some (``None``: leave them as they are). Going back k steps is k single moves.
        """
        source, target = (self._undo, self._redo) if direction == "undo" else (self._redo, self._undo)
        stack = source.get(month_id, [])
        if count < 1 or count > len(stack):
            raise KeyError(count)
        other = target.setdefault(month_id, [])
        state, waiting, carried = pack(current), pending, False
        last = stack[-1]
        for _ in range(count):
            last = stack.pop()
            other.append(replace(last, snapshot=state, proposals=waiting if last.proposals is not None else None))
            state = last.snapshot
            if last.proposals is not None:
                waiting, carried = last.proposals, True
        return replace(last, proposals=waiting if carried else None)

    def scrub(self, month_id: str, scrub: Callable[[Json], bool]) -> None:
        """Apply ``scrub`` (which edits a stored month in place and says whether it
        removed anything) to every snapshot of the month, in both lists."""
        for stack in (self._undo.get(month_id, []), self._redo.get(month_id, [])):
            for index, step in enumerate(stack):
                document = unpack(step.snapshot)
                if scrub(document):
                    stack[index] = replace(step, snapshot=pack(document))

    def drop(self, month_id: Optional[str] = None, note: str = "") -> None:
        """Forget a month's history (every month's, without ``month_id``). ``note``
        says why, in the list of changes, from then on."""
        if month_id is None:
            self._undo, self._redo, self._notes = {}, {}, {}
            return
        self._undo.pop(month_id, None)
        self._redo.pop(month_id, None)
        if note:
            self._notes[month_id] = note

    def note(self, month_id: str) -> str:
        """What the list of changes says under it: how long it is kept, or why it is shorter."""
        return self._notes.get(month_id, KEPT_NOTE)
