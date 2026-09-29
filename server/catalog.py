"""server/catalog.py — canonical class names and track (venue) grouping
(V0.8.9).

Why this exists: the leaderboard dropdowns used to be built ONLY from laps
already uploaded, straight from LMU's raw strings. So (a) a class nobody had
driven yet simply didn't exist, (b) LMU's internal class name "Hyper" showed
up as-is, and (c) a track was whatever string LMU sent, with no notion that
"Fuji Speedway" and its layouts belong together and no way to search them.

What this module does — WITHOUT anyone having to enter tracks, cars or
classes by hand:

  * CLASSES: the six known LMU classes always exist (also before a single
    lap was driven), under readable names. `canonical_class(raw)` maps LMU's
    raw string to that name ("Hyper" -> "Hypercar"). A class we don't know
    is kept exactly as LMU sent it and simply appears in the catalog once a
    lap arrives (auto-learn) — nothing is ever dropped.
  * VENUES: a seeded list of tracks (with search keywords) so every known
    track is listed before its first lap. `venue_for(raw_track)` groups LMU's
    raw track strings — including every layout/variant it sends — under one
    venue. A track we don't know becomes its own venue, so it also shows up
    automatically. Layouts are never invented: they are exactly the raw
    strings LMU actually sent, discovered from real laps.
  * Cars are not listed here on purpose: a car is always tied to the class LMU
    reports for it on each lap, so cars and their class assignment are learned
    automatically from uploads.

The seeded lists are best-effort knowledge of LMU's content. A wrong or
missing entry is harmless (an unmatched venue just stays empty; an unknown
track/class is still auto-added) and is a one-line edit below.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Optional, Tuple

# ---------------------------------------------------------------- classes

# (display name, normalized aliases). Normalization = uppercase, letters and
# digits only, so "LMP2_ELMS", "LMP2 ELMS" and "lmp2elms" are the same key.
# Order = the order shown in the UI (fastest/top class first).
CLASSES: List[Tuple[str, Tuple[str, ...]]] = [
    ("Hypercar", ("HYPER", "HYPERCAR")),
    ("LMP2 WEC", ("LMP2WEC",)),
    ("LMP2 ELMS", ("LMP2ELMS",)),
    ("LMP3", ("LMP3",)),
    ("GTE", ("GTE", "LMGTE")),
    ("GT3", ("GT3", "LMGT3")),
]

_CLASS_BY_ALIAS = {alias: display for display, aliases in CLASSES for alias in aliases}


def normalize_key(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def canonical_class(raw: Optional[str]) -> Optional[str]:
    """LMU's raw class string -> readable class name. Unknown classes are
    returned unchanged (stripped), so they are learned rather than lost."""
    if raw is None:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    return _CLASS_BY_ALIAS.get(normalize_key(cleaned), cleaned)


def known_class_names() -> List[str]:
    return [display for display, _ in CLASSES]


def class_sort_key(name: str):
    order = {display: i for i, (display, _) in enumerate(CLASSES)}
    return (order.get(name, len(order)), name.lower())


# ----------------------------------------------------------------- venues

# (display name, search keywords). A keyword of 5+ characters matches as a
# substring of the compacted track string; shorter ones ("spa", "cota") must
# equal a whole word, so "spa" doesn't match "Espanya".
VENUES: List[Tuple[str, Tuple[str, ...]]] = [
    ("Bahrain International Circuit", ("bahrain", "sakhir")),
    ("Circuit de la Sarthe (Le Mans)", ("sarthe", "lemans")),
    ("Circuit of the Americas", ("cota", "americas")),
    ("Circuit Paul Ricard", ("paulricard", "ricard")),
    ("Fuji Speedway", ("fuji",)),
    ("Imola (Enzo e Dino Ferrari)", ("imola", "enzoedino", "enzo")),
    ("Interlagos (José Carlos Pace)", ("interlagos", "carlospace")),
    ("Lusail International Circuit", ("lusail", "qatar")),
    ("Monza", ("monza",)),
    ("Portimão (Algarve)", ("portimao", "algarve")),
    ("Sebring International Raceway", ("sebring",)),
    ("Spa-Francorchamps", ("spa", "francorchamps")),
]


def _compact(text: str) -> str:
    """Lowercase, accents folded, letters and digits only."""
    import unicodedata

    folded = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", folded.lower())


def _tokens(text: str) -> set:
    import unicodedata

    folded = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return {t for t in re.split(r"[^a-z0-9]+", folded) if t}


def _keyword_matches(keyword: str, compact: str, tokens: set) -> bool:
    if len(keyword) >= 5:
        return keyword in compact
    return keyword in tokens


def venue_for(raw_track: str) -> str:
    """The venue a raw LMU track string belongs to. Unknown tracks are their
    own venue (auto-learn), spelled exactly as LMU sent them."""
    cleaned = (raw_track or "").strip()
    compact, tokens = _compact(cleaned), _tokens(cleaned)
    for display, keywords in VENUES:
        if any(_keyword_matches(k, compact, tokens) for k in keywords):
            return display
    return cleaned


def known_venue_names() -> List[str]:
    return [display for display, _ in VENUES]


def venue_matches_query(venue: str, layouts: Iterable[str], query: str) -> bool:
    """Search: does `query` (any case, accents/punctuation ignored) match the
    venue's name, its search keywords, or any of its layout names?

    Typed-search semantics: a query of 5+ letters/digits matches anywhere in a
    name ("fuji speed" finds Fuji Speedway; "le mans" finds the Sarthe), while
    a SHORT query must match the START of a word — "fu" finds Fuji and "spa"
    finds Spa-Francorchamps, but "spa" does not match "Espanya"."""
    q = _compact(query)
    if not q:
        return True
    texts = [venue, *layouts]
    for display, keywords in VENUES:
        if display == venue:
            texts.extend(keywords)
    for text in texts:
        if len(q) >= 5 and q in _compact(text):
            return True
        if any(token.startswith(q) for token in _tokens(text)):
            return True
    return False


def track_matches_query(raw_track: str, query: str) -> bool:
    """For the 'find my laps at ...' filter: substring on the raw name OR on
    the venue it belongs to — so typing "fuji" finds every Fuji layout."""
    return venue_matches_query(venue_for(raw_track), [raw_track], query)
