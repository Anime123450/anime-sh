"""`anime history` has to answer in the clock the user was watching by.

`watched_at` is stored UTC -- `datetime.now(timezone.utc).isoformat()` -- and
every other view of that field converts before showing it. The history table
formatted it raw, so an episode finished at 22:08 local was listed as 16:38, and
anything watched late in the evening east of UTC was filed under the previous
day.

`anime wrapped` already states the principle for the same column: "a streak that
breaks because of a timezone is a wrong answer to a question about someone's
habits." The plainest view of that column was the one still answering in UTC.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from typer.testing import CliRunner

from anime_sh.cli import main as cli_main
from anime_sh.cli.main import app
from anime_sh.domain.models import HistoryItem

from .fakes import make_anime

runner = CliRunner()

# 2026-10-02 16:38 UTC. At +05:30 that is 22:08 the same day; at -08:00 it is
# 08:38. The instant is fixed, so whatever the local zone, the rendered string
# has to be this instant expressed in it.
WATCHED_AT = datetime(2026, 10, 2, 16, 38, tzinfo=timezone.utc)


class _Library:
    async def history(self, *, limit=50):
        return [
            HistoryItem(
                anime=make_anime(1, "Pokemon"),
                episode=1.0,
                watched_at=WATCHED_AT,
                provider="hianime",
                seconds_watched=1339,
            )
        ]


class _Container:
    def __init__(self):
        self.library_service = _Library()

    async def aclose(self):
        return None


@pytest.fixture
def container(monkeypatch):
    fake = _Container()
    monkeypatch.setattr(cli_main, "build_container", lambda *a, **k: fake)
    return fake


def test_history_prints_the_local_time_of_the_watch(container):
    """Runs in whatever zone the suite is in. Vacuous only in UTC, where the two
    renderings coincide -- which is why the forced-zone test below exists."""
    result = runner.invoke(app, ["history", "--limit", "1"])
    assert result.exit_code == 0

    local = WATCHED_AT.astimezone().strftime("%Y-%m-%d %H:%M")
    assert local in result.stdout, f"expected the local rendering {local!r}"


def test_the_json_output_keeps_the_unambiguous_instant(container):
    """The table is for a person and gets their clock; the JSON is for a program
    and keeps the offset, so a consumer can convert for itself. Localising the
    machine-readable field instead would have thrown the offset away."""
    result = runner.invoke(app, ["history", "--limit", "1", "--json"])
    assert result.exit_code == 0
    assert "2026-10-02T16:38:00+00:00" in result.stdout


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="TZ cannot be forced on Windows")
def test_a_late_evening_watch_is_not_filed_under_the_day_before(container):
    """The half that loses a whole day, forced to a zone where it happens.

    CI runs in UTC, where local time *is* the stored time and neither half of
    this bug can be reproduced. At +05:30 a watch at 00:27 local is 18:57 UTC on
    the previous date, so the raw rendering reported both the wrong time and the
    wrong day.
    """
    before = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Kolkata"
    time.tzset()
    try:
        assert WATCHED_AT.astimezone().strftime("%Y-%m-%d %H:%M") == "2026-10-02 22:08"

        result = runner.invoke(app, ["history", "--limit", "1"])
        assert result.exit_code == 0
        assert "2026-10-02 22:08" in result.stdout
        assert "16:38" not in result.stdout, "that is the UTC wall time, not the user's"

        # The date-rollover half: 20:00 UTC is already the next day at +05:30.
        rolls_over = WATCHED_AT.replace(hour=20)
        assert rolls_over.astimezone().strftime("%Y-%m-%d %H:%M") == "2026-10-03 01:30"
        assert rolls_over.date() != rolls_over.astimezone().date()
    finally:
        if before is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = before
        time.tzset()


def test_every_history_row_is_converted_not_just_the_first():
    """The conversion is inside the row loop, so a second row must get it too --
    a fix applied to a header or a single sample row would pass the test above
    and still leave the table wrong."""
    rows = [
        HistoryItem(
            anime=make_anime(n, f"Show {n}"),
            episode=float(n),
            watched_at=WATCHED_AT - timedelta(days=n),
            provider="hianime",
            seconds_watched=1200,
        )
        for n in (1, 2, 3)
    ]

    class _Multi(_Container):
        def __init__(self):
            super().__init__()

            class _L:
                async def history(self, *, limit=50):
                    return rows

            self.library_service = _L()

    import pytest as _pytest

    monkeypatch = _pytest.MonkeyPatch()
    monkeypatch.setattr(cli_main, "build_container", lambda *a, **k: _Multi())
    try:
        result = runner.invoke(app, ["history", "--limit", "3"])
        assert result.exit_code == 0
        for row in rows:
            want = row.watched_at.astimezone().strftime("%Y-%m-%d %H:%M")
            assert want in result.stdout, f"row {row.episode:g} was not converted"
    finally:
        monkeypatch.undo()
