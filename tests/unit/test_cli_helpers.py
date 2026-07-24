"""Pure CLI formatting helpers."""

from __future__ import annotations

import asyncio

import pytest
import typer

from anime_sh.cli import main as cli_main
from anime_sh.cli.main import _calendar, _ep_list, _match_list_entry, _seasonal, _trending
from anime_sh.domain.errors import MetadataError
from anime_sh.domain.models import Anime, AnimeId, ListEntry, Title


def test_ep_list_collapses_contiguous_runs():
    assert _ep_list([1.0, 2.0, 3.0, 4.0]) == "1–4"


def test_ep_list_keeps_gaps_and_specials_explicit():
    assert _ep_list([1.0, 2.0, 3.0, 5.0, 13.5]) == "1–3, 5, 13.5"


def test_ep_list_single_episode():
    assert _ep_list([1.0]) == "1"


def _entry(anilist, romaji, english=None):
    return ListEntry(
        anime=Anime(id=AnimeId(anilist=anilist),
                    title=Title(romaji=romaji, english=english)),
        status="CURRENT", progress=0,
    )


def test_match_list_entry_prefers_exact_title_over_spinoff():
    entries = [
        _entry(154587, "Sousou no Frieren", "Frieren: Beyond Journey's End"),
        _entry(170068, "Sousou no Frieren: Marumaru no Mahou"),
    ]
    hit = _match_list_entry(entries, "Frieren: Beyond Journey's End")
    assert hit is not None and hit.anime.id.anilist == 154587


def test_match_list_entry_none_when_not_on_list():
    entries = [_entry(1, "One Piece")]
    assert _match_list_entry(entries, "Totally Different Show") is None


# -- browse commands degrade gracefully when AniList is unreachable ---------- #
class _DeadMetadata:
    """Every catalog call fails as if the network/AniList were down."""

    async def trending(self, *, limit=30):
        raise MetadataError("AniList request failed: connect refused")

    async def seasonal(self, season, year):
        raise MetadataError("AniList request failed: connect refused")

    async def airing_schedule(self, start, end):
        raise MetadataError("AniList request failed: connect refused")


class _FakeContainer:
    def __init__(self):
        self.metadata = _DeadMetadata()
        self.closed = False

    async def aclose(self):
        self.closed = True


@pytest.fixture
def dead_container(monkeypatch):
    fake = _FakeContainer()
    monkeypatch.setattr(cli_main, "build_container", lambda *a, **k: fake)
    return fake


@pytest.mark.parametrize("run", [
    lambda: _trending(10, False),
    lambda: _seasonal(None, None, False),
    lambda: _calendar(7, False),
])
def test_browse_commands_exit_cleanly_when_metadata_down(dead_container, run):
    # A MetadataError must surface as a typer.Exit (friendly message + exit 2),
    # never as a raw traceback — and the container must still be closed.
    with pytest.raises(typer.Exit) as ei:
        asyncio.run(run())
    assert ei.value.exit_code == 2
    assert dead_container.closed
