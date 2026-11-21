"""Readable path segments ("slugs") for the website's links.

    Moritz Lachnit        -> moritz-lachnit        /driver/moritz-lachnit
    HyperMotion Esports   -> hypermotion-esports   /team/hypermotion-esports
    Fuji Speedway         -> fuji-speedway         /leaderboard/fuji-speedway/hypercar
    Porsche 963           -> porsche-963
    Circuit de la Sarthe  -> circuit-de-la-sarthe
    Müller · Ørsted       -> muller-ørsted         (accents dropped, other letters kept)

Case folded, accents removed (NFKD, combining marks dropped), every run of characters that is not a
letter or digit becomes one "-". A name with no letter or digit at all has no slug (""): its links
use the number instead (/driver/17). The same function exists in the browser (web/js/core/link.js
slugify) — tests/web/test_links.py checks both give the same answer.
"""

from __future__ import annotations

import unicodedata

MAX_SLUG_LENGTH = 80


def slugify(text: str) -> str:
    folded = unicodedata.normalize("NFKD", str(text or "").casefold())
    out = []
    dash = False
    for ch in folded:
        if unicodedata.category(ch) == "Mn":     # combining accent of the letter before
            continue
        if ch.isalnum():
            out.append(ch)
            dash = False
        elif not dash and out:
            out.append("-")
            dash = True
    slug = "".join(out).strip("-")
    return slug[:MAX_SLUG_LENGTH].rstrip("-")
