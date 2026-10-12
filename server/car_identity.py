"""
server/car_identity.py — which car MODEL a lap was driven in (V0.9.3).

The problem this solves
-----------------------
Until V0.9.2, `laps.car_model` held LMU's raw `veh_filename`. That name is per
LIVERY, not per model: a real recording shows "397_25_499P" for the car named
"499P Custom Team 2025 #397" — car number 397, season 25, model 499P. Two entries
of the same car with different numbers therefore counted as two different cars in
every leaderboard, record and comparison.

The rule
--------
A car's identity is its MODEL. Team, livery, car number and the season of the
livery do not matter. Different SPECIFICATIONS of a model (an "Evo", a car rebuilt
with a different aero package) are different cars. Concretely, the identity comes
from, in this order:

  1. LMU's own model name — TelemInfoV01.mVehicleModel, documented as "brand &
     model name" (LMU 1.3+, recorded by clients from 0.9.3 on). Two cars are the same
     model exactly when LMU gives them the same model name; whether LMU calls a
     variant "… Evo" or "… 2024" is LMU's decision, not a guess of ours.
  2. An ALIAS the server has learned: whenever a 0.9.3 client uploads a lap, it also
     reports the (veh_filename, model name) of every car it saw in the session. So
     the server learns, from the game itself, that e.g. "50_25_499P" and
     "397_25_499P" are both the same model — and laps of older clients, which only
     know the veh_filename, are re-grouped automatically (see learn_aliases).
  3. The veh_filename with the car number removed — the fallback while nothing
     better is known. The season stays in it ("499P (2025)"): without the model name
     we cannot tell a new season's livery of the same car from a new specification,
     and splitting is the safe mistake (a merge cannot be told apart later, a split
     disappears as soon as an alias is learned).

Nothing is ever lost: the raw values LMU sent are kept per lap
(`car_model_raw` = veh_filename, `car_vehicle_model` = model name), so the identity
of every lap can be re-derived with newer rules at any time
(scripts/car_identity_report.py shows the effect first and only applies on request).

Truncated model names (V0.9.3.1)
--------------------------------
mVehicleModel is a 30-byte C string, so LMU cuts long names after 29 characters. Real
examples (observed by the TinyPedal project, which corrects the same three names):
"Aston Martin Vantage AMR LMGT" (…LMGT3), "Chevrolet Corvette Z06 LMGT3." (…LMGT3.R),
"Lamborghini Huracan LMGT3 Evo" (…Evo2). Two consequences: the shown name is wrong, and
two variants whose names differ only after character 29 would share one identity. So:
  * MODEL_NAME_FIXES maps known cut names to the full name (versioned: a change bumps
    IDENTITY_RULES_VERSION, scripts/car_identity_report.py re-derives the laps).
  * a name of the maximum length that is NOT in the table is marked as cut ("…" at the
    end) and listed by the report and the admin page, so it can be added once its full
    name is known — it is never silently passed off as complete.

Learning aliases safely (V0.9.3.1)
----------------------------------
The pairs come from two places: the lap's own car (veh_filename and model name of the same
car, read together) and the grid list (every car of the session, matched by slot id every
few seconds — a torn read or a slot reused by a joining car could pair the wrong two
strings once). A grid pair is therefore only USED after it was reported in
ALIAS_MIN_GRID_SIGHTINGS separate uploads; a pair from the lap's own car is used at once.
Conflicts are handled as before (never used, laps moved back).

Unverified assumptions, stated plainly
--------------------------------------
* The "<number>_<season>_<model>" pattern of veh_filename is taken from ONE real
  recording. Names that do not match it are kept exactly as LMU sent them, so an
  unexpected format can only split, never merge, cars.
* Whether LMU names the specifications of a model differently (for example two
  aero packages of the same car) has to be checked with real data:
  scripts/car_identity_probe.py lists every car of a session as LMU reports it.
  SPEC_RULES below is the hook for the case where LMU does NOT distinguish two
  specifications by name; it is deliberately empty until real data shows a need.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable, List, Optional, Tuple

# Bump whenever the derivation below changes; stored per lap so an old derivation is
# recognisable (scripts/car_identity_report.py re-derives everything on request).
IDENTITY_RULES_VERSION = 2   # 2: MODEL_NAME_FIXES / cut-name marker (V0.9.3.1)

MAX_IDENTITY_LENGTH = 100

SOURCE_MODEL = "vehicle_model"   # LMU's model name of this very lap
SOURCE_ALIAS = "alias"           # veh_filename -> model learned from the game
SOURCE_VEH_FILE = "veh_file"     # fallback: veh_filename without the car number

# "397_25_499P" -> number 397, season 25, model "499P"
VEH_FILE_PATTERN = re.compile(r"^(?P<number>\d{1,4})_(?P<season>\d{2})_(?P<model>.+)$")
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class SpecRule:
    """Splits one LMU model name into specifications when LMU itself does not.

    model: regular expression matched against the model name (full match).
    seasons: inclusive (first, last) season of the veh_filename that belongs to `suffix`.
    suffix: appended to the identity, e.g. "(2024 spec)".
    note: where the evidence for this rule comes from (required — no rule without it).
    """

    model: str
    seasons: Tuple[int, int]
    suffix: str
    note: str


# Empty on purpose: no rule without real evidence (see the module docstring).
SPEC_RULES: Tuple[SpecRule, ...] = ()

# mVehicleModel is char[30]: at most 29 characters reach us (see the module docstring).
MODEL_NAME_MAX_CHARS = 29
TRUNCATED_MARK = "…"

# Cut name -> full name. Evidence for every entry: the TinyPedal project corrects exactly
# these names in its LMU adapter (tinypedal/adapter/lmu_reader.py, LMU_CAR_MODEL_CORRECTION;
# only the car names are taken over — facts, no code). Still to be confirmed with
# scripts/car_identity_probe.py on a full grid (docs/LMU_VERIFICATION_PROTOCOL.md (t)).
MODEL_NAME_FIXES = {
    "Aston Martin Vantage AMR LMGT": "Aston Martin Vantage AMR LMGT3",
    "Chevrolet Corvette Z06 LMGT3.": "Chevrolet Corvette Z06 LMGT3.R",
    "Lamborghini Huracan LMGT3 Evo": "Lamborghini Huracan LMGT3 Evo2",
}

# A pair seen only in grid lists is used after this many separate uploads reported it.
ALIAS_MIN_GRID_SIGHTINGS = 2


def clean_text(value: Optional[str], limit: int = MAX_IDENTITY_LENGTH) -> Optional[str]:
    if value is None:
        return None
    text = _SPACES.sub(" ", str(value)).strip()
    if not text or not text.isprintable():
        return None
    return text[:limit]


def veh_file_parts(veh_file: Optional[str]) -> Optional[Tuple[Optional[int], Optional[int], str]]:
    """(car number, season 2000-based, model token) — number/season None when the name
    does not follow the known pattern."""
    text = clean_text(veh_file)
    if text is None:
        return None
    match = VEH_FILE_PATTERN.match(text)
    if match:
        model = clean_text(match.group("model").replace("_", " "))
        if model:
            return int(match.group("number")), 2000 + int(match.group("season")), model
    # Not the known pattern: kept exactly as LMU sent it. Removing a "leading number"
    # here would be wrong — model names start with digits too ("911_gt3_r").
    return None, None, text


def fallback_identity(veh_file: Optional[str]) -> Optional[str]:
    """The identity derived from veh_filename alone: "397_25_499P" -> "499P (2025)".

    Also the KEY under which an alias (veh_filename -> model name) is stored: every
    livery of one model and season shares it."""
    parts = veh_file_parts(veh_file)
    if parts is None:
        return None
    _number, season, model = parts
    return clean_text(f"{model} ({season})" if season is not None else model)


def _apply_spec_rules(model_name: str, veh_file: Optional[str], rules: Iterable[SpecRule]) -> str:
    parts = veh_file_parts(veh_file)
    season = parts[1] if parts else None
    for rule in rules:
        if re.fullmatch(rule.model, model_name) and season is not None and rule.seasons[0] <= season <= rule.seasons[1]:
            return clean_text(f"{model_name} {rule.suffix}") or model_name
    return model_name


def is_cut(name: Optional[str]) -> bool:
    """Has LMU (probably) cut this model name at the 30-byte limit?"""
    if not name:
        return False
    return len(name.encode("utf-8")) >= MODEL_NAME_MAX_CHARS


def full_model_name(vehicle_model: Optional[str]) -> Optional[str]:
    """LMU's model name, repaired where it was cut: the full name from MODEL_NAME_FIXES, or
    the cut name marked with "…" when the full one is not known. Idempotent."""
    name = clean_text(vehicle_model)
    if name is None:
        return None
    if name.endswith(TRUNCATED_MARK):
        # marked by the client (0.9.3.1+), which sees the cut even when LMU cut right after
        # a space (the name then looks complete once stripped)
        base = name[: -len(TRUNCATED_MARK)].rstrip()
        return _fix(base) or (base + TRUNCATED_MARK if base else None)
    if name in MODEL_NAME_FIXES.values():
        return name
    if _fix(name):
        return _fix(name)
    if is_cut(name):
        return name + TRUNCATED_MARK
    return name


def _fix(name: str) -> Optional[str]:
    # exact match only: a name that merely STARTS like a table entry may be another car
    return MODEL_NAME_FIXES.get(name)


def model_identity(vehicle_model: Optional[str], veh_file: Optional[str] = None,
                   rules: Iterable[SpecRule] = SPEC_RULES) -> Optional[str]:
    name = full_model_name(vehicle_model)
    if name is None:
        return None
    return _apply_spec_rules(name, veh_file, rules)


@dataclass(frozen=True)
class Identity:
    key: Optional[str]      # what laps.car_model holds; None when LMU sent nothing usable
    source: Optional[str]   # SOURCE_* or None


AliasLookup = Callable[[str], Optional[str]]


def resolve(vehicle_model: Optional[str], veh_file: Optional[str],
            alias_lookup: Optional[AliasLookup] = None,
            rules: Iterable[SpecRule] = SPEC_RULES) -> Identity:
    """The identity of one lap, see the module docstring for the order."""
    rules = tuple(rules)
    by_model = model_identity(vehicle_model, veh_file, rules)
    if by_model:
        return Identity(by_model, SOURCE_MODEL)
    fallback = fallback_identity(veh_file)
    if fallback is None:
        return Identity(None, None)
    if alias_lookup is not None:
        learned = alias_lookup(fallback)
        if learned:
            return Identity(learned, SOURCE_ALIAS)
    return Identity(fallback, SOURCE_VEH_FILE)


# ---------------------------------------------------------------------------
# Learning aliases from what the game reports (needs the database)
# ---------------------------------------------------------------------------

MAX_OBSERVATIONS_PER_UPLOAD = 128


def observations(lap_veh_file: Optional[str], lap_vehicle_model: Optional[str],
                 grid: Optional[Iterable[dict]]) -> List[Tuple[str, str, str]]:
    """(alias key, model identity, source) pairs worth learning from one upload: the
    lap's own car first, then every car of the session the client saw."""
    out: List[Tuple[str, str, str]] = []
    seen = set()

    def add(veh_file, model, source):
        key = fallback_identity(veh_file)
        identity = model_identity(model, veh_file)
        if not key or not identity or key in seen:
            return
        seen.add(key)
        out.append((key, identity, source))

    add(lap_veh_file, lap_vehicle_model, "lap")
    for entry in list(grid or [])[:MAX_OBSERVATIONS_PER_UPLOAD]:
        if isinstance(entry, dict):
            add(entry.get("veh_file"), entry.get("model"), "grid")
    return out


