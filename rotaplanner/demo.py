"""Demonstration and test data: an invented centre, invented people.

A made-up guest house with its areas, tasks, times and events, big enough to
show every screen at work (about ninety people, forty-odd tasks in nine
areas, a few events). Names are drawn at random with a fixed seed, so the
data is the same on every run. Nothing here describes a real place or person.
"""

from __future__ import annotations

import random
from typing import Dict, List, Optional, Tuple

from . import editing, month_setup
from . import eligibility as rules
from .model import (
    WEEKDAY_KEYS,
    Area,
    Assignment,
    Catalog,
    DayType,
    Event,
    EventType,
    Marker,
    Month,
    NeverRule,
    Person,
    PersonAvailability,
    PrintTemplate,
    Regular,
    Settings,
    Skill,
    Task,
    availability_key,
    month_dates,
    slot_key,
)
from .months import apply_regulars, create_month, set_headcount
from .schedule import Schedule
from .store import Store

SEED = 2026
DEMO_YEAR, DEMO_MONTH = 2026, 12

# Skill name -> how many of the 94 invented people have it.
SKILLS: Dict[str, int] = {
    "Lunch cook": 13,
    "Supper cook": 15,
    "Kitchen helper": 52,
    "Dining room": 70,
    "Breakfast set-up": 58,
    "Refreshments": 65,
    "Cafe shift": 45,
    "Shop shift": 29,
    "Visitor hosting": 30,
    "Front desk": 16,
    "Front desk – evening class": 16,
    "Steward": 92,
    "Event registration": 21,
    "Housekeeping": 57,
    "Night duty": 21,
    "Closing round": 21,
}
POPULATION = 94
ACTIVE = 81
NON_RESIDENTS = 10

AREAS: List[Tuple[str, str, str]] = [
    ("kitchen", "Kitchen", "orange"),
    ("dining", "Dining room", "amber"),
    ("refreshment", "Refreshments", "pink"),
    ("cafe", "Cafe", "teal"),
    ("shop", "Shop", "violet"),
    ("hosting", "Visitor hosting", "indigo"),
    ("desk", "Front desk", "sky"),
    ("housekeeping", "Housekeeping", "green"),
    ("night", "Night duty", "slate"),
]

WEEKEND = {"sat": 1, "sun": 1}
EVERY_DAY = {"default": 1}

