"""`doctor` has to say how to fix what it found, not only what is wrong.

"mpv: not found on PATH" is a diagnosis. The person most likely to be reading it
is the one who just downloaded a single .exe precisely so they would not have to
think about any of this, and telling them a tool is missing without telling them
how to get it leaves them exactly as stuck.
"""

from __future__ import annotations

import shutil

import pytest

from anime_sh.cli import doctor


@pytest.fixture
def only(monkeypatch):
    """Pretend exactly one package manager is installed."""

    def _install(name: str | None):
        monkeypatch.setattr(
            shutil, "which", lambda n, target=name: f"/x/{n}" if n == target else None
        )

    return _install


@pytest.mark.parametrize(
    "manager, expected",
    [("winget", "winget install"), ("scoop", "scoop install"),
     ("choco", "choco install")],
)
def test_the_hint_names_a_manager_you_actually_have(only, manager, expected, monkeypatch):
    """Naming a package manager the reader does not have is no better than
    naming none — so the choice is made from what is present, not from the
    platform."""
    monkeypatch.setattr("sys.platform", "win32")
    only(manager)
    assert expected in doctor.install_hint("ffmpeg")


def test_with_no_package_manager_it_still_says_something_useful(only, monkeypatch):
    """A clean Windows box has none of the three. Falling silent there abandons
    the exact user the standalone build exists for."""
    monkeypatch.setattr("sys.platform", "win32")
    only(None)
    hint = doctor.install_hint("mpv")
    assert "mpv" in hint and "PATH" in hint
    assert "install {" not in hint, "an unformatted template reached the user"


def test_winget_is_told_the_name_winget_actually_publishes(only, monkeypatch):
    """There is no package plainly called `mpv` on winget — the maintained
    Windows build is `shinchiro.mpv`, which is what the README already tells
    people to install. A hint that fails when pasted is worse than no hint,
    because it costs the reader a round of trying it, and one that disagrees
    with the README costs them a round of wondering which is right."""
    monkeypatch.setattr("sys.platform", "win32")
    only("winget")
    assert "shinchiro.mpv" in doctor.install_hint("mpv")


def test_the_hint_and_the_readme_name_the_same_winget_package(only, monkeypatch):
    """Two places telling a beginner to install different things is how you get
    a bug report that is really a documentation bug."""
    import pathlib

    monkeypatch.setattr("sys.platform", "win32")
    only("winget")
    readme = (pathlib.Path(__file__).resolve().parents[2] / "README.md").read_text(
        encoding="utf-8"
    )
    package = doctor.install_hint("mpv").split()[-1]
    assert package in readme, f"doctor suggests {package}, the README does not"


def test_the_missing_tool_checks_carry_the_hint(only, monkeypatch):
    """The hint has to reach the line the reader is actually looking at."""
    monkeypatch.setattr("sys.platform", "win32")
    only("scoop")
    player = doctor._check_player("mpv")
    ffmpeg = doctor._check_ffmpeg()
    assert not player.ok and "scoop install mpv" in player.detail
    assert not ffmpeg.ok and "scoop install ffmpeg" in ffmpeg.detail


def test_a_tool_that_is_present_reports_its_path_and_no_hint(monkeypatch):
    """Advice on installing something already installed reads as the check not
    working."""
    monkeypatch.setattr(shutil, "which", lambda n: "/usr/bin/" + n)
    check = doctor._check_ffmpeg()
    assert check.ok and check.detail == "/usr/bin/ffmpeg"
    assert "install" not in check.detail


def test_ffmpeg_is_described_as_optional(only, monkeypatch):
    """It is only needed by `anime download`. Presenting it as a hard
    requirement sends people installing a 200 MB tool to watch a stream."""
    monkeypatch.setattr("sys.platform", "win32")
    only(None)
    assert "download" in doctor._check_ffmpeg().detail
