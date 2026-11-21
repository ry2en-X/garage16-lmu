"""Runs the frontend tests of the lap analysis:

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
  * tests/js/v100.test.mjs                — V1.0: legal pages, sign-up form, setup guide, downloads
  * tests/js/v101.test.mjs                — V1.0.1: terms dialog, connected PCs, email banner, page titles
  * tests/js/v101_web.test.mjs            — V1.0.1: landing page, leaderboard, track names, "Your Best"
  * tests/js/v102_links.test.mjs          — V1.0.2: readable links, route table, old #/ links, 404, Copy link
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

JS_DIR = Path(__file__).resolve().parents[1] / "js"

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


@needs_jsdom
def test_v096_menu_fonts_dashboard_team_account_and_picker():
    result = _run("v096_ux.test.mjs")
    assert "V096-UX-OK" in result.stdout, result.stdout + result.stderr


@needs_jsdom
def test_v097_uncertainty_ranking_and_data_quality():
    """Confidence intervals of degradation / fuel effect, consistency without the trend,
    hints that respect the measuring accuracy, the corner ranking, the data-quality texts."""
    result = _run("v097.test.mjs")
    assert "V097-OK" in result.stdout, result.stdout + result.stderr


@needs_jsdom
def test_v099_decision_texts_and_the_integrity_tab():
    """Texts of the decision layer's findings, held / quarantined in the verdict line, the
    admin Integrity tab (quarantined laps, check again, release, audit log, chain check)."""
    result = _run("v099.test.mjs")
    assert "V099-OK" in result.stdout, result.stdout + result.stderr


@needs_jsdom
@pytest.mark.parametrize("lang", ["en", "de"])
def test_v100_legal_pages_sign_up_setup_guide_and_downloads(lang):
    """The legal pages (operator from the server, nothing invented when missing, both
    languages), the one-step sign-up form, the setup guide and the download page's next step."""
    result = _run("v100.test.mjs", lang)
    assert f"V100-{lang.upper()}-OK" in result.stdout, result.stdout + result.stderr
    assert "Error" not in result.stderr, result.stderr


@needs_jsdom
@pytest.mark.parametrize("lang", ["en", "de"])
def test_v101_terms_dialog_connected_pcs_and_titles(lang):
    """The terms dialog after signing in, the Connected PCs card (disconnect), the
    confirm-your-email banner, page titles, {code, message} errors shown as text."""
    result = _run("v101.test.mjs", lang)
    assert f"V101-{lang.upper()}-OK" in result.stdout, result.stdout + result.stderr
    assert "Error" not in result.stderr, result.stderr


@needs_jsdom
@pytest.mark.parametrize("lang", ["en", "de"])
def test_v101_website_pass(lang):
    """Landing page with features and sessions, leaderboard loading notice,
    tracks/classes without laps grouped at the end, column headings, one track name everywhere,
    "Your Best" as a table."""
    result = _run("v101_web.test.mjs", lang)
    assert f"V101-WEB-{lang.upper()}-OK" in result.stdout, result.stdout + result.stderr
    assert "Error" not in result.stderr, result.stderr


@needs_jsdom
def test_v102_readable_links():
    """Links built in one place (core/link.js), read back by the route table
    (core/routes.js), old #/ links forwarded, a real "page not found", the router on readable
    paths with page titles and "Copy link"."""
    result = _run("v102_links.test.mjs")
    assert "V102-LINKS-OK" in result.stdout, result.stdout + result.stderr
    assert "Error" not in result.stderr, result.stderr