# (area, name, skill, period, start, end, label, hours, headcount, priority)
TASKS: List[tuple] = [
    ("kitchen", "Lunch cook", "Lunch cook", "morning", "09:30", "13:30",
     "10:30–14:30 at weekends", 4, EVERY_DAY, "critical"),
    ("kitchen", "Lunch kitchen helper", "Kitchen helper", "morning", "09:45", "13:15",
     "", 3.5, {"sat": 2, "sun": 2, "event": 2}, "normal"),
    ("kitchen", "Lunch washing-up", "Kitchen helper", "afternoon", "14:15", "16:15",
     "", 2, {"event": 1}, "optional"),
    ("kitchen", "Supper cook", "Supper cook", "afternoon", "15:45", "18:45",
     "15:15–18:45 during events", 3, EVERY_DAY, "critical"),
    ("kitchen", "Supper kitchen helper", "Kitchen helper", "afternoon", "15:45", "18:45",
     "", 3, EVERY_DAY, "normal"),
    ("kitchen", "Supper washing-up", "Kitchen helper", "evening", "18:45", "20:15",
     "", 1.5, {"event": 1}, "optional"),
    ("dining", "Breakfast attendant", "Dining room", "morning", "06:45", "08:45",
     "", 2, {"event": 1}, "normal"),
    ("dining", "After breakfast: clear & clean", "Dining room", "morning", "08:45", "10:15",
     "", 1.5, {"sat": 1, "sun": 1, "event": 1}, "normal"),
    ("dining", "Morning break", "Refreshments", "morning", "", "",
     "See the event timetable", 1.5, {"event": 2}, "normal"),
    ("dining", "Lay the tables", "Dining room", "morning", "11:45", "12:45",
     "", 1, {"event": 1}, "optional"),
    ("dining", "Serving", "Dining room", "morning", "12:45", "13:15",
     "", 0.5, {"event": 1}, "optional"),
    ("dining", "After lunch: clear & clean", "Dining room", "afternoon", "14:15", "15:45",
     "", 1.5, {"sat": 1, "sun": 1, "event": 1}, "normal"),
    ("dining", "After supper: clear & clean", "Dining room", "evening", "18:45", "19:45",
     "", 1, {"default": 1, "event": 2}, "normal"),
    ("dining", "Breakfast set-up", "Breakfast set-up", "evening", "20:15", "21:15",
     "", 1, EVERY_DAY, "normal"),
    ("refreshment", "Welcome drinks", "Refreshments", "morning", "08:30", "09:45",
     "", 1.25, {}, "optional"),
    ("refreshment", "Evening drinks", "Refreshments", "evening", "", "",
     "See the event timetable", 2, {"event": 1}, "optional"),
    ("cafe", "Cafe morning shift", "Cafe shift", "morning", "10:30", "13:30",
     "", 3, {"sun": 7}, "normal"),
    ("cafe", "Cafe lunch shift", "Cafe shift", "afternoon", "12:15", "13:45",
     "", 1.5, {"wed": 1, "thu": 1, "fri": 1, "sat": 1, "sun": 2}, "normal"),
    ("cafe", "Cafe afternoon shift", "Cafe shift", "afternoon", "14:00", "17:00",
     "", 3, {"sun": 7}, "normal"),
    ("shop", "Shop morning shift", "Shop shift", "morning", "10:15", "13:30",
     "", 3.25, EVERY_DAY, "normal"),
    ("shop", "Shop afternoon shift", "Shop shift", "afternoon", "14:00", "17:15",
     "", 3.25, EVERY_DAY, "normal"),
    ("hosting", "Visitor hosting – morning", "Visitor hosting", "morning", "10:30", "13:30",
     "", 3, EVERY_DAY, "normal"),
    ("hosting", "Visitor hosting – afternoon", "Visitor hosting", "afternoon", "14:00", "17:00",
     "", 3, EVERY_DAY, "normal"),
    ("desk", "Front desk – morning", "Front desk", "morning", "10:30", "13:30",
     "From 9:00 on market days", 3, EVERY_DAY, "critical"),
    ("desk", "Front desk – afternoon", "Front desk", "afternoon", "14:00", "17:00",
     "", 3, EVERY_DAY, "critical"),
    ("desk", "Front desk – evening", "Front desk", "evening", "17:15", "19:15",
     "", 2, {"event": 1}, "normal"),
    ("desk", "Stewards – early afternoon", "Steward", "afternoon", "14:00", "15:30",
     "", 1.5, {"sun": 1, "event": 1}, "normal"),
    ("desk", "Stewards – late afternoon", "Steward", "afternoon", "15:30", "17:00",
     "", 1.5, {"sun": 1, "event": 1}, "normal"),
    ("desk", "Stewards – evening", "Steward", "evening", "17:15", "19:15",
     "", 2, {"event": 1}, "optional"),
    ("desk", "Meet and greet", "Steward", "morning", "08:30", "09:30",
     "", 1, {"wed": 1}, "normal"),
    ("desk", "Front desk – evening class", "Front desk – evening class", "evening", "19:15", "21:15",
     "", 2, {"tue": 2, "wed": 1}, "normal"),
    ("desk", "Event registration", "Event registration", "evening", "", "",
     "See the event timetable", 2, {"event": 1}, "normal"),
    ("housekeeping", "Accessible toilet", "Housekeeping", "anytime", "", "",
     "", 0.5, EVERY_DAY, "normal"),
    ("housekeeping", "Front entrance & hall", "Housekeeping", "morning", "", "",
     "Done by 9 am", 1, WEEKEND, "normal"),
    ("housekeeping", "Public toilets", "Housekeeping", "morning", "", "",
     "Done by 9 am", 1.5, WEEKEND, "normal"),
    ("housekeeping", "Check the public toilets 2 pm", "Housekeeping", "afternoon", "14:00", "14:15",
     "", 0.25, WEEKEND, "normal"),
    ("housekeeping", "Check the public toilets 5 pm", "Housekeeping", "afternoon", "17:00", "17:15",
     "", 0.25, WEEKEND, "normal"),
    ("housekeeping", "Vacuum the corridor", "Housekeeping", "morning", "", "",
     "Done by 9 am", 0.5, WEEKEND, "optional"),
    ("housekeeping", "Vacuum the stairs", "Housekeeping", "morning", "", "",
     "Done by 9 am", 1, WEEKEND, "optional"),
    ("housekeeping", "Garden room", "Housekeeping", "morning", "", "",
     "See the event timetable", 1.5, {"sun": 1}, "optional"),
    ("housekeeping", "Bathroom checks", "Housekeeping", "anytime", "", "",
     "", 1.5, WEEKEND, "optional"),
    ("night", "Night duty", "Night duty", "evening", "21:30", "07:30",
     "", 0, EVERY_DAY, "critical"),
    ("night", "Closing round", "Closing round", "evening", "21:30", "22:00",
     "", 0.5, EVERY_DAY, "critical"),
]