def alias_usable(row) -> bool:
    """A learned alias is used only when it is not a conflict and is backed by the lap's own
    car or by enough separate grid reports (see the module docstring)."""
    if row is None or row.conflict:
        return False
    return row.source == "lap" or (row.seen_count or 0) >= ALIAS_MIN_GRID_SIGHTINGS


def lookup_alias(db, key: str) -> Optional[str]:
    from .models import CarAlias

    row = db.query(CarAlias).filter(CarAlias.alias_key == key).one_or_none()
    if not alias_usable(row):
        return None
    # stored with the rules of its time — repaired with today's (cut names, V0.9.3.1)
    return model_identity(row.canonical) or row.canonical


def learn_aliases(db, pairs: Iterable[Tuple[str, str, str]]) -> dict:
    """Stores what the game reported and re-groups laps that only had the fallback.

    For each (alias key, model identity):
      * new key            -> stored; laps whose identity is that fallback key (and that
                              have no model name of their own) move to the model identity
      * same model again   -> only counted
      * a DIFFERENT model  -> the key is marked as a conflict: the game says one
                              veh_filename group holds two models, so no alias is used
                              for it, and laps that were moved by it go back to the
                              fallback (nothing is merged that the game itself does not
                              confirm)
    V0.9.3.1: a pair only from grid lists moves laps once it is usable (alias_usable).
    Runs inside the caller's transaction (no commit). Returns counts for logging."""
    from .models import CarAlias, Lap

    def move(key: str, identity: str) -> int:
        return (
            db.query(Lap)
            .filter(Lap.car_identity_source == SOURCE_VEH_FILE, Lap.car_model == key)
            .update({Lap.car_model: identity, Lap.car_identity_source: SOURCE_ALIAS}, synchronize_session=False)
        ) or 0

    now = datetime.now(timezone.utc)
    stats = {"learned": 0, "confirmed": 0, "conflicts": 0, "laps_moved": 0}
    for key, identity, source in pairs:
        row = db.query(CarAlias).filter(CarAlias.alias_key == key).one_or_none()
        if row is None:
            row = CarAlias(alias_key=key, canonical=identity, source=source, conflict=False,
                           first_seen=now, last_seen=now, seen_count=1, rules_version=IDENTITY_RULES_VERSION)
            db.add(row)
            db.flush()
            stats["learned"] += 1
            if alias_usable(row):
                stats["laps_moved"] += move(key, identity)
            continue
        was_usable = alias_usable(row)
        row.last_seen = now
        row.seen_count = (row.seen_count or 0) + 1
        if model_identity(row.canonical) == identity:
            if row.canonical != identity:
                row.canonical = identity            # repaired name (cut names, V0.9.3.1)
                row.rules_version = IDENTITY_RULES_VERSION
            if source == "lap" and row.source != "lap":
                row.source = "lap"
            stats["confirmed"] += 1
            if not was_usable and alias_usable(row):
                stats["laps_moved"] += move(key, identity)
            continue
        if not row.conflict:
            row.conflict = True
            row.conflict_with = identity[:MAX_IDENTITY_LENGTH]
            stats["conflicts"] += 1
            # undo what this alias moved: those laps only know their veh_filename
            reverted = 0
            for lap in db.query(Lap).filter(Lap.car_identity_source == SOURCE_ALIAS,
                                            Lap.car_model.in_({row.canonical, model_identity(row.canonical) or row.canonical})):
                if fallback_identity(lap.car_model_raw) == key:
                    lap.car_model = key
                    lap.car_identity_source = SOURCE_VEH_FILE
                    reverted += 1
            stats["laps_moved"] += reverted
    return stats


