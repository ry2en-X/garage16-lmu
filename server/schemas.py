from __future__ import annotations

import math
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

# Generous but real ceiling — long enough to never reject a genuine lap
# (including slow out-laps under yellow), tight enough to catch garbage
# values (e.g. a stray timestamp landing in the lap_time field).
MAX_LAP_TIME_SECONDS = 3600.0
MAX_SECTOR_COUNT = 12


class DriverRegisterResponse(BaseModel):
    driver_id: int
    display_name: str
    auth_token: str
    client_secret: str
    warning: str = (
        "Dev-only credential issuance. auth_token and client_secret are shown "
        "once, here, and not stored anywhere retrievable — save them now. "
        "Replace this endpoint with real account/key management before launch."
    )


class SetPasswordRequest(BaseModel):
    """POST /accounts/set-password — attach email+password to an existing
    (token-only) driver identity, or change the password on one that
    already has them. Deliberately the same endpoint for both: the
    server can already tell them apart (email already set or not) and a
    driver shouldn't need to remember which flow applies to them."""

    email: str = Field(min_length=3, max_length=254)
    new_password: str = Field(min_length=1, max_length=200)  # real strength check in security.py, not here
    # Required only when the driver already has a password set (proves
    # they're not just someone who stole a bearer token trying to lock
    # the real owner out by attaching their own email/password). Omitted
    # for the very first time a token-only driver adds a password.
    # Bounded (V0.8-NAS security pass) for the same reason new_password
    # is: this goes straight into Argon2's verify_password(), and Argon2
    # is deliberately memory/CPU-hard — an unbounded string here would
    # let a caller force an expensive hash over an arbitrarily large
    # payload, a real amplification vector that plain fast hashes
    # (e.g. hash_token's SHA-256) don't share.
    current_password: Optional[str] = Field(default=None, max_length=200)


class DisplayNameUpdateRequest(BaseModel):
    """PATCH /accounts/me — the name shown on leaderboards, profiles, teams and
    in Discord. Until V0.8.9 it was fixed at registration (whatever was typed
    into the 'New driver' box, even a throwaway "Test") and could never change."""

    display_name: str = Field(min_length=1, max_length=60)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)


class WhoAmIOut(BaseModel):
    """GET /accounts/me — the only thing missing before V0.7.2's dashboard
    rework: a way to resolve "my own driver_id" from just a valid
    credential (bearer token OR session cookie), for the one case that
    didn't already have it — a session restored from a pasted-in
    auth_token (see web/js/pages/auth.js), which never learns driver_id
    any other way. Deliberately minimal — this is identity resolution,
    not a profile; DriverProfileOut (the public one) already covers
    "everything about a driver" for driver_id once known.

    email/email_verified (V0.8-NAS §2) stay within that same "who am I"
    spirit rather than becoming a second endpoint — Account Settings
    needs exactly this to show a verify-email prompt."""

    driver_id: int
    display_name: str
    email: Optional[str] = None
    email_verified: bool = False


class LoginResponse(BaseModel):
    """No auth_token here (V0.7.2) — login's credential is an HttpOnly
    session cookie the browser holds automatically, not a value the
    frontend needs to see or store. See server/sessions.py."""

    driver_id: int
    display_name: str


class ChangePasswordRequest(BaseModel):
    # Same Argon2-cost reasoning as SetPasswordRequest.current_password.
    current_password: str = Field(max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


class PasswordResetRequestRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class PasswordResetConfirmRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=1, max_length=200)


class EmailVerificationConfirmRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)


class StatusResponse(BaseModel):
    status: str = "ok"


class SessionOut(BaseModel):
    """One row in Account Settings → Sessions (spec §3.9). Deliberately
    excludes the session token itself (obviously) and anything about the
    desktop client's auth_token/client_secret — those aren't sessions in
    this sense, see server/sessions.py."""

    id: int
    created_at: Optional[str] = None
    last_used_at: Optional[str] = None
    expires_at: str
    user_agent: Optional[str] = None
    is_current: bool


class RotateTokenResponse(BaseModel):
    auth_token: str
    warning: str = (
        "Shown once, not stored anywhere retrievable — save it now. Your old "
        "token stopped working immediately."
    )


