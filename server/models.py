from __future__ import annotations

from datetime import datetime, timezone


def _utcnow() -> datetime:
    """Timezone-aware UTC now — replaces the deprecated datetime.utcnow()
    (removed in Python 3.12+)."""
    return datetime.now(timezone.utc)

from sqlalchemy import (
    DDL,
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.orm import relationship

from .database import Base


class Driver(Base):
    __tablename__ = "drivers"

    id = Column(Integer, primary_key=True)
    display_name = Column(String, nullable=False)
    # Optional (V0.6.9): a driver can exist token-only forever (the
    # desktop client never needs a password — see routers/accounts.py's
    # login()) or add email+password for web login and self-service
    # password reset. Normalized lowercase before storage (see
    # routers/accounts.py) — case-insensitive uniqueness relies on that,
    # not on a DB-level constraint, same tradeoff SQLite/Postgres both
    # already accept for token_hash below.
    email = Column(String, unique=True, nullable=True, index=True)
    # V0.8-NAS §2: does NOT gate login (see routers/accounts.py's login()
    # docstring) — a driver who set an email before this feature existed
    # isn't retroactively locked out. Purely informational/nudge for now:
    # Account Settings shows a "please verify" prompt when False.
    email_verified = Column(Boolean, nullable=False, default=False)
    email_verified_at = Column(DateTime, nullable=True)
    # Argon2id hash (server/security.py) — never the raw password.
    password_hash = Column(String, nullable=True)
    token_hash = Column(String, unique=True, nullable=False, index=True)
    # Non-null once the token has been revoked (POST /accounts/revoke, or
    # superseded by a rotation) — get_current_driver() rejects any request
    # authenticated with a token whose driver has this set. This is what
    # makes "someone got hold of a token" recoverable rather than
    # permanent: revoke it, rotate to a new one, done.
    token_revoked_at = Column(DateTime, nullable=True)
    # Encrypted at rest (server/crypto.py) — not the raw HMAC key. Decrypt
    # on demand only where the signing key is actually needed (upload
    # verification, or handing it back once at registration/rotation
    # time), never store/log the decrypted value.
    # Moderation: distinct from token_revoked_at (self-service, "my token
    # leaked"). is_locked is an admin action — get_current_driver() checks
    # both, but they're separate levers with separate audit trails.
    is_locked = Column(Boolean, nullable=False, default=False)
    locked_reason = Column(String, nullable=True)
    client_secret = Column(String, nullable=False)
    discord_user_id = Column(String, unique=True, nullable=True, index=True)
    created_at = Column(DateTime, default=_utcnow)

    laps = relationship("Lap", back_populates="driver")


class Lap(Base):
    __tablename__ = "laps"
    __table_args__ = (
        # A retried upload of the same lap (same driver, same telemetry bytes)
        # should not create a second row — this is what makes upload_lap idempotent.
        UniqueConstraint("driver_id", "telemetry_hash", name="uq_driver_telemetry_hash"),
        # V0.9.3.1: a lap's neighbours (the lap before/after it of the same driver) — see
        # server/lap_check.py link_neighbours
        Index("ix_laps_driver_track_lapno", "driver_id", "track_name", "lap_number"),
    )

    id = Column(Integer, primary_key=True)
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=False, index=True)

    track_name = Column(String, nullable=False, index=True)
    car_name = Column(String, nullable=False, index=True)
    # From VehicleScoringInfoV01.vehicle_class / .veh_filename (LMU shared
    # memory, ABI-verified offsets — see client/lmu/structs.py). Distinct
    # from car_name/vehicle_name, which includes team, livery and car
    # number — car_class/car_model don't. Nullable: laps uploaded by
    # clients older than V0.6.3 won't have these.
    #   - car_class: leaderboard grouping for the MAIN leaderboard
    #     (track + class, e.g. "Hypercar" / "LMGT3") — see
    #     routers/leaderboard.py's class_leaderboard().
    #   - car_model: leaderboard grouping for the SUB-leaderboard
    #     (track + exact car model, ignoring team/number/livery) — see
    #     routers/leaderboard.py's car_leaderboard().
    car_class = Column(String, nullable=True, index=True)
    # V0.8.9: car_class above holds the READABLE class name ("Hyper" ->
    # "Hypercar", see server/catalog.py); the string LMU actually sent is kept
    # here so a mapping mistake can always be corrected from the source.
    car_class_raw = Column(String, nullable=True)
    # V0.9.3: car_model is the car's MODEL identity (server/car_identity.py) — every
    # livery, team and car number of one model shares it. What LMU actually sent is
    # kept next to it, so the identity can always be re-derived:
    #   car_model_raw       VehicleScoringInfoV01.veh_filename (what car_model held up to 0.9.2)
    #   car_vehicle_model   TelemInfoV01.mVehicleModel, LMU's "brand & model" name (0.9.3+ clients)
    #   car_identity_source how car_model was decided: vehicle_model | alias | veh_file
    car_model = Column(String, nullable=True, index=True)
    car_model_raw = Column(String, nullable=True)
    car_vehicle_model = Column(String, nullable=True)
    car_identity_source = Column(String, nullable=True, index=True)
    session_type = Column(Integer, nullable=False)
    lap_number = Column(Integer, nullable=False)
    lap_time = Column(Float, nullable=False, index=True)
    sector_times = Column(JSON, nullable=False)
    # AUTHORITATIVE validity, used everywhere (records.py, leaderboard.py):
    # the server's own plausibility verdict (server/validation.py), not a
    # value taken on faith from the client. See client_claimed_valid below.
    is_valid = Column(Boolean, nullable=False)
    # What the desktop client itself believed at upload time — kept purely
    # for audit/debugging (e.g. spotting a client-side bug, or a lap the
    # server disagrees with) and never consulted for leaderboard/record
    # decisions.
    client_claimed_valid = Column(Boolean, nullable=False, default=True)
    # Why is_valid is False (validator failure reasons, or a moderator's
    # note for an admin-invalidated lap). None when is_valid is True.
    invalid_reason = Column(JSON, nullable=True)
    started_at = Column(Float, nullable=False)
    recorded_at = Column(Integer, nullable=False)
    ambient_temp = Column(Float, nullable=True)
    track_temp = Column(Float, nullable=True)
    sample_count = Column(Integer, nullable=True)

    # V0.9.3 — the server's own track-limits verdict (server/track_limits.py), with every
    # finding (what, where on the lap, from which signal). {"version", "status",
    # "findings": [...], "signals": [...]}. NULL for laps checked before V0.9.3.
    validation = Column(JSON, nullable=True)
    # V0.9.3 — admin review of laps the check could not decide on its own:
    # NULL (nothing to review) | "pending" | "cleared" | "invalidated"
    # V0.9.9: | "held" (REVIEW: stored, not official until an admin clears it)
    #         | "quarantined" (the file failed its integrity check — server/integrity.py)
    # "pending" keeps its V0.9.3 meaning (counts provisionally) for laps judged before V0.9.9.
    review_status = Column(String, nullable=True, index=True)
    # V0.9.3 — compact per-lap figures computed once at upload (server/lap_insights.py):
    # fuel/energy/tyre use, input shares, peaks, and the time profile behind the
    # theoretical best lap. Lists and statistics read this instead of the lap files.
    lap_stats = Column(JSON, nullable=True)
    # V0.9.3.1 — everything the verdict depends on that is NOT in the telemetry file: the
    # client's yes/no and reasons, its game summary, the tail samples, the late window after
    # the line, LMU's step/point ratios (server/lap_check.py). Lets
    # scripts/revalidate_laps.py judge a stored lap again with the current rules.
    check_inputs = Column(JSON, nullable=True)
    # V0.9.4 — the car and the session at the end of the lap (pit stops so far, tyre compound,
    # damage, tank size, position, time left; schemas.LapContext). For the Stint Analyzer
    # (server/stints.py). NULL for laps recorded by older clients.
    lap_context = Column(JSON, nullable=True)

    telemetry_hash = Column(String, nullable=False, index=True)
    telemetry_path = Column(String, nullable=False)
    client_version = Column(String, nullable=True)
    sent_at = Column(Float, nullable=True)
    uploaded_at = Column(DateTime, default=_utcnow)
    # V0.9.9 — the received original (server/integrity.py): its size in bytes as received, the
    # request id of the upload that stored it, and the server's MAC over (lap id, driver, sha256,
    # size, upload time) with the integrity key. NULL for laps stored before V0.9.9 until
    # scripts/integrity_check.py --backfill has verified the file and filled them in.
    telemetry_bytes = Column(BigInteger, nullable=True)
    upload_request_id = Column(String(64), nullable=True)
    integrity_mac = Column(String(120), nullable=True)

    driver = relationship("Driver", back_populates="laps")


