"""V0.9.1 — English / German in the web app: nothing may be silently untranslated.

The web code writes every visible text as t("English") / tp("one", "other", n) and the German
versions live in web/js/i18n/de.js (see web/js/i18n.js and scripts/i18n_check.py). These tests
turn "someone added a text and forgot the German one" into a failing build, and also catch the
mistakes that only show up when a page renders: a missing import, a local variable called `t`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import i18n_check  # noqa: E402

# German entries that are deliberately the same as the English text (names and cognates).
SAME_IN_BOTH_LANGUAGES = {
    # V0.9.2: "Client" and "Download" (download page) are the usual German words too
    "Admin", "Analyse", "Client", "Dashboard", "Delta", "Discord", "Download", "GARAGE16", "Garage16", "Garage16 Best", "Lap ID",
    "Lap ID:", "Layout", "Leaderboard", "Leaderboards →", "PBs", "Status", "System", "Team Best", "Teams",
    # V0.9.3: the usual German statistics words
    "Median", "Trend",
    "Teams →", "Version", "ok", "{0}: {1} s @ {2}",
    # V0.9.4: the two dashboards keep their names; "Stint", "Tank", "Position" are German words too
    "LapTime Analyzer", "Stint Analyzer", "Stint", "Stints", "Stint {0}", "Tank", "Tank (L)", "Position", "Name", "{0} min",
}


@pytest.fixture(scope="module")
def result():
    return i18n_check.check()


def test_the_checker_finds_the_texts_at_all(result):
    # a regex that silently matches nothing would make every other test here pass for free
    assert len(result["used"]) > 400
    assert len(result["german"]) > 400
    assert "Choose Lap 1" in result["used"] and "Compare lap" in result["used"]
    assert "Toggle menu" in result["used"], "static texts in index.html (data-i18n) are collected too"


def test_every_text_in_the_code_has_a_german_entry(result):
    missing = result["problems"]["missing"]
    assert not missing, f"{len(missing)} texts have no German entry in web/js/i18n/de.js, e.g. {missing[:5]}"


def test_no_stale_german_entries(result):
    unused = result["problems"]["unused"]
    assert not unused, f"German entries nothing uses any more (delete them): {unused[:5]}"


def test_placeholders_match_between_english_and_german(result):
    bad = result["problems"]["placeholder"]
    assert not bad, f"{{0}}-style placeholders differ between English and German for: {bad[:5]}"


def test_no_german_text_is_empty(result):
    assert not result["problems"]["empty"]


def test_no_file_hides_the_translation_function_with_a_local_t(result):
    assert not result["problems"]["shadowed"], result["problems"]["shadowed"]


def test_no_file_uses_t_without_importing_it(result):
    assert not result["problems"]["unimported"], result["problems"]["unimported"]


def test_the_english_source_text_is_english():
    """The code's text is the key, so it must be the English one — German in the code would mean
    English users see German."""
    german_letters = re.compile(r"[äöüÄÖÜß]")
    offenders = [k for k in i18n_check.collect_keys() if german_letters.search(k)]
    assert not offenders, offenders[:5]


def test_only_cognates_are_left_untranslated():
    identical = {k for k, v in i18n_check.load_german().items() if k == v}
    assert identical <= SAME_IN_BOTH_LANGUAGES, sorted(identical - SAME_IN_BOTH_LANGUAGES)


def test_german_entries_look_german_not_like_a_copy_with_a_typo():
    """A translation that equals its key apart from letter case / whitespace is almost certainly a
    paste of the English text. (A German noun capitalised where the English word was not —
    "admin" -> "Admin" — is fine, so a changed first letter is allowed.)"""
    table = i18n_check.load_german()
    sloppy = [
        k for k, v in table.items()
        if k != v and k.strip().lower() == v.strip().lower() and k.strip()[:1].isupper() == v.strip()[:1].isupper()
    ]
    assert not sloppy, sloppy


def test_dictionary_file_is_strictly_one_entry_per_line_and_has_no_duplicates():
    # load_german() raises on a malformed line or a duplicate key
    assert i18n_check.load_german()


def test_index_html_marks_the_static_menu_for_translation():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    for label in ("Leaderboard", "Circuits", "LapTime Analyzer", "Stint Analyzer", "Dashboard", "Teams", "Account"):
        assert f'data-i18n="{label}"' in html, label
    assert 'data-i18n="not signed in"' in html
    assert 'id="langSwitchSlot"' in html, "the EN | DE switch needs its slot in the menu"
    assert '<html lang="en">' in html, "English is the default language"


def test_the_router_starts_the_language_popup_and_switch():
    router = (ROOT / "web" / "js" / "router.js").read_text(encoding="utf-8")
    assert "ensureLanguageChosen()" in router and "createLanguageSwitch()" in router and "applyStaticTranslations()" in router
