"""routers/legal.py — what the website's legal pages fill in.

The texts themselves (legal notice, privacy policy, terms of use, licences) are part of the web
app (web/js/legal/). Only the parts that differ per instance come from here, out of .env:

  * the operator — name, postal address, email (GARAGE16_OPERATOR_*). Kept out of the code on
    purpose: the repository is public, and another instance has another operator. Not configured
    = null, and the page says so instead of showing a made-up address;
  * the minimum age (GARAGE16_MIN_AGE, default 16);
  * the version of the terms (TERMS_VERSION — bumped whenever the texts change materially; it is
    stored with every sign-up, drivers.terms_version);
  * whether anybody may sign up ("open"), only people with the registration secret ("secret"), or
    it is a local development server ("development").
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from server.accounts.schemas import LegalInfoOut, OperatorOut
from server.core.config import Settings, settings

# The date the current legal texts took effect. Change it together with web/js/legal/*.js.
# Privacy policy — the PC's name per connected PC, the deletion of unconfirmed
# sign-ups, the hashed email in the sign-in limits, the fair-play review; everybody accepts once more.
TERMS_VERSION = "2026-10-26"

router = APIRouter(tags=["legal"])


def registration_mode(cfg: Settings) -> str:
    if cfg.open_registration:
        return "open"
    if cfg.registration_secret is not None:
        return "secret"
    return "development"


@router.get("/public/legal", response_model=LegalInfoOut)
def legal_info(response: Response) -> LegalInfoOut:
    operator = None
    if settings.operator_name:
        operator = OperatorOut(
            name=settings.operator_name,
            address=list(settings.operator_address),
            email=settings.operator_email,
        )
    # It carries the operator's postal address: not for search engines. Browsers may keep it an hour.
    response.headers["Cache-Control"] = "public, max-age=3600"
    response.headers["X-Robots-Tag"] = "noindex"
    return LegalInfoOut(
        operator=operator,
        min_age=settings.min_age,
        terms_version=TERMS_VERSION,
        registration=registration_mode(settings),
    )
