"""What the program can do, independent of how it is asked (the HTTP layer).

``App`` holds the data in memory, applies changes through the domain modules,
saves them, and keeps an undo history for each month.
"""

from __future__ import annotations

import base64
import binascii
import calendar
import copy
import re
import threading
from datetime import date
from http import HTTPStatus
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import availability, editing, emails, mail, month_setup, months, people_import, report, solver, transfer, view
from .exporters import documents, pdf, personal, sheet, versions, xlsx_rota
from .model import (
    EVENT_TASK_DAYS, EVENT_TASKS_PRINTED, FREQUENCIES, PRINT_SIZES, WEEKDAY_KEYS, ConfigTemplate, Month, PrintTemplate,
    Task, new_id,
)
from .schedule import Schedule
from .store import Store
from .sync import PenError, Sync
from .updates import UpdateError, Updates

TYPICAL = "typical"
"""Scope name of the typical month: the configuration new months start from."""
UNDO_LIMIT = 200
MAX_UPLOAD_BYTES = 20_000_000

Json = Dict[str, Any]


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.payload = {"error": message, **extra}


class App:
    """Application state shared by all requests, guarded by one lock."""

    def __init__(self, store: Store, today: Optional[date] = None) -> None:
        self.store = store
        self.lock = threading.RLock()
        self.catalog = store.load_catalog()
        self.people = store.load_people()
        self.settings = store.load_settings()
        self.templates = store.load_templates()
        self._months: Dict[str, Month] = {}
        self._undo: Dict[str, List[Json]] = {}
        self._redo: Dict[str, List[Json]] = {}
        self._proposals: Dict[str, List[solver.Proposal]] = {}  # suggestions not yet accepted, per month
        # What the last "Prefill" said beside its suggestions (what stays open, and
        # why), with the days and area it was asked for: "Suggest for these too"
        # goes further in the same ones. Forgotten with the suggestions.
        self._suggested: Dict[str, Json] = {}
        self.sync = Sync(store)
        self.sync.on_incoming = self.sync_auto
        self.updates = Updates(Path(__file__).resolve().parent.parent, store)  # is there a newer version?
        self.mail = mail.Mail(store.root)  # optional: off until an account is set up in Settings
        self.forget_old_comments(today or date.today())

    # -- reading -----------------------------------------------------------

    def bootstrap(self) -> Json:
        ids = self.store.list_months()
        return {
            "centre": self.settings.centre_name,
            "months": [{"id": i, "title": self.month(i).title()} for i in ids],
            "current": month_in_preparation(ids, date.today()),
            # Nothing yet on this computer: the first screen offers where to begin.
            "empty": self.store.is_empty() and not ids and not self.people,
        }

    def month(self, month_id: str) -> Month:
        if month_id not in self._months:
            self._months[month_id] = self.store.load_month(month_id)
        return self._months[month_id]

    def _around(self, month_id: str) -> List[Month]:
        """The months just before and after ``month_id``, those that exist: the rules
        look a few days into them (a night shift on the 31st, a week across two months).
        Only read: a change to one month never writes into the others."""
        year, number = (int(part) for part in month_id.split("-"))
        index = year * 12 + number - 1
        existing = set(self.store.list_months())
        ids = [Month.make_id((index + step) // 12, (index + step) % 12 + 1) for step in (-1, 1)]
        return [self.month(m) for m in ids if m in existing and m != month_id]

    def month_view(self, month_id: str) -> Json:
        result = self._month_view(month_id)
        # Where the rota stands (draft, with the manager, published…), shown on every screen.
        result["phase"] = versions.phase(self.month(month_id), self.people, self.settings)
        # What the last suggestions left open, and why; not once they were all used
        # (taken by hand meanwhile), unless there was nothing to suggest at all.
        kept = self._suggested.get(month_id)
        report = kept["report"] if kept else None
        result["suggested"] = report if report and (result["proposals"] or not report["proposed"]) else None
        return result

    def _month_view(self, month_id: str) -> Json:
        return view.month_view(
            self.month(month_id),
            self.catalog,
            self.people,
            self.settings,
            can_undo=bool(self._undo.get(month_id)),
            can_redo=bool(self._redo.get(month_id)),
            proposals=self._proposals.get(month_id, []),
            around=self._around(month_id),
        )

    def candidates(self, month_id: str, query: Dict[str, str]) -> Json:
        return view.candidates_view(
            self.month(month_id), self.people, self.settings,
            query.get("task", ""), query.get("date", ""), to_int(query.get("position")), catalog=self.catalog,
            around=self._around(month_id),
        )

    def eligible(self, month_id: str, person_id: str) -> Json:
        return view.eligible_slots(self.month(month_id), self.people, self.settings, person_id,
                                   around=self._around(month_id))

    # -- changing ----------------------------------------------------------

    def _change(
        self, month_id: str, change: Callable[[Month], bool], answer: Optional[Callable[[str], Json]] = None,
        record: bool = True,
    ) -> Json:
        """Apply ``change``; if it reports a change, make it undoable (unless ``record``
        is off) and save.

        Answers with the month as the rota shows it, or with ``answer(month_id)``.
        """
        month = self.month(month_id)
        before = month.to_dict()
        try:
            changed = change(month)
        except Exception:
            # A change that fails half-way must not leave half of it behind.
            self._months[month_id] = Month.from_dict(before)
            raise
        if changed:
            if record:
                undo = self._undo.setdefault(month_id, [])
                undo.append(before)
                del undo[:-UNDO_LIMIT]
                self._redo[month_id] = []
            self.store.save_month(month)
        return (answer or self.month_view)(month_id)

    # -- help: suggestions, rebalancing, shortages ---------------------------------

    def prefill(self, month_id: str, body: Json) -> Json:
        """Suggest someone for the open shifts (of some days or one area), at most a
        quarter over each person's target.

        With ``"beyond": true`` ("Suggest for these too"): the same days and area
        as the suggestions waiting, without that ceiling (the limits on each
        person's card still hold). The shifts of those suggestions stay suggested,
        though one may go to someone else when that fills an open shift; the new
        ones are added to them."""
        month = self.month(month_id)
        around = self._around(month_id)
        if body.get("beyond"):
            kept = self._suggested.get(month_id)
            if not kept:
                raise ApiError(HTTPStatus.CONFLICT, "These suggestions are out of date: ask for suggestions again.")
            dates, areas = kept["dates"], kept["areas"]
            held = [p for p in view.still_valid(Schedule(month, self.people, self.settings, around),
                                                self._proposals.get(month_id, [])) if not p.replaces]
            ceiling: Optional[float] = None
        else:
            dates = set(body["dates"]) if body.get("dates") else None
            areas = {str(body["area_id"])} if body.get("area_id") else None
            held, ceiling = [], solver.SUGGEST_UP_TO
        proposals, left, report = solver.prefill(month, self.people, self.settings, dates=dates, area_ids=areas,
                                                 around=around, ceiling=ceiling, held=held)
        self._proposals[month_id] = proposals  # every suggestion waiting now, the held ones included
        if report["proposed"] or report["left"]:
            self._suggested[month_id] = {"report": report, "dates": dates, "areas": areas}
        else:
            self._suggested.pop(month_id, None)  # nothing was open there: nothing to say
        result = self.month_view(month_id)
        waiting = {p.key for p in held}
        result["prefill"] = {"proposed": sum(1 for p in proposals if p.key not in waiting), "left_open": len(left)}
        return result

    def rebalance(self, month_id: str, _body: Json) -> Json:
        proposals = solver.rebalance(self.month(month_id), self.people, self.settings, around=self._around(month_id))
        self._proposals[month_id] = proposals
        self._suggested.pop(month_id, None)
        result = self.month_view(month_id)
        result["rebalance"] = {"proposed": len(proposals)}
        return result

    def accept_proposals(self, month_id: str, body: Json) -> Json:
        """Accept the proposals (all, or those in ``keys``) as one undoable change.

        A proposal that no longer fits (someone else was placed meanwhile, or a
        rule now blocks it) is skipped and reported.
        """
        wanted = set(body.get("keys") or [])
        pending = self._proposals.get(month_id, [])
        chosen = [p for p in pending if not wanted or p.key in wanted]
        accepted, skipped = 0, 0
        around = self._around(month_id)

        def change(month: Month) -> bool:
            nonlocal accepted, skipped
            schedule_valid = {p.key for p in view.still_valid(Schedule(month, self.people, self.settings), chosen)}
            for proposal in chosen:
                if proposal.key not in schedule_valid:
                    skipped += 1
                    continue
                try:
                    made = months.assign(month, self.people, self.settings, proposal.task_id, proposal.date,
                                         proposal.position, person_id=proposal.person_id, around=around)
                    made.source = "auto"
                    accepted += 1
                except (months.RuleViolation, ValueError):
                    skipped += 1
            return accepted > 0

        self._change(month_id, change)
        done = {p.key for p in chosen}
        self._proposals[month_id] = [p for p in pending if p.key not in done]
        if not self._proposals[month_id]:
            self._suggested.pop(month_id, None)
        result = self.month_view(month_id)
        result["accepted"], result["skipped"] = accepted, skipped
        return result

    def discard_proposals(self, month_id: str, body: Json) -> Json:
        unwanted = set(body.get("keys") or [])
        self._proposals[month_id] = [p for p in self._proposals.get(month_id, [])
                                     if unwanted and p.key not in unwanted]
        if not self._proposals[month_id]:
            self._suggested.pop(month_id, None)  # also "OK" under "Nothing to suggest"
        return self.month_view(month_id)

    def swap(self, month_id: str, body: Json) -> Json:
        """Free someone for a shift by giving another of their shifts (that day, or that week) to someone else."""
        target, source = months.parse_slot_key(str(body.get("key"))), months.parse_slot_key(str(body.get("from")))
        around = self._around(month_id)

        def change(month: Month) -> bool:
            try:
                months.assign(month, self.people, self.settings, *source, person_id=str(body.get("replacement")),
                              around=around)
                months.assign(month, self.people, self.settings, *target, person_id=str(body.get("person_id")),
                              around=around)
            except months.RuleViolation as violation:
                raise ApiError(HTTPStatus.CONFLICT, f"This exchange no longer works: {violation}") from violation
            return True

        return self._change(month_id, change)

    def check(self, month_id: str) -> Json:
        """What still needs attention, and the balance of hours (with the two months before)."""
        ids = self.store.list_months()
        earlier = [m for m in ids if m < month_id][-2:]
        return report.check_report(self.month(month_id), self.people, self.settings,
                                   [self.month(m) for m in reversed(earlier)], around=self._around(month_id))

    def shortages(self, month_id: str) -> Json:
        names = {s.id: s.name for s in self.catalog.skills}
        return {"items": solver.shortages(self.month(month_id), self.people, self.settings, names,
                                          around=self._around(month_id))}

    # -- creating and preparing a month ----------------------------------------

    def new_month_info(self) -> Json:
        """Which month to create next, and what it can start from."""
        ids = self.store.list_months()
        latest = tuple(int(part) for part in ids[-1].split("-")) if ids else (date.today().year, date.today().month)
        choices = []
        year, number = latest
        for _ in range(3):
            year, number = month_setup.next_month_after(year, number)
            month_id = Month.make_id(year, number)
            choices.append({"id": month_id, "year": year, "month": number,
                            "title": f"{calendar.month_name[number]} {year}", "exists": month_id in ids})
        return {
            "choices": choices,
            "sources": self._sources(),
            "default_source": f"month:{ids[-1]}" if ids else TYPICAL,
            "regulars": len(self.catalog.regulars),
        }

    def _sources(self, exclude: str = "") -> List[Json]:
        """Configurations a month can take its areas and tasks from."""
        months_list = [m for m in reversed(self.store.list_months()) if m != exclude]
        return ([{"id": f"month:{m}", "label": self.month(m).title(), "tasks": len(self.month(m).tasks)} for m in months_list]
                + [{"id": f"template:{t.id}", "label": f"Template: {t.name}", "tasks": len(t.tasks)} for t in self.templates]
                + [{"id": TYPICAL, "label": "Typical month", "tasks": len(self.catalog.tasks)}])

    def _source(self, ref: str) -> editing.TaskConfig:
        kind, _, name = ref.partition(":")
        if ref == TYPICAL:
            return self.catalog
        if kind == "month":
            return self.month(name)
        template = next((t for t in self.templates if t.id == name), None) if kind == "template" else None
        if template is None:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Choose where the tasks come from.")
        return template

    def create_month(self, body: Json) -> Json:
        year, number = to_int(body.get("year")), to_int(body.get("month"))
        if not (2000 <= year <= 2100 and 1 <= number <= 12):
            raise ApiError(HTTPStatus.BAD_REQUEST, "Choose a month.")
        month_id = Month.make_id(year, number)
        if month_id in self.store.list_months():
            raise ApiError(HTTPStatus.CONFLICT, f"{calendar.month_name[number]} {year} already exists.")
        source = self._source(str(body.get("source") or TYPICAL))
        month = month_setup.new_month(self.catalog, source, year, number, self.people, self.settings,
                                      around=self._around(month_id))
        # Events and closures already planned that run into this month come with it.
        month_setup.take_linked_items(month, [self.month(m) for m in self._neighbours(month_id)])
        self.store.save_month(month)
        self._months[month_id] = month
        return {"id": month_id, "placed": len(month.assignments), "bootstrap": self.bootstrap()}

    def _neighbours(self, month_id: str) -> List[str]:
        """Existing months an event or closure of ``month_id`` could reach: it lasts
        at most 62 days, so up to three months on each side."""
        year, number = (int(part) for part in month_id.split("-"))
        index = year * 12 + number - 1
        nearby = {Month.make_id((index + step) // 12, (index + step) % 12 + 1) for step in (-3, -2, -1, 1, 2, 3)}
        return sorted(nearby & set(self.store.list_months()))

    def _spread(self, month_id: str, kind: str, item_id: str) -> List[Json]:
        """After a change to an event or closure, update the other months it touches.

        Returns the months it reaches that do not exist yet, so the screen can
        offer to create them.
        """
        source = self.month(month_id)
        item = next((x for x in getattr(source, kind) if x.id == item_id), None)
        tasks = month_setup.event_tasks(source, item_id) if kind == "events" else []
        for other in self._neighbours(month_id):
            # Not an undo step of the other month: undoing there would only undo this copy.
            self._change(other, lambda m: month_setup.sync_item(m, kind, item_id, item, tasks, source.areas),
                         answer=lambda _: {}, record=False)
        existing = set(self.store.list_months())
        missing = [m for m in month_setup.months_spanned(item.start, item.end) if m not in existing] if item else []
        return [{"id": m, "year": int(m[:4]), "month": int(m[5:]), "title": f"{calendar.month_name[int(m[5:])]} {m[:4]}"}
                for m in missing]

    def month_setup(self, month_id: str) -> Json:
        month = self.month(month_id)
        days_overview = month_setup.overview(month, self.people, self.settings)
        return {
            "id": month.id,
            "title": month.title(),
            "days": [
                {
                    "date": iso, "day": int(iso[-2:]), "weekday": date.fromisoformat(iso).strftime("%a"),
                    "weekend": date.fromisoformat(iso).weekday() >= 5,
                    "day_type": month.day_type(iso), "set_by_hand": iso in month.day_types,
                    **days_overview["days"][iso],
                }
                for iso in month.dates()
            ],
            "weeks": days_overview["weeks"],
            "totals": {k: days_overview[k] for k in ("hours_needed", "hours_available", "balance", "hours_short",
                                                     "people", "replied")},
            "events": [{**e.to_dict(), "tasks": [self._event_task_json(t, month) for t in month_setup.event_tasks(month, e.id)]}
                       for e in month.events],
            "markers": [m.to_dict() for m in month.markers],
            "areas": [{"id": a.id, "name": a.name, "color": a.color} for a in month.areas if not a.archived],
            "tasks": [{"id": t.id, "name": t.name, "area_id": t.area_id} for t in month.tasks if not t.archived],
            "skills": [{"id": s.id, "name": s.name} for s in self.catalog.skills],
            "event_types": self._event_types_json(),
            "event_days": EVENT_TASK_DAYS,
            "history": {"undo": bool(self._undo.get(month_id)), "redo": bool(self._redo.get(month_id))},
        }

    def _event_task_json(self, task: Task, config: editing.TaskConfig) -> Json:
        """A task of an event (in a month) or of an event type (in the catalog), with what the screens show."""
        area = next((a for a in config.areas if a.id == task.area_id), None)
        skill = next((s.name for s in self.catalog.skills if s.id == task.skill_id), "Anyone")
        return {**task.to_dict(), "area": area.name if area else "?", "color": area.color if area else "slate",
                "skill": skill, "time": task.time_text(), "people": task.headcount.get("default", 1),
                "days_text": EVENT_TASK_DAYS.get(task.event_days, "")}

    def _event_types_json(self) -> List[Json]:
        return [{"id": t.id, "name": t.name, "big": t.big, "tasks_on_main_rota": t.tasks_on_main_rota,
                 "tasks": [self._event_task_json(task, self.catalog) for task in t.tasks]}
                for t in self.catalog.event_types]

    def _setup_change(self, month_id: str, change: Callable[[Month], Any]) -> Json:
        return self._change(month_id, lambda month: change(month) is not False, answer=self.month_setup)

    def save_event(self, month_id: str, body: Json) -> Json:
        saved = []
        self._setup_change(month_id, lambda m: saved.append(month_setup.save_event(m, body.get("event") or {})))
        return self._after_linked_change(month_id, "events", saved[0].id)

    def delete_event(self, month_id: str, body: Json) -> Json:
        self._setup_change(month_id, lambda m: month_setup.delete_event(m, str(body.get("id"))))
        return self._after_linked_change(month_id, "events", str(body.get("id")))

    def save_marker(self, month_id: str, body: Json) -> Json:
        saved = []
        self._setup_change(month_id, lambda m: saved.append(month_setup.save_marker(m, body.get("marker") or {})))
        return self._after_linked_change(month_id, "markers", saved[0].id)

    def delete_marker(self, month_id: str, body: Json) -> Json:
        self._setup_change(month_id, lambda m: month_setup.delete_marker(m, str(body.get("id"))))
        return self._after_linked_change(month_id, "markers", str(body.get("id")))

    # -- tasks of one event, and event types that keep the usual ones ----------

    def save_event_task(self, month_id: str, body: Json) -> Json:
        task = body.get("task") or {}
        self._setup_change(month_id, lambda m: month_setup.save_event_task(m, self.catalog, task))
        return self._after_linked_change(month_id, "events", str(task.get("event_id")))

    def delete_event_task(self, month_id: str, body: Json) -> Json:
        task = next((t for t in self.month(month_id).tasks if t.id == body.get("id")), None)
        if task is None or not task.event_id:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Unknown task.")
        self._setup_change(month_id, lambda m: month_setup.delete_event_task(m, task.id))
        return self._after_linked_change(month_id, "events", task.event_id)

    def add_type_tasks(self, month_id: str, body: Json) -> Json:
        event_type = next((t for t in self.catalog.event_types if t.id == body.get("type_id")), None)
        if event_type is None:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Unknown type of event.")
        event_id = str(body.get("event_id"))
        added = []
        self._setup_change(month_id, lambda m: added.append(month_setup.add_type_tasks(m, event_id, event_type)) or added[0] > 0)
        result = self._after_linked_change(month_id, "events", event_id)
        result["added"] = added[0] if added else 0
        return result

    def save_event_as_type(self, month_id: str, body: Json) -> Json:
        """Keep an event's tasks as those of its type (a type of the same name is updated);
        the event becomes of that type."""
        name, event_id = str(body.get("name") or ""), str(body.get("event_id"))
        same = next((t for t in self.catalog.event_types if t.name.lower() == " ".join(name.split()).lower()), None)
        event_type = month_setup.type_from_event(self.month(month_id), event_id, name, same.id if same else None)
        self.catalog.event_types = [t for t in self.catalog.event_types if t is not same] + [event_type]
        self.catalog.event_types.sort(key=lambda t: t.name.lower())
        self.store.save_catalog(self.catalog)

        def mark(month: Month) -> bool:
            event = next(e for e in month.events if e.id == event_id)
            changed, event.type_id = event.type_id != event_type.id, event_type.id
            return changed

        self._setup_change(month_id, mark)
        return self._after_linked_change(month_id, "events", event_id)

    # Event types, edited on their own (Tasks & Areas): they live in the catalog.

    def save_event_type(self, body: Json) -> Json:
        editing.save_event_type(self.catalog, body.get("type") or {})
        return self._save_setup(catalog=True)

    def delete_event_type(self, body: Json) -> Json:
        """Forget a type of event; events of that type keep their tasks."""
        editing.delete_event_type(self.catalog, str(body.get("id")))
        return self._save_setup(catalog=True)

    def save_type_task(self, body: Json) -> Json:
        editing.save_type_task(self.catalog, body.get("task") or {})
        return self._save_setup(catalog=True)

    def delete_type_task(self, body: Json) -> Json:
        editing.delete_type_task(self.catalog, str(body.get("type_id")), str(body.get("id")))
        return self._save_setup(catalog=True)

    def order_type_tasks(self, body: Json) -> Json:
        editing.order_type_tasks(self.catalog, str(body.get("type_id")), [str(i) for i in body.get("ids") or []])
        return self._save_setup(catalog=True)

    def _after_linked_change(self, month_id: str, kind: str, item_id: str) -> Json:
        missing = self._spread(month_id, kind, item_id)
        result = self.month_setup(month_id)
        result["missing_months"] = missing
        return result

    def set_day_type(self, month_id: str, body: Json) -> Json:
        day_type = body.get("day_type")
        return self._setup_change(month_id, lambda m: month_setup.set_day_type(
            m, str(body.get("date")), str(day_type) if day_type else None))

    def reapply_regulars(self, month_id: str, _body: Json) -> Json:
        around = self._around(month_id)
        return self._setup_change(
            month_id, lambda m: months.apply_regulars(m, self.catalog, self.people, self.settings, around) > 0)

    # -- availability -----------------------------------------------------------

    def availability(self, month_id: str) -> Json:
        month = self.month(month_id)
        result = availability.summary(month, self.people)
        result["away_regulars"] = len(availability.away_regulars(month, self.people, self.settings))
        result["title"] = month.title()
        result["comments_until"] = month.dates()[-1]  # deleted once the month is over
        result["history"] = {"undo": bool(self._undo.get(month_id)), "redo": bool(self._redo.get(month_id))}
        return result

    def availability_preview(self, month_id: str, body: Json) -> Json:
        return availability.preview(self.month(month_id), self.people, text=str(body.get("text") or ""),
                                    file_bytes=_decode_file(body), filename=str(body.get("filename") or ""),
                                    salt=self.store.email_salt())

    def availability_apply(self, month_id: str, body: Json) -> Json:
        """Save the ticked replies, and remember the addresses the coordinator chose to."""
        saved = 0
        replies = list(body.get("replies") or [])

        def change(month: Month) -> bool:
            nonlocal saved
            saved = availability.apply(month, self.people, replies)
            return saved > 0

        result = self._change(month_id, change, answer=self.availability)
        remembered = 0
        for reply in replies[:availability.MAX_ROWS]:
            if reply.get("remember_email"):
                remembered += emails.remember(self.people, str(reply.get("person_id") or ""), reply.get("email_fp"),
                                              self.store.email_salt())
        if remembered:
            self.store.save_people(self.people)
            result = self.availability(month_id)
        result["saved"], result["remembered"] = saved, remembered
        return result

    def availability_set(self, month_id: str, body: Json) -> Json:
        return self._change(month_id, lambda m: availability.set_person(
            m, self.people, str(body.get("person_id")), list(body.get("unavailable") or []),
            bool(body.get("responded", True))), answer=self.availability)

    def forget_comments(self, month_id: str, _body: Json = None) -> Json:
        """Delete the availability comments of a month everywhere they are kept:
        the month, its backups and its undo history. It cannot be undone, on purpose."""
        month = self.month(month_id)
        count = availability.forget_comments(month)
        for snapshot in self._undo.get(month_id, []) + self._redo.get(month_id, []):
            availability.scrub_comments(snapshot)
        self.store.save_month(month)  # this may back up the old file: scrub after
        self.store.scrub_backups(month_id, availability.scrub_comments)
        transfer.scrub_safety_copies(self.store)
        self._publish()  # the shared folder's copy loses them too, at once
        result = self.availability(month_id)
        result["forgotten"] = count
        return result

    def forget_old_comments(self, today: date) -> List[str]:
        """Once a month is over, its rota no longer needs the comments: delete them."""
        done = []
        existing = set(self.store.list_months())
        for month_id in sorted(existing | set(self.store.backed_up_months())):
            year, number = (int(part) for part in month_id.split("-"))
            if (year, number) >= (today.year, today.month):
                continue
            if month_id in existing and availability.has_comments(self.month(month_id)):
                self.forget_comments(month_id)
                done.append(month_id)
            else:  # the month itself is clean, or gone: its backups may not be
                self.store.scrub_backups(month_id, availability.scrub_comments)
        transfer.scrub_safety_copies(self.store)
        return done

    def availability_comment(self, month_id: str, body: Json) -> Json:
        return self._change(month_id, lambda m: availability.mark_comment(
            m, str(body.get("person_id")), bool(body.get("done"))), answer=self.availability)

    def free_regulars(self, month_id: str, _body: Json) -> Json:
        freed = 0

        def change(month: Month) -> bool:
            nonlocal freed
            freed = availability.free_away_regulars(month, self.people, self.settings)
            return freed > 0

        result = self._change(month_id, change, answer=self.availability)
        result["freed"] = freed
        return result

    def assign(self, month_id: str, body: Json) -> Json:
        around = self._around(month_id)

        def change(month: Month) -> bool:
            reason = body.get("reason")
            try:
                months.assign(
                    month, self.people, self.settings,
                    str(body.get("task_id", "")), str(body.get("date", "")), to_int(body.get("position")),
                    person_id=str(body.get("person_id") or ""), guest=str(body.get("guest") or ""),
                    guest_kind=str(body.get("guest_kind") or ""),
                    force=bool(body.get("force")), reason=str(reason) if reason is not None else None,
                    around=around,
                )
                return True
            except months.RuleViolation as violation:
                raise ApiError(
                    HTTPStatus.CONFLICT, str(violation),
                    findings=[{"severity": f.severity, "message": f.message} for f in violation.findings],
                ) from violation

        return self._change(month_id, change)

    def clear(self, month_id: str, body: Json) -> Json:
        return self._change(
            month_id,
            lambda month: months.clear(
                month, str(body.get("task_id", "")), str(body.get("date", "")), to_int(body.get("position"))
            ),
        )

    def set_note(self, month_id: str, body: Json) -> Json:
        return self._change(month_id, lambda month: months.set_note(
            month, str(body.get("task_id", "")), str(body.get("date", "")), to_int(body.get("position")),
            str(body.get("note") or "")))

    def highlight(self, month_id: str, body: Json) -> Json:
        return self._change(
            month_id,
            lambda month: months.set_wanted(
                month, self.people, self.settings,
                str(body.get("task_id", "")), str(body.get("date", "")), to_int(body.get("position")),
                bool(body.get("on")),
            ),
        )

    def headcount(self, month_id: str, body: Json) -> Json:
        count = body.get("count")
        return self._change(
            month_id,
            lambda month: months.set_headcount(
                month, str(body.get("task_id", "")), str(body.get("date", "")),
                None if count is None else to_int(count),
            ),
        )

    def set_time(self, month_id: str, body: Json) -> Json:
        data = None if body.get("clear") else body
        return self._change(
            month_id,
            lambda month: months.set_time(month, str(body.get("task_id", "")), str(body.get("date", "")), data),
        )

    def undo(self, month_id: str, _body: Json) -> Json:
        return self._step(month_id, self._undo, self._redo)

    def redo(self, month_id: str, _body: Json) -> Json:
        return self._step(month_id, self._redo, self._undo)

    def _step(self, month_id: str, source: Dict[str, List[Json]], target: Dict[str, List[Json]]) -> Json:
        stack = source.get(month_id)
        if not stack:
            raise ApiError(HTTPStatus.CONFLICT, "Nothing to undo or redo.")
        current = self.month(month_id)
        target.setdefault(month_id, []).append(current.to_dict())
        restored = Month.from_dict(stack.pop())
        restored.mailed = dict(current.mailed)  # an e-mail sent cannot be undone: who got one stays known
        self._months[month_id] = restored
        self.store.save_month(restored)
        # Events and closures shared with other months follow the undo there too.
        for kind in month_setup.LINKED_KINDS:
            for item_id in self._linked_differences(current, restored, kind):
                self._spread(month_id, kind, item_id)
        return self.month_view(month_id)

    @staticmethod
    def _linked_differences(before: Month, after: Month, kind: str) -> List[str]:
        """Ids of the events (with their tasks) or closures that differ between two states of a month."""
        def state(month: Month) -> Dict[str, Any]:
            found = {}
            for item in getattr(month, kind):
                tasks = [t.to_dict() for t in month_setup.event_tasks(month, item.id)] if kind == "events" else []
                found[item.id] = (item.to_dict(), tasks)
            return found
        old, new = state(before), state(after)
        return [i for i in set(old) | set(new) if old.get(i) != new.get(i)]

    # -- permanent set-up: people, skills, areas, tasks, regular week ---------

    def setup(self) -> Json:
        """Everything the set-up screens show."""
        return {
            "catalog": self.catalog.to_dict(),
            "event_types": self._event_types_json(),
            "event_days": EVENT_TASK_DAYS,
            "people": [emails.public(p) for p in self.people],
            "settings": self.settings.to_dict(),
            "frequencies": FREQUENCIES,
            "regular_load": editing.regular_load(self.catalog),
            "colours": list(editing.AREA_COLOURS),
            "weekdays": list(WEEKDAY_KEYS),
            "months": self.store.list_months(),
        }

    def _save_setup(self, people: bool = False, catalog: bool = False, settings: bool = False) -> Json:
        if people:
            self.store.save_people(self.people)
        if catalog:
            self.store.save_catalog(self.catalog)
        if settings:
            self.store.save_settings(self.settings)
        return self.setup()

    def save_person(self, body: Json) -> Json:
        editing.save_person(self.people, self.catalog, body.get("person") or {}, salt=self.store.email_salt())
        return self._save_setup(people=True)

    def person_skill(self, body: Json) -> Json:
        editing.set_person_skill(self.people, self.catalog, str(body.get("person_id")),
                                 str(body.get("skill_id")), bool(body.get("on")))
        return self._save_setup(people=True)

    def import_people_preview(self, body: Json) -> Json:
        return people_import.preview(self.people, self.catalog, text=str(body.get("text") or ""),
                                     file_bytes=_decode_file(body), filename=str(body.get("filename") or ""),
                                     salt=self.store.email_salt())

    def import_people_apply(self, body: Json) -> Json:
        rows = list(body.get("rows") or [])
        salt = self.store.email_salt()
        added = people_import.apply(self.people, self.catalog, rows, salt)
        remembered = people_import.remember_emails(self.people, rows, salt)
        result = self._save_setup(people=True)
        result["added"], result["remembered"] = len(added), remembered
        return result

    def save_skill(self, body: Json) -> Json:
        skill = editing.save_skill(self.catalog, body.get("skill") or {})
        for person_id in body.get("people") or []:  # "who can do this?" when creating it
            editing.set_person_skill(self.people, self.catalog, str(person_id), skill.id, True)
        return self._save_setup(people=True, catalog=True)

    def delete_skill(self, body: Json) -> Json:
        months_list = [self.month(m) for m in self.store.list_months()]
        editing.delete_skill(self.catalog, self.people, str(body.get("id")), months_list + list(self.templates))
        return self._save_setup(people=True, catalog=True)

    def set_regular(self, body: Json) -> Json:
        editing.set_regular(self.catalog, self.people, body)
        return self._save_setup(catalog=True)

    def save_settings(self, body: Json) -> Json:
        editing.save_settings(self.settings, body.get("settings") or {})
        return self._save_setup(settings=True)

    def message_preview(self, body: Json) -> Json:
        """What a schedule message looks like with these words (nothing is saved)."""
        greeting = editing.message_words(body.get("message_greeting"), editing.GREETING_LENGTH)
        closing = editing.message_words(body.get("message_closing"), editing.CLOSING_LENGTH)
        subject = editing.message_subject(body.get("message_subject", self.settings.message_subject))
        today = date.today()
        return {"text": personal.sample_message(today, self.settings.centre_name, greeting, closing),
                "subject": personal.subject_text(subject, today.strftime("%B %Y"))}

    # -- task configurations: each month has its own; "typical" starts new ones

    def _target(self, scope: str) -> editing.TaskConfig:
        return self.catalog if scope == TYPICAL else self.month(scope)

    def config(self, scope: str) -> Json:
        """Areas and tasks of a month (or of the typical month), and where else to copy from."""
        scope = scope or TYPICAL
        target = self._target(scope)
        months = self.store.list_months()
        return {
            "scope": scope,
            "title": "Typical month" if scope == TYPICAL else target.title(),
            "areas": [a.to_dict() for a in target.areas],
            "tasks": [t.to_dict() for t in target.tasks if not t.event_id],
            "event_tasks": sum(1 for t in target.tasks if t.event_id),
            "scopes": [{"id": m, "title": self.month(m).title()} for m in reversed(months)]
                      + [{"id": TYPICAL, "title": "Typical month"}],
            "sources": self._sources(exclude=scope),
            "templates": [{"id": t.id, "name": t.name, "tasks": len(t.tasks), "created": t.created}
                          for t in self.templates],
        }

    def _edit_config(self, body: Json, edit: Callable[[editing.TaskConfig], Any]) -> Json:
        """Apply ``edit`` to the configuration named by ``body["scope"]`` and save it.

        A month's configuration is part of the month, so the change can be undone
        from the rota like any other.
        """
        scope = str(body.get("scope") or TYPICAL)
        if scope == TYPICAL:
            edit(self.catalog)
            self.store.save_catalog(self.catalog)
        else:
            self._change(scope, lambda month: edit(month) is not False)
        return self.config(scope)

    def save_area(self, body: Json) -> Json:
        return self._edit_config(body, lambda c: editing.save_area(c, body.get("area") or {}))

    def order_areas(self, body: Json) -> Json:
        return self._edit_config(body, lambda c: editing.reorder(c.areas, [str(i) for i in body.get("ids") or []]))

    def save_task(self, body: Json) -> Json:
        return self._edit_config(body, lambda c: editing.save_task(c, self.catalog, body.get("task") or {}))

    def task_times(self, body: Json) -> Json:
        """For the task form, as the times are typed: how long that is, and which
        parts of the day it covers (nothing is saved). Without both times: nothing to say."""
        start, end = str(body.get("start") or ""), str(body.get("end") or "")
        hours = editing.duration_hours(start, end)
        return {"hours": hours, "periods": editing.periods_for_times(start, end) if hours is not None else None}

    def duplicate_task(self, body: Json) -> Json:
        return self._edit_config(body, lambda c: editing.duplicate_task(c, str(body.get("id"))))

    def order_tasks(self, body: Json) -> Json:
        return self._edit_config(body, lambda c: editing.reorder(c.tasks, [str(i) for i in body.get("ids") or []]))

    def use_config(self, body: Json) -> Json:
        """Give a month the configuration of another month, a template or the typical month."""
        scope, source = str(body.get("scope") or ""), str(body.get("source") or "")
        if scope == TYPICAL:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Choose a month to change.")
        if source == f"month:{scope}":
            raise ApiError(HTTPStatus.BAD_REQUEST, "Choose another month.")
        origin = self._source(source)
        removed = 0

        def change(month: Month) -> None:
            nonlocal removed
            removed = editing.use_configuration(month, origin)

        result = self._edit_config(body, change)
        result["removed"] = removed
        return result

    def save_template(self, body: Json) -> Json:
        scope = str(body.get("scope") or TYPICAL)
        name = " ".join(str(body.get("name") or "").split())[:80]
        if not name:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Give the template a name.")
        source = self._target(scope)
        self.templates.append(ConfigTemplate(
            id=new_id("template"), name=name, areas=copy.deepcopy(source.areas),
            tasks=[copy.deepcopy(t) for t in source.tasks if not t.event_id], created=date.today().isoformat(),
        ))
        self.store.save_templates(self.templates)
        return self.config(scope)

    def delete_template(self, body: Json) -> Json:
        self.templates = [t for t in self.templates if t.id != body.get("id")]
        self.store.save_templates(self.templates)
        return self.config(str(body.get("scope") or TYPICAL))

    # -- printing and exporting ---------------------------------------------

    def _document(self, month_id: str, kind: str, query: Dict[str, str]) -> documents.Document:
        """One of the printed documents of a month, with the options chosen, its
        header saying which version it follows (or that it is a draft)."""
        document = self._document_body(month_id, kind, query)
        month = self.month(month_id)
        has_changes = bool(month.versions) and bool(versions.changes(month, self.people, self.settings))
        document.stamp = versions.stamp(month, date.today(), has_changes)
        return document

    def _document_body(self, month_id: str, kind: str, query: Dict[str, str]) -> documents.Document:
        month = self.month(month_id)
        times = query.get("times", "1") != "0"
        if kind == "event":
            event = next((e for e in month.events if e.id == query.get("event")), None)
            if event is None:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Choose an event.")
            existing = set(self.store.list_months())
            parts, missing = [], []
            for other_id in month_setup.months_spanned(event.start, event.end):
                if other_id not in existing:
                    missing.append(f"{calendar.month_name[int(other_id[5:])]} {other_id[:4]}")
                    continue
                other = self.month(other_id)
                parts.append((sheet.build_sheet(other, self.people, self.settings),
                              [d for d in event.dates() if d.startswith(other_id)]))
            return documents.event_rota(event.name, event.start, event.end, parts, missing,
                                        one_day_per_page=query.get("per_page", "1") != "0", show_times=times)
        if kind == "people":
            chosen = [query["person"]] if query.get("person") else None
            return personal.personal_rotas(month, self.people, personal.shifts_of(month, self.people, self.settings),
                                           chosen, page_each=query.get("per_page") == "1")
        printed = sheet.build_sheet(month, self.people, self.settings)
        if kind == "month":
            return documents.monthly(printed, self._print_template(query))
        if kind == "weekly":
            areas = [a for a in query.get("areas", "").split(",") if a]
            return documents.weekly(printed, area_ids=areas or None, show_times=times)
        if kind == "open":
            return documents.open_shifts(printed, only_wanted=query.get("wanted") == "1")
        raise ApiError(HTTPStatus.NOT_FOUND, "Unknown document.")

    def _print_template(self, query: Dict[str, str]) -> PrintTemplate:
        """The template named in the address, else one made of the options given."""
        chosen = next((t for t in self.settings.print_templates if t.id == query.get("template")), None)
        if chosen is not None:
            return chosen
        size = {"a4": "a4x2"}.get(query.get("paper", "a3"), query.get("paper", "a3"))
        return PrintTemplate(id="", name="Rota", size=size if size in PRINT_SIZES else "a3",
                             show_times=query.get("times", "1") != "0")

    def print_page(self, month_id: str, kind: str, query: Dict[str, str]) -> str:
        """A document as an HTML page for the browser (preview and Print)."""
        return documents.page(self._document(month_id, kind, query), standalone=False)

    def exports_folder(self, month_id: str = "") -> Path:
        folder = self.store.root / "exports"
        return folder / month_id if month_id else folder

    def export_pdf(self, month_id: str, body: Json) -> Json:
        """Save a document in the exports folder: the Excel file, everyone's
        calendar files, or a printed document as PDF. Runs without the lock held
        while a browser makes a PDF, so the program stays responsive."""
        query = {str(k): str(v) for k, v in (body.get("options") or {}).items()}
        if body.get("doc") in ("xlsx", "calendars"):
            with self.lock:
                return self._export_file(month_id, str(body["doc"]))
        with self.lock:
            document = self._document(month_id, str(body.get("doc") or ""), query)
            html = documents.page(document, standalone=True)
            target = self.exports_folder(month_id) / f"{_safe_filename(document.filename)}.pdf"
        browser = pdf.find_browser()
        if browser is None:
            return {"saved": False, "reason": "no_browser"}
        try:
            pdf.html_to_pdf(html, target, browser)
        except pdf.PdfError as error:
            raise ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, str(error)) from error
        return {"saved": True, "name": target.name, "folder": str(target.parent)}

    def _export_file(self, month_id: str, kind: str) -> Json:
        month = self.month(month_id)
        folder = self.exports_folder(month_id)
        folder.mkdir(parents=True, exist_ok=True)
        if kind == "xlsx":
            printed = sheet.build_sheet(month, self.people, self.settings)
            has_changes = bool(month.versions) and bool(versions.changes(month, self.people, self.settings))
            stamp = versions.stamp(month, date.today(), has_changes)
            target = folder / f"Rota {month.title()}.xlsx"
            target.write_bytes(xlsx_rota.workbook(printed, stamp))
            return {"saved": True, "name": target.name, "folder": str(folder)}
        calendars = folder / "calendars"
        calendars.mkdir(exist_ok=True)
        shifts = personal.shifts_of(month, self.people, self.settings)
        count = 0
        for person in self.people:
            if shifts.get(person.id):
                text = personal.calendar(month, person, shifts[person.id], self.settings.centre_name)
                (calendars / f"{_safe_filename(person.name)}.ics").write_text(text, encoding="utf-8", newline="")
                count += 1
        return {"saved": True, "name": f"{count} calendar files", "folder": str(calendars)}

    def personal(self, month_id: str, query: Dict[str, str]) -> Json:
        """One person's message and calendar file, for the Publish screen."""
        month = self.month(month_id)
        person = next((p for p in self.people if p.id == query.get("person")), None)
        if person is None:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Choose a person.")
        mine = personal.shifts_of(month, self.people, self.settings).get(person.id, [])
        return {
            "name": person.name, "shifts": len(mine),
            "message": self._message(month, person, mine),
            "calendar_name": f"{_safe_filename(person.name)} - rota {month.title()}.ics",
            "calendar": personal.calendar(month, person, mine, self.settings.centre_name),
        }

    def _message(self, month: Month, person: Any, shifts: List[Any]) -> str:
        s = self.settings
        return personal.message(month, person, shifts, s.centre_name, s.message_greeting, s.message_closing)

    def messages(self, month_id: str) -> Json:
        """Everyone's schedule message at once, so picking a person is instant."""
        month = self.month(month_id)
        shifts = personal.shifts_of(month, self.people, self.settings)
        people = [p for p in self.people if not p.archived and (p.active or shifts.get(p.id))]
        return {"people": [{
            "id": p.id, "name": p.name, "full": f"{p.first_name} {p.last_name}".strip() or p.name,
            "shifts": len(shifts.get(p.id, [])), "hours": round(sum(s.hours for s in shifts.get(p.id, [])), 2),
            "message": self._message(month, p, shifts.get(p.id, [])),
        } for p in sorted(people, key=lambda p: p.name.lower())]}

    # -- e-mailing the schedules (optional, mail.py) -------------------------------

    def mail_info(self) -> Json:
        return self.mail.describe()

    def mail_save(self, body: Json) -> Json:
        try:
            self.mail.save(body.get("account") or {})
        except mail.MailError as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(error)) from None
        return self.mail.describe()

    def mail_remove(self, _body: Json) -> Json:
        try:
            self.mail.remove()
        except mail.MailError as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(error)) from None
        return self.mail.describe()

    def mail_test(self, _body: Json) -> Json:
        """Connects to the mail server: runs without the app lock."""
        try:
            sent_to = self.mail.test()
        except mail.MailError as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(error)) from None
        return {**self.mail.describe(), "sent_to": sent_to}

    def mail_status(self) -> Json:
        return self.mail.status()

    def mail_stop(self, _body: Json) -> Json:
        self.mail.stop()
        return self.mail.status()

    def _mail_list(self, body: Json) -> List[mail.Entry]:
        try:
            entries = mail.read_list(str(body.get("text") or ""), _decode_file(body), str(body.get("filename") or ""))
        except ValueError as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(error)) from None
        if not entries:
            raise ApiError(HTTPStatus.BAD_REQUEST, "No e-mail address found in this list.")
        return entries

    def _mail_version(self, month: Month) -> int:
        reference = versions.reference(month)
        return reference.number if reference else 0

    def mail_preview(self, month_id: str, body: Json) -> Json:
        """Who each address of the list is, and who there is to write to. Nothing is sent."""
        if not self.mail.configured:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Sending by e-mail is not set up (Settings › Schedules by e-mail).")
        month = self.month(month_id)
        shifts = personal.shifts_of(month, self.people, self.settings)
        result = mail.preview(self._mail_list(body), self.people, shifts, self.store.email_salt(),
                              month.mailed, self._mail_version(month))
        result["phase"] = versions.phase(month, self.people, self.settings)
        return result

    def _mail_rows(self, body: Json) -> List[Json]:
        """The rows confirmed on the screen ({row, person_id, remember}), first one per person."""
        rows, seen = [], set()
        for row in list(body.get("rows") or [])[:mail.MAX_ROWS]:
            if isinstance(row, dict) and isinstance(row.get("row"), int) and row.get("person_id") and row["person_id"] not in seen:
                seen.add(row["person_id"])
                rows.append(row)
        return rows

    def mail_plan(self, month_id: str, body: Json) -> Json:
        """For the people chosen in the list: how many messages, and who is missing. No address needed."""
        month = self.month(month_id)
        who = body.get("who") if body.get("who") in mail.WHO else "all"
        shifts = personal.shifts_of(month, self.people, self.settings)
        return mail.plan([str(r["person_id"]) for r in self._mail_rows(body)], self.people, shifts, month.mailed,
                         self._mail_version(month), who)

    def mail_send(self, month_id: str, body: Json) -> Json:
        """Start sending to the people confirmed on the screen. Addresses are read
        again from the list, used for the sending only, and never saved."""
        if not self.mail.configured:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Sending by e-mail is not set up (Settings › Schedules by e-mail).")
        who = body.get("who") if body.get("who") in mail.WHO else "all"
        month = self.month(month_id)
        version = self._mail_version(month)
        entries = {entry.row: entry for entry in self._mail_list(body)}
        shifts = personal.shifts_of(month, self.people, self.settings)
        by_id = {p.id: p for p in self.people if not p.archived}
        rows = [row for row in self._mail_rows(body) if row["row"] in entries and row["person_id"] in by_id]
        send_to = set(mail.plan([row["person_id"] for row in rows], self.people, shifts, month.mailed, version, who)["send_to"])
        subject = personal.subject_text(self.settings.message_subject, month.title())
        outgoing = []
        for row in rows:
            person, entry = by_id[row["person_id"]], entries[row["row"]]
            if person.id not in send_to:
                continue
            mine = shifts.get(person.id, [])
            outgoing.append(mail.Outgoing(
                person_id=person.id, name=f"{person.first_name} {person.last_name}".strip() or person.name,
                address=entry.address, subject=subject, text=self._message(month, person, mine),
                calendar_name=f"{_safe_filename(person.name)} - rota {month.title()}.ics",
                calendar=personal.calendar(month, person, mine, self.settings.centre_name)))

        def record(person_id: str) -> None:
            """This person was reached, for this version (no address). Saved at once; not an undo step."""
            with self.lock:
                current = self.month(month_id)
                current.mailed[person_id] = version
                self.store.save_month(current)

        try:
            self.mail.send(outgoing, record)
        except mail.MailError as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(error)) from None
        # Only now that the sending has started: remember the addresses the coordinator ticked.
        salt = self.store.email_salt()
        remembered = sum(emails.remember(self.people, row["person_id"], emails.fingerprint(entries[row["row"]].address, salt), salt)
                         for row in rows if row.get("remember"))
        if remembered:
            self.store.save_people(self.people)
        return {**self.mail.status(), "remembered": remembered}

    # -- versions --------------------------------------------------------------

    def versions_info(self, month_id: str) -> Json:
        month = self.month(month_id)
        found = versions.changes(month, self.people, self.settings)
        return {
            "versions": [{"number": v.number, "published": v.published, "audience": v.audience} for v in month.versions],
            "changes": found,
            "comments": sum(1 for r in month.availability.values() if r.comment),
            "phase": versions.phase(month, self.people, self.settings, found),
        }

    def publish_version(self, month_id: str, body: Json) -> Json:
        """A new version: for the manager to check, or published for the residents. Once
        published the rota is done, so the comments can go at the same time."""
        audience = str(body.get("audience") or versions.RESIDENTS)
        if audience not in versions.AUDIENCES:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Unknown choice.")
        self._change(month_id, lambda m: versions.publish(m, self.people, self.settings, audience=audience) is not None,
                     answer=lambda _: {})
        if body.get("forget_comments"):
            self.forget_comments(month_id)
        return self.versions_info(month_id)

    def exports_info(self, _body: Json = None) -> Json:
        """The print templates, and whether PDFs can be saved directly (else: the browser's Print dialog)."""
        return {
            "pdf": pdf.find_browser() is not None,
            "templates": [t.to_dict() for t in self.settings.print_templates],
            "sizes": PRINT_SIZES,
            "event_tasks": EVENT_TASKS_PRINTED,
            "layouts": {size: {output: documents.poster_layout(size, output) for output in ("a3_sheets", "one_page")}
                        for size in PRINT_SIZES},
        }

    def save_print_template(self, body: Json) -> Json:
        editing.save_print_template(self.settings, self.catalog, body.get("template") or {})
        self.store.save_settings(self.settings)
        return self.exports_info()

    def delete_print_template(self, body: Json) -> Json:
        editing.delete_print_template(self.settings, str(body.get("id")))
        self.store.save_settings(self.settings)
        return self.exports_info()

    def duplicate_print_template(self, body: Json) -> Json:
        editing.duplicate_print_template(self.settings, str(body.get("id")))
        self.store.save_settings(self.settings)
        return self.exports_info()

    def open_exports(self, month_id: str, _body: Json) -> Json:
        pdf.open_folder(self.exports_folder(month_id))
        return {"opened": True}

    # -- moving to another computer -----------------------------------------

    def export_data(self, body: Json) -> Json:
        """Everything in one file, handed to the browser to save (on a USB stick…)."""
        data = transfer.export_bytes(self.store, include_comments=bool(body.get("include_comments")))
        return {"name": transfer.export_name(), "file": base64.b64encode(data).decode("ascii")}

    def import_preview(self, body: Json) -> Json:
        try:
            return transfer.summary(self.store, _decode_file(body))
        except transfer.TransferError as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(error)) from error

    def import_apply(self, body: Json) -> Json:
        """Replace all the data with the file's; the current data is kept in a backup first."""
        try:
            safety = transfer.replace_data(self.store, _decode_file(body))
        except transfer.TransferError as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(error)) from error
        self.catalog, self.people = self.store.load_catalog(), self.store.load_people()
        self.settings, self.templates = self.store.load_settings(), self.store.load_templates()
        self._months, self._undo, self._redo, self._proposals, self._suggested = {}, {}, {}, {}, {}
        self.forget_old_comments(date.today())  # same rule as when the program starts
        return {"safety": safety, "bootstrap": self.bootstrap()}

    # -- the other computers (sync.py) ------------------------------------------
    #
    # Lock order, always: this app's lock, then the synchronisation's. Nothing
    # here holds the app's lock while the meeting place is talked to (a server
    # may be slow): only what touches the app's own state takes it.

    def _publish(self) -> None:
        """Something changed here: have it sent (at once to a folder, soon to a server)."""
        try:
            if self.sync.local:
                self.sync.publish()
            else:
                self.sync.touch()
        except Exception:  # noqa: BLE001 — the meeting place must never stop the program
            pass

    def _after_taking(self, result: Json) -> None:
        """Other computers' versions were written to the data folder: load them."""
        if not result["replaced"]:
            return
        self.catalog, self.people = self.store.load_catalog(), self.store.load_people()
        self.settings, self.templates = self.store.load_settings(), self.store.load_templates()
        for month_id in result["replaced_months"]:
            # Undo would bring back what the other computer's version replaced: start afresh.
            for kept in (self._months, self._undo, self._redo, self._proposals, self._suggested):
                kept.pop(month_id, None)
        if any(not unit.startswith("months/") for unit in result["replaced"]):
            self._proposals, self._suggested = {}, {}  # suggestions were made for other people or tasks

    def sync_auto(self) -> None:
        """Take what needs no choice (called by the courier when the others' files changed)."""
        with self.lock:
            self._after_taking(self.sync.apply_automatic())

    def sync_status(self) -> Json:
        self.sync.watched()  # a page is open and asking: keep what it shows fresh
        status = self.sync.status()
        if self.sync.deliver():
            status = self.sync.status()
        return status

    def sync_preview(self, _body: Json) -> Json:
        return self.sync.preview()

    def sync_apply(self, body: Json) -> Json:
        """Take what the others have newer (and what was chosen), then reload."""
        choices = {str(k): str(v) for k, v in (body.get("choices") or {}).items()}
        result = self.sync.apply(str(body.get("plan") or ""), choices)
        self._after_taking(result)
        result["bootstrap"] = self.bootstrap()
        return result

    def sync_choose(self, body: Json) -> Json:
        """Synchronise from the screen. Choosing between versions changes the shared
        data, so it needs the pen like any other change; taking what is simply newer does not."""
        if body.get("choices"):
            try:
                self.sync.ensure_pen()
            except PenError as error:
                raise ApiError(HTTPStatus.LOCKED, str(error), kind=error.kind, pen=error.info) from None
        with self.lock:
            return self.sync_apply(body)

    def sync_test(self, body: Json) -> Json:
        """"Test the connection": can this computer use that meeting place?"""
        return {"ok": True, "place": self.sync.test_place(body.get("place"))}

    def sync_setup(self, body: Json) -> Json:
        """Start (make a pairing code), join (type it), rename this computer, or meet elsewhere."""
        action = body.get("action")
        place = body.get("place")
        code = ""
        if action == "start":
            code = self.sync.start(str(body.get("name") or ""), place)
        elif action == "join":
            if not self.store.is_empty() and not body.get("confirmed"):
                raise ApiError(HTTPStatus.CONFLICT, "This computer already has data. Joining keeps it: what differs "
                               "from the others' will be asked, one by one. Confirm to go on.", confirm=True)
            self.sync.join(str(body.get("name") or ""), place, str(body.get("code") or ""))
        elif action == "rename":
            self.sync.rename(str(body.get("name") or ""))
        elif action == "place":
            self.sync.change_place(place)
        else:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Unknown choice.")
        self.sync.deliver()  # a computer that joins takes everything at once
        return {**self.sync.status(), "code": code, "bootstrap": self.bootstrap_locked()}

    def bootstrap_locked(self) -> Json:
        with self.lock:
            return self.bootstrap()

    def sync_check(self, _body: Json) -> Json:
        """"Check now"."""
        return self.sync.check_now()

    def sync_options(self, body: Json) -> Json:
        if "auto" in body:
            self.sync.set_auto(bool(body["auto"]))
        if body.get("resume"):
            pen = self.sync.pen()
            if body["resume"] == "mine" and pen["read_only"]:
                # This computer's data would become the newest everywhere: not while another computer edits.
                # (Not ensure_pen: taking the pen first takes what is newer, the very data not chosen.)
                raise ApiError(HTTPStatus.LOCKED, pen["text"], kind="held", pen=pen)
            self.sync.resume(str(body["resume"]))
        self.sync.deliver()
        return self.sync.status()

    def sync_forget(self, body: Json) -> Json:
        self.sync.forget(str(body.get("computer") or ""))
        return self.sync.status()

    def sync_pen(self, body: Json) -> Json:
        """Take the pen (also from a computer that went away), ask the one editing to hand
        it over, give it back (or stop asking), or work offline by choice."""
        action = body.get("action")
        try:
            if action == "take":
                self.sync.take_pen(force=bool(body.get("force")))
            elif action == "ask":
                self.sync.ask_pen()
            elif action in ("release", "cancel"):
                self.sync.release_pen()
            elif action == "offline":
                self.sync.work_offline(bool(body.get("on", True)))
            else:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Unknown choice.")
        except PenError as error:  # the screen offers what can be done instead (take over, work offline)
            raise ApiError(HTTPStatus.LOCKED, str(error), kind=error.kind, pen=error.info) from None
        return self.sync.status()

    def sync_code(self, _body: Json) -> Json:
        return {"code": self.sync.code()}

    def sync_stop(self, _body: Json) -> Json:
        removed = self.sync.stop()
        return {**self.sync.status(), "removed": removed}

    # -- a newer version of the program (updates.py) ----------------------------

    def update_info(self) -> Json:
        return self.updates.info()

    def update_check(self, _body: Json) -> Json:
        """"Check now" (asks the program's repository; never under the app lock)."""
        return self.updates.check()

    def update_options(self, body: Json) -> Json:
        return self.updates.set_auto(bool(body.get("auto")))

    def update_install(self, body: Json) -> Json:
        """Put the new version in place (the one the screen listed), then the program
        starts again. The app lock is held only while the data is copied, never while
        the repository is asked or the new version is tried."""
        try:
            return self.updates.install(expected=str(body.get("target") or ""), data_lock=self.lock)
        except UpdateError as error:
            raise ApiError(HTTPStatus.CONFLICT, str(error)) from None


def _decode_file(body: Json) -> bytes:
    """A file sent by the browser as base64 text."""
    encoded = body.get("file") or ""
    if not encoded:
        return b""
    if len(encoded) > MAX_UPLOAD_BYTES * 4 // 3 + 4:
        raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "This file is too large.")
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ApiError(HTTPStatus.BAD_REQUEST, "The file could not be read.") from None


def month_in_preparation(ids: List[str], today: date) -> Optional[str]:
    """The month to open first: the rota is prepared around the 10th for the
    next month, so next month if it exists, else this month, else the latest."""
    following = date(today.year + today.month // 12, today.month % 12 + 1, 1)
    for candidate in (Month.make_id(following.year, following.month), Month.make_id(today.year, today.month)):
        if candidate in ids:
            return candidate
    return ids[-1] if ids else None


def _safe_filename(name: str) -> str:
    """A file name that works on every system ("Rota December 2026")."""
    return re.sub(r'[\\/:*?"<>|]+', "-", name).strip(" .") or "rota"


def to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ApiError(HTTPStatus.BAD_REQUEST, "A whole number was expected.") from None
