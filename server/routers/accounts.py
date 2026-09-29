"""
routers/accounts.py — Credential issuance, rotation, and revocation.

register_driver() is still not a real login system (no password, no email
verification) — see the module-level warning in DriverRegisterResponse.
What changed vs. the original dev-only version:

  - Registration can be gated behind LMU_GARAGE_REGISTRATION_SECRET (server
    config), so a server reachable beyond localhost doesn't let anyone
    mint themselves a driver identity for free.
  - client_secret is encrypted at rest (server/crypto.py) instead of
    sitting in the DB in plaintext.
  - Tokens and secrets can now be revoked and rotated. Rotation (new
    credential, same identity, old one dies) is the everyday tool; revoke
    (kill the identity's only credential, no way back in) is for "this
    leaked" emergencies. Previously neither existed — a leaked token or
    secret was valid forever with no way to invalidate it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from ..auth import get_current_driver
from ..config import settings
from ..crypto import encrypt_secret
from ..database import get_db
from ..email_provider import get_email_provider
from .. import discord_link
from ..driver_lifecycle import delete_driver_rows
from ..models import Driver, EmailVerificationToken, Lap, PasswordResetToken
from ..models import Session as SessionModel
from ..rate_limit import rate_limit
from ..sessions import (
    SESSION_COOKIE_NAME,
    SESSION_LIFETIME,
    create_session,
    revoke_all_sessions_for_driver,
    revoke_session,
)
from ..team_lifecycle import TeamOwnershipConflict, resolve_owned_teams_before_driver_deletion
from ..schemas import (
    AccountExport,
    ChangePasswordRequest,
    DiscordLinkStatusOut,
    DisplayNameUpdateRequest,
    DriverRegisterResponse,
    EmailVerificationConfirmRequest,
    LapSummary,
    LinkCodeResponse,
    LoginRequest,
    LoginResponse,
    PasswordResetConfirmRequest,
    PasswordResetRequestRequest,
    RotateSecretResponse,
    RotateTokenResponse,
    SessionOut,
    SetPasswordRequest,
    StatusResponse,
    WhoAmIOut,
)
from ..security import (
    generate_secret,
    generate_token,
    hash_password,
    hash_token,
    is_valid_email,
    normalize_email,
    validate_password_strength,
    verify_password,
)

router = APIRouter(prefix="/accounts", tags=["accounts"])

# How long a password-reset link stays valid before it must be requested
# again — short enough that a leaked/intercepted email is a small window,
# long enough that a real person has time to actually check their inbox.
PASSWORD_RESET_TOKEN_LIFETIME = timedelta(minutes=30)

# V0.8-NAS §2 — longer than the password-reset window on purpose: a
# forgotten password is urgent (you're locked out right now); confirming
# an email is not, and a friend might not check that inbox for a day or
# two. 48 hours is generous without being indefinite.
EMAIL_VERIFICATION_TOKEN_LIFETIME = timedelta(hours=48)


def _send_verification_email(db: Session, driver: Driver, email: str) -> None:
    """Issues a fresh verification token for `email` and emails the link.
    Does not touch driver.email_verified itself — the caller (set_password)
    is responsible for resetting that when the email actually changes;
    this function only ever adds a new token + sends mail."""
    token = generate_token()
    db.add(EmailVerificationToken(
        token_hash=hash_token(token),
        driver_id=driver.id,
        email=email,
        expires_at=datetime.now(timezone.utc) + EMAIL_VERIFICATION_TOKEN_LIFETIME,
    ))
    verify_link = f"{settings.frontend_url}/#/verify-email?token={token}"
    get_email_provider().send(
        to=email,
        subject="Garage16 — verify your email",
        body=(
            f"Confirm this email address for your Garage16 driver account.\n\n"
            f"Verify: {verify_link}\n\n"
            f"This link expires in {int(EMAIL_VERIFICATION_TOKEN_LIFETIME.total_seconds() // 3600)} "
            f"hours. If you didn't request this, you can ignore this email."
        ),
    )


def _check_registration_secret(x_registration_secret: Optional[str]) -> None:
    if settings.registration_secret is None:
        return  # gating disabled — open registration (see config.py warning at startup)
    if not x_registration_secret or x_registration_secret != settings.registration_secret:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing or invalid registration secret.")


@router.post("/register", response_model=DriverRegisterResponse)
def register_driver(
    display_name: str = Query(..., min_length=1, max_length=60),
    x_registration_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("register")),
) -> DriverRegisterResponse:
    _check_registration_secret(x_registration_secret)

    display_name = display_name.strip()
    if not display_name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "display_name must not be blank.")

    token = generate_token()
    secret = generate_secret()
    driver = Driver(
        display_name=display_name,
        token_hash=hash_token(token),
        client_secret=encrypt_secret(secret),
    )
    db.add(driver)
    db.commit()
    db.refresh(driver)
    return DriverRegisterResponse(
        driver_id=driver.id,
        display_name=driver.display_name,
        auth_token=token,
        client_secret=secret,
    )


@router.post("/rotate-token", response_model=RotateTokenResponse)
def rotate_token(
    driver: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
) -> RotateTokenResponse:
    """Issue a new auth token for the authenticated driver and invalidate
    the old one immediately. Use this if a token may have leaked, or on a
    routine rotation schedule — no need to re-register (which would create
    a brand-new, disconnected driver identity)."""
    new_token = _issue_fresh_token(driver, db)
    return RotateTokenResponse(auth_token=new_token)


@router.post("/rotate-secret", response_model=RotateSecretResponse)
def rotate_secret(
    driver: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
) -> RotateSecretResponse:
    """Issue a new client_secret (the HMAC signing key used by the desktop
    client) for the authenticated driver. Run `python -m client.main
    --reconfigure` afterwards to save the new secret locally."""
    new_secret = generate_secret()
    driver.client_secret = encrypt_secret(new_secret)
    db.add(driver)
    db.commit()
    return RotateSecretResponse(client_secret=new_secret)


@router.post("/revoke", status_code=status.HTTP_204_NO_CONTENT)
def revoke_token(
    driver: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
) -> None:
    """Immediately invalidates the current auth token — every client using
    it (web session, desktop app, Discord link) is signed out on its next
    request. If this driver has no email/password set (see set_password
    below), this is terminal for the identity: there's no way back in via
    the desktop client afterwards — that specific credential is dead for
    good, so use this for "this token is compromised, kill it now" (lost
    device, leaked token), not for routine rotation, where rotate-token
    above is the one to use instead (issues a new token without cutting
    off access). A driver WITH a password set is NOT locked out of the
    WEB app by this, though: POST /accounts/login (V0.7.2) still works —
    it grants access via its own, fully independent session cookie (see
    server/sessions.py), never by reviving this token."""
    driver.token_revoked_at = datetime.now(timezone.utc)
    db.add(driver)
    db.commit()


def _issue_fresh_token(driver: Driver, db: Session) -> str:
    """Shared by login() and rotate_token(): mint a new bearer token for
    `driver`, invalidating whatever token it had before. Login uses this
    so a password-authenticated sign-in also recovers a driver whose
    token was previously revoked — same mechanism, different entry
    point."""
    new_token = generate_token()
    driver.token_hash = hash_token(new_token)
    driver.token_revoked_at = None
    db.add(driver)
    db.commit()
    return new_token


@router.post("/set-password", response_model=StatusResponse)
def set_password(
    body: SetPasswordRequest,
    driver: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
) -> StatusResponse:
    """Attach email+password to a token-only driver identity, or change
    the password on one that already has them (see SetPasswordRequest's
    docstring for why both share one endpoint). This is what turns a
    driver from "recoverable only by keeping the auth_token safe" into
    "recoverable via POST /accounts/login and password-reset" — see
    revoke_token's docstring above.
    """
    email = normalize_email(body.email)
    if not is_valid_email(email):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That doesn't look like a valid email address.")

    if driver.password_hash is not None:
        # Already has credentials — this is a "change my password" call,
        # not a first-time setup. Require proof of the CURRENT password so
        # someone who merely stole a bearer token can't lock the real
        # owner out by attaching their own email/password over it.
        if not body.current_password or not verify_password(body.current_password, driver.password_hash):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Current password is incorrect.")

    try:
        validate_password_strength(body.new_password)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    existing = (
        db.query(Driver)
        .filter(Driver.email == email, Driver.id != driver.id)
        .one_or_none()
    )
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That email address is already in use.")

    # V0.8-NAS §2: only reset verification state when the email is
    # actually NEW or different — a plain password change (same email,
    # already verified) must not silently un-verify a driver every time
    # they update their password.
    email_changed = driver.email != email
    driver.email = email
    driver.password_hash = hash_password(body.new_password)
    if email_changed:
        driver.email_verified = False
        driver.email_verified_at = None
    db.add(driver)
    db.commit()
    if email_changed:
        _send_verification_email(db, driver, email)
        db.commit()
    return StatusResponse()


@router.post("/verify-email/resend", response_model=StatusResponse)
def resend_verification_email(
    driver: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("email_verification", per_driver=True)),
) -> StatusResponse:
    """Re-sends the verification link for the driver's CURRENT email.
    404s if there's no email set at all (nothing to verify) rather than
    silently succeeding — that's a real usage error the Account Settings
    UI shouldn't need to guess about. Already-verified is not an error
    either — it's the answer, sent back as a plain 200 status the
    frontend already checks."""
    if driver.email is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No email set on this account yet.")
    if driver.email_verified:
        return StatusResponse(status="already_verified")
    _send_verification_email(db, driver, driver.email)
    db.commit()
    return StatusResponse()


@router.post("/verify-email/confirm", response_model=StatusResponse)
def confirm_email_verification(
    body: EmailVerificationConfirmRequest,
    db: Session = Depends(get_db),
) -> StatusResponse:
    """Public (no auth) — the link in the email is the proof, same as
    password-reset confirm. Rejects a token whose captured `email` no
    longer matches the driver's current email (see
    EmailVerificationToken's docstring): confirming a stale token from
    before a later email change must verify nothing."""
    token_hash = hash_token(body.token)
    verification = db.query(EmailVerificationToken).filter(
        EmailVerificationToken.token_hash == token_hash
    ).one_or_none()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if (
        verification is None
        or verification.used_at is not None
        or verification.expires_at < now
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired verification link.")

    driver = db.query(Driver).filter(Driver.id == verification.driver_id).one_or_none()
    if driver is None or driver.email != verification.email:
        # Deleted account, or the email moved on since this link was
        # issued (see docstring) — same generic rejection either way.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired verification link.")

    driver.email_verified = True
    driver.email_verified_at = now
    verification.used_at = now
    db.add(driver)
    db.add(verification)
    db.commit()
    return StatusResponse()


@router.post("/login", response_model=LoginResponse)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("login")),
) -> LoginResponse:
    """Email+password sign-in. Creates a web session (server/sessions.py)
    and sets it as an HttpOnly cookie — completely separate from the
    desktop client's Bearer auth_token (see that module's docstring for
    why: V0.7.0's first version of this endpoint reused
    _issue_fresh_token(), which overwrote drivers.token_hash and silently
    disconnected a driver's running LMU client on every web login — a
    real bug, not a hypothetical one). No token is returned in the
    response body; the browser holds the session via the cookie alone.

    Deliberately generic on failure ("Invalid email or password") whether
    the email doesn't exist, has no password set, or the password is
    wrong — distinguishing those would let an attacker enumerate which
    emails have accounts. is_locked is checked and reported distinctly
    (403, not 401) — get_current_driver already does the same for
    token-based auth, so this isn't a new information leak, just the
    existing convention applied here too.
    """
    email = normalize_email(body.email)
    driver = db.query(Driver).filter(Driver.email == email).one_or_none()
    if driver is None or driver.password_hash is None or not verify_password(body.password, driver.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password.")
    if driver.is_locked:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account has been locked.")

    raw_session_token = create_session(db, driver, user_agent=request.headers.get("user-agent"))
    db.commit()
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_session_token,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        httponly=True,
        secure=settings.require_https,
        samesite="lax",
        path="/",
    )
    return LoginResponse(driver_id=driver.id, display_name=driver.display_name)


@router.post("/logout", response_model=StatusResponse)
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    session_cookie: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME),
) -> StatusResponse:
    """Ends the CURRENT web session only (see logout_all for every
    device). A no-op, not an error, if there's no session cookie to begin
    with — logging out twice, or logging out a bearer-token-only client
    that never had a cookie session, shouldn't 401."""
    if session_cookie:
        revoke_session(db, session_cookie)
        db.commit()
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return StatusResponse()


