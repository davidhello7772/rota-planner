"""Recognising people by their e-mail address without keeping it.

Only a one-way fingerprint of the address is kept (``Person.email_fp``): a
slow hash (PBKDF2-SHA256, present in every Python build, unlike scrypt) of the
address, lower-cased, with a random salt of the installation. Each
fingerprint names its salt (a short tag), so one made with another salt is
never stored where it could not match. The address itself cannot be read
back from it, nor used to write to anyone. When the availability form's replies come in with their
e-mail column, each address is turned into a fingerprint and compared; the
address is then dropped, never saved, logged or sent to the screen.

Limit, said plainly in the help: someone who holds the data folder and
guesses an address can check the guess. The slow hash makes trying many
guesses costly; it does not make one guess impossible.

The salt lives in ``people.json`` (``Store.email_salt``), so it always travels
with the fingerprints made with it (moving to another computer, sync).
"""

from __future__ import annotations

import hashlib
import re
from typing import Dict, List, Optional

from .model import Person

PREFIX = "p1:"
ROUNDS = 40_000  # ~15 ms each: a hundred replies stay quick
FINGERPRINT = re.compile(r"^p1:[0-9a-f]{8}:[0-9a-f]{32}$")
ADDRESS = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,253}\.[^@\s]{1,63}$")


def normalise(address: str) -> str:
    """The address as compared: trimmed, lower case. Empty if it is not an address."""
    text = str(address or "").strip().strip("<>").casefold()
    return text if ADDRESS.match(text) else ""


def salt_tag(salt: bytes) -> str:
    return hashlib.sha256(b"salt tag" + salt).hexdigest()[:8]


def fingerprint(address: str, salt: bytes) -> str:
    """The fingerprint of an address, or "" if it is not an address."""
    text = normalise(address)
    if not text:
        return ""
    digest = hashlib.pbkdf2_hmac("sha256", text.encode("utf-8"), salt, ROUNDS, dklen=16)
    return f"{PREFIX}{salt_tag(salt)}:{digest.hex()}"


def valid(value: object, salt: bytes = b"") -> bool:
    """A fingerprint, made with ``salt`` when one is given."""
    if not (isinstance(value, str) and FINGERPRINT.match(value)):
        return False
    return not salt or value.split(":")[1] == salt_tag(salt)


def owners(people: List[Person]) -> Dict[str, str]:
    """Fingerprint → person id, for the people with a known address."""
    return {p.email_fp: p.id for p in people if p.email_fp}


def remember(people: List[Person], person_id: str, value: str, salt: bytes) -> bool:
    """Keep ``value`` as the person's address; an address belongs to one person only.
    A fingerprint made with another salt (the people were replaced meanwhile) is
    refused: it could never match. Returns whether anything changed."""
    if not valid(value, salt):
        return False
    person: Optional[Person] = next((p for p in people if p.id == person_id), None)
    if person is None:
        return False
    changed = False
    for other in people:
        if other is not person and other.email_fp == value:
            other.email_fp, changed = "", True
    if person.email_fp != value:
        person.email_fp, changed = value, True
    return changed


def status(person: Optional[Person], value: str, owner: Optional[str]) -> str:
    """A reply's address against a person: "" (no address or no person), "recognised"
    (theirs), "new" (they have none, nobody else has it), "changed" (they have
    another one), "other" (it is someone else's)."""
    if not value or person is None:
        return ""
    if owner == person.id:
        return "recognised"
    if owner is not None:
        return "other"
    return "changed" if person.email_fp else "new"


def remember_by_default(state: str) -> bool:
    """Only a first address, known to nobody, is remembered without asking."""
    return state == "new"


def public(person: Person) -> dict:
    """A person as the screens get them: whether an address is known, never its fingerprint."""
    data = person.to_dict()
    data["has_email"] = bool(data.pop("email_fp", ""))
    return data
