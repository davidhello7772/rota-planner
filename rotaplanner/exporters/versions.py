"""Numbered versions of a month's rota: what was given out, and what changed since.

A rota goes through phases: a **draft**; a version given **to the manager to
check** (once or several times), with **corrections** after each check; then
a version **published** for the residents and printed, which may still get
**adjustments** afterwards. Each version freezes who is on each shift and
says who it was for. Later printouts say which version they follow and mark
the shifts that changed, so people only need to look at those. ``phase``
tells where the month stands.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from ..model import Month, Person, Settings, Version, parse_date
from ..schedule import Schedule
from ..view import person_labels

Json = Dict[str, Any]


def place_label(name: str, note: str = "") -> str:
    """A place as handed out: the name, and its note if any ("Ana B. (arrives at 10)")."""
    return f"{name} ({note})" if name and note else name


def current_places(month: Month, people: List[Person], settings: Settings) -> Dict[str, str]:
    """Slot key -> name shown, for every required or filled position."""
    schedule = Schedule(month, people, settings)
    names = {pid: label["short"] for pid, label in person_labels(people).items()}
    places = {}
    for task_id in schedule.tasks:
        for iso in schedule.dates:
            for position in range(schedule.positions(task_id, iso)):
                assignment = schedule.at((task_id, iso, position))
                label = ""
                if assignment is not None:
                    name = names.get(assignment.person_id, "?") if assignment.person_id else assignment.guest_label
                    label = place_label(name, assignment.note)
                places[f"{task_id}|{iso}|{position}"] = label
    return places


MANAGER, RESIDENTS = "manager", "residents"
AUDIENCES = (MANAGER, RESIDENTS)


def publish(month: Month, people: List[Person], settings: Settings, today: Optional[date] = None,
            audience: str = RESIDENTS) -> Version:
    """Freeze the rota as a new version: for the manager to check, or published for the residents."""
    if audience not in AUDIENCES:
        raise ValueError("Unknown choice.")
    version = Version(number=len(month.versions) + 1, published=(today or date.today()).isoformat(),
                      places=current_places(month, people, settings), audience=audience)
    month.versions.append(version)
    return version


def reference(month: Month) -> Optional[Version]:
    """The version printouts are compared with (the ● marks, "changed since").

    Once the rota is published, that is what is on the wall: it stays the
    reference until an updated version is published, even if a version is
    given to the manager in between. Before that, the last version given.
    """
    published = [v for v in month.versions if v.audience != MANAGER]
    if published:
        return published[-1]
    return month.versions[-1] if month.versions else None


def changed(before: Optional[Version], key: str, label: str) -> bool:
    """Has this place changed since ``before`` (a shift that was not there and is still open has not)?"""
    if before is None:
        return False
    previous = before.places.get(key)
    if previous is None:
        return label != ""
    return previous != label


def changes(month: Month, people: List[Person], settings: Settings) -> List[Json]:
    """Places that changed since the reference version, in date order."""
    last = reference(month)
    if last is None:
        return []
    now = current_places(month, people, settings)
    tasks = {t.id: t.name for t in month.tasks}
    found = []
    for key in sorted(set(now) | set(last.places), key=lambda k: (k.split("|")[1], k)):
        before, after = last.places.get(key), now.get(key)
        if (before or "") == (after or "") or (before is None and not after):
            continue
        task_id, iso, _ = key.split("|")
        found.append({"key": key, "date": iso, "task": tasks.get(task_id, "?"),
                      "before": before or "", "after": after or "",
                      "gone": after is None})  # the shift itself is no longer needed
    return found


def print_says(version: Optional[Version]) -> str:
    """How printouts name the version they follow."""
    if version is None:
        return "Draft"
    return f"Version {version.number}" + (" to check" if version.audience == MANAGER else "")


def stamp(month: Month, printed: date, has_changes: bool) -> str:
    """The words in each printout's header. A version for the manager says "to check",
    so a copy made for checking is never taken for the published rota."""
    day = f"{printed.day} {printed.strftime('%b %Y')}"
    last = reference(month)
    if last is None:
        return f"Draft · printed {day}"
    published = parse_date(last.published)
    text = f"{print_says(last)} · {published.day} {published.strftime('%b %Y')}"
    return f"{text} · ● changed since, printed {day}" if has_changes else text


# --------------------------------------------------------------------------
# Where the month stands
# --------------------------------------------------------------------------

DRAFT, CHECKING, CORRECTIONS, PUBLISHED, ADJUSTED = "draft", "checking", "corrections", "published", "adjusted"
STEPS = (("draft", "Draft"), ("checking", "Manager’s check"), ("corrections", "Corrections"), ("published", "Published"))


def _day(iso: str) -> str:
    day = parse_date(iso)
    return f"{day.day} {day.strftime('%b')}"


def _count(n: int) -> str:
    return f"{n} change{'s' if n != 1 else ''}"


def phase(month: Month, people: List[Person], settings: Settings, found: Optional[List[Json]] = None) -> Json:
    """The phase of the rota, in words, with the steps for the screen.

    ``state``: draft · checking (with the manager, nothing changed since) ·
    corrections (changed since the manager's version) · published (for the
    residents, nothing changed since) · adjusted (changed since it was published).
    ``found``: the result of ``changes``, when the caller already has it.
    """
    last = reference(month)
    latest = month.versions[-1] if month.versions else None
    changed_since = len(changes(month, people, settings) if found is None else found) if last else 0
    checked = [v for v in month.versions if v.audience == MANAGER]
    # Was anything changed between a version the manager saw and the one that followed?
    corrected = any(v.audience == MANAGER and v.places != after.places
                    for v, after in zip(month.versions, month.versions[1:]))
    if last is None:
        state, short, tone = DRAFT, "Draft", ""
        text = "Not given to anyone yet. Printouts say “Draft”."
    elif last.audience == MANAGER and not changed_since:
        state, short, tone = CHECKING, f"With the manager · v{last.number}", "info"
        text = f"Version {last.number} was given to the manager to check on {_day(last.published)}. Nothing changed since."
    elif last.audience == MANAGER:
        state, short, tone = CORRECTIONS, f"Corrections · {_count(changed_since)}", "warn"
        text = (f"{_count(changed_since).capitalize()} since version {last.number}, given to the manager on {_day(last.published)}. "
                "Give it to the manager again, or publish it.")
    elif not changed_since:
        state, short, tone = PUBLISHED, f"Published · v{last.number}", "ok"
        text = f"Version {last.number} was published for the residents on {_day(last.published)}. What was printed is up to date."
    else:
        state, short, tone = ADJUSTED, f"Published · {_count(changed_since)}", "warn"
        text = (f"{_count(changed_since).capitalize()} since version {last.number}, published on {_day(last.published)}: "
                "what is on the wall is no longer up to date. Print again now and the changes are marked ●; "
                "publish an updated version for a clean copy without marks.")
    if latest is not None and last is not None and latest is not last:
        # Given to the manager again after it was published: said, but the wall copy stays the reference.
        text += f" Version {latest.number} was given to the manager on {_day(latest.published)}."
    current = PUBLISHED if state == ADJUSTED else state
    order = [key for key, _ in STEPS]
    steps = []
    for key, label in STEPS:
        if key == current:
            status = "current"
        elif order.index(key) < order.index(current):
            # A check that never happened (published straight away), or a check
            # that asked for no correction, is shown as skipped, not done.
            happened = {DRAFT: True, CHECKING: bool(checked), CORRECTIONS: corrected}.get(key, True)
            status = "done" if happened else "skipped"
        else:
            status = "todo"
        steps.append({"key": key, "label": label, "status": status})
    return {
        "state": state, "short": short, "tone": tone, "text": text, "steps": steps,
        "changes": changed_since, "next_number": len(month.versions) + 1,
        "version": last.number if last else 0,
        "published": bool(last is not None and last.audience != MANAGER),
        "print_says": print_says(last),
    }
