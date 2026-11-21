"""The upload contract (lap metadata), lap lists, leaderboards, reports and the admin's lap views."""
from __future__ import annotations

import math
from typing import List, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

# Generous but real ceiling — long enough to never reject a genuine lap
# (including slow out-laps under yellow), tight enough to catch garbage
# values (e.g. a stray timestamp landing in the lap_time field).
MAX_LAP_TIME_SECONDS = 3600.0


MAX_SECTOR_COUNT = 12


class GameLapState(BaseModel):
    """What the client read from the game for one lap (its summary only; the
    server recomputes everything from the telemetry file and decides)."""

    model_config = ConfigDict(extra="forbid")

    count_lap_flag: Optional[int] = Field(default=None, ge=0, le=2)
    invalidated: Optional[StrictBool] = None
    track_limits_steps: Optional[int] = Field(default=None, ge=0, le=1000)
    penalties_added: Optional[int] = Field(default=None, ge=0, le=1000)


def _finite_number(value):
    if value is not None and (isinstance(value, bool) or not math.isfinite(float(value))):
        raise ValueError("must be a finite number (not NaN or Infinity)")
    return value


class LateWindowReport(BaseModel):
    """What LMU reported in the first seconds after the finish line
    (client/telemetry/lap_verdict.py LateWindow). Change lists are [seconds after the
    line, value]."""

    model_config = ConfigDict(extra="forbid")

    seconds: float = Field(ge=0, le=600)
    complete: StrictBool
    lap_time_at_line: float = Field(ge=-1e6, le=1e6)
    last_lap_time: float = Field(ge=-1e6, le=1e6)
    steps: List[List[float]] = Field(default_factory=list, max_length=32)
    penalties: List[List[float]] = Field(default_factory=list, max_length=32)
    excursion_at: Optional[float] = Field(default=None, ge=0, le=600)
    can_see_excursions: StrictBool = False

    @field_validator("seconds", "lap_time_at_line", "last_lap_time", "excursion_at")
    @classmethod
    def _finite(cls, value):
        return _finite_number(value)

    @field_validator("steps", "penalties")
    @classmethod
    def _pairs(cls, value):
        for row in value:
            if len(row) != 2 or not all(math.isfinite(float(x)) for x in row):
                raise ValueError("entries must be [seconds, value] pairs of finite numbers")
            if not (0 <= row[0] <= 600 and 0 <= row[1] <= 1000):
                raise ValueError("entry out of range")
        return value


class LapTimeCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_: float = Field(alias="from", ge=-1e6, le=1e6)
    to: float = Field(gt=0, le=MAX_LAP_TIME_SECONDS)
    after_s: float = Field(ge=-1, le=600)


class LapTimeUnconfirmed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lmu: float = Field(ge=-1e6, le=1e6)
    measured: float = Field(ge=-1e6, le=1e6)


class LapReport(BaseModel):
    """Why the client judged the lap as it did (reason codes, see
    server/laps/track_limits.py RULES) and the late window after the line."""

    model_config = ConfigDict(extra="forbid")

    reasons: List[str] = Field(default_factory=list, max_length=20)
    late: Optional[LateWindowReport] = None
    # LMU's lap time arrived after the lap was finalised (corrected by the client), or
    # never matched the measured duration (kept as LMU reported it) — diagnosis only
    lap_time_corrected: Optional[LapTimeCorrection] = None
    lap_time_unconfirmed: Optional[LapTimeUnconfirmed] = None
    sectors_late_s: Optional[float] = Field(default=None, ge=-1, le=600)

    @field_validator("reasons")
    @classmethod
    def _codes(cls, value):
        for code in value:
            if not (1 <= len(code) <= 40) or not all(c.islower() or c == "_" for c in code):
                raise ValueError("reason codes are short lower-case identifiers")
        return value


class LapDamage(BaseModel):
    """Body damage at the end of the lap, as LMU reports it."""

    model_config = ConfigDict(extra="forbid")

    dent_max: int = Field(ge=0, le=255)
    overheating: StrictBool = False
    detached: StrictBool = False