class Team(Base):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True)
    # No unique=True/index=True here — uniqueness is enforced by the
    # case-insensitive functional index below instead (migration 0008),
    # so a plain-name index would just be redundant with it.
    name = Column(String, nullable=False)
    invite_code = Column(String, nullable=False, unique=True, index=True)
    description = Column(String, nullable=True)
    # Per-team opt-out for TEAM_BEST Discord announcements — owner/admin
    # editable via PATCH /teams/{id} (V0.6.8). Defaults True; a team that
    # never touches this keeps the pre-existing announce-everything
    # behavior. See discord_bot/bot.py's _post_pending_events().
    discord_announcements_enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=_utcnow)

    memberships = relationship("TeamMembership", back_populates="team")


# Case-insensitive uniqueness (V0.7.2, migration 0008) — "Garage16" and
# "garage16" must collide. Declared here (not as index=True on the column
# above) so dev/test's Base.metadata.create_all reproduces the exact same
# constraint the migration creates in production; both use lower(name)
# rather than either relying only on the ORM-level check in
# routers/teams.py's create_team()/rename logic, per the explicit "Nicht
# nur im Python-Code" requirement.
Index("ix_teams_name_lower", func.lower(Team.name), unique=True)


class TeamMembership(Base):
    __tablename__ = "team_memberships"
    __table_args__ = (UniqueConstraint("team_id", "driver_id", name="uq_team_driver"),)

    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False, index=True)
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=False, index=True)
    # "owner" | "admin" | "member" — see server/routers/teams.py's
    # permission helpers (V0.6.8). Exactly one "owner" per team, enforced
    # in application logic (create_team sets it; transfer-ownership moves
    # it; nothing else can create a second one).
    role = Column(String, nullable=False, default="member")
    joined_at = Column(DateTime, default=_utcnow)

    team = relationship("Team", back_populates="memberships")
    driver = relationship("Driver")


