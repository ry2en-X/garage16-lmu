from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from .database import get_db
from .models import DeviceToken, Driver
from .security import hash_token
from .sessions import SESSION_COOKIE_NAME, get_driver_for_session_token


# "last used" in the device list is updated at most this often — not one write per request
_TOUCH_EVERY = timedelta(minutes=10)


def _touch(db: Session, device: DeviceToken) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    last = device.last_used_at
    if last is not None and last.tzinfo is not None:
        last = last.replace(tzinfo=None)
    if last is None or now - last >= _TOUCH_EVERY:
        device.last_used_at = now
        try:
            db.commit()
        except Exception:   # a busy database must never turn a valid request into an error
            db.rollback()


def get_current_driver(
    request: Request,
    authorization: Optional[str] = Header(None),
    session_cookie: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME),
    db: Session = Depends(get_db),
) -> Driver:
    """Resolves the calling driver from EITHER credential domain — the
    desktop client's Bearer auth_token (checked first, since every
    existing test and the desktop client itself use it, and because an
    explicit header is a stronger signal of intent than an ambient
    cookie) or a web-login session cookie (V0.7.2, see sessions.py). One
    identity, two acceptable proofs — not two parallel auth systems: both
    paths return the same Driver, and everything downstream (rate limits,
    is_locked, request.state.driver_id) is identical either way.
    """
    if authorization and authorization.startswith("Bearer "):
        token = authorization[len("Bearer "):].strip()
        token_hash = hash_token(token)
        driver = db.query(Driver).filter(Driver.token_hash == token_hash).one_or_none()
        if driver is not None:
            if driver.token_revoked_at is not None:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This token has been revoked.")
        else:
            # V1.0.1: one token per connected PC (desktop-login) — see models.DeviceToken
            device = db.query(DeviceToken).filter(DeviceToken.token_hash == token_hash).one_or_none()
            if device is None:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid auth token.")
            if device.revoked_at is not None:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This token has been revoked.")
            driver = device.driver
            if driver is None:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid auth token.")
            request.state.device_token_id = device.id
            _touch(db, device)
        if driver.is_locked:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "This account has been locked.")
        # Lets rate_limit(per_driver=True) key on driver_id without re-running
        # auth as a second dependency (see server/rate_limit.py).
        request.state.driver_id = driver.id
        return driver

    if session_cookie:
        driver = get_driver_for_session_token(db, session_cookie)
        if driver is not None:
            request.state.driver_id = driver.id
            return driver
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired or invalid — sign in again.")

    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED, "Missing or malformed Authorization header."
    )
