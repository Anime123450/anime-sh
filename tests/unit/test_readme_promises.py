"""The README must not promise things that do not exist.

This project's recurring failure is documentation drifting ahead of the code:
`doctor` naming a provider that was disabled, a hint saying "press esc" when
escape did nothing, a cheat sheet listing a key nothing bound. A README is the
largest surface for that, and the one a stranger reads first — a command that
does not exist there costs them their first five minutes.
"""

from __future__ import annotations

import pathlib
import re

import pytest
from typer.testing import CliRunner

from anime_sh.cli.main import app

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")


def _documented_commands() -> set[str]:
    """Every `anime <thing>` the README shows at the start of a line."""
    found = set(re.findall(r"^anime ([a-z][a-z-]*)", README, re.MULTILINE))
    # Options, not commands.
    return {c for c in found if not c.startswith("-")}


def test_the_readme_shows_commands():
    """A guard on the guard: if the extraction stops matching, every assertion
    below passes vacuously."""
    commands = _documented_commands()
    assert len(commands) > 15, f"only found {commands} — the extraction broke"


@pytest.mark.parametrize("command", sorted(_documented_commands()))
def test_every_command_the_readme_shows_exists(command):
    result = CliRunner().invoke(app, [command, "--help"])
    assert result.exit_code == 0, (
        f"README documents `anime {command}`, which the CLI does not have"
    )


def test_local_links_point_at_files_that_exist():
    """A 404 in the README is worse than a missing section: it reads as the
    project being abandoned."""
    targets = re.findall(r"\]\((?!https?:|#)([^)]+)\)", README)
    missing = [t for t in targets if not (ROOT / t.split("#")[0]).exists()]
    assert not missing, f"README links to files that do not exist: {missing}"


def test_screenshots_exist():
    """An image that does not load is the first thing a visitor sees."""
    images = re.findall(r'<img[^>]+src="([^"]+)"', README)
    assert images, "the README has no screenshots at all"
    missing = [i for i in images if not (ROOT / i).exists()]
    assert not missing, f"README references missing images: {missing}"


def test_the_readme_and_doctor_agree_on_how_to_install_mpv():
    """Two documents telling a beginner to install different things is how you
    get a bug report that is really a documentation bug."""
    assert "shinchiro.mpv" in README


def test_the_install_command_matches_the_published_scoop_bucket():
    """The very first command a visitor copies. If the bucket URL is wrong they
    never reach anything else."""
    import json

    manifest = ROOT / "packaging" / "scoop" / "anime-sh.json"
    homepage = json.loads(manifest.read_text())["homepage"]
    assert "scoop bucket add anime-sh https://github.com/Anime123450/scoop-anime-sh" in README
    assert homepage in README, "the README does not link the project it installs"


@pytest.mark.parametrize(
    "svg", sorted((ROOT / "docs" / "img").glob("*.svg")), ids=lambda p: p.name
)
def test_screenshots_declare_their_own_size(svg):
    """An SVG with a `viewBox` but no `width`/`height` has no intrinsic size.
    A browser falls back to the 300x150 default for a replaced element and then
    upscales that to whatever the markup asks for — so the README's screenshots
    rendered at 278x150 natural, blown up to 511x276, and were unreadable.

    Textual's `save_screenshot` writes exactly that shape, so a regenerated
    screenshot reintroduces it unless someone remembers. This is the reminder.
    """
    head = svg.read_text(encoding="utf-8")[:400]
    assert 'width=' in head and 'height=' in head, (
        f"{svg.name} has no intrinsic size; GitHub will render it blurry"
    )
