"""Help with filling the rota: suggestions, rebalancing, shortages, ideas.

Nothing here changes the month. Every function works on a ``Schedule`` (an
indexed copy of the month) and returns *proposals* the coordinator accepts or
not. The rules are the ones in ``eligibility``: a proposal never breaks a
hard rule.

Prefill works in three passes:

1. **Build**: open shifts are filled one by one, essential tasks first, and
   among them the shifts with the fewest possible people first (they are the
   ones that would be lost by filling others before). Each goes to the best
   ranked person (fewest hours against target, fair weekends and evenings…).
2. **Repair**: for a shift still open, find someone kept away only by
   another shift placed in pass 1 that someone else can take instead.
3. **Improve**: for a short time, try giving each placed shift to a better
   ranked person, keeping every change that improves the ranking.
"""

from __future__ import annotations

import random
import re
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple

from . import eligibility as rules
from .model import Assignment, Month, Person, Settings, parse_date, slot_key, weekday_key
from .schedule import Schedule, Slot

PRIORITY_RANK = {"critical": 0, "normal": 1, "optional": 2}
IMPROVE_SECONDS = 1.5
IMPROVE_MIN_GAIN = 0.05
REBALANCE_MIN_GAIN = 0.5
REBALANCE_MIN_GAP = 0.25
REBALANCE_MAX_MOVES = 20
CLASH_RULES = {"double_booked", "overlap", "daily_tasks", "daily_hours"}
"""Rules that another shift on the same day can cause: moving that shift may lift them."""


@dataclass
class Proposal:
    task_id: str
    date: str
    position: int
    person_id: str
    replaces: str = ""
    why: str = ""

    @property
    def key(self) -> str:
        return slot_key(self.task_id, self.date, self.position)

    def to_dict(self) -> Dict[str, object]:
        return {**asdict(self), "key": self.key}


def _hours(value: float) -> str:
    return f"{round(value, 1):g} h"


def _options(schedule: Schedule, slot: Slot, ignore: Optional[Assignment] = None) -> List[rules.Candidate]:
    task_id, iso, _ = slot
    found = rules.candidates(schedule, schedule.tasks[task_id], iso, ignore=ignore)
    return [c for c in found if c.group != rules.BLOCKED]


def _why(schedule: Schedule, person_id: str) -> str:
    text = f"{_hours(schedule.hours(person_id))} of {_hours(schedule.target(person_id))} this month"
    if not schedule.has_replied(person_id):
        text += " · has not replied to the availability form"
    return text


# --------------------------------------------------------------------------
# Prefill
# --------------------------------------------------------------------------


def prefill(
    month: Month,
    people: Iterable[Person],
    settings: Settings,
    dates: Optional[Set[str]] = None,
    area_ids: Optional[Set[str]] = None,
    seed: int = 0,
    seconds: float = IMPROVE_SECONDS,
) -> Tuple[List[Proposal], List[Slot]]:
    """Proposals for open shifts (in the given days / areas); and those left open."""
    schedule = Schedule(month, people, settings)

    def in_scope(slot: Slot) -> bool:
        task = schedule.tasks[slot[0]]
        return (dates is None or slot[1] in dates) and (area_ids is None or task.area_id in area_ids)

    slots = [s for s in schedule.open_slots() if in_scope(s)]
    choices = {s: len(_options(schedule, s)) for s in slots}
    slots.sort(key=lambda s: (PRIORITY_RANK.get(schedule.tasks[s[0]].priority, 1), choices[s], s[1], s[0], s[2]))

    placed: Dict[Slot, Assignment] = {}
    for slot in slots:
        options = _options(schedule, slot)
        if options:
            _place(schedule, placed, slot, options[0].person.id)

    for slot in [s for s in slots if s not in placed]:
        _repair(schedule, placed, slot)

    _improve(schedule, placed, random.Random(seed), time.monotonic() + seconds)

    proposals = [
        Proposal(task_id, iso, position, a.person_id, why=_why(schedule, a.person_id))
        for (task_id, iso, position), a in sorted(placed.items(), key=lambda item: (item[0][1], item[0][0], item[0][2]))
    ]
    return proposals, [s for s in slots if s not in placed]


def _place(schedule: Schedule, placed: Dict[Slot, Assignment], slot: Slot, person_id: str) -> None:
    assignment = Assignment(*slot, person_id=person_id, source="auto")
    schedule.add(assignment)
    placed[slot] = assignment


def _unplace(schedule: Schedule, placed: Dict[Slot, Assignment], slot: Slot) -> str:
    assignment = placed.pop(slot)
    schedule.remove(assignment)
    return assignment.person_id


