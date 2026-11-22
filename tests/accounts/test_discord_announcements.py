"""The Discord bot's announcement loop (discord_bot/bot.py _post_pending_events) against a real
database and a fake Discord client.

V1.0.2 regression: with one registered channel the bot could not reach, an event was never stamped
"announced", so every turn of the loop (10 s) posted it AGAIN to the channels that worked — the
"Team Best" messages repeated without end."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

discord = pytest.importorskip("discord")

from server.core.database import SessionLocal  # noqa: E402
from server.core.models import DiscordChannel, Lap, RecordEvent, Team  # noqa: E402
from tests.helpers.synthetic_lap import make_lap  # noqa: E402
from tests.helpers.v093 import headers, register, upload_df  # noqa: E402


class FakeChannel:
    def __init__(self, fail=None):
        self.sent = []
        self.fail = fail

    async def send(self, embed=None):
        if self.fail is not None:
            raise self.fail
        self.sent.append(embed.title + " | " + embed.description)


class FakeClient:
    def __init__(self, channels):
        self.channels = channels

    def get_channel(self, channel_id):
        return self.channels.get(str(channel_id))


@pytest.fixture()
def bot(monkeypatch):
    import discord_bot.bot as bot_module

    # V1.0.3: the digest delay is switched off here; the digest tests below set it themselves
    monkeypatch.setenv("GARAGE16_DISCORD_DIGEST_S", "0")
    bot_module._delivered.clear()
    bot_module._attempts.clear()
    yield bot_module
    bot_module._delivered.clear()
    bot_module._attempts.clear()


def _team_with_lap(client):
    """A team with one member who uploaded one lap; all record events of that upload are cleared."""
    owner = register(client, "Moritz Lachnit")
    resp = client.post("/teams", json={"name": "HyperMotion"}, headers=headers(owner))
    assert resp.status_code in (200, 201), resp.text
    team_id = resp.json()["id"]
    lap_id = upload_df(client, owner, make_lap(seed=7, n=1900, lap_time=90.0), lap_time=90.0)["lap_id"]
    with SessionLocal() as db:
        db.query(RecordEvent).delete()
        db.commit()
    return owner["driver_id"], team_id, lap_id


def _add_events(driver_id, team_id, lap_id, times, *, track="Bahrain", car="R-ace GP 2026 #85:ELMS"):
    with SessionLocal() as db:
        previous = None
        for t in times:
            db.add(RecordEvent(lap_id=lap_id, driver_id=driver_id, team_id=team_id, record_type="TEAM_BEST",
                               track_name=track, car_name=car, lap_time=t, previous_best=previous))
            previous = t
        db.commit()


def _channels(team_id, *ids):
    with SessionLocal() as db:
        for n, ch in enumerate(ids):
            db.add(DiscordChannel(team_id=team_id, guild_id="g", channel_id=ch, registered_by=str(n)))
        db.commit()


def _pending():
    with SessionLocal() as db:
        return db.query(RecordEvent).filter(RecordEvent.announced_at.is_(None)).count()


def _run(bot, client, turns=5):
    bot.client = client
    for _ in range(turns):
        asyncio.run(bot._post_pending_events())


def test_a_channel_the_bot_cannot_reach_does_not_make_the_others_repeat(client, bot, monkeypatch):
    driver_id, team_id, lap_id = _team_with_lap(client)
    _channels(team_id, "111", "222")                       # 222: deleted / bot removed
    _add_events(driver_id, team_id, lap_id, [79.569])
    working = FakeChannel()
    monkeypatch.setattr(bot, "client", FakeClient({"111": working}))

    _run(bot, bot.client, turns=5)

    assert len(working.sent) == 1, working.sent
    assert _pending() == 0


def test_a_channel_that_refuses_the_bot_is_skipped_not_retried(client, bot):
    driver_id, team_id, lap_id = _team_with_lap(client)
    _channels(team_id, "111", "333")
    _add_events(driver_id, team_id, lap_id, [79.569])

    class _Resp:
        status = 403
        reason = "Forbidden"

    forbidden = discord.Forbidden(_Resp(), "Missing Access")
    working, refusing = FakeChannel(), FakeChannel(fail=forbidden)
    _run(bot, FakeClient({"111": working, "333": refusing}), turns=5)

    assert len(working.sent) == 1 and _pending() == 0


def test_a_passing_error_retries_only_the_channel_that_failed_and_gives_up_in_the_end(client, bot):
    driver_id, team_id, lap_id = _team_with_lap(client)
    _channels(team_id, "111", "444")
    _add_events(driver_id, team_id, lap_id, [79.569])
    flaky = FakeChannel(fail=discord.DiscordException("gateway hiccup"))
    working = FakeChannel()
    fake = FakeClient({"111": working, "444": flaky})

    _run(bot, fake, turns=3)
    assert len(working.sent) == 1 and _pending() == 1    # still trying 444, but 111 got it once
    flaky.fail = None
    _run(bot, fake, turns=3)
    assert len(working.sent) == 1 and len(flaky.sent) == 1 and _pending() == 0

    _add_events(driver_id, team_id, lap_id, [78.0])
    flaky.fail = discord.DiscordException("down")
    _run(bot, fake, turns=bot.MAX_SEND_ATTEMPTS + 2)
    assert _pending() == 0, "an event is given up after MAX_SEND_ATTEMPTS, not retried forever"
    assert len(working.sent) == 2


def test_a_failed_stamp_does_not_send_the_event_again(client, bot, monkeypatch):
    driver_id, team_id, lap_id = _team_with_lap(client)
    _channels(team_id, "111")
    _add_events(driver_id, team_id, lap_id, [79.569])
    working = FakeChannel()
    real_stamp = bot._stamp_announced
    calls = []

    def broken_stamp(event_id, *also):
        calls.append(event_id)
        if len(calls) <= 2:
            return None          # as if the commit had failed: the event stays pending
        return real_stamp(event_id, *also)

    monkeypatch.setattr(bot, "_stamp_announced", broken_stamp)
    _run(bot, FakeClient({"111": working}), turns=4)

    assert len(working.sent) == 1, working.sent
    assert _pending() == 0


def test_several_waiting_improvements_of_one_record_become_one_message(client, bot):
    driver_id, team_id, lap_id = _team_with_lap(client)
    _channels(team_id, "111")
    _add_events(driver_id, team_id, lap_id, [79.569, 79.556, 78.812, 78.477])
    _add_events(driver_id, team_id, lap_id, [96.566, 96.542], track="Monza", car="499P Custom Team 2026 #397")
    working = FakeChannel()

    _run(bot, FakeClient({"111": working}), turns=3)

    assert len(working.sent) == 2, working.sent
    assert any("Bahrain" in m and "1:18.477" in m for m in working.sent)
    assert any("Monza" in m and "1:36.542" in m for m in working.sent)
    assert _pending() == 0


def test_a_team_that_switched_announcements_off_gets_nothing(client, bot):
    driver_id, team_id, lap_id = _team_with_lap(client)
    _channels(team_id, "111")
    with SessionLocal() as db:
        db.get(Team, team_id).discord_announcements_enabled = False
        db.commit()
    _add_events(driver_id, team_id, lap_id, [79.569])
    working = FakeChannel()
    _run(bot, FakeClient({"111": working}), turns=2)
    assert working.sent == [] and _pending() == 0


# ------------------------------------------------------------ V1.0.3 car name

def test_the_title_shows_the_car_model_and_falls_back_to_the_livery_name(client, bot):
    driver_id, team_id, lap_id = _team_with_lap(client)
    _channels(team_id, "111")
    with SessionLocal() as db:
        db.get(Lap, lap_id).car_model = "Peugeot 9X8"
        db.commit()
    _add_events(driver_id, team_id, lap_id, [79.569], car="499P Custom Team 2026 #397")
    working = FakeChannel()
    _run(bot, FakeClient({"111": working}), turns=2)
    assert len(working.sent) == 1 and "Peugeot 9X8" in working.sent[0] and "#397" not in working.sent[0]

    with SessionLocal() as db:
        db.get(Lap, lap_id).car_model = None
        db.commit()
    _add_events(driver_id, team_id, lap_id, [78.0], car="R-ace GP 2026 #85:ELMS")
    _run(bot, FakeClient({"111": working}), turns=2)
    assert "R-ace GP 2026 #85:ELMS" in working.sent[1]


# --------------------------------------------------------------- V1.0.3 digest

def _age_events(seconds_ago_by_index):
    """Set created_at of the pending events (oldest first) to the given ages in seconds."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as db:
        events = db.query(RecordEvent).order_by(RecordEvent.id.asc()).all()
        for event, ago in zip(events, seconds_ago_by_index):
            event.created_at = now - timedelta(seconds=ago)
        db.commit()


