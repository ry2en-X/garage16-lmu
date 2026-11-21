"""Request and response bodies of the account, driver-profile and legal endpoints."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from server.laps.schemas import LapSummary


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
    # Bounded for the same reason new_password
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


class SignupRequest(BaseModel):
    """POST /accounts/signup — the website's sign-up form: driver name, email, password, and
    the two confirmations the terms require. Creates the account WITH email and password (the
    desktop client then signs in with them, POST /accounts/desktop-login), so nobody has to copy
    tokens any more."""

    display_name: str = Field(min_length=1, max_length=60)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)   # strength: security.py
    accept_terms: bool = False      # terms of use + privacy policy read and accepted
    confirm_min_age: bool = False   # at least GARAGE16_MIN_AGE years old


class SignupResponse(BaseModel):
    """POST /accounts/signup. The same answer whether the address is new or already has an
    account — the difference only goes to that mailbox (a confirmation link, or "you already have an
    account"), so the form cannot be used to find out who is registered. The browser is not signed in:
    the driver confirms the email first and then signs in."""

    status: str = "check_email"


class DesktopLoginRequest(BaseModel):
    """POST /accounts/desktop-login — the desktop client's "Connect" with email + password.

    `device_name` (the computer's name, shown in Account → Connected PCs), `accept_terms`
    (the client's checkbox when the terms changed since the driver last accepted them) and the
    client's version. Clients 1.0.0 send none of them."""

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)
    device_name: Optional[str] = Field(default=None, max_length=80)
    accept_terms: bool = False
    client_version: Optional[str] = Field(default=None, max_length=32)


class AcceptTermsRequest(BaseModel):
    """POST /accounts/accept-terms — accounts from before V1.0, or after the terms changed."""

    accept_terms: bool = False
    confirm_min_age: bool = False


class DeviceOut(BaseModel):
    """One connected PC (Account → Connected PCs, V1.0.1). Never the token itself."""

    id: int
    name: str
    created_at: Optional[str] = None
    last_used_at: Optional[str] = None
    is_current: bool = False


class DesktopLoginResponse(BaseModel):
    """The desktop client's credentials. The auth token belongs to THIS PC (other connected
    PCs keep theirs, up to GARAGE16_MAX_DEVICES); the client secret is the account's existing one."""

    driver_id: int
    display_name: str
    auth_token: str
    client_secret: str


class OperatorOut(BaseModel):
    name: str
    address: List[str] = []
    email: Optional[str] = None


class LegalInfoOut(BaseModel):
    """GET /legal — what the legal pages fill in: the operator (from .env; null when not
    configured), the minimum age, the version of the terms and whether anybody may sign up."""

    operator: Optional[OperatorOut] = None
    min_age: int
    terms_version: str
    registration: str               # "open" | "secret" | "development"


class WhoAmIOut(BaseModel):
    """GET /accounts/me — the only thing missing before V0.7.2's dashboard
    rework: a way to resolve "my own driver_id" from just a valid
    credential (bearer token OR session cookie), for the one case that
    didn't already have it — a session restored from a pasted-in
    auth_token (see web/js/pages/auth.js), which never learns driver_id
    any other way. Deliberately minimal — this is identity resolution,
    not a profile; DriverProfileOut (the public one) already covers
    "everything about a driver" for driver_id once known.

    email/email_verified stay within that same "who am I"
    spirit rather than becoming a second endpoint — Account Settings
    needs exactly this to show a verify-email prompt."""

    driver_id: int
    display_name: str
    email: Optional[str] = None
    email_verified: bool = False
    # The terms this driver accepted, the current ones, and whether they must accept (again)
    terms_version: Optional[str] = None
    current_terms_version: Optional[str] = None
    terms_required: bool = False


class AppSessionResponse(BaseModel):
    """POST /accounts/app-session: a web session for the desktop app's
    analysis window. Unlike LoginResponse this DOES carry the raw session token —
    the caller is the desktop client, already holding a stronger credential (its
    own auth token), and it hands the token straight to its own window, which
    turns it into the usual HttpOnly cookie via /accounts/app-session/activate."""

    session_token: str
    driver_id: int
    display_name: str
    expires_in: int  # seconds


class AppSessionActivateRequest(BaseModel):
    session_token: str = Field(min_length=16, max_length=200)


class LoginResponse(BaseModel):
    """No auth_token here — login's credential is an HttpOnly
    session cookie the browser holds automatically, not a value the
    frontend needs to see or store. See server/accounts/sessions.py."""

    driver_id: int
    display_name: str
    # What the website has to ask right after signing in
    email_verified: Optional[bool] = None
    terms_required: Optional[bool] = None


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
    this sense, see server/accounts/sessions.py."""

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
    # The Garage16 Best of this track and class (anybody's), for "Your Best" per class
    class_best_time: Optional[float] = None
    class_best_driver_id: Optional[int] = None
    class_best_driver_name: Optional[str] = None


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
    # The lap behind the entry (link to the analysis, one row per lap) and the car model
    lap_id: Optional[int] = None
    car_model: Optional[str] = None


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


class LinkCodeResponse(BaseModel):
    code: str
    expires_in_seconds: int
    # The exact text to paste into Discord, so the web UI doesn't
    # have to assemble it (and can't drift from the bot's command name).
    command: Optional[str] = None


class DiscordLinkStatusOut(BaseModel):
    """GET /accounts/discord — deliberately just a flag. The Discord user
    id itself is never sent to the browser (nothing there needs it)."""

    linked: bool


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
    # Everything else the database holds about the driver (docs/DATA_PROTECTION.md)
    email: Optional[str] = None
    email_verified: Optional[bool] = None
    teams: List[dict] = []
    sessions: List[dict] = []
    records: List[dict] = []
    upload_log: List[dict] = []
    # Sha256 / size / request of every stored original, and the audit events of the driver
    lap_files: List[dict] = []
    audit_events: List[dict] = []
    # When and which version of the terms the driver accepted (null: account from before V1.0)
    terms_accepted_at: Optional[str] = None
    terms_version: Optional[str] = None
    # The connected PCs (name and dates — never a token)
    devices: List[dict] = []