def _repair(schedule: Schedule, placed: Dict[Slot, Assignment], slot: Slot) -> bool:
    """Free someone for ``slot`` by giving one of their shifts that day to another person."""
    task_id, iso, _ = slot
    task = schedule.task_on(task_id, iso)
    for person in schedule.people.values():
        blocking = [f for f in rules.evaluate(rules.Placement(schedule, person, task, iso)) if f.severity == rules.BLOCK]
        if not blocking or any(f.rule not in CLASH_RULES for f in blocking):
            continue
        for other in list(schedule.on_day(person.id, iso)):
            other_slot = other.slot
            if placed.get(other_slot) is not other:
                continue  # only shifts proposed in this run may move
            _unplace(schedule, placed, other_slot)
            if rules.group_of(rules.evaluate(rules.Placement(schedule, person, task, iso))) != rules.BLOCKED:
                _place(schedule, placed, slot, person.id)
                replacement = [c for c in _options(schedule, other_slot) if c.person.id != person.id]
                if replacement:
                    _place(schedule, placed, other_slot, replacement[0].person.id)
                    return True
                _unplace(schedule, placed, slot)
            _place(schedule, placed, other_slot, person.id)
    return False


def _improve(schedule: Schedule, placed: Dict[Slot, Assignment], rng: random.Random, deadline: float) -> None:
    """Give placed shifts to better ranked people while it helps and time allows."""
    order = {rules.BEST: 0, rules.POSSIBLE: 1}
    improved = True
    while improved and time.monotonic() < deadline:
        improved = False
        slots = list(placed)
        rng.shuffle(slots)
        for slot in slots:
            if time.monotonic() >= deadline:
                return
            current = placed[slot]
            options = _options(schedule, slot, ignore=current)
            mine = next((c for c in options if c.person.id == current.person_id), None)
            best = options[0] if options else None
            if best is None or mine is None or best.person.id == current.person_id:
                continue
            if (order[best.group], best.cost + IMPROVE_MIN_GAIN) < (order[mine.group], mine.cost):
                _unplace(schedule, placed, slot)
                _place(schedule, placed, slot, best.person.id)
                improved = True


# --------------------------------------------------------------------------
# Rebalance: suggest moving shifts from the busiest to the least busy
# --------------------------------------------------------------------------


def _worth_moving(schedule: Schedule, assignment: Assignment, taker: str) -> bool:
    """Is giving this shift to ``taker`` a clear improvement, not a shuffle?

    For a shift with hours: the two people must be at least REBALANCE_MIN_GAP
    of their targets apart, and the move must not leave the taker busier
    than the giver. For a shift counted as zero hours (night security…):
    only to share weekends or evenings, when they differ by two or more.
    """
    giver = assignment.person_id
    hours = schedule.hours_of(assignment)
    if hours > 0:
        def ratio(person: str, person_hours: float) -> float:
            return person_hours / max(schedule.target(person), 1.0)

        before_gap = ratio(giver, schedule.hours(giver)) - ratio(taker, schedule.hours(taker))
        after_gap = ratio(giver, schedule.hours(giver) - hours) - ratio(taker, schedule.hours(taker) + hours)
        return before_gap >= REBALANCE_MIN_GAP and after_gap >= 0
    if parse_date(assignment.date).weekday() >= 5:
        return schedule.weekends(giver) - schedule.weekends(taker) >= 2
    if "evening" in schedule.task_on(assignment.task_id, assignment.date).periods:
        return schedule.evenings(giver) - schedule.evenings(taker) >= 2
    return False


def rebalance(month: Month, people: Iterable[Person], settings: Settings,
              max_moves: int = REBALANCE_MAX_MOVES) -> List[Proposal]:
    """Single moves that clearly even out the work, best first; each person moves once at most.

    Regular-week, locked and forced shifts stay where they are: they were
    decided on purpose.
    """
    schedule = Schedule(month, people, settings)
    moves = []
    for assignment in month.assignments:
        if not assignment.person_id or assignment.locked or assignment.forced_reason is not None \
                or assignment.source == "regular" or assignment.task_id not in schedule.tasks:
            continue
        options = _options(schedule, assignment.slot, ignore=assignment)
        mine = next((c for c in options if c.person.id == assignment.person_id), None)
        better = next((c for c in options if c.group == rules.BEST and c.person.id != assignment.person_id
                       and _worth_moving(schedule, assignment, c.person.id)), None)
        if mine and better and mine.cost - better.cost >= REBALANCE_MIN_GAIN:
            moves.append((mine.cost - better.cost, assignment, better.person.id))
    moves.sort(key=lambda move: -move[0])

    chosen: List[Proposal] = []
    involved: Set[str] = set()
    for _, assignment, person_id in moves:
        if len(chosen) >= max_moves:
            break
        if assignment.person_id in involved or person_id in involved:
            continue
        chosen.append(Proposal(*assignment.slot, person_id=person_id, replaces=assignment.person_id,
                               why=_move_why(schedule, assignment, person_id)))
        involved.update((assignment.person_id, person_id))
    return chosen


