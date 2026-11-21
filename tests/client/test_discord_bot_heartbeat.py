"""Test for the Discord bot's heartbeat file — the signal
docker-compose.yml's HEALTHCHECK uses, since a bot process has no port to
probe directly."""

import tempfile
from pathlib import Path

import pytest


def test_touch_heartbeat_creates_file(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        heartbeat_path = Path(tmp) / "heartbeat"
        monkeypatch.setenv("GARAGE16_BOT_HEARTBEAT_PATH", str(heartbeat_path))

        pytest.importorskip("discord")  # Skip, not fail, where discord.py is not installed
        import discord_bot.bot as bot_module
        import importlib
        importlib.reload(bot_module)  # picks up the new env var for _HEARTBEAT_PATH

        assert not heartbeat_path.exists()
        bot_module._touch_heartbeat()
        assert heartbeat_path.exists()

        # Reset for other tests in the same process.
        monkeypatch.delenv("GARAGE16_BOT_HEARTBEAT_PATH", raising=False)
        importlib.reload(bot_module)


def test_touch_heartbeat_does_not_raise_on_unwritable_path(monkeypatch):
    """A best-effort signal must never crash the bot over a filesystem hiccup."""
    monkeypatch.setenv("GARAGE16_BOT_HEARTBEAT_PATH", "/nonexistent-dir/heartbeat")

    pytest.importorskip("discord")  # Skip, not fail, where discord.py is not installed
    import discord_bot.bot as bot_module
    import importlib
    importlib.reload(bot_module)

    bot_module._touch_heartbeat()  # must not raise

    monkeypatch.delenv("GARAGE16_BOT_HEARTBEAT_PATH", raising=False)
    importlib.reload(bot_module)
