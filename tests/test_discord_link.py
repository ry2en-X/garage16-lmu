"""Discord account linking (V0.8.7): the web API (status / one-time link
code / unlink), the shared logic in server/discord_link.py, and the
Discord bot's /link and /unlink commands — all against a real database.

Before V0.8.7 this mechanism had NO tests, no unlink (the bot told people
to "unlink first" with no way to do it), no status, and a driver who was
already linked could silently be overwritten."""

from __future__ import annotations

import asyncio
import re
import threading
from datetime import datetime, timedelta, timezone

import pytest

from server import discord_link
from server.database import SessionLocal
from server.models import Driver, LinkCode

CODE_RE = re.compile(r"^[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{4}-[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{4}$")


@pytest.fixture(autouse=True)
def _fresh_limiter():
    discord_link.failed_attempts.clear()
    yield
    discord_link.failed_attempts.clear()


def _register(client, name="Driver"):
    resp = client.post("/accounts/register", params={"display_name": name})
    assert resp.status_code == 200
    return resp.json()


def _auth(account):
    return {"Authorization": f"Bearer {account['auth_token']}"}


def _code(client, account) -> str:
    resp = client.post("/accounts/discord/link-code", headers=_auth(account))
    assert resp.status_code == 200, resp.text
    return resp.json()["code"]


def _redeem(code: str, discord_id: str):
    db = SessionLocal()
    try:
        return discord_link.redeem_account_link_code(db, code, discord_id)
    finally:
        db.close()


def _driver(driver_id: int) -> Driver:
    db = SessionLocal()
    try:
        d = db.get(Driver, driver_id)
        db.expunge(d)
        return d
    finally:
        db.close()


# ------------------------------------------------------------- API basics

def test_status_starts_unlinked_and_requires_auth(client):
    assert client.get("/accounts/discord").status_code == 401
    account = _register(client)
    assert client.get("/accounts/discord", headers=_auth(account)).json() == {"linked": False}


def test_generate_code_endpoint_requires_auth(client):
    assert client.post("/accounts/discord/link-code").status_code == 401


def test_link_code_has_a_typeable_shape_and_the_command_to_paste(client):
    account = _register(client)
    body = client.post("/accounts/discord/link-code", headers=_auth(account)).json()
    assert CODE_RE.match(body["code"]), body["code"]
    assert body["expires_in_seconds"] == 600
    assert body["command"] == f"/link {body['code']}"


def test_codes_never_contain_ambiguous_characters():
    for _ in range(200):
        code = discord_link.generate_code()
        assert len(code) == 8
        assert not set(code) & set("0O1IL-_")


def test_status_never_leaks_the_discord_user_id(client):
    account = _register(client)
    _redeem(_code(client, account), "123456789012345678")
    body = client.get("/accounts/discord", headers=_auth(account)).json()
    assert body == {"linked": True}


def test_deprecated_teams_path_still_issues_working_codes(client):
    account = _register(client)
    resp = client.post("/teams/discord-link-code", headers=_auth(account))
    assert resp.status_code == 200
    assert CODE_RE.match(resp.json()["code"])
    assert _redeem(resp.json()["code"], "111").ok


# ------------------------------------------------------ redeeming (the bot)

def test_redeeming_links_the_discord_account_to_the_driver(client):
    account = _register(client, "Linker")
    result = _redeem(_code(client, account), "555")
    assert result.ok and result.driver_name == "Linker"
    assert _driver(account["driver_id"]).discord_user_id == "555"
    assert client.get("/accounts/discord", headers=_auth(account)).json()["linked"] is True
    assert client.get("/accounts/me/export", headers=_auth(account)).json()["discord_linked"] is True


def test_a_code_works_exactly_once(client):
    account = _register(client)
    code = _code(client, account)
    assert _redeem(code, "1").ok
    second = _redeem(code, "2")
    assert not second.ok and "invalid or expired" in second.message


def test_an_expired_code_is_rejected(client):
    account = _register(client)
    code = _code(client, account)
    db = SessionLocal()
    try:
        row = db.query(LinkCode).filter(LinkCode.code == discord_link.normalize_code(code)).one()
        row.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        db.commit()
    finally:
        db.close()
    result = _redeem(code, "1")
    assert not result.ok and "invalid or expired" in result.message
    assert _driver(account["driver_id"]).discord_user_id is None


@pytest.mark.parametrize("mangle", [str.lower, lambda c: c.replace("-", ""), lambda c: c.replace("-", " "), lambda c: f"  {c}  "])
def test_matching_ignores_case_spaces_and_dashes(client, mangle):
    account = _register(client)
    code = _code(client, account)
    assert _redeem(mangle(code), "777").ok