# ---------------------------------------------------------------------------
# What to check by hand (V0.9.3.1): the report and the admin page list these
# ---------------------------------------------------------------------------

def identity_suspects(rows: Iterable[dict]) -> List[dict]:
    """rows: {"identity", "veh_file", "car_class", "laps"} (one per distinct combination).
    Returns one entry per identity that needs a human look:
      cut_name        LMU cut the model name and the full name is not known yet
      several_models  laps of this identity come from veh_filenames with different model
                      parts — two variants LMU names alike? (or LMU renamed a file)
      several_classes laps of this identity are in different classes — certainly worth a look
    Nothing here changes an identity; it only says where real data must decide."""
    groups: dict = {}
    for row in rows:
        identity = row.get("identity")
        if not identity:
            continue
        g = groups.setdefault(identity, {"tokens": set(), "classes": set(), "laps": 0})
        parts = veh_file_parts(row.get("veh_file"))
        if parts is not None:
            g["tokens"].add(parts[2])
        if row.get("car_class"):
            g["classes"].add(str(row["car_class"]))
        g["laps"] += int(row.get("laps") or 0)
    out = []
    for identity, g in sorted(groups.items()):
        reasons = []
        if identity.endswith(TRUNCATED_MARK):
            reasons.append("cut_name")
        if len(g["tokens"]) > 1:
            reasons.append("several_models")
        if len(g["classes"]) > 1:
            reasons.append("several_classes")
        if reasons:
            out.append({"identity": identity, "reasons": reasons, "laps": g["laps"],
                        "veh_models": sorted(g["tokens"]), "classes": sorted(g["classes"])})
    return out