def test_the_digest_holds_a_young_event_back(client, bot, monkeypatch):
    monkeypatch.setenv("GARAGE16_DISCORD_DIGEST_S", "300")
    driver_id, team_id, lap_id = _team_with_lap(client)
    _channels(team_id, "111")
    _add_events(driver_id, team_id, lap_id, [79.569])
    _age_events([100])
    working = FakeChannel()
    _run(bot, FakeClient({"111": working}), turns=3)
    assert working.sent == [] and _pending() == 1

    _age_events([301])
    _run(bot, FakeClient({"111": working}), turns=3)
    assert len(working.sent) == 1 and _pending() == 0


def test_a_newer_event_restarts_the_wait(client, bot, monkeypatch):
    monkeypatch.setenv("GARAGE16_DISCORD_DIGEST_S", "300")
    driver_id, team_id, lap_id = _team_with_lap(client)
    _channels(team_id, "111")
    _add_events(driver_id, team_id, lap_id, [79.569, 79.0])
    _age_events([1000, 10])          # the first is old, but the second is new: nothing yet
    working = FakeChannel()
    _run(bot, FakeClient({"111": working}), turns=3)
    assert working.sent == [] and _pending() == 2


def test_after_the_window_one_message_sums_up_the_improvements(client, bot, monkeypatch):
    monkeypatch.setenv("GARAGE16_DISCORD_DIGEST_S", "300")
    driver_id, team_id, lap_id = _team_with_lap(client)
    _channels(team_id, "111")
    _add_events(driver_id, team_id, lap_id, [79.569, 79.556, 78.812, 78.477])
    _age_events([900, 700, 500, 400])
    working = FakeChannel()
    _run(bot, FakeClient({"111": working}), turns=3)
    assert len(working.sent) == 1, working.sent
    # the first event has no previous time here, so only the count is shown
    assert "1:18.477" in working.sent[0] and "4 improvements this session" in working.sent[0]
    assert _pending() == 0


