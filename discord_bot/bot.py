"""
discord_bot/bot.py — Announces lap-time records to registered channels and
answers on-demand queries via slash commands.

Two jobs, one process:
  1. Background poll loop: reads unannounced rows from RecordEvent (written
     by server/records.py whenever an uploaded lap sets a record) and posts
     an embed. WR events go to every channel registered with /link_global;
     TEAM_BEST events go to the channels registered for that team via
     /link_team. PR events aren't broadcast — nobody but the driver cares,
     and they're already answerable on demand via /pb.
  2. Slash commands: /link (connect a Discord account to a driver profile),
     /link_team, /link_global, /pb, /wr, /teambest, /leaderboard — each
     filterable by track and car.

This talks directly to the same database as the FastAPI server (it's one
deployment, two processes), so there's no extra internal API to keep in
sync. If you ever split the bot onto different infrastructure than the
server, swap the direct DB calls below for HTTP calls to the backend.

Setup:
    pip install -r discord_bot/requirements.txt
    export DISCORD_BOT_TOKEN=...          # from the Discord developer portal
    export LMU_GARAGE_DB_URL=...          # same value the server uses, if not sqlite default
    python -m discord_bot.bot
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from sqlalchemy import func
from sqlalchemy.orm import Session

from server.database import SessionLocal
from server.discord_link import redeem_account_link_code, unlink_discord_user
from server.models import Driver, DiscordChannel, Lap, RecordEvent, Team, TeamMembership

logger = logging.getLogger("lmu_garage.discord_bot")

POLL_SECONDS = 10

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# Guards against on_ready firing more than once (e.g. after a gateway
# reconnect) and spinning up a second, parallel announce_loop() — which
# would poll and post the same RecordEvents twice.
_announce_task: Optional[asyncio.Task] = None


def _fmt(seconds: Optional[float]) -> str:
    if not seconds or seconds <= 0:
        return "—"
    minutes, secs = divmod(seconds, 60)
    return f"{int(minutes)}:{secs:06.3f}"


def _db() -> Session:
    return SessionLocal()


# ------------------------------------------------------------- outbox loop --

# Path the container's HEALTHCHECK probes (see docker-compose.yml /
# Dockerfile.discord_bot). A bot process has no port to open for a
# conventional TCP/HTTP healthcheck, so this file's mtime is the signal
# instead: "was the main loop still turning within the last N seconds?"
# Touched once per cycle, success or failure — a caught exception inside
# the loop (logged, not re-raised) still updates it, since the process
# itself is alive and will retry; only a genuine hang or crash lets the
# file go stale.
_HEARTBEAT_PATH = os.environ.get("LMU_GARAGE_BOT_HEARTBEAT_PATH", "/tmp/discord_bot_heartbeat")


def _touch_heartbeat() -> None:
    try:
        Path(_HEARTBEAT_PATH).touch()
    except OSError:
        pass  # heartbeat is a best-effort signal, never worth crashing the bot over


async def announce_loop() -> None:
    await client.wait_until_ready()
    while not client.is_closed():
        try:
            await _post_pending_events()
        except Exception:
            logger.exception("Error posting record events")
        _touch_heartbeat()
        await asyncio.sleep(POLL_SECONDS)


async def _post_pending_events() -> None:
    # Read pending events and their data in a short-lived session, then
    # close it BEFORE awaiting Discord sends. The V0.5.2 approach held
    # the session (and under SQLite, the write lock) across every
    # `await channel.send()`, meaning Discord latency blocked the API.
    db = _db()
    try:
        pending = (
            db.query(RecordEvent)
            .filter(RecordEvent.announced_at.is_(None))
            .order_by(RecordEvent.created_at.asc())
            .limit(50)
            .all()
        )
        # Materialize everything we need into plain dicts so the session
        # can close before any network I/O.
        events_to_send = []
        for event in pending:
            driver = db.query(Driver).get(event.driver_id)
            is_wr = event.record_type == "WR"
            if is_wr:
                channel_rows = db.query(DiscordChannel).filter(DiscordChannel.team_id.is_(None)).all()
            else:
                team = db.query(Team).get(event.team_id) if event.team_id else None
                # V0.6.8: a team can opt out of TEAM_BEST announcements via
                # the web dashboard (PATCH /teams/{id}). Treat "opted out"
                # the same as "no channels registered" — the send loop
                # below already handles that case (stamps announced,
                # moves on) without needing a separate branch here.
                if team is not None and not team.discord_announcements_enabled:
                    channel_rows = []
                else:
                    channel_rows = db.query(DiscordChannel).filter(DiscordChannel.team_id == event.team_id).all()

            events_to_send.append({
                "event_id": event.id,
                "record_type": event.record_type,
                "track_name": event.track_name,
                "car_name": event.car_name,
                "lap_time": event.lap_time,
                "previous_best": event.previous_best,
                "driver_name": driver.display_name if driver else "Unknown driver",
                "is_wr": is_wr,
                "channel_ids": [row.channel_id for row in channel_rows],
            })
    finally:
        db.close()

    # Now send — no DB session held.
    for evt in events_to_send:
        if evt["record_type"] == "PR":
            # PR is queryable via /pb, not broadcast.
            _stamp_announced(evt["event_id"])
            continue

        if not evt["channel_ids"]:
            _stamp_announced(evt["event_id"])
            continue

        # P0-5: Don't broadcast "first time set" WR — any invented
        # track/car combination creates one, enabling WR spam.
        if evt["is_wr"] and evt["previous_best"] is None:
            _stamp_announced(evt["event_id"])
            continue

        # Truncate title to Discord's 256-char limit.
        raw_title = ("🌍 World Record" if evt["is_wr"] else "🏁 Team Best") + f": {evt['track_name']} — {evt['car_name']}"
        title = raw_title[:256]

        embed = discord.Embed(
            title=title,
            description=(
                f"**{evt['driver_name']}** set **{_fmt(evt['lap_time'])}**"
                + (f" (previous: {_fmt(evt['previous_best'])})" if evt["previous_best"] else "")
            ),
            color=discord.Color.gold() if evt["is_wr"] else discord.Color.blue(),
        )

        all_sent = True
        for ch_id in evt["channel_ids"]:
            channel = client.get_channel(int(ch_id))
            if channel is None:
                logger.warning("Channel %s not found/accessible; will retry", ch_id)
                all_sent = False
                continue
            try:
                await channel.send(embed=embed)
            except discord.DiscordException:
                logger.exception("Failed to send announcement to channel %s", ch_id)
                all_sent = False

        if all_sent:
            _stamp_announced(evt["event_id"])


def _stamp_announced(event_id: int) -> None:
    """Short-lived session to stamp announced_at — no lock held during sends."""
    db = _db()
    try:
        event = db.query(RecordEvent).get(event_id)
        if event is not None:
            event.announced_at = datetime.now(timezone.utc)
            db.add(event)
            db.commit()
    finally:
        db.close()


# ------------------------------------------------------------------ helpers --

def _driver_for(discord_user_id: int, db: Session) -> Optional[Driver]:
    return db.query(Driver).filter(Driver.discord_user_id == str(discord_user_id)).one_or_none()


def _best_lap(
    db: Session, track: str, car: str,
    driver_id: Optional[int] = None, team_id: Optional[int] = None,
):
    q = db.query(Lap).filter(
        Lap.is_valid.is_(True), Lap.track_name.ilike(track), Lap.car_name.ilike(car)
    )
    if driver_id is not None:
        q = q.filter(Lap.driver_id == driver_id)
    if team_id is not None:
        q = q.join(TeamMembership, TeamMembership.driver_id == Lap.driver_id).filter(
            TeamMembership.team_id == team_id
        )
    return q.order_by(Lap.lap_time.asc()).first()


# -------------------------------------------------------------- slash cmds --

@tree.command(description="Link your Discord account to your Garage16 driver profile.")
@app_commands.describe(code="One-time code from the website (Account -> Link Discord), e.g. ABCD-EFGH")
async def link(interaction: discord.Interaction, code: str) -> None:
    # All rules (single use, expiry, one-to-one, throttling of wrong
    # guesses) live in server/discord_link.py so they're tested against a
    # real database; this command only relays the outcome. Ephemeral: only
    # the person who ran it sees it.
    db = _db()
    try:
        result = redeem_account_link_code(db, code, str(interaction.user.id))
    finally:
        db.close()
    await interaction.response.send_message(result.message, ephemeral=True)


@tree.command(description="Unlink your Discord account from your Garage16 driver profile.")
async def unlink(interaction: discord.Interaction) -> None:
    db = _db()
    try:
        name = unlink_discord_user(db, str(interaction.user.id))
    finally:
        db.close()
    if name is None:
        await interaction.response.send_message("Your Discord account isn't linked to any driver.", ephemeral=True)
    else:
        await interaction.response.send_message(
            f"Unlinked from driver **{name}**. Use `/link` with a fresh code to link again.", ephemeral=True
        )


@tree.command(description="Register this channel for a team's record announcements.")
@app_commands.default_permissions(manage_channels=True)
@app_commands.describe(invite_code="The team's invite code from the web app")
async def link_team(interaction: discord.Interaction, invite_code: str) -> None:
    db = _db()
    try:
        team = db.query(Team).filter(Team.invite_code == invite_code.strip().upper()).one_or_none()
        if team is None:
            await interaction.response.send_message("No team with that invite code.", ephemeral=True)
            return
        existing = db.query(DiscordChannel).filter(
            DiscordChannel.channel_id == str(interaction.channel_id)
        ).with_for_update().one_or_none()
        if existing:
            existing.team_id = team.id
        else:
            db.add(DiscordChannel(
                team_id=team.id, guild_id=str(interaction.guild_id),
                channel_id=str(interaction.channel_id), registered_by=str(interaction.user.id),
            ))
        db.commit()
        await interaction.response.send_message(f"This channel now announces **{team.name}** team bests.")
    finally:
        db.close()


@tree.command(description="Register this channel for global world-record announcements.")
@app_commands.default_permissions(manage_channels=True)
async def link_global(interaction: discord.Interaction) -> None:
    db = _db()
    try:
        existing = db.query(DiscordChannel).filter(
            DiscordChannel.channel_id == str(interaction.channel_id)
        ).with_for_update().one_or_none()
        if existing:
            existing.team_id = None
        else:
            db.add(DiscordChannel(
                team_id=None, guild_id=str(interaction.guild_id),
                channel_id=str(interaction.channel_id), registered_by=str(interaction.user.id),
            ))
        db.commit()
        await interaction.response.send_message("This channel now announces world records.")
    finally:
        db.close()


@tree.command(name="pb", description="Your personal best on a track/car.")
async def pb(interaction: discord.Interaction, track: str, car: str) -> None:
    db = _db()
    try:
        driver = _driver_for(interaction.user.id, db)
        if driver is None:
            await interaction.response.send_message(
                "Your Discord isn't linked yet — run `/link <code>` first.", ephemeral=True
            )
            return
        lap = _best_lap(db, track, car, driver_id=driver.id)
        if lap is None:
            await interaction.response.send_message(f"No valid laps for {track} / {car} yet.")
            return
        await interaction.response.send_message(f"Your best on **{track} / {car}**: **{_fmt(lap.lap_time)}**")
    finally:
        db.close()


@tree.command(name="wr", description="World record on a track/car.")
async def wr(interaction: discord.Interaction, track: str, car: str) -> None:
    db = _db()
    try:
        lap = _best_lap(db, track, car)
        if lap is None:
            await interaction.response.send_message(f"No valid laps for {track} / {car} yet.")
            return
        driver = db.query(Driver).get(lap.driver_id)
        await interaction.response.send_message(
            f"World record on **{track} / {car}**: **{_fmt(lap.lap_time)}** by {driver.display_name}"
        )
    finally:
        db.close()


@tree.command(name="teambest", description="Your team's best on a track/car.")
async def teambest(interaction: discord.Interaction, track: str, car: str, team: Optional[str] = None) -> None:
    db = _db()
    try:
        driver = _driver_for(interaction.user.id, db)
        if driver is None:
            await interaction.response.send_message(
                "Your Discord isn't linked yet — run `/link <code>` first.", ephemeral=True
            )
            return
        q = db.query(Team).join(TeamMembership, TeamMembership.team_id == Team.id).filter(
            TeamMembership.driver_id == driver.id
        )
        if team:
            q = q.filter(Team.name.ilike(team))
        teams = q.all()
        if not teams:
            await interaction.response.send_message(
                "You're not on a team (or no match for that name).", ephemeral=True
            )
            return
        lines = []
        for t in teams:
            lap = _best_lap(db, track, car, team_id=t.id)
            if lap is None:
                lines.append(f"**{t.name}**: no valid laps yet")
            else:
                d = db.query(Driver).get(lap.driver_id)
                lines.append(f"**{t.name}**: {_fmt(lap.lap_time)} by {d.display_name}")
        await interaction.response.send_message(f"Team best — {track} / {car}\n" + "\n".join(lines))
    finally:
        db.close()


@tree.command(description="Top laps for a track/car.")
async def leaderboard(interaction: discord.Interaction, track: str, car: str, limit: int = 5) -> None:
    from sqlalchemy import select
    db = _db()
    try:
        # ROW_NUMBER approach — same fix as server/routers/leaderboard.py:
        # the previous GROUP BY + JOIN-on-min(lap_time) returned duplicate
        # rows when a driver had two laps with identical times.
        ranked = (
            select(
                Lap.id,
                Lap.driver_id,
                Lap.lap_time,
                func.row_number()
                .over(partition_by=Lap.driver_id, order_by=(Lap.lap_time.asc(), Lap.id.asc()))
                .label("rank"),
            )
            .where(Lap.track_name.ilike(track), Lap.car_model.ilike(car), Lap.is_valid.is_(True))
            .subquery()
        )
        stmt = (
            select(ranked.c.lap_time, Driver.display_name)
            .join(Driver, Driver.id == ranked.c.driver_id)
            .where(ranked.c.rank == 1)
            .order_by(ranked.c.lap_time.asc())
            .limit(min(limit, 20))
        )
        rows = db.execute(stmt).all()
        if not rows:
            await interaction.response.send_message(f"No valid laps for {track} / {car} yet.")
            return
        lines = [f"{i + 1}. {name} — {_fmt(time)}" for i, (time, name) in enumerate(rows)]
        await interaction.response.send_message(f"Leaderboard — {track} / {car}\n" + "\n".join(lines))
    finally:
        db.close()


@client.event
async def on_ready() -> None:
    global _announce_task
    await tree.sync()
    logger.info("Discord bot logged in as %s", client.user)
    # on_ready can fire again after a gateway reconnect. Only start the
    # poll loop if it isn't already running, or we'd end up with two (or
    # more) tasks posting the same RecordEvents in parallel.
    if _announce_task is None or _announce_task.done():
        _announce_task = client.loop.create_task(announce_loop())


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("Set DISCORD_BOT_TOKEN in the environment.")
    client.run(token)


if __name__ == "__main__":
    main()