class RotateSecretResponse(BaseModel):
    client_secret: str
    warning: str = (
        "Shown once, not stored anywhere retrievable — save it now, then run "
        "`python -m client.main --reconfigure` to update the desktop client. "
        "Your old secret stopped working immediately."
    )


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

    @field_validator("track_name", "car_name", "telemetry_file")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("car_class", "car_model")
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


class LeaderboardEntry(BaseModel):
    driver_id: int
    driver_name: str
    lap_time: float
    sector_times: List[float]
    is_valid: bool
    uploaded_at: str


class TeamCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class TeamJoinRequest(BaseModel):
    invite_code: str = Field(min_length=1, max_length=32)


class TeamInvitationCreateRequest(BaseModel):
    """POST /teams/{id}/invitations. Addressed by driver_id, not a name
    or email — see models.TeamInvitation's docstring for why. The web UI
    resolves a display name to a driver_id first via GET
    /drivers/lookup, same as any other caller would."""

    invited_driver_id: int


class TeamInvitationOut(BaseModel):
    id: int
    team_id: int
    team_name: str
    invited_driver_id: int
    invited_display_name: str
    created_by_driver_id: int
    created_by_display_name: str
    status: str  # pending | accepted | declined | expired | revoked
    created_at: str
    expires_at: str
    accepted_at: Optional[str] = None
    declined_at: Optional[str] = None
    revoked_at: Optional[str] = None


class DriverLookupOut(BaseModel):
    """GET /drivers/lookup?display_name=... — the minimal, privacy-safe
    primitive team invitations need (resolve a known display name to a
    driver_id) without building a full driver directory/search feature.
    Never includes email or anything else private — display_name is
    already public (leaderboards, team member lists)."""

    driver_id: int
    display_name: str


class DriverPersonalRecordOut(BaseModel):
    """One row in a driver's public personal-bests list — their own best
    *valid* lap for a given (track, car_model), same grouping
    records.py's PR/WR logic already uses (car_model, not car_name —
    ignores livery/team/number)."""

    track_name: str
    car_name: str
    car_class: Optional[str] = None
    car_model: Optional[str] = None
    lap_time: float
    set_at: str


class DriverActivityItem(BaseModel):
    """One entry in a driver's public recent-activity list. Same shape as
    TeamActivityItem (both reuse RecordEvent, no separate activity-log
    table) but kept as its own schema — this is a per-driver view, not
    scoped to any one team's membership."""

    record_type: str  # "PR" | "WR" | "TEAM_BEST"
    track_name: str
    car_name: str
    lap_time: float
    previous_best: Optional[float] = None
    created_at: str


class DriverTeamOut(BaseModel):
    """A team a driver publicly belongs to — id/name/role only, never the
    team's invite_code (that's a credential, not public information)."""

    team_id: int
    team_name: str
    role: str


class DriverProfileOut(BaseModel):
    """GET /drivers/{id}/public (§4). Deliberately excludes: email,
    password_hash, auth_token/client_secret, session data, is_locked —
    none of those fields are even queried for this endpoint, let alone
    returned (see routers/drivers.py)."""

    driver_id: int
    display_name: str
    total_laps: int
    tracks_driven: int
    cars_driven: int
    personal_records: List[DriverPersonalRecordOut]
    recent_activity: List[DriverActivityItem]
    teams: List[DriverTeamOut]


class TeamOut(BaseModel):
    id: int
    name: str
    invite_code: str
    member_count: int
    role: Optional[str] = None  # the CALLING driver's role on this team — None for /join's response shape reuse


class TeamUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=1000)
    discord_announcements_enabled: Optional[bool] = None


class TeamRoleChangeRequest(BaseModel):
    role: str = Field(pattern="^(admin|member)$")  # "owner" is never set via this endpoint — see transfer-ownership


class TeamTransferOwnershipRequest(BaseModel):
    new_owner_driver_id: int


class TeamDetail(BaseModel):
    """Team-Übersicht — every number computed live from Lap/RecordEvent,
    nothing artificially stored (per the explicit requirement: don't add
    counter columns, derive from existing data)."""

    id: int
    name: str
    description: Optional[str] = None
    invite_code: str
    discord_announcements_enabled: bool
    my_role: str
    member_count: int
    active_driver_count: int  # uploaded at least one lap in the last 14 days
    total_laps: int
    pbs_this_week: int  # RecordEvent record_type="PR" in the last 7 days
    team_best_count: int  # distinct (track, car_model) combos with a valid team lap


