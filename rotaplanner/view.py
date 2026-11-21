"""The month as the screen needs it.

The browser only displays; every number and status it shows is computed here,
so the rules stay in one place (Python) and are covered by the tests.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional

from . import checks, demand, solver, tips, workload
from . import eligibility as rules
from .model import (
    Assignment,
    Catalog,
    Month,
    Person,
    Settings,
    Task,
    parse_date,
    slot_key,
)
from .months import regulars_on
from .schedule import Schedule
from .solver import Proposal

Json = Dict[str, Any]


def person_labels(people: Iterable[Person]) -> Dict[str, Json]:
    """Short name, full name and unique initials for every person."""
    people = list(people)
    labels: Dict[str, Json] = {}
    for person in people:
        full = f"{person.first_name} {person.last_name}".strip() or person.name
        labels[person.id] = {"short": person.name, "full": full, "initials": _initials(person, 1)}
    clashes = Counter(label["initials"] for label in labels.values())
    for person in people:
        if clashes[labels[person.id]["initials"]] > 1:
            labels[person.id]["initials"] = _initials(person, 2)
    seen: Counter = Counter()
    for person in people:
        label = labels[person.id]
        seen[label["initials"]] += 1
        if seen[label["initials"]] > 1:
            label["initials"] += str(seen[label["initials"]])
    return labels


def _initials(person: Person, first_letters: int) -> str:
    first = (person.first_name or person.name).strip()
    last = person.last_name.strip()
    if not last:
        words = person.name.replace(".", " ").split()
        last = words[-1] if len(words) > 1 else ""
    return (first[:first_letters].capitalize() + last[:1].upper()) or "?"


def _task_json(task: Task, skill_names: Dict[str, str], month: Month) -> Json:
    event = next((e for e in month.events if e.id == task.event_id), None) if task.event_id else None
    return {
        "event": event.name if event else None,  # a task of this event only
        "event_id": event.id if event else None,
        "on_main_rota": month.on_main_rota(task),
        "id": task.id,
        "name": task.name,
        "time": task.time_text(),
        "hours": task.hours,
        "periods": task.periods,
        "anytime": task.any_time,
        "skill": skill_names.get(task.skill_id or "", "Anyone"),
        "priority": task.priority,
    }


def _findings_json(findings: Iterable[rules.Finding]) -> List[Json]:
    return [{"severity": f.severity, "message": f.message} for f in findings]


def _assignment_json(assignment: Assignment, status: checks.Status) -> Json:
    return {
        "person": assignment.person_id,
        "guest": assignment.guest_label if assignment.outsider else "",
        "guest_kind": assignment.guest_kind or ("guest" if assignment.outsider else ""),
        "unnamed": assignment.outsider and not assignment.guest,  # name to come
        "state": status.state,
        "findings": _findings_json(status.findings),
        "source": assignment.source,
        "locked": assignment.locked,
        "forced": assignment.forced_reason,
        "note": assignment.note,
    }


def month_view(
    month: Month,
    catalog: Catalog,
    people: List[Person],
    settings: Settings,
    history: Optional[Json] = None,
    proposals: Iterable[Proposal] = (),
    around: Iterable[Month] = (),
) -> Json:
    """``proposals`` are suggestions not yet accepted; they are shown on their slots.
    ``history``: what can be undone and redone (``App._history_json``).
    ``around``: the months next door, which the rules look into (see ``Schedule``)."""
    schedule = Schedule(month, people, settings, around)
    statuses = checks.all_statuses(schedule)
    proposals = still_valid(schedule, proposals)
    proposed = {p.key: p for p in proposals}
    labels = person_labels(people)
    skill_names = {skill.id: skill.name for skill in catalog.skills}
    regulars = {iso: regulars_on(catalog, iso) for iso in schedule.dates}

    wanted = set(month.wanted)
    wanted_open = 0
    cells: Dict[str, Json] = {}
    day_stats: Dict[str, Counter] = defaultdict(Counter)
    for task in month.tasks:
        adjusted_days = month.demand_overrides.get(task.id, {})
        for iso in schedule.dates:
            closed = demand.closure(month, task, iso)
            count = schedule.positions(task.id, iso)
            if count == 0 and closed is None and iso not in adjusted_days and f"{task.id}|{iso}" not in month.time_changes:
                continue
            required = schedule.required.get((task.id, iso), 0)
            slots = []
            for position in range(count):
                assignment = schedule.at((task.id, iso, position))
                proposal = proposed.get(slot_key(task.id, iso, position))
                if assignment is not None:
                    slot = _assignment_json(assignment, statuses[assignment.slot])
                    if slot["state"] == checks.CONFLICT:
                        day_stats[iso]["conflicts"] += 1
                else:
                    regular = regulars[iso].get((task.id, position))
                    cover_for = labels.get(regular.person_id, {}).get("short") if regular else None
                    is_wanted = slot_key(task.id, iso, position) in wanted
                    wanted_open += is_wanted
                    slot = {"open": True, "cover_for": cover_for, "wanted": is_wanted}
                if proposal is not None:
                    slot["proposal"] = {"person": proposal.person_id, "why": proposal.why, "replaces": proposal.replaces}
                slots.append(slot)
            day_stats[iso]["required"] += required
            day_stats[iso]["filled"] += sum(1 for p in schedule.cell(task.id, iso) if p < required)
            change = month.time_changes.get(f"{task.id}|{iso}")
            cells[f"{task.id}|{iso}"] = {
                "time": schedule.task_on(task.id, iso).time_text() if change else None,
                "required": required,
                "usual": demand.rule_headcount(task, iso, month.day_type(iso), month.events),
                "adjusted": iso in adjusted_days,
                "closed": closed.label if closed else None,
                "slots": slots,
            }
    summary = checks.summarize(schedule, statuses).to_dict()
    summary["wanted"] = wanted_open
    # The weeks (Monday to Sunday) the month touches; each day says which is its own.
    calendar_weeks = workload.weeks(schedule)
    week_index = {week["start"]: i for i, week in enumerate(calendar_weeks)}

    return {
        "id": month.id,
        "title": month.title(),
        "year": month.year,
        "month": month.month,
        "days": [_day_json(month, iso, day_stats[iso], week_index[schedule.week_of(iso)]) for iso in schedule.dates],
        "calendar_weeks": calendar_weeks,
        "events": [e.to_dict() for e in month.events],
        "markers": [m.to_dict() for m in month.markers],
        "areas": [
            {
                "id": area.id,
                "name": area.name,
                "manager": area.manager,
                "color": area.color,
                "tasks": [
                    _task_json(task, skill_names, month) for task in month.tasks if task.area_id == area.id
                ],
            }
            for area in month.areas
        ],
        "cells": cells,
        "people": [_person_json(schedule, person, labels, skill_names) for person in people if not person.archived],
        "summary": summary,
        "tips": tips.month_tips(schedule, statuses, labels),
        "history": history or {"undo": None, "redo": None, "auto": None},
        "proposals": [p.to_dict() for p in proposals],
    }


def _day_json(month: Month, iso: str, stats: Counter, week: int) -> Json:
    day = parse_date(iso)
    day_type = month.day_type(iso)
    return {
        "date": iso,
        "week": week,   # its index in "calendar_weeks"
        "day": day.day,
        "weekday": day.strftime("%a"),
        "weekend": day.weekday() >= 5,
        "day_type": day_type,
        "required": stats["required"],
        "filled": stats["filled"],
        "conflicts": stats["conflicts"],
    }


def _person_json(
    schedule: Schedule, person: Person, labels: Dict[str, Json], skill_names: Dict[str, str]
) -> Json:
    label = labels[person.id]
    by_week = workload.by_week(schedule, person.id)
    return {
        "id": person.id,
        "name": label["short"],
        "full": label["full"],
        "initials": label["initials"],
        "active": person.active,
        "resident": person.resident,
        **workload.summary(schedule, person.id),   # hours, target, the average a week and their words
        "by_week": by_week,   # aligned with "calendar_weeks"
        "far_note": workload.far_note(schedule, person.id, by_week),   # what a marked week means, when there is one
        "presence": round(schedule.presence(person.id), 2),
        "replied": schedule.has_replied(person.id),
        "skills": [skill_names[s] for s in person.skills if s in skill_names],
        "limits": rules.limits_text(person),
        "note": person.note,
    }


def candidates_view(
    month: Month,
    people: List[Person],
    settings: Settings,
    task_id: str,
    iso: str,
    position: int,
    catalog: Optional[Catalog] = None,
    around: Iterable[Month] = (),
) -> Json:
    """Everyone for one slot, ranked, with the reasons behind each rank; and
    ideas when nobody can simply take it."""
    around = list(around)
    schedule = Schedule(month, people, settings, around)
    task = schedule.tasks.get(task_id)
    if task is None or iso not in schedule.dates:
        raise ValueError("Unknown task or date.")
    current: Optional[Assignment] = schedule.at((task_id, iso, position))
    labels = person_labels(people)

    on_day = schedule.task_on(task.id, iso)
    ranked = []
    for candidate in rules.candidates(schedule, task, iso, ignore=current):
        person = candidate.person
        busy = [
            schedule.tasks[a.task_id].name
            for a in schedule.on_day(person.id, iso)
            if a is not current and a.task_id in schedule.tasks
        ]
        holds = current is not None and current.person_id == person.id   # this place is already theirs
        ranked.append(
            {
                "id": person.id,
                "name": labels[person.id]["short"],
                "full": labels[person.id]["full"],
                "group": candidate.group,
                **workload.summary(schedule, person.id),
                # Their hours in the shift's week and in the month, with what this shift would add.
                "load": workload.choice(schedule, person.id, iso, 0.0 if holds else on_day.hours),
                "away": workload.away(schedule, person.id),   # their time away this month, from their reply
                "preference": person.preferences.get(task.area_id),
                "same_day": busy,
                # Going over the target is shown by the bars ("load"); it stays a rule everywhere else.
                "findings": _findings_json(f for f in candidate.findings if f.rule != "over_target"),
                "note": person.note,
            }
        )
    return {
        "task": {"id": task.id, "name": task.name, "time": on_day.time_text(), "hours": on_day.hours,
                 "start": on_day.start, "end": on_day.end, "label": on_day.time_label,
                 "usual_time": task.time_text() if on_day is not task else None},
        "date": iso,
        "position": position,
        "current": (current.person_id or current.guest_label) if current else None,
        "current_guest": {"kind": current.guest_kind or "guest", "name": current.guest} if current and current.outsider else None,
        "candidates": ranked,
        "ideas": solver.ideas(month, people, settings, catalog.regulars if catalog else [], task_id, iso, position,
                              around=around)
                 if current is None and not any(c["group"] == rules.BEST for c in ranked) else [],
    }


def still_valid(schedule: Schedule, proposals: Iterable[Proposal]) -> List[Proposal]:
    """Proposals whose slot is still as it was when they were made."""
    valid = []
    for proposal in proposals:
        if proposal.task_id not in schedule.tasks or proposal.person_id not in schedule.people:
            continue
        current = schedule.at((proposal.task_id, proposal.date, proposal.position))
        unchanged = current is None if not proposal.replaces else current is not None and current.person_id == proposal.replaces
        if unchanged and proposal.position < schedule.required.get((proposal.task_id, proposal.date), 0):
            valid.append(proposal)
    return valid


def eligible_slots(
    month: Month, people: List[Person], settings: Settings, person_id: str, around: Iterable[Month] = ()
) -> Json:
    """Open slots this person could take, and how cleanly."""
    schedule = Schedule(month, people, settings, around)
    person = schedule.people.get(person_id)
    if person is None:
        raise ValueError("Unknown person.")
    result = {}
    for task_id, iso, position in schedule.open_slots():
        placement = rules.Placement(schedule, person, schedule.task_on(task_id, iso), iso)
        group = rules.group_of(rules.evaluate(placement))
        if group != rules.BLOCKED:
            result[f"{task_id}|{iso}|{position}"] = group
    return {"person": person_id, "slots": result}