# Tasks that usually have a regular person on given weekdays.
REGULAR_TASKS = (
    "Lunch cook", "Supper cook", "Supper kitchen helper",
    "After supper: clear & clean", "Breakfast set-up", "Shop morning shift",
    "Shop afternoon shift", "Visitor hosting – morning", "Visitor hosting – afternoon",
    "Front desk – morning", "Front desk – afternoon", "Front desk – evening class",
    "Accessible toilet", "Night duty", "Closing round", "Cafe lunch shift",
)  # fmt: skip
REGULAR_POOL_SIZE = 3
REGULAR_CHANCE = 0.7

FIRST_NAMES = (
    "Ada", "Alma", "Amara", "Anika", "Arlo", "Ayla", "Basil", "Bea", "Bodhi", "Bruno", "Caleb",
    "Celine", "Clara", "Dara", "Darius", "Delia", "Eamon", "Edith", "Elio", "Elsa", "Emil",
    "Enzo", "Esme", "Ezra", "Farah", "Felix", "Fenna", "Flora", "Gael", "Greta", "Hana",
    "Hugo", "Ida", "Ilse", "Imran", "Ines", "Iris", "Ivo", "Jana", "Jonah", "Juno", "Kai",
    "Kian", "Kira", "Lars", "Leila", "Lena", "Leon", "Lior", "Livia", "Luca", "Lumi", "Mael",
    "Mara", "Marek", "Maya", "Milo", "Mina", "Nadia", "Nia", "Niko", "Noor", "Nora", "Odile",
    "Olek", "Omar", "Oona", "Orla", "Otto", "Pavel", "Pia", "Quinn", "Rafa", "Rana", "Remy",
    "Rhea", "Rina", "Rohan", "Rosa", "Sacha", "Saoirse", "Selma", "Silas", "Sven", "Tali",
    "Tara", "Teo", "Thea", "Theo", "Uma", "Vera", "Viggo", "Wren", "Yara", "Yusuf", "Zane",
    "Zara", "Zelda", "Zoe", "Anouk", "Bram", "Cosima",
)  # fmt: skip
LAST_NAMES = (
    "Abbott", "Alvarez", "Arden", "Bakker", "Barros", "Becker", "Bianchi", "Blake", "Brandt",
    "Carver", "Castillo", "Costa", "Dahl", "Delmar", "Dumont", "Ekberg", "Ellison", "Farrow",
    "Ferreira", "Fischer", "Garnier", "Greco", "Hale", "Hartmann", "Holm", "Ibarra", "Jansen",
    "Kaur", "Keller", "Kowal", "Laine", "Larsen", "Lemaire", "Lindqvist", "Lowe", "Marin",
    "Moretti", "Nakamura", "Nolan", "Novak", "Okafor", "Olsen", "Ortega", "Pace", "Petrov",
    "Quint", "Rahman", "Reyes", "Rossi", "Sato", "Silva", "Sorensen", "Stone", "Tanaka",
    "Thorne", "Ueda", "Valdez", "Vance", "Varga", "Weber", "Wilde", "Young", "Zeller",
)  # fmt: skip