def _move_why(schedule: Schedule, assignment: Assignment, taker: str) -> str:
    giver, hours = assignment.person_id, schedule.hours_of(assignment)
    def name(person: str) -> str:
        return schedule.people[person].name

    if hours > 0:
        return (f"{name(giver)}: {_hours(schedule.hours(giver))} → {_hours(schedule.hours(giver) - hours)}"
                f" of {_hours(schedule.target(giver))} · {name(taker)}: {_hours(schedule.hours(taker))} →"
                f" {_hours(schedule.hours(taker) + hours)} of {_hours(schedule.target(taker))}")
    if parse_date(assignment.date).weekday() >= 5:
        return f"Weekends: {name(giver)} {schedule.weekends(giver)}, {name(taker)} {schedule.weekends(taker)}"
    return f"Evenings: {name(giver)} {schedule.evenings(giver)}, {name(taker)} {schedule.evenings(taker)}"


# --------------------------------------------------------------------------
# Shortages, before or while filling
# --------------------------------------------------------------------------


SHIFTS_A_DAY = 2
"""How many shifts one person is counted for on a day when estimating shortages
(unless their own limit is lower): lunch and supper is common, more is rare."""


def _max_matching(slots: List[Slot], options: Dict[Slot, List[str]], capacity: Dict[str, int]) -> int:
    """How many of ``slots`` can be filled at once, each person taking at most
    ``capacity`` of them (augmenting paths over copies of each person)."""
    owner: Dict[Tuple[str, int], Slot] = {}

    def assign(slot: Slot, seen: Set[Tuple[str, int]]) -> bool:
        for person in options[slot]:
            for copy in range(capacity.get(person, SHIFTS_A_DAY)):
                seat = (person, copy)
                if seat in seen:
                    continue
                seen.add(seat)
                if seat not in owner or assign(owner[seat], seen):
                    owner[seat] = slot
                    return True
        return False

    return sum(1 for slot in slots if assign(slot, set()))


def shortages(month: Month, people: Iterable[Person], settings: Settings,
              skill_names: Dict[str, str], limit: int = 6) -> List[Dict[str, object]]:
    """Days where open shifts outnumber the people who could take them, worst first;
    then the skills most often missing."""
    schedule = Schedule(month, people, settings)
    by_day: Dict[str, List[Slot]] = defaultdict(list)
    for slot in schedule.open_slots():
        by_day[slot[1]].append(slot)
    found = []
    nobody_by_skill: Counter = Counter()
    for iso, slots in by_day.items():
        options = {slot: [c.person.id for c in _options(schedule, slot)] for slot in slots}
        nobody = [s for s in slots if not options[s]]
        for slot in nobody:
            task = schedule.tasks[slot[0]]
            nobody_by_skill[task.skill_id or ""] += 1
        capacity = {p.id: min(SHIFTS_A_DAY, p.max_tasks_per_day or SHIFTS_A_DAY)
                    for p in schedule.people.values()}
        fillable = _max_matching(slots, options, capacity)
        if fillable < len(slots):
            worst = Counter(schedule.tasks[s[0]].name for s in nobody).most_common(2)
            found.append({
                "date": iso,
                "open": len(slots),
                "fillable": fillable,
                "nobody": len(nobody),
                "text": (f"{parse_date(iso).strftime('%a %d')}: {len(slots)} open, at most {fillable} can be filled"
                         + (f" — nobody free for {', '.join(name for name, _ in worst)}" if worst else "")),
            })
    found.sort(key=lambda item: (item["fillable"] - item["open"], item["date"]))
    return found[:limit] + _skill_hints(schedule, nobody_by_skill, skill_names)


def _skill_hints(schedule: Schedule, nobody_by_skill: Counter, skill_names: Dict[str, str]) -> List[Dict[str, object]]:
    """The skills most often missing: training a few more people would help most."""
    hints = []
    for skill_id, count in nobody_by_skill.most_common(2):
        if skill_id and count >= 3:
            trained = sum(1 for p in schedule.people.values() if p.active and skill_id in p.skills)
            name = skill_names.get(skill_id, "this skill")
            hints.append({"skill": skill_id, "open": count, "trained": trained,
                          "text": f"{count} open shifts need “{name}” and nobody free has it "
                                  f"({trained} people have it): training a few more would help most."})
    return hints


