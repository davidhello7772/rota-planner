"""A tiny, readable data set for tests. All names are invented."""

from rotaplanner.model import (
    Area,
    Catalog,
    Month,
    Person,
    PersonAvailability,
    Settings,
    Skill,
    Task,
)
from rotaplanner.months import create_month
from rotaplanner.schedule import Schedule

YEAR, MONTH = 2026, 12  # 1 December 2026 is a Tuesday


def catalog() -> Catalog:
    return Catalog(
        skills=[Skill("cook", "Cook"), Skill("guard", "Night guard")],
        areas=[Area("kitchen", "Kitchen", color="orange"), Area("security", "Security")],
        tasks=[
            Task("lunch", "kitchen", "Lunch cook", periods=["morning"], skill_id="cook",
                 start="09:00", end="13:00", hours=4, headcount={"default": 1}),
            Task("helper", "kitchen", "Kitchen helper", periods=["morning"],
                 start="10:00", end="13:00", hours=3, headcount={"sat": 2, "sun": 2, "event": 3}),
            Task("supper", "kitchen", "Supper cook", periods=["afternoon"], skill_id="cook",
                 start="16:00", end="19:00", hours=3, headcount={"default": 1}),
            Task("night", "security", "Night guard", periods=["evening"], skill_id="guard",
                 start="22:00", end="08:00", hours=0, headcount={"default": 1}),
            Task("breakfast", "kitchen", "Breakfast", periods=["morning"],
                 start="07:00", end="09:00", hours=2, headcount={"default": 1}),
        ],
    )


def people() -> list:
    return [
        Person("ana", "Ana B.", first_name="Ana", last_name="Berg", skills=["cook"]),
        Person("ben", "Ben C.", first_name="Ben", last_name="Cole", skills=["cook", "guard"]),
        Person("cleo", "Cleo D.", first_name="Cleo", last_name="Dane", skills=["guard"]),
        Person("dov", "Dov E.", first_name="Dov", last_name="Earl", skills=[], active=False),
    ]


def month(cat: Catalog = None) -> Month:
    return create_month(cat or catalog(), YEAR, MONTH)


def schedule(m: Month, ppl=None, settings: Settings = None) -> Schedule:
    return Schedule(m, ppl if ppl is not None else people(), settings or Settings())


def away(m: Month, person_id: str, *keys: str) -> None:
    m.availability[person_id] = PersonAvailability(unavailable=list(keys))
