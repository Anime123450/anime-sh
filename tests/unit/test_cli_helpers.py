"""Pure CLI formatting helpers."""

from __future__ import annotations

from anime_sh.cli.main import _ep_list, _match_list_entry
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