# --------------------------------------------------------------------------
# Ideas for one shift nobody obvious can take
# --------------------------------------------------------------------------


def _mentions(comment: str, iso: str, task_name: str) -> bool:
    """Does the comment speak of this day, or of this kind of task without naming another day?"""
    text = comment.lower()
    days = {int(d) for d in re.findall(r"\b(\d{1,2})(?:st|nd|rd|th)?\b", text)}
    if days:
        return parse_date(iso).day in days
    words = [w for w in re.findall(r"[a-z]{4,}", task_name.lower()) if w not in {"shift", "with", "clean"}]
    return any(w in text for w in words)


def ideas(month: Month, people: Iterable[Person], settings: Settings, catalog_regulars: Iterable,
          task_id: str, iso: str, position: int, limit: int = 6) -> List[Dict[str, object]]:
    """Ways out for a shift with no clean candidate, gentlest first."""
    schedule = Schedule(month, people, settings)
    task = schedule.task_on(task_id, iso)
    current = schedule.at((task_id, iso, position))
    found: List[Dict[str, object]] = []

    # 1. Someone free but for another shift that day, which someone else can take.
    for person in schedule.people.values():
        if len(found) >= 3:
            break
        blocking = [f for f in rules.evaluate(rules.Placement(schedule, person, task, iso, ignore=current))
                    if f.severity == rules.BLOCK]
        if not blocking or any(f.rule not in CLASH_RULES for f in blocking):
            continue
        for other in list(schedule.on_day(person.id, iso)):
            if other.locked or other.forced_reason is not None or other.task_id not in schedule.tasks:
                continue
            schedule.remove(other)
            free_now = rules.group_of(rules.evaluate(rules.Placement(schedule, person, task, iso, ignore=current))) != rules.BLOCKED
            replacement = [c for c in _options(schedule, other.slot) if c.person.id != person.id] if free_now else []
            schedule.add(other)
            if replacement:
                other_task = schedule.tasks[other.task_id]
                found.append({
                    "kind": "swap",
                    "text": f"Move {person.name} here from {other_task.name}; {replacement[0].person.name} can take {other_task.name}.",
                    "action": {"person_id": person.id, "from": slot_key(*other.slot), "replacement": replacement[0].person.id},
                })
                break

    # 2. People who did not reply are treated as available: worth asking first.
    for candidate in _options(schedule, (task_id, iso, position), ignore=current)[:limit]:
        if not schedule.has_replied(candidate.person.id):
            found.append({"kind": "ask", "person_id": candidate.person.id,
                          "text": f"{candidate.person.name} has not replied to the availability form: ask them."})
            break

    # 3. A comment that mentions this day or this kind of task.
    for person_id, reply in month.availability.items():
        person = schedule.people.get(person_id)
        if person and person.active and reply.comment and _mentions(reply.comment, iso, task.name):
            found.append({"kind": "comment", "person_id": person_id,
                          "text": f"{person.name} wrote: “{reply.comment[:140]}”"})

    # 4. Away that day but usually here on that weekday: perhaps they could come.
    weekday = weekday_key(iso)
    regular_people = {r.person_id for r in catalog_regulars if r.weekday == weekday}
    for person_id in sorted(regular_people):
        person = schedule.people.get(person_id)
        if person and person.active and any(schedule.is_unavailable(person_id, iso, p) for p in task.periods):
            found.append({"kind": "away", "person_id": person_id,
                          "text": f"{person.name} is away that day but usually helps on {parse_date(iso).strftime('%A')}s: ask if they could come."})
            break

    # 5. Not trained, but helps in the same area: could be shown how.
    if task.skill_id:
        area_skills = {t.skill_id for t in schedule.tasks.values() if t.area_id == task.area_id and t.skill_id}
        for candidate in rules.candidates(schedule, task, iso, ignore=current):
            rules_hit = {f.rule for f in candidate.findings if f.severity == rules.BLOCK}
            if rules_hit == {"not_trained"} and area_skills & set(candidate.person.skills):
                found.append({"kind": "train", "person_id": candidate.person.id,
                              "text": f"{candidate.person.name} helps in this area but is not trained for this task: could they be shown how? (Assign anyway)"})
                break

    found.append({"kind": "need", "text": "Or leave it open for sign-up, or mark it not needed this day."})
    return found[:limit]