class TeamStatsOut(BaseModel):
    """GET /teams/{id}/stats?window=... (V0.7.2 §9.4) — same shape as
    TeamDetail's fixed-window fields, but for a caller-selected window
    instead of the hardcoded 14/7-day ones there (which stay as-is for
    backward compatibility, see that endpoint)."""

    window: str
    laps: int
    pbs: int
    active_drivers: int
    records: int  # TEAM_BEST events for this team created within the window


class TeamMemberOut(BaseModel):
    driver_id: int
    display_name: str
    role: str
    is_active: bool  # uploaded at least one lap in the last 14 days
    lap_count: int
    pb_count: int  # total RecordEvent record_type="PR" ever, for this driver
    last_activity: Optional[str] = None  # ISO timestamp of their most recent lap, if any


class TeamRecordOut(BaseModel):
    """One row per (track, car_model) the team has a valid lap on — the
    team's current best there. Derived live from Lap, same principle as
    the rest of the leaderboard system (no CurrentRecord table).

    your_lap_time/gap (V0.7.2 §21 "You vs Team") are None when the
    caller has no valid lap of their own on this exact (track,
    car_model) — not 0.0, which would misleadingly read as "you tied the
    record". you_hold_it is true exactly when the caller IS the
    record-holder (driver_name matches them), in which case gap is 0.0."""

    track_name: str
    car_class: Optional[str] = None
    car_model: str
    lap_time: float
    driver_name: str
    your_lap_time: Optional[float] = None
    gap: Optional[float] = None
    you_hold_it: bool = False


class TeamActivityItem(BaseModel):
    """One entry in the team's activity console — reuses RecordEvent,
    no separate activity-log table."""

    record_type: str  # "PR" | "WR" | "TEAM_BEST"
    driver_name: str
    track_name: str
    car_name: str
    lap_time: float
    previous_best: Optional[float] = None
    created_at: str


class TeamLeaderboardEntry(BaseModel):
    driver_id: int
    driver_name: str
    lap_time: float
    sector_times: List[float]
    is_valid: bool
    uploaded_at: str
    delta_to_leader: float  # 0.0 for the leader themself


class CatalogLayoutOut(BaseModel):
    track_name: str  # exactly the string LMU sent — this is what leaderboard queries use
    lap_count: int


class CatalogVenueOut(BaseModel):
    """One track with all of its layouts/variants (V0.8.9). `layouts` is empty
    for a known track nobody has driven yet — it is still listed, so every
    track exists in the UI before its first lap."""

    venue: str
    lap_count: int
    layouts: List[CatalogLayoutOut]


class CatalogClassOptionOut(BaseModel):
    name: str
    lap_count: int  # valid laps in this class (at the chosen track, if one was given)


class RecentActivityEntry(BaseModel):
    """GET /leaderboard/recent (V0.7.2 landing page) — the most recent
    valid laps platform-wide. Same trust level as the leaderboard itself
    (public, driver names already shown there) — nothing new is exposed,
    just a different, time-ordered slice of the same Lap data."""

    driver_id: int
    driver_name: str
    track_name: str
    car_name: str
    lap_time: float
    uploaded_at: str


class LinkCodeResponse(BaseModel):
    code: str
    expires_in_seconds: int
    # V0.8.7: the exact text to paste into Discord, so the web UI doesn't
    # have to assemble it (and can't drift from the bot's command name).
    command: Optional[str] = None


class DiscordLinkStatusOut(BaseModel):
    """GET /accounts/discord — deliberately just a flag. The Discord user
    id itself is never sent to the browser (nothing there needs it)."""

    linked: bool


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


class AccountExport(BaseModel):
    """Self-service data export — GDPR/DSGVO Art. 15/20 style 'what do you
    have about me' response. Deliberately does not include raw telemetry
    file contents (those are downloadable separately if needed) — this is
    the structured data the DB actually holds about the driver."""

    driver_id: int
    display_name: str
    created_at: str
    discord_linked: bool
    laps: List[LapSummary]