def test_the_digest_names_the_time_before_the_first_improvement(client, bot, monkeypatch):
    monkeypatch.setenv("GARAGE16_DISCORD_DIGEST_S", "300")
    driver_id, team_id, lap_id = _team_with_lap(client)
    _channels(team_id, "111")
    _add_events(driver_id, team_id, lap_id, [80.0, 79.569, 79.0])
    with SessionLocal() as db:
        first = db.query(RecordEvent).order_by(RecordEvent.id.asc()).first()
        first.previous_best = 81.0
        db.commit()
    _age_events([900, 800, 700])
    working = FakeChannel()
    _run(bot, FakeClient({"111": working}), turns=2)
    assert len(working.sent) == 1
    assert "(previous: 1:21.000 · 3 improvements this session)" in working.sent[0], working.sent


def test_digest_zero_posts_at_once(client, bot, monkeypatch):
    monkeypatch.setenv("GARAGE16_DISCORD_DIGEST_S", "0")
    driver_id, team_id, lap_id = _team_with_lap(client)
    _channels(team_id, "111")
    _add_events(driver_id, team_id, lap_id, [79.569])     # created just now
    working = FakeChannel()
    _run(bot, FakeClient({"111": working}), turns=1)
    assert len(working.sent) == 1 and "improvements" not in working.sent[0]


def test_a_digest_group_is_stamped_in_one_transaction(client, bot, monkeypatch):
    """V1.0.3 review: the newest event and the older ones it replaces are stamped together — a
    failure in between used to leave an older, slower time to be announced on its own later."""
    monkeypatch.setenv("GARAGE16_DISCORD_DIGEST_S", "0")
    driver_id, team_id, lap_id = _team_with_lap(client)
    _channels(team_id, "111")
    _add_events(driver_id, team_id, lap_id, [79.569, 79.0, 78.5])
    working = FakeChannel()
    seen = []
    real = bot._stamp_announced
    monkeypatch.setattr(bot, "_stamp_announced", lambda event_id, *also: (seen.append((event_id, also)), real(event_id, *also)))
    _run(bot, FakeClient({"111": working}), turns=2)
    assert len(working.sent) == 1 and _pending() == 0
    assert len(seen) == 1 and len(seen[0][1]) == 2, seen