class LapContext(BaseModel):
    """The car and the session at the end of the lap (client/telemetry/parser.py
    lap_context): what the Stint Analyzer needs beyond the telemetry file. Every field is
    optional: the client leaves out whatever LMU did not report."""

    model_config = ConfigDict(extra="forbid")

    pitstops: Optional[int] = Field(default=None, ge=0, le=500)
    place: Optional[int] = Field(default=None, ge=1, le=255)
    compound_front: Optional[int] = Field(default=None, ge=0, le=255)
    compound_rear: Optional[int] = Field(default=None, ge=0, le=255)
    compound_front_name: Optional[str] = Field(default=None, max_length=18)
    compound_rear_name: Optional[str] = Field(default=None, max_length=18)
    fuel_capacity_l: Optional[float] = Field(default=None, ge=0.5, le=500)
    session_left_s: Optional[float] = Field(default=None, ge=0, le=10 * 86400)
    max_laps: Optional[int] = Field(default=None, ge=0, le=100000)
    damage: Optional[LapDamage] = None

    @field_validator("compound_front_name", "compound_rear_name")
    @classmethod
    def _printable(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = "".join(ch for ch in value if ch.isprintable()).strip()
        return value or None

    @field_validator("fuel_capacity_l", "session_left_s")
    @classmethod
    def _finite_number(cls, value: Optional[float]) -> Optional[float]:
        if value is not None and not math.isfinite(value):
            raise ValueError("must be a finite number")
        return value


class GridVehicle(BaseModel):
    """One car the client saw in the session (server/catalog/car_identity.py)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    veh_file: str = Field(min_length=1, max_length=64)
    model: Optional[str] = Field(default=None, max_length=64)
    vehicle_class: Optional[str] = Field(default=None, max_length=64, alias="class")


class LapMetadata(BaseModel):
    """Validates envelope.metadata from an upload before any of it reaches
    the DB. Field checks per the pre-launch review:
      - lap_time: > 0 and under a realistic ceiling
      - lap_number, sample_count: >= 0
      - sector_times: only numbers, each within a realistic range
      - track_name, car_name, telemetry_file: length-limited, non-blank
      - is_valid: an actual boolean (StrictBool), not "truthy" 0/1/"yes"
      - extra="forbid": an upload with a field this model doesn't know
        about is rejected outright, rather than silently ignored — a
        client/server version mismatch should be loud, not silently drop
        data on the floor
      - every float is explicitly checked with math.isfinite(): most of
        them are already indirectly protected by having both an upper and
        lower Field bound (any comparison with NaN is False, so NaN fails
        every gt/ge/lt/le check; ±inf fails whichever bound is on the
        infinite side) — but a field with only one-sided bounds does NOT
        catch the open side (started_at: Field(ge=0) lets +inf through,
        since inf >= 0 is true), and the old sector_times validator's
        `item < 0 or item > MAX` check let NaN through entirely (both
        comparisons are False). Explicit isfinite() closes both gaps
        instead of relying on Field bounds' coincidental side effects.

    Deliberately validated *after* the HMAC signature check in
    routers/telemetry.py, not before: an unauthenticated request shouldn't
    get detailed field-by-field feedback about what's wrong with a payload
    it never proved it was allowed to send.
    """

    model_config = ConfigDict(extra="forbid")

    track_name: str = Field(min_length=1, max_length=200)
    car_name: str = Field(min_length=1, max_length=200)
    # Optional (not Optional=required-absent-on-old-clients): a client
    # older than V0.6.3 won't send these at all, and that must not break
    # its uploads — see uploader.py's _SERVER_FIELDS allowlist, which is
    # what actually determines whether a field is sent, not this schema.
    car_class: Optional[str] = Field(default=None, max_length=100)
    car_model: Optional[str] = Field(default=None, max_length=100)
    # rF2-style session type id — not a documented enum in this codebase,
    # so this is a sanity bound (rejects garbage), not a strict allowlist.
    session_type: int = Field(ge=-10, le=50)
    lap_number: int = Field(ge=0)
    lap_time: float = Field(gt=0, le=MAX_LAP_TIME_SECONDS)
    sector_times: List[float]
    is_valid: StrictBool
    started_at: float = Field(ge=0, le=MAX_LAP_TIME_SECONDS * 24)  # elapsed race time, generously bounded
    recorded_at: int = Field(ge=0)
    ambient_temp: Optional[float] = Field(default=None, ge=-50, le=100)
    track_temp: Optional[float] = Field(default=None, ge=-50, le=150)
    sample_count: Optional[int] = Field(default=None, ge=0)
    telemetry_file: str = Field(min_length=1, max_length=255)
    # V0.9.3 (all optional — older clients send none of them)
    vehicle_model: Optional[str] = Field(default=None, max_length=100)
    tail_samples: Optional[int] = Field(default=None, ge=0, le=1000)
    track_grip_level: Optional[int] = Field(default=None, ge=0, le=4)
    fixed_setup: Optional[StrictBool] = None
    track_limits_steps_per_point: Optional[int] = Field(default=None, ge=0, le=255)
    track_limits_steps_per_penalty: Optional[int] = Field(default=None, ge=0, le=255)
    game_lap_state: Optional[GameLapState] = None
    grid_vehicles: Optional[List[GridVehicle]] = Field(default=None, max_length=128)
    # V0.9.3.1
    lap_report: Optional[LapReport] = None
    # V0.9.4
    lap_context: Optional[LapContext] = None

    @field_validator("track_name", "car_name", "telemetry_file")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("car_class", "car_model", "vehicle_model")
    @classmethod
    def _blank_to_none(cls, value: Optional[str]) -> Optional[str]:
        # Unlike track_name/car_name, a blank car_class/car_model isn't an
        # error — LMU can genuinely report an empty vehicle_class or
        # veh_filename for some cars/mods. Treat blank the same as absent
        # rather than rejecting the whole upload over it.
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("lap_time", "started_at", "ambient_temp", "track_temp")
    @classmethod
    def _finite(cls, value: Optional[float]) -> Optional[float]:
        if value is not None and not math.isfinite(value):
            raise ValueError("must be a finite number (not NaN or Infinity)")
        return value

    @field_validator("sector_times", mode="before")
    @classmethod
    def _validate_sector_times(cls, value: object) -> List[float]:
        if not isinstance(value, list) or not value:
            raise ValueError("must be a non-empty list of numbers")
        if len(value) > MAX_SECTOR_COUNT:
            raise ValueError(f"too many sector times (max {MAX_SECTOR_COUNT})")
        cleaned: List[float] = []
        for item in value:
            # bool is a subclass of int in Python — exclude it explicitly,
            # or `True` would silently pass as sector time 1.0.
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise ValueError("sector_times must contain only numbers")
            item = float(item)
            # Explicit isfinite() check BEFORE the range check below: NaN
            # compares False against both `< 0` and `> MAX`, so a range
            # check alone would silently let a NaN sector time through —
            # this was a real gap, not a hypothetical one.
            if not math.isfinite(item):
                raise ValueError("sector_times must not contain NaN or Infinity")
            if item < 0 or item > MAX_LAP_TIME_SECONDS:
                raise ValueError("sector time out of realistic range")
            cleaned.append(item)
        return cleaned


class UploadResponse(BaseModel):
    status: str
    lap_id: int
    duplicate: bool = False


class LapSummary(BaseModel):
    id: int
    track_name: str
    car_name: str
    session_type: int
    lap_number: int
    lap_time: float
    sector_times: List[float]
    is_valid: bool
    invalid_reason: Optional[List[str]] = None
    ambient_temp: Optional[float] = None
    track_temp: Optional[float] = None
    uploaded_at: str
    # "full" or "basic" — whether the lap was recorded by a client that
    # writes the extended channels (see server/core/versioning.py). Lists only; the
    # analysis decides from the file itself.
    data_level: Optional[str] = None
    # Car model identity and the server's track-limits verdict
    car_model: Optional[str] = None
    validation_status: Optional[str] = None
    review_status: Optional[str] = None


class LeaderboardEntry(BaseModel):
    # The id of the ranked lap itself, so the lap-analysis view can
    # open exactly the lap a leaderboard row shows. Optional only so a client
    # that builds this model without it (older tests/tools) keeps working.
    lap_id: Optional[int] = None
    driver_id: int
    driver_name: str
    lap_time: float
    sector_times: List[float]
    is_valid: bool
    uploaded_at: str
    data_level: Optional[str] = None  # See LapSummary.data_level


class RecentActivityEntry(BaseModel):
    """GET /leaderboard/recent — the most recent
    valid laps platform-wide. Same trust level as the leaderboard itself
    (public, driver names already shown there) — nothing new is exposed,
    just a different, time-ordered slice of the same Lap data."""

    driver_id: int
    driver_name: str
    track_name: str
    car_name: str
    lap_time: float
    uploaded_at: str
    # The lap behind the entry (link to the analysis, one row per lap) and the car model
    lap_id: Optional[int] = None
    car_model: Optional[str] = None
    # The records this lap set ("WR" = Garage16 Best, "TEAM_BEST", "PR"), most important first
    records: List[str] = []


class LapReportRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class AdminLapSummary(BaseModel):
    """Enough to actually judge a lap without a second round-trip — used
    both standalone (GET /admin/laps/{id}) and nested in LapReportOut."""

    id: int
    driver_id: int
    driver_name: str
    track_name: str
    car_name: str
    car_class: Optional[str] = None
    car_model: Optional[str] = None
    lap_time: float
    is_valid: bool
    invalid_reason: Optional[List[str]] = None
    client_claimed_valid: bool
    uploaded_at: str
    # The server's track-limits verdict (server/laps/track_limits.py) and its review
    validation: Optional[dict] = None
    review_status: Optional[str] = None
    car_model_raw: Optional[str] = None
    car_identity_source: Optional[str] = None


class LapReportOut(BaseModel):
    """Response for a driver's own POST /telemetry/laps/{id}/report — just
    confirms what they filed. See AdminLapReportOut for the enriched
    admin-review shape (different endpoint, different audience)."""

    id: int
    lap_id: int
    reported_by_driver_id: int
    reason: str
    created_at: str
    resolved_at: Optional[str] = None
    resolution: Optional[str] = None


class AdminLapReportOut(BaseModel):
    """GET /admin/reports — the moderation view, with enough about the
    reported lap and reporter to actually judge the report without a
    second round-trip per row."""

    id: int
    lap: AdminLapSummary
    reported_by_driver_id: int
    reported_by_display_name: str
    reason: str
    created_at: str
    resolved_at: Optional[str] = None
    resolution: Optional[str] = None


class AdminDriverOut(BaseModel):
    """GET /admin/drivers — includes email, unlike every public/driver-
    facing schema in this project (DriverProfileOut, DriverLookupOut):
    this endpoint is gated by the operator admin token, a different and
    higher trust boundary than a driver's own session, and an admin
    plausibly needs it for a support/moderation contact. Never returned
    from anything a driver or the public can reach."""

    driver_id: int
    display_name: str
    email: Optional[str] = None
    is_locked: bool
    locked_reason: Optional[str] = None
    total_laps: int


class AdminInvalidateRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class AdminLockRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class AdminReviewDecision(BaseModel):
    """POST /admin/laps/{id}/review: the admin's decision on a lap the
    track-limits check could not decide on its own."""

    decision: str = Field(pattern="^(clear|invalidate)$")
    note: Optional[str] = Field(default=None, max_length=500)


class RaceRules(BaseModel):
    """What a race prescribes. Everything optional except what makes the calculator work at
    all; values the admin does not know stay empty and the calculator says so instead of
    assuming them."""

    model_config = ConfigDict(extra="forbid")

    race_laps: Optional[int] = Field(default=None, ge=1, le=10000)
    race_minutes: Optional[float] = Field(default=None, gt=0, le=48 * 60)
    min_pit_stops: int = Field(default=0, ge=0, le=100)
    max_stint_laps: Optional[int] = Field(default=None, ge=1, le=10000)
    max_stint_minutes: Optional[float] = Field(default=None, gt=0, le=48 * 60)
    mandatory_tyre_change: StrictBool = False
    tyre_sets: Optional[int] = Field(default=None, ge=1, le=100)
    refuelling: StrictBool = True
    refuel_l_per_s: Optional[float] = Field(default=None, gt=0, le=50)
    tyre_change_s: Optional[float] = Field(default=None, ge=0, le=600)
    min_stop_s: Optional[float] = Field(default=None, ge=0, le=3600)
    pit_lane_loss_s: Optional[float] = Field(default=None, ge=0, le=600)
    fuel_margin_laps: float = Field(default=1.0, ge=0, le=20)
    notes: Optional[str] = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _length(self):
        if self.race_laps is None and self.race_minutes is None:
            raise ValueError("a race length is needed: race_laps or race_minutes")
        return self


class RaceRuleSetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    description: Optional[str] = Field(default=None, max_length=500)
    track_name: Optional[str] = Field(default=None, max_length=200)
    car_class: Optional[str] = Field(default=None, max_length=64)
    rules: RaceRules

    @field_validator("name", "description", "track_name", "car_class")
    @classmethod
    def _strip(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        return value or None


class RaceRuleSetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: Optional[str] = None
    track_name: Optional[str] = None
    car_class: Optional[str] = None
    rules: dict