@router.post("/logout-all", response_model=StatusResponse)
def logout_all(
    response: Response,
    driver: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
) -> StatusResponse:
    """Revokes every web session for this driver (all browsers/devices
    signed in via email+password) — but deliberately NOT the desktop
    client's auth_token/client_secret, a separate credential domain with
    its own revoke (POST /accounts/revoke). Works whether the caller is
    currently authenticated via cookie or via the bearer token, since
    get_current_driver resolves either to the same driver."""
    revoke_all_sessions_for_driver(db, driver.id)
    db.commit()
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return StatusResponse()


@router.get("/sessions", response_model=List[SessionOut])
def list_sessions(
    driver: Driver = Depends(get_current_driver),
    session_cookie: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME),
    db: Session = Depends(get_db),
) -> List[SessionOut]:
    """Account Settings → Sessions (spec §3.9): every currently-valid web
    session for this driver, so they can spot one they don't recognize
    and revoke it. Does NOT include the desktop client's auth_token — that
    isn't a "session" in this sense (see sessions.py's module docstring),
    and it's a single fixed credential, not a list of them."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = (
        db.query(SessionModel)
        .filter(
            SessionModel.driver_id == driver.id,
            SessionModel.revoked_at.is_(None),
            SessionModel.expires_at >= now,
        )
        .order_by(SessionModel.created_at.desc())
        .all()
    )
    current_hash = hash_token(session_cookie) if session_cookie else None
    return [
        SessionOut(
            id=s.id,
            created_at=s.created_at.isoformat() if s.created_at else None,
            last_used_at=s.last_used_at.isoformat() if s.last_used_at else None,
            expires_at=s.expires_at.isoformat(),
            user_agent=s.user_agent,
            is_current=(s.session_token_hash == current_hash),
        )
        for s in rows
    ]


@router.delete("/sessions/{session_id}", response_model=StatusResponse)
def revoke_one_session(
    session_id: int,
    driver: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
) -> StatusResponse:
    """Revoke one specific session by id (the "this doesn't look like me"
    button next to a row in the Sessions list) — as opposed to
    logout-all's blanket revoke. Scoped to the caller's own driver_id so
    one driver can't revoke another's session by guessing ids."""
    session_row = (
        db.query(SessionModel)
        .filter(SessionModel.id == session_id, SessionModel.driver_id == driver.id)
        .one_or_none()
    )
    if session_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found.")
    if session_row.revoked_at is None:
        session_row.revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.add(session_row)
        db.commit()
    return StatusResponse()


@router.post("/change-password", response_model=StatusResponse)
def change_password(
    body: ChangePasswordRequest,
    driver: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
) -> StatusResponse:
    if driver.password_hash is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No password set on this account yet — use POST /accounts/set-password first.",
        )
    if not verify_password(body.current_password, driver.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Current password is incorrect.")
    try:
        validate_password_strength(body.new_password)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    driver.password_hash = hash_password(body.new_password)
    db.add(driver)
    db.commit()
    return StatusResponse()


@router.post("/password-reset/request", response_model=StatusResponse)
def request_password_reset(
    body: PasswordResetRequestRequest,
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("password_reset")),
) -> StatusResponse:
    """Always returns 200/ok, whether or not that email belongs to an
    account with a password set — a response that varied by that would
    let anyone probe which emails are registered. The actual email (if
    any) is sent as a side effect, not reflected in the response."""
    email = normalize_email(body.email)
    driver = db.query(Driver).filter(Driver.email == email).one_or_none()

    if driver is not None and driver.password_hash is not None:
        token = generate_token()
        db.add(PasswordResetToken(
            token_hash=hash_token(token),
            driver_id=driver.id,
            expires_at=datetime.now(timezone.utc) + PASSWORD_RESET_TOKEN_LIFETIME,
        ))
        db.commit()

        reset_link = f"{settings.frontend_url}/#/reset-password?token={token}"
        get_email_provider().send(
            to=email,
            subject="Garage16 — reset your password",
            body=(
                f"Someone (hopefully you) requested a password reset for your "
                f"Garage16 driver account.\n\n"
                f"Reset your password: {reset_link}\n\n"
                f"This link expires in {int(PASSWORD_RESET_TOKEN_LIFETIME.total_seconds() // 60)} "
                f"minutes and can only be used once. If you didn't request this, "
                f"you can ignore this email — your password hasn't changed."
            ),
        )
    # else: silently do nothing — see docstring above for why.

    return StatusResponse()


@router.post("/password-reset/confirm", response_model=StatusResponse)
def confirm_password_reset(
    body: PasswordResetConfirmRequest,
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("password_reset")),
) -> StatusResponse:
    token_hash = hash_token(body.token)
    reset_token = (
        db.query(PasswordResetToken)
        .filter(PasswordResetToken.token_hash == token_hash)
        .one_or_none()
    )
    # Same naive-vs-aware pitfall already fixed in discord_bot/bot.py's
    # link-code check: DateTime columns round-trip as naive (no
    # timezone=True) under SQLite AND PostgreSQL alike, so comparing
    # against an aware "now" raises TypeError. .replace(tzinfo=None)
    # keeps the UTC value, drops the tz marker.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if (
        reset_token is None
        or reset_token.used_at is not None
        or reset_token.expires_at < now
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired reset token.")

    try:
        validate_password_strength(body.new_password)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    driver = db.query(Driver).filter(Driver.id == reset_token.driver_id).one_or_none()
    if driver is None:
        # Driver was deleted after the token was issued but before it was
        # used — the token is simply no longer redeemable.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired reset token.")

    driver.password_hash = hash_password(body.new_password)
    reset_token.used_at = now
    db.add(driver)
    db.add(reset_token)
    db.commit()
    # Deliberately does NOT touch driver.token_hash/client_secret — those
    # are a separate credential domain (desktop client auth, see
    # security.py's module docstring) from the web password. Resetting a
    # forgotten web password shouldn't silently disconnect a working
    # desktop client. If the reset was needed because of a full account
    # compromise, POST /accounts/revoke is the explicit tool for that.
    return StatusResponse()


@router.get("/me", response_model=WhoAmIOut)
def whoami(driver: Driver = Depends(get_current_driver)) -> WhoAmIOut:
    """Resolves the caller's own identity from whichever credential they
    presented (desktop bearer token or web session cookie — see
    server/auth.py). See WhoAmIOut's docstring for why this needed to
    exist."""
    return WhoAmIOut(
        driver_id=driver.id, display_name=driver.display_name,
        email=driver.email, email_verified=driver.email_verified,
    )


# --- Discord account link (V0.8.7) -------------------------------------
# The logic lives in server/discord_link.py, shared with the Discord bot's
# /link and /unlink commands. Previously the only web endpoint was
# POST /teams/discord-link-code (still served — see teams.py — as an alias)
# and there was no status and no unlink at all.


def issue_discord_link_code(driver: Driver, db: Session) -> LinkCodeResponse:
    try:
        code, expires_at = discord_link.create_account_link_code(db, driver)
    except discord_link.DiscordAlreadyLinked:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Your account is already linked to a Discord account. Unlink it first to link a different one.",
        )
    shown = discord_link.format_code(code)
    return LinkCodeResponse(
        code=shown,
        expires_in_seconds=int(discord_link.CODE_LIFETIME.total_seconds()),
        command=f"/link {shown}",
    )


@router.get("/discord", response_model=DiscordLinkStatusOut)
def discord_link_status(driver: Driver = Depends(get_current_driver)) -> DiscordLinkStatusOut:
    return DiscordLinkStatusOut(linked=driver.discord_user_id is not None)


@router.post("/discord/link-code", response_model=LinkCodeResponse)
def create_discord_link_code(
    driver: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("link_code", per_driver=True)),
) -> LinkCodeResponse:
    """The "profile link token": a one-time code, valid 10 minutes, that
    the driver types into Discord as `/link <code>`. 409 if already linked;
    a fresh code invalidates any earlier unused one."""
    return issue_discord_link_code(driver, db)


@router.delete("/discord", response_model=StatusResponse)
def unlink_discord(driver: Driver = Depends(get_current_driver), db: Session = Depends(get_db)) -> StatusResponse:
    """Removes the Discord link (idempotent — unlinking when nothing is
    linked is a harmless 200) and cancels any pending link code."""
    removed = discord_link.unlink_driver(db, driver)
    return StatusResponse(status="unlinked" if removed else "not_linked")


@router.patch("/me", response_model=WhoAmIOut)
def update_my_display_name(
    body: DisplayNameUpdateRequest,
    driver: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
) -> WhoAmIOut:
    """Rename yourself. Everything that shows a driver's name reads it from the
    driver row at display time (leaderboards, profiles, teams, invitations,
    Discord announcements), so the new name appears everywhere at once — old
    laps and records are not rewritten because they never stored the name.
    Names are not unique (as at registration); blank or whitespace-only names
    are rejected."""
    name = " ".join(body.display_name.split())  # trims and collapses inner whitespace
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The name must not be blank.")
    driver.display_name = name
    db.add(driver)
    db.commit()
    return WhoAmIOut(driver_id=driver.id, display_name=driver.display_name, email=driver.email, email_verified=driver.email_verified)


@router.get("/me/export", response_model=AccountExport)
def export_my_data(
    driver: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
) -> AccountExport:
    """Self-service data export (DSGVO Art. 15/20-style 'what do you have
    about me'). Returns the structured DB data — driver profile and lap
    records. Deliberately does not inline raw telemetry file bytes (those
    would make this response arbitrarily large); the telemetry_path on
    each lap is server-internal only and not exposed here."""
    laps = db.query(Lap).filter(Lap.driver_id == driver.id).order_by(Lap.uploaded_at.asc()).all()
    return AccountExport(
        driver_id=driver.id,
        display_name=driver.display_name,
        created_at=driver.created_at.isoformat(),
        discord_linked=driver.discord_user_id is not None,
        laps=[
            LapSummary(
                id=lap.id,
                track_name=lap.track_name,
                car_name=lap.car_name,
                session_type=lap.session_type,
                lap_number=lap.lap_number,
                lap_time=lap.lap_time,
                sector_times=lap.sector_times,
                is_valid=lap.is_valid,
                invalid_reason=lap.invalid_reason,
                ambient_temp=lap.ambient_temp,
                track_temp=lap.track_temp,
                uploaded_at=lap.uploaded_at.isoformat(),
            )
            for lap in laps
        ],
    )


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_account(
    driver: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
) -> None:
    """Self-service full account deletion — driver, laps, telemetry files,
    record events, team memberships, discord link, and any lap reports
    they filed. Irreversible; no confirmation step here because the web
    UI is expected to confirm before calling this (see web/js/pages/account.js).

    The cascade itself lives in server/driver_lifecycle.py (shared with the
    admin endpoint; V0.8.7 completed it — it used to miss several tables,
    which made this endpoint fail with a 500 on PostgreSQL).

    V0.7.2: refuses (409) to proceed if the driver owns a team that still
    has other members — deleting the account would otherwise silently
    leave that team without an owner. A solo-owned team (this driver is
    the only member) is deleted along with the account; a team with other
    members must be explicitly transferred (POST
    .../transfer-ownership) or deleted by its owner first — see
    server/team_lifecycle.py's resolve_owned_teams_before_driver_deletion
    for why this isn't done automatically here.
    """
    try:
        resolve_owned_teams_before_driver_deletion(db, driver.id, force=False)
    except TeamOwnershipConflict as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "You're the owner of a team with other members — transfer ownership or "
            f"delete the team before deleting your account: {', '.join(t.name for t in exc.teams)}",
        ) from exc

    telemetry_paths = delete_driver_rows(db, driver)

    # Best-effort file cleanup — DB deletion above is the source of truth;
    # a leftover file after a crash here is an orphan (P2 cleanup concern,
    # not a correctness one), not a driver whose data wasn't deleted.
    for path_str in telemetry_paths:
        try:
            Path(path_str).unlink(missing_ok=True)
        except OSError:
            pass
