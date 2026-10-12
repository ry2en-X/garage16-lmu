"""Runs the frontend tests of the lap analysis (V0.9.0, extended in V0.9.1):

  * tests/js/analysis_model.test.mjs       — the view logic, plain Node
  * tests/js/analysis_layout.test.mjs      — V0.9.1: layout model + storage, colours, delta/sector
                                             summary, touch gestures, window registry; plain Node
  * tests/js/analysis_page.test.mjs        — the pages, charts, track map, physics view
                                             and circuit library in jsdom
  * tests/js/analysis_layout_page.test.mjs — V0.9.1: standard layout, layout editor, compare
                                             dialog, quick starts, no request on cursor moves; jsdom
  * tests/js/i18n.test.mjs                 — V0.9.1: English/German lookup, first-visit language popup,
                                             menu switch; jsdom
  * tests/js/i18n_pages.test.mjs           — V0.9.1: the real router and pages rendered in German, in
                                             English and on a first visit (language popup); jsdom
  * tests/js/desktop_app.test.mjs          — V0.9.2: sign-in inside the desktop app's analysis window
  * tests/js/v093_analysis.test.mjs        — V0.9.3: coaching, events, histograms, tyres, track-map
                                             markers, the sessions page and the theoretical best
                                             (app_bridge.js) and the download page; jsdom

Skipped — never faked — when Node or jsdom is missing; enable them once with
`cd tests/js && npm install`."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

JS_DIR = Path(__file__).resolve().parent / "js"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
needs_jsdom = pytest.mark.skipif(
    shutil.which("node") is None or not (JS_DIR / "node_modules" / "jsdom").exists(),
    reason="node/jsdom not installed — run `cd tests/js && npm install` to enable the frontend DOM tests",
)


def _run(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["node", script, *args], cwd=JS_DIR, capture_output=True, text=True, timeout=120)


@needs_node
def test_analysis_view_logic():
    result = _run("analysis_model.test.mjs")
    assert "ANALYSIS-MODEL-OK" in result.stdout, result.stdout + result.stderr


@needs_jsdom
def test_analysis_pages_charts_map_physics_and_circuits():
    result = _run("analysis_page.test.mjs")
    assert "ANALYSIS-PAGE-OK" in result.stdout, result.stdout + result.stderr


@needs_node
def test_analysis_layout_logic_colours_insights_touch_and_registry():
    result = _run("analysis_layout.test.mjs")
    assert "ANALYSIS-LAYOUT-OK" in result.stdout, result.stdout + result.stderr


@needs_jsdom
def test_analysis_layout_editor_compare_dialog_quick_starts_and_cursor_without_requests():
    result = _run("analysis_layout_page.test.mjs")
    assert "ANALYSIS-LAYOUT-PAGE-OK" in result.stdout, result.stdout + result.stderr


@needs_jsdom
def test_language_support_lookup_popup_and_switch():
    result = _run("i18n.test.mjs")
    assert "I18N-OK" in result.stdout, result.stdout + result.stderr


@needs_jsdom
@pytest.mark.parametrize("mode", ["de", "en", "none"])
def test_real_pages_render_in_the_chosen_language(mode):
    """German stored -> German menu and pages (and no English left behind); English stored -> as
    before; nothing stored -> English underneath plus the language popup."""
    result = _run("i18n_pages.test.mjs", mode)
    assert f"I18N-PAGES-{mode.upper()}-OK" in result.stdout, result.stdout + result.stderr


@needs_jsdom
def test_desktop_app_window_sign_in_and_download_page():
    result = _run("desktop_app.test.mjs")
    assert "DESKTOP-APP-OK" in result.stdout, result.stdout + result.stderr


@needs_jsdom
def test_v093_windows_sessions_page_and_theoretical_best():
    result = _run("v093_analysis.test.mjs")
    assert "V093-ANALYSIS-OK" in result.stdout, result.stdout + result.stderr


@needs_jsdom
def test_v094_stint_analyzer_statistics_and_dashboard():
    result = _run("v094_stints.test.mjs")
    assert "V094-STINTS-OK" in result.stdout, result.stdout + result.stderr


@needs_jsdom
def test_v094_laptime_analyzer_lines_corners_coach_and_extra_laps():
    result = _run("v094_laptime.test.mjs")
    assert "V094-LAPTIME-OK" in result.stdout, result.stdout + result.stderr