def test_legacy_six_character_codes_issued_before_v0_8_7_still_redeem(client):
    account = _register(client)
    db = SessionLocal()
    try:
        db.add(LinkCode(code="AB12CD", kind="account", driver_id=account["driver_id"],
                        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=5)))
        db.commit()
    finally:
        db.close()
    assert _redeem("ab12-cd", "888").ok


def test_a_made_up_code_is_rejected(client):
    _register(client)
    result = _redeem("ZZZZ-ZZZZ", "1")
    assert not result.ok and "invalid or expired" in result.message


def test_an_empty_code_is_rejected_without_error(client):
    assert not _redeem("", "1").ok
    assert not _redeem("---", "1").ok


def test_team_link_codes_cannot_be_redeemed_as_account_codes(client):
    account = _register(client)
    db = SessionLocal()
    try:
        db.add(LinkCode(code="TEAMCODE", kind="team", driver_id=account["driver_id"],
                        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=5)))
        db.commit()
    finally:
        db.close()
    assert not _redeem("TEAMCODE", "1").ok
    assert _driver(account["driver_id"]).discord_user_id is None


# ------------------------------------------------- one-to-one and overwrites

def test_generating_a_new_code_invalidates_the_previous_one(client):
    account = _register(client)
    first = _code(client, account)
    second = _code(client, account)
    assert first != second
    assert not _redeem(first, "1").ok  # only one live code at a time
    assert _redeem(second, "1").ok


def test_an_already_linked_driver_cannot_generate_a_code(client):
    account = _register(client)
    _redeem(_code(client, account), "1")
    resp = client.post("/accounts/discord/link-code", headers=_auth(account))
    assert resp.status_code == 409
    assert "already linked" in resp.json()["detail"]
    # the deprecated alias enforces the same rule
    assert client.post("/teams/discord-link-code", headers=_auth(account)).status_code == 409


def test_one_discord_account_cannot_link_a_second_driver(client):
    a, b = _register(client, "A"), _register(client, "B")
    assert _redeem(_code(client, a), "same-discord").ok
    result = _redeem(_code(client, b), "same-discord")
    assert not result.ok and "already linked to **A**" in result.message and "/unlink" in result.message
    assert _driver(b["driver_id"]).discord_user_id is None


def test_a_code_issued_before_the_driver_linked_elsewhere_cannot_overwrite_that_link(client):
    account = _register(client)
    code = _code(client, account)
    db = SessionLocal()  # the driver got linked to another Discord account in the meantime
    try:
        db.get(Driver, account["driver_id"]).discord_user_id = "original"
        db.commit()
    finally:
        db.close()
    result = _redeem(code, "intruder")
    assert not result.ok and "already linked to a different Discord account" in result.message
    assert _driver(account["driver_id"]).discord_user_id == "original"
    assert not _redeem(code, "intruder").ok  # the code was consumed


# ----------------------------------------------------------------- unlinking

def test_unlink_clears_the_link_and_allows_linking_again(client):
    account = _register(client)
    _redeem(_code(client, account), "1")
    resp = client.delete("/accounts/discord", headers=_auth(account))
    assert resp.status_code == 200 and resp.json()["status"] == "unlinked"
    assert client.get("/accounts/discord", headers=_auth(account)).json() == {"linked": False}
    assert _driver(account["driver_id"]).discord_user_id is None
    assert _redeem(_code(client, account), "2").ok  # switch to a different Discord account


def test_unlinking_when_nothing_is_linked_is_harmless(client):
    account = _register(client)
    resp = client.delete("/accounts/discord", headers=_auth(account))
    assert resp.status_code == 200 and resp.json()["status"] == "not_linked"


def test_unlink_requires_auth(client):
    assert client.delete("/accounts/discord").status_code == 401


def test_unlinking_cancels_a_pending_code(client):
    account = _register(client)
    code = _code(client, account)
    client.delete("/accounts/discord", headers=_auth(account))
    assert not _redeem(code, "1").ok  # a stale code can't silently re-link after an unlink


def test_bot_side_unlink_returns_the_driver_name(client):
    account = _register(client, "Botty")
    _redeem(_code(client, account), "999")
    db = SessionLocal()
    try:
        assert discord_link.unlink_discord_user(db, "999") == "Botty"
        assert discord_link.unlink_discord_user(db, "999") is None  # nothing left to unlink
        assert discord_link.unlink_discord_user(db, "never-linked") is None
    finally:
        db.close()
    assert _driver(account["driver_id"]).discord_user_id is None