class DiscordChannel(Base):
    __tablename__ = "discord_channels"

    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True, index=True)
    guild_id = Column(String, nullable=False, index=True)
    channel_id = Column(String, nullable=False, unique=True, index=True)
    registered_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    team = relationship("Team")


class LinkCode(Base):
    __tablename__ = "link_codes"

    id = Column(Integer, primary_key=True)
    code = Column(String, nullable=False, unique=True, index=True)
    kind = Column(String, nullable=False)
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)


class Session(Base):
    """A web-login session (V0.7.2), created by POST /accounts/login and
    presented back as an HttpOnly cookie — see server/sessions.py. This is
    a completely separate credential domain from Driver.token_hash /
    client_secret (the desktop client's own credentials, see security.py's
    module docstring): logging into the web app must never be able to
    invalidate a driver's running LMU client, and vice versa. Only the
    hash is stored, same principle as Driver.token_hash and
    PasswordResetToken.token_hash."""

    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True)
    session_token_hash = Column(String, unique=True, nullable=False, index=True)
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=_utcnow)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    # Best-effort, display-only (account "Sessions" list) — never used for
    # any security decision (trivially spoofable by the client).
    user_agent = Column(String, nullable=True)

    driver = relationship("Driver")


class TeamInvitation(Base):
    """A real, stateful team invitation (V0.7.2 §9.2) — distinct from
    Team.invite_code (a link/code anyone who has it can redeem). This is
    the opposite direction: a team owner/admin names a SPECIFIC existing
    driver, who then explicitly accepts or declines.

    Addressed by invited_driver_id only — deliberately NOT an
    invited_email option, even though the spec's own data model
    mentions "invited_driver_id / invited_email" as an either/or. Reason:
    email is private (§3.11 — "Email darf niemals auf öffentlichen
    Profilen erscheinen"), so a driver_id-only design is the one that
    doesn't need to expose anyone's email to use. An email-based invite
    to someone who doesn't have an account yet would need its own
    token-and-registration-linking flow (real added complexity, similar
    in shape to PasswordResetToken) for a small-community platform where
    the existing Team.invite_code link already covers "invite someone
    without an account yet" perfectly well. If that changes, this is
    where a nullable invited_email column would get added later —
    additive, not a rework of what's here.

    status: pending -> accepted | declined | revoked, or pending ->
    expired (computed lazily against expires_at wherever a pending
    invitation is read — see routers/teams.py — not a background job;
    same pattern this project already uses for PasswordResetToken and
    Team.invite_code-adjacent LinkCode).
    """

    __tablename__ = "team_invitations"

    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False, index=True)
    invited_driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=False, index=True)
    created_by_driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=False)
    status = Column(String, nullable=False, default="pending")
    created_at = Column(DateTime, default=_utcnow)
    expires_at = Column(DateTime, nullable=False)
    accepted_at = Column(DateTime, nullable=True)
    declined_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)

    team = relationship("Team")
    invited_driver = relationship("Driver", foreign_keys=[invited_driver_id])
    created_by = relationship("Driver", foreign_keys=[created_by_driver_id])