COMMENTS = (
    "Away visiting family for a few days, thank you!",
    "I can cook lunch on Monday 7th if needed.",
    "Happy to do extra cafe shifts this month.",
    "Please no evening tasks this month, I have an evening class.",
    "Back from holiday on the 10th.",
    "I would love to do some visitor hosting again.",
    "Thursday works well for the dining room and vacuuming.",
    "Unavailable from the 20th to the end of the month.",
    "Could I avoid the night duty this month please?",
    "Thank you!",
)  # fmt: skip

NOTES = (
    "Prefers morning tasks.",
    "Learning to cook lunch.",
    "No ladders, please.",
    "Drives in from town; not before 9 am.",
)  # fmt: skip


def _slug(text: str) -> str:
    words = "".join(ch if ch.isalnum() else " " for ch in text.lower()).split()
    return "-".join(words)


def build_catalog() -> Catalog:
    skills = [Skill(id=f"skill-{_slug(name)}", name=name) for name in SKILLS]
    skill_ids = {skill.name: skill.id for skill in skills}
    areas = [Area(id=f"area-{key}", name=name, color=color) for key, name, color in AREAS]
    tasks = [
        Task(
            id=f"task-{_slug(name)}",
            area_id=f"area-{area}",
            name=name,
            skill_id=skill_ids[skill],
            periods=[period],
            start=start,
            end=end,
            time_label=label,
            hours=hours,
            headcount=dict(headcount),
            priority=priority,
        )
        for area, name, skill, period, start, end, label, hours, headcount, priority in TASKS
    ]
    return Catalog(
        skills=skills,
        areas=areas,
        tasks=tasks,
        day_types=[DayType(id="event", name="Event day")],
        event_types=EVENT_TYPES,
    )


def _event_task(set_id: str, area: str, name: str, start: str, end: str, people: int, days: str) -> Task:
    return Task(id=f"{set_id}-{_slug(name)}", area_id=f"area-{area}", name=name, start=start, end=end,
                periods=editing.periods_for_times(start, end), hours=editing.duration_hours(start, end) or 0.0,
                headcount={"default": people}, event_days=days)


# Kinds of event, with the tasks they need on top of the usual ones.
EVENT_TYPES = [
    EventType(id="type-retreat", name="Retreat", tasks_on_main_rota=True, tasks=[
        _event_task("type-retreat", "refreshment", "Hot chocolate", "20:30", "21:15", 2, "first"),
        _event_task("type-retreat", "desk", "Retreat registration", "16:00", "19:00", 1, "first"),
    ]),
    EventType(id="type-festival", name="Festival", big=True, tasks_on_main_rota=False, tasks=[
        _event_task("type-festival", "desk", "Registration desk", "08:00", "12:00", 3, "all"),
        _event_task("type-festival", "hosting", "Car park stewards", "08:30", "10:30", 2, "all"),
        _event_task("type-festival", "shop", "Market stall", "12:30", "14:30", 2, "not_first"),
        _event_task("type-festival", "housekeeping", "Final clean-up", "", "", 4, "last"),
    ]),
]


def invented_names(rng: random.Random, count: int) -> List[Tuple[str, str]]:
    """``count`` distinct (first, last) names, none of them real people's."""
    if count > len(FIRST_NAMES) * len(LAST_NAMES):
        raise ValueError("Not enough invented names.")
    names = set()
    while len(names) < count:
        names.add((rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)))
    ordered = sorted(names)
    rng.shuffle(ordered)
    return ordered


