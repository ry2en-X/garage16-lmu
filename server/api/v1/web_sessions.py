"""Web sign-in: login and logout, the HttpOnly session cookie, the desktop app's analysis-window session, and the list of signed-in browsers."""

from __future__ import annotations


from datetime import datetime, timezone
from typing import List, Optional

from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    Header,
    HTTPException,
    Request,
    Response,
    status,
)
from sqlalchemy.orm import Session

from server.accounts.auth import get_current_driver
from server.accounts.schemas import (
    AppSessionActivateRequest,
    AppSessionResponse,
    LoginRequest,
    LoginResponse,
    SessionOut,
    StatusResponse,
)
from server.accounts.sessions import (
    SESSION_COOKIE_NAME,
    SESSION_LIFETIME,
    create_session,
    get_driver_for_session_token,
    revoke_all_sessions_for_driver,
    revoke_session,
)
from server.core.config import settings
from server.core.database import get_db
from server.core.models import (
    Driver,
)
from server.core.models import Session as SessionModel
from server.core.rate_limit import rate_limit
from server.core.security import (
    hash_token,
    normalize_email,
)
from server.api.v1.accounts import _login_response, _sign_in_checked

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.post("/login", response_model=LoginResponse)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("login")),
) -> LoginResponse:
    """Email+password sign-in. Creates a web session (server/accounts/sessions.py)
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
    driver = _sign_in_checked(db, email, body.password)   # Failures also counted per address
    if driver.is_locked:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account has been locked.")

    raw_session_token = create_session(db, driver, user_agent=request.headers.get("user-agent"))
    db.commit()
    _set_session_cookie(response, raw_session_token)
    return _login_response(driver)


def _set_session_cookie(response: Response, raw_session_token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_session_token,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        httponly=True,
        secure=settings.require_https,
        samesite="lax",
        path="/",
    )


APP_SESSION_USER_AGENT = "Garage16 desktop app (analysis window)"


def _is_same_origin(request: Request) -> bool:
    """True when the browser says the request comes from this server's own pages.
    Modern browsers (WebView2 included) send Sec-Fetch-Site and, on a POST, Origin;
    both must agree when present, and at least one must be present."""
    fetch_site = request.headers.get("sec-fetch-site")
    origin = request.headers.get("origin")
    if fetch_site is not None and fetch_site != "same-origin":
        return False
    if origin is None:
        return fetch_site == "same-origin"
    from urllib.parse import urlparse

    parsed = urlparse(origin)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    allowed = {o.rstrip("/") for o in settings.cors_origins}
    return (bool(parsed.netloc) and parsed.netloc == host) or origin.rstrip("/") in allowed


@router.post("/app-session", response_model=AppSessionResponse)
def create_app_session(
    request: Request,
    response: Response,
    authorization: Optional[str] = Header(None),
    driver: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("app_session", per_driver=True)),
) -> AppSessionResponse:
    if not (authorization and authorization.startswith("Bearer ")):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the Garage16 desktop app can open an app session.")
    raw_session_token = create_session(db, driver, user_agent=APP_SESSION_USER_AGENT)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return AppSessionResponse(
        session_token=raw_session_token,
        driver_id=driver.id,
        display_name=driver.display_name,
        expires_in=int(SESSION_LIFETIME.total_seconds()),
    )


@router.post("/app-session/activate", response_model=LoginResponse)
def activate_app_session(
    body: AppSessionActivateRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("app_session")),
) -> LoginResponse:
    if not _is_same_origin(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cross-site request refused.")
    driver = get_driver_for_session_token(db, body.session_token)
    if driver is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired or invalid — sign in again.")
    if driver.is_locked:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account has been locked.")
    db.commit()  # last_used_at
    _set_session_cookie(response, body.session_token)
    response.headers["Cache-Control"] = "no-store"
    return _login_response(driver)


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