class EmailVerificationToken(Base):
    """V0.8-NAS §2. Same hashed-at-rest, single-use, expiring pattern as
    PasswordResetToken — deliberately not reusing that table, since a
    verification token and a password-reset token grant very different
    things (proving you can read an inbox vs. taking over the account)
    and mixing their lifecycles would be confusing to reason about.

    `email` is captured at request time, not read from Driver.email at
    confirm time: if the driver changes their email again before
    confirming an older token, that stale token must verify nothing —
    comparing against the row it was issued for (not whatever the row
    says now) is what makes that safe."""

    __tablename__ = "email_verification_tokens"

    id = Column(Integer, primary_key=True)
    token_hash = Column(String, unique=True, nullable=False, index=True)
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=False, index=True)
    email = Column(String, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)

    driver = relationship("Driver")


class PasswordResetToken(Base):
    """Forgot-password flow (V0.6.9). Deliberately a separate table from
    LinkCode: LinkCode's `code` is stored in PLAINTEXT because it's a
    short, human-typed, low-value code read out loud for a Discord slash
    command. A password reset token grants account takeover if leaked, so
    per the spec it must never sit in the DB in plaintext — this table
    stores only its hash (security.py's hash_token/generate_token, the
    same functions already used for auth tokens — no new crypto
    primitive introduced for this)."""

    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True)
    token_hash = Column(String, unique=True, nullable=False, index=True)
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=_utcnow)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)

    driver = relationship("Driver")


class RecordEvent(Base):
    __tablename__ = "record_events"

    id = Column(Integer, primary_key=True)
    lap_id = Column(Integer, ForeignKey("laps.id"), nullable=False, index=True)
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=False, index=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True, index=True)
    record_type = Column(String, nullable=False)
    track_name = Column(String, nullable=False)
    car_name = Column(String, nullable=False)
    lap_time = Column(Float, nullable=False)
    previous_best = Column(Float, nullable=True)
    created_at = Column(DateTime, default=_utcnow, index=True)
    announced_at = Column(DateTime, nullable=True)

    lap = relationship("Lap")
    driver = relationship("Driver")
    team = relationship("Team")


class LapReport(Base):
    """A driver flagging another lap as suspicious. Purely advisory —
    doesn't change is_valid by itself; an admin reviews and acts via the
    /admin router (invalidate the lap, resolve the report either way)."""

    __tablename__ = "lap_reports"

    id = Column(Integer, primary_key=True)
    lap_id = Column(Integer, ForeignKey("laps.id"), nullable=False, index=True)
    reported_by_driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=False)
    reason = Column(String, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    resolved_at = Column(DateTime, nullable=True)
    resolution = Column(String, nullable=True)

    lap = relationship("Lap")
    reported_by = relationship("Driver")


class CarAlias(Base):
    """V0.9.3 — a veh_filename group (one model, one season; see
    server/car_identity.py fallback_identity) and the model LMU reports for it.

    Learned automatically from uploads: the lap's own car and every car the client saw
    in the session. `conflict` is set when the game reported two different models for
    the same key — such a key is then never used to group laps."""

    __tablename__ = "car_aliases"

    id = Column(Integer, primary_key=True)
    alias_key = Column(String, nullable=False, unique=True, index=True)
    canonical = Column(String, nullable=False)
    source = Column(String, nullable=False)          # "lap" | "grid"
    conflict = Column(Boolean, nullable=False, default=False)
    conflict_with = Column(String, nullable=True)
    seen_count = Column(Integer, nullable=False, default=1)
    rules_version = Column(Integer, nullable=False, default=1)
    first_seen = Column(DateTime, default=_utcnow)
    last_seen = Column(DateTime, default=_utcnow)


class RaceRuleSet(Base):
    """V0.9.4 — a race's rules for the Stint Analyzer's strategy calculator (minimum pit
    stops, maximum stint length, mandatory tyre change, refuelling, pit lane figures), kept by
    an admin. `rules` is validated by schemas.RaceRules; nothing is assumed beyond it."""

    __tablename__ = "race_rule_sets"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    description = Column(String, nullable=True)
    track_name = Column(String, nullable=True, index=True)
    car_class = Column(String, nullable=True)
    rules = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class UploadEvent(Base):
    """V0.9.7 — one row per upload attempt: how it ended and why. The operations view
    (GET /admin/operations) reads error rates, reasons and slow uploads from it; the request id
    is the same one the response carries (X-Request-ID) and the server log line shows, so one
    failed upload can be followed from the client's log to the server's.

    No personal data beyond the driver id (deleted with the driver, server/driver_lifecycle.py);
    rows older than UPLOAD_EVENT_RETENTION_DAYS are removed on their own."""

    __tablename__ = "upload_events"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=_utcnow, index=True)
    request_id = Column(String, nullable=True, index=True)
    driver_id = Column(Integer, nullable=True, index=True)
    lap_id = Column(Integer, nullable=True)
    outcome = Column(String, nullable=False)          # accepted | duplicate | rejected | error
    status_code = Column(Integer, nullable=False)
    reason = Column(String, nullable=True)            # short machine-readable reason / message
    client_version = Column(String, nullable=True)
    track_name = Column(String, nullable=True)
    file_bytes = Column(Integer, nullable=True)
    duration_ms = Column(Float, nullable=True)


