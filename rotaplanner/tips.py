"""Short, actionable hints for the coordinator, shown in the Assistant panel."""

from __future__ import annotations

from collections import Counter
from typing import Dict, List

from . import checks
from .model import parse_date, slot_key
from .schedule import Schedule, Slot

UNDER_LOADED_SHARE = 0.5
"""People below this share of their target hours are pointed out as free hands."""


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _people(count: int) -> str:
    return f"{count} {'person' if count == 1 else 'people'}"


def month_tips(
    schedule: Schedule, statuses: Dict[Slot, checks.Status], labels: Dict[str, dict]
) -> List[Dict[str, str]]:
    found: List[Dict[str, str]] = []
    open_slots = schedule.open_slots()

    # Tasks kept on an event's own rota are not in the month grid: say where their open shifts are.
    month = schedule.month
    hidden = Counter(schedule.tasks[t].event_id for t, _, _ in open_slots
                     if not month.on_main_rota(schedule.tasks[t]))
    for event in month.events:
        if hidden.get(event.id):
            found.append({
                "tone": "info", "event": event.id,
                "text": f"{_plural(hidden[event.id], 'open shift')} on the {event.name} rota (its own tasks).",
            })

    conflicts = sum(1 for s in statuses.values() if s.state == checks.CONFLICT)
    if conflicts:
        verb = "breaks" if conflicts == 1 else "break"
        found.append({
            "tone": "bad",
            "text": f"{_plural(conflicts, 'assignment')} {verb} a rule. Click a red name to fix it.",
        })

    wanted = set(schedule.month.wanted)
    still_wanted = sum(1 for t, iso, pos in open_slots if slot_key(t, iso, pos) in wanted)
    if still_wanted:
        found.append({
            "tone": "warn",
            "text": f"{_plural(still_wanted, 'highlighted shift')} still to fill.",
        })

    if open_slots:
        by_day = Counter(iso for _, iso, _ in open_slots)
        day, count = by_day.most_common(1)[0]
        area_names = {area.id: area.name for area in schedule.month.areas}
        by_area = Counter(area_names.get(schedule.tasks[t].area_id, "?") for t, _, _ in open_slots)
        area, area_count = by_area.most_common(1)[0]
        found.append({
            "tone": "warn",
            "text": (
                f"{_plural(len(open_slots), 'open shift')}. Most are in {area} ({area_count}); "
                f"the busiest day is {parse_date(day).strftime('%a %d')} ({count} open)."
            ),
        })

    active = [p for p in schedule.people.values() if p.active and not p.archived]
    under = [
        p for p in active
        if schedule.presence(p.id) > 0
        and schedule.hours(p.id) < UNDER_LOADED_SHARE * schedule.target(p.id)
    ]
    if under:
        names = ", ".join(labels[p.id]["short"] for p in sorted(under, key=lambda p: schedule.hours(p.id))[:4])
        more = "…" if len(under) > 4 else ""
        found.append({
            "tone": "info",
            "text": f"{_people(len(under))} below half of their target hours ({names}{more}): "
                    "good candidates for open shifts.",
        })

    silent = [p for p in active if not schedule.has_replied(p.id)]
    if silent:
        found.append({
            "tone": "info",
            "text": f"No availability reply from {_people(len(silent))}; they are treated as available.",
        })
    return found
