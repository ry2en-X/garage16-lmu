"""names.py — driver names that cannot be confused with each other (V1.0.1).

With open registration two people could pick the same name and appear twice in a leaderboard, or a
newcomer could take the name of a known driver with a different capital letter or an invisible
character. Every name is therefore compared in one normalised form, `name_key()`, which is stored
unique (drivers.display_name_key):

  * Unicode NFKC — full-width letters, ligatures and similar forms become their plain letters;
  * case folded — "MAX" == "max" (also ß == ss);
  * invisible characters removed (format characters such as zero-width spaces, and controls);
  * whitespace collapsed to single spaces and trimmed.

`clean_display_name()` is what is stored and shown: the name as typed, trimmed, inner whitespace
collapsed, controls and invisible characters removed. It is rejected if nothing is left, if it is
longer than 60 characters, or if its key is one of a few reserved words (admin, garage16, …).
"""

from __future__ import annotations

import unicodedata
from typing import Optional

from sqlalchemy.orm import Session

MAX_LENGTH = 60

# Names nobody may take — they would look official.
RESERVED_KEYS = frozenset({
    "admin", "administrator", "moderator", "mod", "garage16", "garage 16", "system", "support",
    "root", "server", "operator", "betreiber",
})


class InvalidName(ValueError):
    """A display name that cannot be used, with a message for the user."""


def _visible(ch: str) -> bool:
    category = unicodedata.category(ch)
    return not (category.startswith("C"))   # Cc controls, Cf format (zero-width …), Co, Cn, Cs


def clean_display_name(raw: str) -> str:
    """The name as it will be stored and shown, or InvalidName with a reason."""
    text = "".join(ch if _visible(ch) else (" " if ch in "\t\n\r" else "") for ch in str(raw or ""))
    text = " ".join(text.split())
    if not text:
        raise InvalidName("The name must not be blank.")
    if len(text) > MAX_LENGTH:
        raise InvalidName(f"The name may be at most {MAX_LENGTH} characters long.")
    if name_key(text) in RESERVED_KEYS:
        raise InvalidName("That name is reserved — please choose another one.")
    return text


def name_key(name: str) -> str:
    """The form in which two names are compared (see module docstring)."""
    text = unicodedata.normalize("NFKC", str(name or ""))
    text = "".join(ch for ch in text if _visible(ch) or ch.isspace())
    text = text.casefold()
    return " ".join(text.split())[:120]


def name_taken(db: Session, name: str, except_driver_id: Optional[int] = None) -> bool:
    """True if another driver already uses this name (in its compared form)."""
    from .models import Driver

    query = db.query(Driver.id).filter(Driver.display_name_key == name_key(name))
    if except_driver_id is not None:
        query = query.filter(Driver.id != except_driver_id)
    return query.first() is not None


NAME_TAKEN_MESSAGE = "That driver name is already taken — please choose another one."