class AuditEvent(Base):
    """V0.9.9 — integrity, validation and admin events (server/audit.py), chained: every row's
    `mac` is an HMAC (integrity key) over the previous row's mac and this row's content, so a row
    changed, removed or inserted afterwards breaks the chain (scripts/integrity_check.py --audit).
    `driver_id` is deliberately NOT covered by the chain: it is personal data and is set to NULL
    when the driver is deleted (server/driver_lifecycle.py). A database trigger refuses any other
    UPDATE (migration 0017 and the DDL below). Limits: whoever holds the integrity key AND can write
    the database can rewrite the whole chain — docs/ARCHITECTURE.md."""

    __tablename__ = "audit_events"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=_utcnow, index=True)
    kind = Column(String(40), nullable=False, index=True)
    actor = Column(String(20), nullable=False)        # system | admin | script
    lap_id = Column(Integer, nullable=True, index=True)
    driver_id = Column(Integer, nullable=True, index=True)
    request_id = Column(String(64), nullable=True, index=True)
    detail = Column(JSON, nullable=True)
    prev_mac = Column(String(120), nullable=True)
    mac = Column(String(120), nullable=False)


# The same guard migration 0017 installs, for databases created from the models (tests, dev).
_AUDIT_TRIGGER_SQLITE = DDL(
    "CREATE TRIGGER IF NOT EXISTS audit_events_no_update BEFORE UPDATE ON audit_events "
    "WHEN NEW.id IS NOT OLD.id OR NEW.created_at IS NOT OLD.created_at OR NEW.kind IS NOT OLD.kind "
    "OR NEW.actor IS NOT OLD.actor OR NEW.lap_id IS NOT OLD.lap_id OR NEW.request_id IS NOT OLD.request_id "
    "OR NEW.detail IS NOT OLD.detail OR NEW.prev_mac IS NOT OLD.prev_mac OR NEW.mac IS NOT OLD.mac "
    "OR (NEW.driver_id IS NOT NULL AND NEW.driver_id IS NOT OLD.driver_id) "
    "BEGIN SELECT RAISE(ABORT, 'audit_events rows are append-only'); END"
)
_AUDIT_TRIGGER_PG_FN = DDL(
    "CREATE OR REPLACE FUNCTION audit_events_no_update() RETURNS trigger AS $$ BEGIN "
    "IF NEW.id IS DISTINCT FROM OLD.id OR NEW.created_at IS DISTINCT FROM OLD.created_at "
    "OR NEW.kind IS DISTINCT FROM OLD.kind OR NEW.actor IS DISTINCT FROM OLD.actor "
    "OR NEW.lap_id IS DISTINCT FROM OLD.lap_id OR NEW.request_id IS DISTINCT FROM OLD.request_id "
    "OR NEW.detail::text IS DISTINCT FROM OLD.detail::text OR NEW.prev_mac IS DISTINCT FROM OLD.prev_mac "
    "OR NEW.mac IS DISTINCT FROM OLD.mac "
    "OR (NEW.driver_id IS NOT NULL AND NEW.driver_id IS DISTINCT FROM OLD.driver_id) THEN "
    "RAISE EXCEPTION 'audit_events rows are append-only'; END IF; RETURN NEW; END; $$ LANGUAGE plpgsql"
)
_AUDIT_TRIGGER_PG = DDL(
    "CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events "
    "FOR EACH ROW EXECUTE FUNCTION audit_events_no_update()"
)
event.listen(AuditEvent.__table__, "after_create", _AUDIT_TRIGGER_SQLITE.execute_if(dialect="sqlite"))
event.listen(AuditEvent.__table__, "after_create", _AUDIT_TRIGGER_PG_FN.execute_if(dialect="postgresql"))
event.listen(AuditEvent.__table__, "after_create", _AUDIT_TRIGGER_PG.execute_if(dialect="postgresql"))
