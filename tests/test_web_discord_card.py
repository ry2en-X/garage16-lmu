"""Runs the real jsdom DOM test of the account page's Discord card
(tests/js/discord_card.test.mjs). Skipped — never faked — when Node or
jsdom isn't installed; set it up once with `cd tests/js && npm install`."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

JS_DIR = Path(__file__).resolve().parent / "js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not (JS_DIR / "node_modules" / "jsdom").exists(),
    reason="node/jsdom not installed — run `cd tests/js && npm install` to enable the frontend DOM tests",
)


def test_discord_card_renders_generates_flips_to_linked_and_unlinks():
    result = subprocess.run(["node", "discord_card.test.mjs"], cwd=JS_DIR, capture_output=True, text=True, timeout=60)
    assert "DISCORD-CARD-OK" in result.stdout, result.stdout + result.stderr
