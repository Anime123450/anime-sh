"""No real credentials in a public repo.

anime-sh deliberately ships none: `anime auth login` walks each user through
registering their own AniList app, and the token lands 0600 in their own config
directory. That promise is only worth anything if the repository keeps it, and
the way it gets broken is never malice — it is someone reaching for the handiest
value to type while writing a test. One did exactly that and carried a real
AniList client id for months.

This checks the working tree. History is a separate problem: a value committed
last year is still there after the file changes, and only a rewrite removes it.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SEARCH_DIRS = ("src", "tests", "docs", "scripts", "packaging")
SUFFIXES = {".py", ".md", ".toml", ".yaml", ".yml", ".json", ".ps1", ".sh", ".rb"}
SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", "build", "dist", ".claude"}

# Values that are obviously placeholders. A test needs *something* in these
# fields, so the rule is "unmistakably fake", not "absent".
ALLOWED = {
    "00000", "12345", "00000000", "sekret", "secret", "token", "test",
    "fake", "dummy", "changeme", "xxx", "your_id", "your-id",
    "YOUR_ID", "YOUR_SECRET", "<YOUR_ID>", "<YOUR_SECRET>",
}

# AniList issues five-digit client ids; a bare one is the shape that slipped in.
# \W{0,8} rather than a hand-listed separator class: the real leak was written
# as an equality assertion on a subscript, and a class that forgot `]` sailed
# straight past it. (Writing the offending value into this comment would have
# put it back in the repository - which is how the first draft failed its own
# check.)
_CLIENT_ID = re.compile(r"client[_-]?id\W{0,8}[\"'](\d{4,8})[\"']", re.I)
# The leak also appeared as a positional argument, where the key name is not on
# the line at all.
_EXCHANGE = re.compile(r"exchange_code\(\s*[\"'](\d{4,8})[\"']")
_SECRETISH = re.compile(
    r"(client[_-]?secret|access[_-]?token|api[_-]?key)[\"'\s:=]+[\"']([^\"']{12,})[\"']",
    re.I,
)


def _obviously_fake(value: str) -> bool:
    """Whether a numeric id reads as a placeholder rather than a real one.

    Two distinct digits or fewer covers what people actually type when they
    need a number that is clearly not real - 0000, 1111, 4242, 12345678 - while
    a genuine five-digit AniList id has more variety than that.
    """
    return value in ALLOWED or len(set(value)) <= 2


def _files():
    for name in SEARCH_DIRS:
        base = ROOT / name
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.suffix.lower() not in SUFFIXES:
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            yield path
    for name in ("README.md", "CHANGELOG.md", "NOTES.md", "pyproject.toml"):
        if (ROOT / name).exists():
            yield ROOT / name


@pytest.fixture(scope="module")
def sources():
    return [(p, p.read_text(encoding="utf-8", errors="replace")) for p in _files()]


def test_the_scan_actually_reads_files(sources):
    """A guard on the guard: if the walk breaks, everything below passes
    vacuously."""
    assert len(sources) > 40, f"only found {len(sources)} files to scan"


def test_no_real_looking_client_id(sources):
    found = []
    for path, text in sources:
        for pattern in (_CLIENT_ID, _EXCHANGE):
            for match in pattern.finditer(text):
                value = match.group(1)
                if _obviously_fake(value):
                    continue
                found.append(f"{path.relative_to(ROOT)}: client id {value!r}")
    assert not found, (
        "a real-looking client id is in the repository - use an obviously fake "
        f"one: {found}"
    )


def test_no_secret_or_token_literals(sources):
    found = []
    for path, text in sources:
        for match in _SECRETISH.finditer(text):
            value = match.group(2)
            if value.lower() in ALLOWED:
                continue
            # Placeholders people actually write, and env-var indirection.
            if value.startswith(("<", "${", "$", "{{")) or value.isupper():
                continue
            if re.fullmatch(r"[A-Za-z.\-_ ]{0,24}", value):
                continue  # prose, not a credential
            found.append(f"{path.relative_to(ROOT)}: {match.group(1)}={value[:12]}…")
    assert not found, f"credential-shaped literals in the repository: {found}"


def test_the_users_own_email_is_not_in_the_repository(sources):
    """Name and age may appear where a package genuinely needs them; contact
    details and accounts should not."""
    # Built from parts so this file does not trip its own check.
    needle = "eco" + "coder"
    leaked = [
        str(path.relative_to(ROOT))
        for path, text in sources
        if needle in text.lower() and path.name != pathlib.Path(__file__).name
    ]
    assert not leaked, f"personal email address appears in: {leaked}"
