"""`anime mark` writes to AniList, and AniList has no undo.

The command *sets* progress rather than advancing it, so every one of these is
a way to destroy watch history with a plausible-looking command. All three were
real: found by running the command with odd numbers and then looking at what had
actually changed on the account.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from anime_sh.cli import main as cli_main
from anime_sh.cli.main import app
from anime_sh.domain.models import AnimeId, WatchProgress

from .fakes import make_anime

runner = CliRunner()


class _Search:
    async def best_match(self, query):
        return make_anime()


class _Library:
    def __init__(self):
        self.marked: list[tuple[float, bool]] = []

    async def mark_watched(self, anime, up_to, *, single=False):
        self.marked.append((up_to, single))
        return [up_to] if single else [float(n) for n in range(1, int(up_to) + 1)]


class _Tracker:
    """Stands in for AniList, already showing the user 28 episodes in."""

    name = "anilist"

    def __init__(self, progress: int | None = 28):
        self.progress = progress
        self.pushed: list[int] = []

    async def pull(self):
        if self.progress is None:
            return []
        from datetime import datetime, timezone

        return [WatchProgress(anime_id=AnimeId(anilist=154587),
                              episode=float(self.progress), position_s=0,
                              duration_s=0, updated_at=datetime.now(timezone.utc),
                              completed=True)]

    async def push(self, progress, *, total=None):
        self.pushed.append(int(progress.episode))


class _Container:
    def __init__(self, tracker):
        self.search = _Search()
        self.library_service = _Library()
        self.tracker = tracker

    async def aclose(self):
        pass


@pytest.fixture
def wired(monkeypatch):
    def _build(tracker=None):
        c = _Container(_Tracker() if tracker is None else tracker)
        monkeypatch.setattr(cli_main, "build_container", lambda *a, **k: c)
        return c

    return _build


def test_marking_below_your_current_progress_is_refused(wired):
    """The one that cost real data. AniList was at 28; `mark -e 5` set it to 5
    and dropped the show from "completed" back to "watching". Nothing warned,
    and there is no undo."""
    c = wired()
    result = runner.invoke(app, ["mark", "frieren", "-e", "5"])
    assert result.exit_code == 2
    assert "28" in result.output and "lower" in result.output
    assert c.tracker.pushed == [], "AniList was written to anyway"
    assert c.library_service.marked == [], "local rows were written before refusing"


def test_force_is_how_you_say_you_meant_it(wired):
    c = wired()
    result = runner.invoke(app, ["mark", "frieren", "-e", "5", "--force"])
    assert result.exit_code == 0
    assert c.tracker.pushed == [5]


def test_marking_forward_is_never_blocked(wired):
    """The guard must only catch the destructive direction. Catching up is the
    entire point of the command."""
    c = wired()
    result = runner.invoke(app, ["mark", "frieren", "-e", "28"])
    assert result.exit_code == 0
    assert c.tracker.pushed == [28]


def test_a_single_mark_never_touches_anilist(wired):
    """`--single` says "this one episode", AniList only understands "N episodes
    finished". Sending one as the other is what turned a completed show into
    "watching, 5" when marking a special numbered 5.5."""
    c = wired()
    result = runner.invoke(app, ["mark", "frieren", "-e", "5.5", "--single"])
    assert result.exit_code == 0
    assert c.library_service.marked == [(5.5, True)]
    assert c.tracker.pushed == [], "a single mark was pushed as overall progress"
    assert "AniList" not in result.output, "claimed a sync that did not happen"


def test_an_unreadable_anilist_does_not_block_the_mark(wired):
    """Failing to read the current progress is not evidence that marking is
    safe, but it is also not a reason to refuse — the local mark is the part
    the user asked for, and offline has to keep working."""

    class _Broken(_Tracker):
        async def pull(self):
            raise RuntimeError("network down")

    c = wired(_Broken())
    result = runner.invoke(app, ["mark", "frieren", "-e", "5"])
    assert result.exit_code == 0
    assert c.library_service.marked == [(5.0, False)]


def test_the_message_reports_what_was_written(wired):
    """It used to echo the request instead. When the two could differ, it
    printed things like "episodes 1-0 watched"."""
    c = wired()
    result = runner.invoke(app, ["mark", "frieren", "-e", "3", "--force"])
    assert "1–3" in result.output