def test_deleting_the_account_frees_the_discord_account_for_someone_else(client):
    a = _register(client, "Leaver")
    _redeem(_code(client, a), "reusable")
    assert client.delete("/accounts/me", headers=_auth(a)).status_code == 204
    b = _register(client, "Newcomer")
    assert _redeem(_code(client, b), "reusable").ok


# -------------------------------------------------------- brute-force limits

def test_repeated_wrong_codes_are_throttled_even_for_a_valid_code_afterwards(client):
    account = _register(client)
    good = _code(client, account)
    for _ in range(discord_link.MAX_FAILED_ATTEMPTS):
        assert not _redeem("ZZZZ-ZZZZ", "guesser").ok
    blocked = _redeem(good, "guesser")
    assert not blocked.ok and "Too many wrong codes" in blocked.message
    # a different Discord user is unaffected, and the valid code is intact
    assert _redeem(good, "someone-else").ok


def test_the_throttle_window_expires_and_success_resets_it():
    limiter = discord_link._FailedAttemptLimiter()
    for _ in range(discord_link.MAX_FAILED_ATTEMPTS):
        limiter.record_failure("u", now=1000.0)
    assert limiter.blocked("u", now=1000.0 + 1)
    assert not limiter.blocked("u", now=1000.0 + discord_link.FAILED_ATTEMPT_WINDOW_SECONDS + 1)
    limiter.record_failure("v", now=1.0)
    limiter.clear("v")
    assert not limiter.blocked("v", now=2.0)


def test_generating_codes_is_rate_limited_per_driver(client):
    account = _register(client)
    statuses = [client.post("/accounts/discord/link-code", headers=_auth(account)).status_code for _ in range(12)]
    assert statuses[:10] == [200] * 10 and 429 in statuses[10:]


# ------------------------------------------------------- housekeeping / races

def test_stale_codes_are_swept_when_a_new_one_is_created(client):
    account = _register(client)
    db = SessionLocal()
    try:
        db.add(LinkCode(code="OLDOLD11", kind="account", driver_id=account["driver_id"],
                        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=3)))
        db.commit()
    finally:
        db.close()
    _code(client, account)
    db = SessionLocal()
    try:
        assert db.query(LinkCode).filter(LinkCode.code == "OLDOLD11").count() == 0
    finally:
        db.close()


def test_two_discord_users_racing_for_one_code_exactly_one_wins(client):
    account = _register(client)
    code = _code(client, account)
    results = {}

    def go(uid):
        db = SessionLocal()
        try:
            results[uid] = discord_link.redeem_account_link_code(db, code, uid)
        finally:
            db.close()

    threads = [threading.Thread(target=go, args=(uid,)) for uid in ("racer-1", "racer-2")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    winners = [uid for uid, r in results.items() if r.ok]
    assert len(winners) == 1, results
    assert _driver(account["driver_id"]).discord_user_id == winners[0]


# ---------------------------------------------------- the real bot commands

class _FakeResponse:
    def __init__(self):
        self.sent = []

    async def send_message(self, content, ephemeral=False):
        self.sent.append((content, ephemeral))


class _FakeInteraction:
    def __init__(self, user_id):
        self.user = type("U", (), {"id": user_id})()
        self.response = _FakeResponse()


def test_the_bots_link_and_unlink_commands_drive_the_shared_logic(client):
    import discord_bot.bot as bot

    account = _register(client, "Racer")
    code = _code(client, account)

    link_interaction = _FakeInteraction(424242)
    asyncio.run(bot.link.callback(link_interaction, code))
    message, ephemeral = link_interaction.response.sent[0]
    assert "Linked to driver **Racer**" in message and ephemeral is True  # only the user sees it
    assert _driver(account["driver_id"]).discord_user_id == "424242"

    again = _FakeInteraction(424242)
    asyncio.run(bot.link.callback(again, "AAAA-BBBB"))
    assert "already linked to **Racer**" in again.response.sent[0][0]

    unlink_interaction = _FakeInteraction(424242)
    asyncio.run(bot.unlink.callback(unlink_interaction))
    assert "Unlinked from driver **Racer**" in unlink_interaction.response.sent[0][0]
    assert _driver(account["driver_id"]).discord_user_id is None

    nothing = _FakeInteraction(424242)
    asyncio.run(bot.unlink.callback(nothing))
    assert "isn't linked" in nothing.response.sent[0][0]


def test_the_bot_registers_link_and_unlink_as_slash_commands():
    import discord_bot.bot as bot

    names = {c.name for c in bot.tree.get_commands()}
    assert {"link", "unlink"} <= names
