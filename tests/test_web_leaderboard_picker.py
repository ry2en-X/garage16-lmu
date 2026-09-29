"""Runs the real jsdom DOM test of the leaderboard's search-first track picker
(tests/js/leaderboard_picker.test.mjs, V0.8.9). Skipped — never faked — when
Node or jsdom isn't installed; set it up once with `cd tests/js && npm install`."""

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


def test_search_finds_the_whole_track_with_variants_and_every_class_is_offered():
    result = subprocess.run(["node", "leaderboard_picker.test.mjs"], cwd=JS_DIR, capture_output=True, text=True, timeout=60)
    assert "LEADERBOARD-PICKER-OK" in result.stdout, result.stdout + result.stderr