def build_people(catalog: Catalog, rng: random.Random) -> List[Person]:
    people = []
    for index, (first, last) in enumerate(invented_names(rng, POPULATION)):
        people.append(
            Person(
                id=f"person-{index + 1:03d}",
                name=f"{first} {last[0]}.",
                first_name=first,
                last_name=last,
                active=index < ACTIVE,
                resident=index >= NON_RESIDENTS,
            )
        )

    for skill in catalog.skills:
        for person in rng.sample(people, SKILLS[skill.name]):
            person.skills.append(skill.id)

    area_ids = [area.id for area in catalog.areas]
    for person in rng.sample(people, 40):
        person.preferences = {area_id: rng.randint(1, 5) for area_id in area_ids}
    for person in people[:NON_RESIDENTS]:
        person.target_hours_per_week = 1.5
    for person in rng.sample(people, 6):
        person.never.append(NeverRule(weekday="any-day", period="evening"))
    for person in rng.sample(people, 5):
        person.never.append(NeverRule(weekday=rng.choice(WEEKDAY_KEYS), period="any"))
    for person in rng.sample(people, 6):
        person.max_tasks_per_day = 1
    for person in rng.sample(people, 3):
        person.max_hours = 20.0
    for person, note in zip(rng.sample(people, len(NOTES)), NOTES):
        person.note = note
    # Drawn last, so that everything drawn before stays as it was.
    for person in rng.sample(people, 4):
        person.max_shifts_per_week = rng.choice((2, 3))
    _expand_every_day_rules(people)
    return people


def _expand_every_day_rules(people: List[Person]) -> None:
    """Turn the generator's "any-day" shorthand into one rule per weekday."""
    for person in people:
        expanded = []
        for rule in person.never:
            if rule.weekday == "any-day":
                expanded += [NeverRule(weekday=day, period=rule.period) for day in WEEKDAY_KEYS]
            else:
                expanded.append(rule)
        person.never = expanded


def build_regulars(catalog: Catalog, people: List[Person], rng: random.Random) -> List[Regular]:
    """A few regular people per task, spread over the weekdays the task runs."""
    active = [p for p in people if p.active]
    busy_weekdays: Dict[str, set] = {p.id: set() for p in active}
    regulars = []
    for task in catalog.tasks:
        if task.name not in REGULAR_TASKS:
            continue
        trained = [p for p in active if task.skill_id in p.skills]
        pool = rng.sample(trained, min(REGULAR_POOL_SIZE, len(trained)))
        for weekday in WEEKDAY_KEYS:
            needed = task.headcount.get(weekday, task.headcount.get("default", 0))
            if not needed or rng.random() > REGULAR_CHANCE:
                continue
            free = [p for p in pool if weekday not in busy_weekdays[p.id]]
            if not free:
                continue
            person = rng.choice(free)
            busy_weekdays[person.id].add(weekday)
            regulars.append(
                Regular(id=f"regular-{len(regulars) + 1:03d}", person_id=person.id,
                        task_id=task.id, weekday=weekday)
            )
    return regulars


def build_availability(
    people: List[Person], year: int, month: int, rng: random.Random
) -> Dict[str, PersonAvailability]:
    dates = month_dates(year, month)
    replies: Dict[str, PersonAvailability] = {}
    for person in people:
        if not person.active or rng.random() > 0.74:
            continue
        away = set()
        if rng.random() < 0.35:
            length = rng.randint(3, 12)
            first = rng.randrange(0, len(dates) - length + 1)
            for iso in dates[first:first + length]:
                away.update(availability_key(iso, p) for p in ("morning", "afternoon", "evening"))
        if rng.random() < 0.3:
            for iso in rng.sample(dates, rng.randint(2, 6)):
                away.add(availability_key(iso, "evening"))
        if rng.random() < 0.15:
            for iso in rng.sample(dates, rng.randint(2, 4)):
                away.add(availability_key(iso, "morning"))
        comment = rng.choice(COMMENTS) if rng.random() < 0.3 else ""
        replies[person.id] = PersonAvailability(unavailable=sorted(away), comment=comment)
    return replies


