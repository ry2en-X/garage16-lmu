#!/usr/bin/env python3
"""scripts/i18n_check.py — are all English texts in the web app translated to German? (V0.9.1)

The web app writes every visible text as t("English text") / tp("one", "other", n)
(web/js/i18n.js); the German versions live in web/js/i18n/de.js, one entry per line:

    "English text": "Deutscher Text",

This script collects every t()/tp() key from web/js and compares it with de.js:
  missing     used in the code, no German entry          -> shown in English to German users
  unused      German entry nobody uses any more          -> stale, delete it
  placeholder the {0} / {name} placeholders differ       -> would print "{0}" or drop a value
  shadowed    a file that imports t() also has a local variable called t -> t("...") would crash
  unimported  a file calls t() / tp() without importing it       -> ReferenceError when the page renders

Usage:  python scripts/i18n_check.py        (exit code 1 if anything is wrong)
        python scripts/i18n_check.py --list-missing   (the English keys still to translate)
tests/test_i18n.py runs the same checks, so CI fails on a forgotten translation.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB_JS = ROOT / "web" / "js"
DE_FILE = WEB_JS / "i18n" / "de.js"
NOT_SOURCE = {"de.js"}
INDEX_HTML = ROOT / "web" / "index.html"

_STR = r'"((?:[^"\\\n]|\\.)*)"'
_T_CALL = re.compile(r'(?<![\w.$])t\(\s*' + _STR)
_TP_CALL = re.compile(r'(?<![\w.$])tp\(\s*' + _STR + r'\s*,\s*' + _STR)
_DE_LINE = re.compile(r'^\s*' + _STR + r'\s*:\s*' + _STR + r',?\s*$')
_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_DATA_I18N = re.compile(r'data-i18n(?:-aria-label)?="([^"]*)"')
_SHADOW = [
    re.compile(r"\(\s*t\s*\)\s*=>"), re.compile(r"\(\s*t\s*,"), re.compile(r",\s*t\s*\)\s*=>"),
    re.compile(r"\b(?:const|let|var)\s+t\b"), re.compile(r"for\s*\(\s*(?:const|let|var)\s+t\b"),
    re.compile(r"function\s*\w*\s*\(\s*(?:[^()=]*,\s*)?t\s*(?:,|\))"), re.compile(r"\bcatch\s*\(\s*t\s*\)"),
]


def _unescape(raw: str) -> str:
    return json.loads('"' + raw + '"')


def source_files() -> list:
    return sorted(p for p in WEB_JS.rglob("*.js") if p.name not in NOT_SOURCE)


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?<![:\"'`\\])//[^\n]*", "", text)


def collect_keys() -> dict:
    """{english key: [file:line, ...]}"""
    keys: dict = {}
    for path in source_files():
        text = _strip_comments(path.read_text(encoding="utf-8"))
        for number, line in enumerate(text.split("\n"), 1):
            found = [m.group(1) for m in _T_CALL.finditer(line)]
            for m in _TP_CALL.finditer(line):
                found += [m.group(1), m.group(2)]
            for raw in found:
                keys.setdefault(_unescape(raw), []).append(f"{path.relative_to(ROOT).as_posix()}:{number}")
    # static markup: data-i18n="Text" and data-i18n-aria-label="Text" in index.html
    html = INDEX_HTML.read_text(encoding="utf-8")
    for number, line in enumerate(html.split("\n"), 1):
        for m in _DATA_I18N.finditer(line):
            keys.setdefault(m.group(1), []).append(f"web/index.html:{number}")
    return keys


def load_german() -> dict:
    """{english key: german text}; raises on a malformed line or a duplicate key."""
    table: dict = {}
    for number, line in enumerate(DE_FILE.read_text(encoding="utf-8").split("\n"), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "export", "}", "{")):
            continue
        match = _DE_LINE.match(line)
        if not match:
            raise ValueError(f"{DE_FILE.name}:{number}: not an entry of the form \"English\": \"Deutsch\",  ->  {line[:80]}")
        key, value = _unescape(match.group(1)), _unescape(match.group(2))
        if key in table:
            raise ValueError(f"{DE_FILE.name}:{number}: duplicate key {key!r}")
        table[key] = value
    return table


def placeholders(text: str) -> list:
    return sorted(_PLACEHOLDER.findall(text))


def shadowed_files() -> list:
    """Files that import t() and also declare something called t (it would hide the function)."""
    bad = []
    for path in source_files():
        text = _strip_comments(path.read_text(encoding="utf-8"))
        if not re.search(r"import\s*\{[^}]*\bt\b[^}]*\}\s*from\s*\"[^\"]*i18n\.js\"", text):
            continue
        for number, line in enumerate(text.split("\n"), 1):
            if "import" in line and "i18n.js" in line:
                continue
            if any(p.search(line) for p in _SHADOW):
                bad.append(f"{path.relative_to(ROOT).as_posix()}:{number}: {line.strip()[:90]}")
    return bad


def unimported_files() -> list:
    """Files that call t() / tp() but do not import them from i18n.js."""
    bad = []
    for path in source_files():
        if path.name == "i18n.js":
            continue
        text = _strip_comments(path.read_text(encoding="utf-8"))
        imported = re.search(r"import\s*\{([^}]*)\}\s*from\s*\"[^\"]*i18n\.js\"", text)
        names = {n.strip() for n in imported.group(1).split(",")} if imported else set()
        for name in ("t", "tp"):
            if re.search(r"(?<![\w.$])" + name + r"\(", text) and name not in names:
                bad.append(f"{path.relative_to(ROOT).as_posix()}: calls {name}() without importing it")
    return bad


def check() -> dict:
    used = collect_keys()
    german = load_german()
    problems = {
        "missing": sorted(k for k in used if k not in german),
        "unused": sorted(k for k in german if k not in used),
        "placeholder": sorted(k for k in used if k in german and placeholders(k) != placeholders(german[k])),
        "empty": sorted(k for k in german if not german[k].strip() and k.strip()),
        "shadowed": shadowed_files(),
        "unimported": unimported_files(),
    }
    return {"used": used, "german": german, "problems": problems}


def main(argv: list) -> int:
    result = check()
    problems = result["problems"]
    if "--list-missing" in argv:
        for key in problems["missing"]:
            print(json.dumps(key, ensure_ascii=False))
        return 0
    print(f"{len(result['used'])} texts in the code, {len(result['german'])} German entries")
    bad = False
    for name, items in problems.items():
        if items:
            bad = True
            print(f"\n{name} ({len(items)}):")
            for item in items:
                print("  " + (json.dumps(item, ensure_ascii=False) if name not in ("shadowed", "unimported") else item))
    print("\nOK" if not bad else "\nFAILED")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
