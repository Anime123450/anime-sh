"""The release notes extractor must survive a legacy console encoding.

`scripts/changelog_section.py` feeds the release workflow, and the workflow
treats a non-zero exit as "this version has no notes". The first run of it
reported nine versions as sectionless -- every one of which had a perfectly good
section. The real cause was that those nine describe provider request flows with
a real U+2192 arrow ("/search -> /ajax/episode/list"), and Python piped on
Windows picks cp1252 for stdout and raises encoding them. A backfill driven by
that output would have silently skipped exactly the releases with the most
interesting notes.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "changelog_section.py"
CHANGELOG = REPO / "CHANGELOG.md"


def _run(*args: str, encoding: str | None = None) -> subprocess.CompletedProcess[bytes]:
    env = dict(os.environ)
    if encoding:
        env["PYTHONIOENCODING"] = encoding
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        env=env,
    )


def _versions() -> list[str]:
    proc = _run("--list")
    assert proc.returncode == 0
    return [
        line.split("\t")[0]
        for line in proc.stdout.decode("utf-8").splitlines()
        if line.strip()
    ]


@pytest.mark.skipif(not CHANGELOG.is_file(), reason="needs the real changelog")
def test_every_documented_version_extracts_under_a_legacy_console_encoding():
    """cp1252 is what Windows hands a piped process. Every section in the real
    changelog has to come out anyway, because the release workflow reads this."""
    versions = _versions()
    assert len(versions) > 30, "expected the full release history"

    failed = []
    for version in versions:
        proc = _run(version, encoding="cp1252")
        if proc.returncode != 0 or not proc.stdout.strip():
            failed.append(version)
    assert not failed, f"no notes produced under cp1252 for: {failed}"


@pytest.mark.skipif(not CHANGELOG.is_file(), reason="needs the real changelog")
def test_arrow_bearing_notes_survive_the_round_trip():
    """0.2.2's notes contain the character that caused the original failure."""
    proc = _run("0.2.2", encoding="cp1252")
    assert proc.returncode == 0
    assert "→" in proc.stdout.decode("utf-8")


def test_a_missing_version_fails_loudly_rather_than_publishing_nothing():
    """The workflow keys off this exit code; an empty success would publish an
    empty release page."""
    proc = _run("99.99.99")
    assert proc.returncode == 1
    assert b"no section" in proc.stderr.lower()


def test_a_leading_v_is_accepted():
    """Tags are `v0.2.40`; changelog headings are `[0.2.40]`. The workflow passes
    the tag straight through."""
    with_v = _run("v0.2.40").stdout
    without = _run("0.2.40").stdout
    assert with_v == without
    assert with_v.strip()