def demo_month(catalog: Catalog, people: List[Person], settings: Settings, rng: random.Random) -> Month:
    month = create_month(catalog, DEMO_YEAR, DEMO_MONTH)
    month.events = [
        Event(id="event-mini-break", name="Weekend retreat", start="2026-12-11", end="2026-12-13", type_id="type-retreat"),
        Event(id="event-winter", name="Winter festival", start="2026-12-24", end="2026-12-27", big=True,
              type_id="type-festival"),
        Event(id="event-new-year", name="New Year retreat", start="2026-12-30", end="2027-01-02", big=True,
              type_id="type-retreat"),
    ]
    for event in month.events:
        event_type = next(t for t in EVENT_TYPES if t.id == event.type_id)
        event.tasks_on_main_rota = event_type.tasks_on_main_rota
        month_setup.add_type_tasks(month, event.id, event_type)
    month.markers = [
        Marker(id="marker-volunteers", kind="offrota", label="Volunteers’ day",
               start="2026-12-07", end="2026-12-07"),
        Marker(id="marker-cafe", kind="closed", label="Cafe closed for maintenance",
               start="2026-12-16", end="2026-12-18", area_ids=["area-cafe"]),
        Marker(id="marker-carols", kind="note", label="Carol singing after supper",
               start="2026-12-22", end="2026-12-22"),
    ]
    month.availability = build_availability(people, DEMO_YEAR, DEMO_MONTH, rng)
    apply_regulars(month, catalog, people, settings)
    _fill_part_of_the_rest(month, people, settings, rng)
    _add_showcase_cases(month, people, settings)
    return month


def _fill_part_of_the_rest(month: Month, people: List[Person], settings: Settings, rng: random.Random) -> None:
    """Fill most open slots with a reasonable person, as a coordinator would.

    Some slots stay open on purpose so the demo shows a rota in progress.
    """
    schedule = Schedule(month, people, settings)
    slots = schedule.open_slots()
    rng.shuffle(slots)
    for task_id, iso, position in slots[: int(len(slots) * 0.8)]:
        best = rules.candidates(schedule, schedule.tasks[task_id], iso)[0]
        if best.group == rules.BLOCKED:
            continue
        assignment = Assignment(task_id, iso, position, person_id=best.person.id)
        month.assignments.append(assignment)
        schedule.add(assignment)


def _add_showcase_cases(month: Month, people: List[Person], settings: Settings) -> None:
    """One example of each special case, so every state can be seen."""
    schedule = Schedule(month, people, settings)
    open_slots = schedule.open_slots()

    def first_open(task_id: str) -> Optional[Tuple[str, str, int]]:
        return next((slot for slot in open_slots if slot[0] == task_id), None)

    guest_slot = first_open("task-cafe-morning-shift")
    if guest_slot:
        month.assignments.append(Assignment(*guest_slot, guest="Marta (volunteer)"))

    forced_slot = first_open("task-breakfast-set-up") or first_open("task-cafe-afternoon-shift")
    away = next(
        (p for p in people if p.active and forced_slot
         and schedule.is_unavailable(p.id, forced_slot[1], "evening")),
        None,
    )
    if forced_slot and away:
        month.assignments.append(
            Assignment(*forced_slot, person_id=away.id, forced_reason="Agreed by phone, back early")
        )

    # A clash as it would arrive from an imported rota: someone untrained.
    for task_id, iso, position in open_slots:
        task = schedule.tasks[task_id]
        untrained = next(
            (p for p in people
             if p.active and task.skill_id not in p.skills and not schedule.on_day(p.id, iso)),
            None,
        )
        if untrained and (task_id, iso, position) not in (guest_slot, forced_slot):
            month.assignments.append(Assignment(task_id, iso, position, person_id=untrained.id))
            break

    # Shifts to fill when possible, and one switched off for a quiet day.
    schedule = Schedule(month, people, settings)
    still_open = schedule.open_slots()
    month.wanted = [slot_key(*slot) for slot in still_open[1:4]]
    quiet_day = next(s for s in still_open[4:] if schedule.required[(s[0], s[1])] == 1)
    set_headcount(month, quiet_day[0], quiet_day[1], 0)


def build_demo(store: Store) -> None:
    """Write a complete demonstration data set into ``store``."""
    rng = random.Random(SEED)
    catalog = build_catalog()
    people = build_people(catalog, rng)
    catalog.regulars = build_regulars(catalog, people, rng)
    # About 15 hours a month of each active person: 3.5 hours a week.
    settings = Settings(centre_name="Demo centre", default_target_hours_per_week=3.5)
    settings.print_templates.append(PrintTemplate(id="print-food", name="Kitchen + Dining room + Cafe", size="a3",
                                                  area_ids=["area-kitchen", "area-dining", "area-cafe"]))
    store.save_catalog(catalog)
    store.save_people(people)
    store.save_settings(settings)
    store.save_month(demo_month(catalog, people, settings, rng))
