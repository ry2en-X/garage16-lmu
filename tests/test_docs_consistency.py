"""Documentation can't silently drift from the product (V0.8.6 §9–§11):
the friend guide asks for no developer tooling, every client message it
quotes really exists in the client code, and every file the docs point at
exists."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRIENDS = (ROOT / "docs" / "FRIENDS_GUIDE.md").read_text(encoding="utf-8")
# Markdown hard-wraps lines; compare quoted messages with whitespace collapsed.
FRIENDS_FLAT = " ".join(FRIENDS.split())
def _client_string_literals() -> str:
    """Every string literal in the client, as Python actually sees it —
    adjacent literals ("a " "b") are already merged by the parser, so a
    message wrapped across source lines still matches."""
    import ast

    pieces = []
    for path in (ROOT / "client").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                pieces.append(node.value)
    return "\n".join(pieces)


CLIENT_SOURCE = _client_string_literals()


def test_friends_guide_asks_for_no_developer_tooling():
    """§9: no Python install, pip, terminal commands, JSON editing or
    environment variables for normal users."""
    forbidden = ["pip install", "python -m", "py -m", "python.org", "requirements.txt",
                 "client_config.json", "setx ", "Umgebungsvariable", "Eingabeaufforderung im Projektordner"]
    hits = [f for f in forbidden if f in FRIENDS]
    assert hits == []


def test_friends_guide_covers_the_five_step_flow():
    assert "1. Installieren → 2. Starten → 3. Account verbinden → 4. LMU starten → 5. Fahren" in FRIENDS
    for heading in ("## 1. Installieren", "## 2. Starten", "## 3. Account verbinden", "## 4. LMU starten", "## 5. Fahren"):
        assert heading in FRIENDS


def test_every_client_message_quoted_in_the_friends_guide_exists_in_the_code():
    quoted = [
        "Connect Garage16 to your account",
        "Couldn't reach that server",
        "Can't reach the Garage16 server right now",
        "A new version of Garage16",
        "Your recorded laps are kept and will upload after you update",
        "Open download page",
        "Garage16 rejected your login",
        "Reconnect account",
        "isn't secure enough to switch automatically",
        "Please ask whoever runs your Garage16 server for updated connection details",
        "Your Garage16 server has moved — switched to",
        "Waiting for LMU to start...",
        "Connected to LMU shared memory.",
    ]
    for phrase in quoted:
        assert phrase in FRIENDS_FLAT, f"guide no longer mentions: {phrase!r}"
        assert phrase in CLIENT_SOURCE, f"guide quotes a message the client doesn't contain: {phrase!r}"


def test_files_the_docs_point_at_exist():
    for doc in ("README.md", "docs/ADMIN_GUIDE.md", "docs/FRIENDS_GUIDE.md", "docs/CLIENT_BUILD.md", "docs/CLIENT_INSTALL.md"):
        text = (ROOT / doc).read_text(encoding="utf-8")
        for ref in set(re.findall(r"`((?:docs|packaging|scripts|discord_bot)/[A-Za-z0-9_./-]+\.[a-z0-9]+)`", text)):
            assert (ROOT / ref).exists(), f"{doc} references missing file {ref}"


def test_server_settings_named_in_the_docs_exist():
    # Settings are read in several server modules (config.py, crypto.py,
    # ...) and by the container scripts — search all of them.
    config = "\n".join(
        [p.read_text(encoding="utf-8") for p in (ROOT / "server").rglob("*.py")]
        + [(ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8"), (ROOT / "docker-compose.yml").read_text(encoding="utf-8")]
    )
    for doc in ("docs/ADMIN_GUIDE.md", "docs/CLIENT_BUILD.md", ".env.example"):
        for var in set(re.findall(r"LMU_GARAGE_[A-Z_]+", (ROOT / doc).read_text(encoding="utf-8"))):
            assert var in config or var in ("LMU_GARAGE_TEST_PG_URL",), f"{doc} mentions {var}, unknown to the server code / docker files"


def test_discord_link_ui_texts_quoted_in_the_friends_guide_exist_in_the_web_code():
    web = " ".join((ROOT / "web" / "js" / "pages" / "account.js").read_text(encoding="utf-8").split())
    guide = " ".join(FRIENDS.split())
    for phrase in ("Account → Link Discord", "Generate link code", "Copy command", "Unlink Discord", "linked"):
        assert phrase.replace("Account → ", "") in web or phrase in web, phrase
        assert phrase in guide, phrase
    assert "/unlink" in guide


def test_discord_bot_messages_quoted_in_the_friends_guide_exist_in_the_code():
    source = " ".join((ROOT / "server" / "discord_link.py").read_text(encoding="utf-8").split())
    guide = " ".join(FRIENDS.split())
    for phrase in ("That code is invalid or expired", "Run `/unlink` first", "Too many wrong codes"):
        assert phrase in source, phrase
        assert phrase in guide, phrase
