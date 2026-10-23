"""FranchiseService: watch-order map, suggested-next rules, episode info.

All offline: a fake metadata source serves a Re:ZERO-shaped relation graph and
a fake library serves progress rows.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from anime_sh.app.franchise import (
    EntryStatus,
    EpisodeInfo,
    FranchiseService,
    parse_jikan_episodes,
    parse_streaming_titles,
)
from anime_sh.domain.errors import AnimeShError
from anime_sh.domain.models import (
    Anime,
    AnimeId,
    Format,
    Status,
    Title,
    WatchProgress,
)
from anime_sh.infra.metadata.anilist import AniListMetadata, _start_date, _to_anime

S1, S2, S2P2, S3, SNOW, BOND, BREAK = 1, 2, 3, 4, 5, 6, 7


def A(i, name, fmt=Format.TV, d=None, eps=12, status=Status.FINISHED, **kw):
    kw.setdefault("year", d.year if d else None)
    return Anime(
        id=AnimeId(anilist=i, mal=i + 1000), title=Title(romaji=name), format=fmt,
        status=status, episode_count=eps, start_date=d, **kw,
    )


CAST = {
    S1: A(S1, "Re:ZERO S1", d=date(2016, 4, 4), eps=25),
    S2: A(S2, "Re:ZERO S2", d=date(2020, 7, 8), eps=13),
    S2P2: A(S2P2, "Re:ZERO S2 Part 2", d=date(2021, 1, 6), eps=12),
    S3: A(S3, "Re:ZERO S3", d=date(2024, 10, 2), eps=8),
    SNOW: A(SNOW, "Memory Snow", Format.MOVIE, date(2018, 10, 6), eps=1),
    BOND: A(BOND, "Frozen Bond", Format.OVA, date(2019, 10, 4), eps=1),
    BREAK: A(BREAK, "Break Time", Format.ONA, date(2016, 10, 12), eps=25),
}
EDGES = {
    S1: [("SEQUEL", S2), ("SIDE_STORY", SNOW), ("SIDE_STORY", BOND),
         ("SPIN_OFF", BREAK), ("ADAPTATION", 99), ("ALTERNATIVE", 98)],
    S2: [("PREQUEL", S1), ("SEQUEL", S2P2)],
    S2P2: [("PREQUEL", S2), ("SEQUEL", S3)],
    S3: [("PREQUEL", S2P2)],
    SNOW: [("PARENT", S1)],
    BOND: [("PARENT", S1)],
    BREAK: [("PARENT", S1), ("SEQUEL", 1000)],  # must never be walked
}


class FakeMeta:
    def __init__(self, cast=None, edges=None, fail=None):
        self.cast, self.edges, self.fail = cast or CAST, edges or EDGES, fail
        self.calls: list[int] = []
        self.titles: list[str] = []
        self.jikan: list[dict] = []

    async def relations(self, id):
        self.calls.append(id.anilist)
        if self.fail:
            raise self.fail
        return [(r, self.cast[t]) for r, t in self.edges.get(id.anilist, [])
                if t in self.cast]

    async def streaming_episode_titles(self, id):
        return self.titles

    async def jikan_episodes(self, mal_id):
        return self.jikan


class FakeLibrary:
    def __init__(self):
        self.rows: dict[int, list[WatchProgress]] = {}
        self.marked: list[tuple[int, float]] = []

    async def progress_for(self, anime_id):
        return self.rows.get(anime_id.anilist, [])

    async def mark_watched(self, anime, up_to, *, single=False):
        self.marked.append((anime.id.anilist, up_to))
        self.watch(anime.id.anilist, int(up_to))
        return [float(n) for n in range(1, int(up_to) + 1)]

    def watch(self, aid, n, *, completed=True):
        now = datetime.now(timezone.utc)
        self.rows[aid] = [
            WatchProgress(AnimeId(aid), float(e), 1, 1, now, completed=completed)
            for e in range(1, n + 1)
        ]


def svc(meta=None, lib=None):
    return FranchiseService(meta or FakeMeta(), lib or FakeLibrary())


def names(fr):
    return [e.anime.title.romaji for e in fr.entries]


# -- structure ----------------------------------------------------------------
async def test_orders_by_start_date_and_flags_main_line():
    fr = await svc().build(CAST[S1])
    assert names(fr) == [
        "Re:ZERO S1", "Break Time", "Memory Snow", "Frozen Bond",
        "Re:ZERO S2", "Re:ZERO S2 Part 2", "Re:ZERO S3",
    ]
    by = {e.anime.id.anilist: e for e in fr.entries}
    assert [by[i].main_line for i in (S1, S2, S2P2, S3)] == [True] * 4
    assert not any(by[i].main_line for i in (SNOW, BOND, BREAK))
    assert by[S1].kind == by[S3].kind == "season"
    assert by[SNOW].kind == "movie" and by[BOND].kind == "ova"
    assert by[BREAK].kind == "spin-off"
    assert not fr.truncated


async def test_cycles_do_not_duplicate_and_spinoffs_are_not_walked():
    meta = FakeMeta()
    fr = await svc(meta).build(CAST[S2])      # enter in the middle
    ids = [e.anime.id.anilist for e in fr.entries]
    assert sorted(ids) == sorted(CAST) and len(set(ids)) == len(ids)
    assert BREAK not in meta.calls            # one hop only
    assert len(meta.calls) == len(set(meta.calls))
    assert fr.current.anime.id.anilist == S2 and fr.current.is_current


async def test_entering_at_a_side_story_still_finds_the_main_line():
    fr = await svc().build(CAST[SNOW])
    assert fr.current.anime.id.anilist == SNOW and not fr.current.main_line
    assert [e.anime.id.anilist for e in fr.entries if e.main_line] == [S1, S2, S2P2, S3]
    assert fr.suggested.anime.id.anilist == S1   # nothing watched -> first main


async def test_music_skipped_and_undated_go_last():
    cast = dict(CAST)
    cast[50] = A(50, "OP Single", Format.MUSIC, date(2016, 5, 1))
    cast[51] = A(51, "Mystery OVA", Format.OVA)           # no date, no year
    cast[52] = A(52, "Year Only", Format.SPECIAL, None, year=2017)
    edges = {**EDGES, S1: EDGES[S1] + [("SIDE_STORY", 50), ("SIDE_STORY", 51),
                                       ("SIDE_STORY", 52)]}
    fr = await svc(FakeMeta(cast, edges)).build(CAST[S1])
    assert "OP Single" not in names(fr)
    assert names(fr)[-1] == "Mystery OVA"
    assert names(fr).index("Year Only") < names(fr).index("Memory Snow")


async def test_summary_kind_and_recap_sorts_after_same_year_entry():
    cast = dict(CAST)
    cast[60] = A(60, "Recap Movie", Format.MOVIE, date(2016, 3, 1), eps=1)
    edges = {**EDGES, S1: EDGES[S1] + [("SUMMARY", 60)]}
    fr = await svc(FakeMeta(cast, edges)).build(CAST[S1])
    by = {e.anime.title.romaji: e for e in fr.entries}
    assert by["Recap Movie"].kind == "summary" and not by["Recap Movie"].main_line
    assert names(fr).index("Recap Movie") > names(fr).index("Re:ZERO S1")


# -- suggested next -----------------------------------------------------------
def idx(fr):
    return fr.suggested.anime.id.anilist if fr.suggested else None


async def test_nothing_watched_suggests_current_if_main_line():
    s = svc()
    assert idx(await s.build(CAST[S2])) == S2
    assert idx(await s.build(CAST[S1])) == S1


async def test_s1_done_suggests_s2():
    lib = FakeLibrary()
    lib.watch(S1, 25)
    fr = await svc(lib=lib).build(CAST[S3])   # entering at S3 still points on from S1
    assert fr.entries[0].status is EntryStatus.COMPLETED
    assert idx(fr) == S2


async def test_s2_watched_elsewhere_only_suggests_s2_part_2_not_s1():
    lib = FakeLibrary()
    lib.watch(S2, 13)
    fr = await svc(lib=lib).build(CAST[S1])
    assert idx(fr) == S2P2
    by = {e.anime.id.anilist: e for e in fr.entries}
    assert by[S1].status is EntryStatus.NOT_STARTED


async def test_s2_half_done_suggests_s2():
    lib = FakeLibrary()
    lib.watch(S1, 25)
    lib.watch(S2, 5)
    fr = await svc(lib=lib).build(CAST[S1])
    by = {e.anime.id.anilist: e for e in fr.entries}
    assert by[S2].status is EntryStatus.IN_PROGRESS and by[S2].watched == 5
    assert idx(fr) == S2


async def test_in_progress_main_line_beats_in_progress_side_story():
    lib = FakeLibrary()
    lib.watch(SNOW, 1, completed=False)       # half a movie
    lib.watch(S2, 3)
    assert idx(await svc(lib=lib).build(CAST[S1])) == S2


async def test_in_progress_side_story_alone_is_suggested():
    lib = FakeLibrary()
    lib.watch(BOND, 1, completed=False)
    assert idx(await svc(lib=lib).build(CAST[S1])) == BOND


async def test_side_story_never_outranks_unwatched_main_line_entry():
    lib = FakeLibrary()
    lib.watch(S1, 25)                          # Memory Snow, Frozen Bond unwatched
    assert idx(await svc(lib=lib).build(CAST[S1])) == S2


async def test_all_main_done_suggests_first_unwatched_side_story():
    lib = FakeLibrary()
    for i in (S1, S2, S2P2, S3):
        lib.watch(i, CAST[i].episode_count)
    fr = await svc(lib=lib).build(CAST[S1])
    assert idx(fr) == BREAK     # earliest-dated unwatched non-main entry
    lib.watch(BREAK, 25); lib.watch(SNOW, 1); lib.watch(BOND, 1)
    assert (await svc(lib=lib).build(CAST[S1])).suggested_index is None


async def test_tracker_pull_row_counts_as_completed():
    # sync.pull stores "N episodes done" as one completed row at episode N.
    lib = FakeLibrary()
    lib.rows[S3] = [WatchProgress(AnimeId(S3), 8.0, 0, 0,
                                  datetime.now(timezone.utc), completed=True)]
    fr = await svc(lib=lib).build(CAST[S1])
    by = {e.anime.id.anilist: e for e in fr.entries}
    assert by[S3].status is EntryStatus.COMPLETED and by[S3].watched == 8


async def test_statuses_refresh_but_walk_is_cached():
    meta, lib = FakeMeta(), FakeLibrary()
    s = svc(meta, lib)
    await s.build(CAST[S1])
    calls = len(meta.calls)
    lib.watch(S1, 25)
    fr = await s.build(CAST[S2])               # different member, same franchise
    assert len(meta.calls) == calls
    assert fr.entries[0].status is EntryStatus.COMPLETED


# -- limits and failure -------------------------------------------------------
async def test_entry_cap_sets_truncated():
    cast = {1: A(1, "Show 0", d=date(2000, 1, 1))}
    edges = {}
    for i in range(1, 60):
        cast[i + 1] = A(i + 1, f"Show {i}", d=date(2000 + i // 12, i % 12 + 1, 1))
        edges.setdefault(i, []).append(("SEQUEL", i + 1))
        edges.setdefault(i + 1, []).append(("PREQUEL", i))
    meta = FakeMeta(cast, edges)
    fr = await svc(meta).build(cast[1])
    assert fr.truncated
    assert len(fr.entries) <= 30 and len(meta.calls) <= 12


async def test_wide_fanout_is_capped_by_entries():
    cast = {1: A(1, "Hub", d=date(2000, 1, 1))}
    edges = {1: []}
    for i in range(2, 60):
        cast[i] = A(i, f"Side {i}", Format.OVA, date(2001, 1, 1) + timedelta(days=i))
        edges[1].append(("SIDE_STORY", i))
    fr = await svc(FakeMeta(cast, edges)).build(cast[1])
    assert len(fr.entries) == 30 and fr.truncated


async def test_failure_returns_just_the_current_entry():
    s = svc(FakeMeta(fail=RuntimeError("anilist down")))
    fr = await s.build(CAST[S2])
    assert len(fr.entries) == 1 and fr.current.anime.id.anilist == S2
    assert fr.current.main_line and fr.suggested_index == 0
    # and a failure is not cached: the next call retries
    s.metadata.fail = None
    assert len((await s.build(CAST[S2])).entries) == 7


async def test_progress_failure_does_not_raise():
    class Boom(FakeLibrary):
        async def progress_for(self, anime_id):
            raise OSError("db locked")
    fr = await svc(lib=Boom()).build(CAST[S1])
    assert all(e.status is EntryStatus.NOT_STARTED for e in fr.entries)


# -- mark ---------------------------------------------------------------------
async def test_mark_entry_watched_finished_show():
    lib = FakeLibrary()
    assert await svc(lib=lib).mark_entry_watched(CAST[S2]) == 13
    assert lib.marked == [(S2, 13)]


async def test_mark_entry_watched_airing_uses_aired_through():
    now = datetime.now(timezone.utc)
    a = A(9, "Airing", eps=24, status=Status.RELEASING,
          next_airing_episode=6, next_airing_at=now + timedelta(days=2))
    lib = FakeLibrary()
    assert await svc(lib=lib).mark_entry_watched(a) == 5
    assert lib.marked == [(9, 5)]


async def test_mark_entry_watched_movie_without_count_is_one():
    lib = FakeLibrary()
    m = A(10, "Movie", Format.MOVIE, eps=None)
    assert await svc(lib=lib).mark_entry_watched(m) == 1


async def test_mark_entry_watched_unknown_count_raises_clearly():
    with pytest.raises(AnimeShError, match="episode count"):
        await svc().mark_entry_watched(A(11, "Mystery", eps=None))
    with pytest.raises(AnimeShError):
        await svc().mark_entry_watched(
            A(12, "Airing?", eps=None, status=Status.RELEASING))


# -- episode info -------------------------------------------------------------
def test_parse_streaming_titles():
    got = parse_streaming_titles([
        "Episode 3 - The Malevolent Hand", "Episode 1 - The End of the Beginning",
        "Episode 12 – En dash title", "Episode 4: Colon title",
        "Episode 5", "Bonus clip", "", None, "Episode 3 - dup site",
    ])
    assert got == {
        1: "The End of the Beginning", 3: "The Malevolent Hand",
        4: "Colon title", 12: "En dash title",
    }


JIKAN = [
    {"mal_id": 1, "title": "The End of the Beginning", "aired": "2016-04-04T00:00:00+00:00",
     "filler": False, "recap": False},
    {"mal_id": 2, "title": "Episode 2", "aired": None, "filler": True, "recap": False},
    {"mal_id": 3, "title": "Recap", "aired": "2016-04-18T00:00:00+00:00",
     "filler": False, "recap": True},
    {"title": "no number"},
]


def test_parse_jikan_episodes():
    got = parse_jikan_episodes(JIKAN)
    assert set(got) == {1, 2, 3}
    assert got[1] == EpisodeInfo(1, "The End of the Beginning", "2016-04-04")
    assert got[2].title is None and got[2].filler and got[2].aired is None
    assert got[3].recap


async def test_episode_info_merges_anilist_titles_with_jikan_flags():
    meta = FakeMeta()
    meta.titles = ["Episode 1 - AniList One", "Episode 4 - AniList Four"]
    meta.jikan = JIKAN
    info = await svc(meta).episode_info(CAST[S1])
    assert info[1].title == "AniList One" and info[1].aired == "2016-04-04"
    assert info[3].title == "Recap" and info[3].recap
    assert info[2].filler
    assert info[4].title == "AniList Four" and info[4].aired is None


async def test_episode_info_failure_is_empty_dict():
    class Down(FakeMeta):
        async def streaming_episode_titles(self, id):
            raise RuntimeError("x")
        async def jikan_episodes(self, mal_id):
            raise RuntimeError("y")
    assert await svc(Down()).episode_info(CAST[S1]) == {}
    # no metadata hooks at all
    class Bare:
        pass
    assert await FranchiseService(Bare(), FakeLibrary()).episode_info(CAST[S1]) == {}


async def test_episode_info_skips_jikan_without_mal_id():
    meta = FakeMeta()
    meta.jikan = JIKAN
    a = Anime(id=AnimeId(anilist=77), title=Title(romaji="No MAL"))
    assert await svc(meta).episode_info(a) == {}


# -- AniList layer: start_date and the Jikan/stream fetchers ------------------
def test_start_date_parsing():
    assert _start_date({"year": 2016, "month": 4, "day": 4}) == date(2016, 4, 4)
    assert _start_date({"year": 2024, "month": None, "day": None}) == date(2024, 1, 1)
    assert _start_date({"year": 2024, "month": 7, "day": None}) == date(2024, 7, 1)
    assert _start_date({"year": None, "month": 7, "day": 1}) is None
    assert _start_date(None) is None
    assert _start_date({"year": 2023, "month": 2, "day": 31}) is None  # nonsense


def test_to_anime_reads_start_date_and_tolerates_absence():
    m = {"id": 1, "title": {"romaji": "X"}, "format": "TV_SHORT",
         "startDate": {"year": 2020, "month": 5, "day": None}}
    a = _to_anime(m)
    assert a.start_date == date(2020, 5, 1) and a.format is Format.TV_SHORT
    assert _to_anime({"id": 2, "title": {"romaji": "Y"}}).start_date is None


class _Http:
    def __init__(self, pages):
        self.pages, self.urls = pages, []

    async def post_json(self, url, *, json=None, headers=None):
        return {"data": {"Media": {"streamingEpisodes": [
            {"title": "Episode 1 - A"}, {"title": None}, {"title": "Episode 2 - B"}]}}}

    async def get_json(self, url, *, params=None, headers=None):
        self.urls.append((url, params["page"]))
        return self.pages[params["page"] - 1]


async def test_anilist_streaming_titles_and_jikan_paging(monkeypatch):
    monkeypatch.setattr("anime_sh.infra.metadata.anilist._JIKAN_PAGE_GAP_S", 0)
    pages = [{"data": [{"mal_id": 1, "title": "A"}],
              "pagination": {"has_next_page": True}},
             {"data": [{"mal_id": 2, "title": "B"}],
              "pagination": {"has_next_page": False}}]
    http = _Http(pages)
    meta = AniListMetadata(http=http)
    assert await meta.streaming_episode_titles(AnimeId(anilist=1)) == [
        "Episode 1 - A", "Episode 2 - B"]
    rows = await meta.jikan_episodes(21355)
    assert [r["mal_id"] for r in rows] == [1, 2]
    assert [u[1] for u in http.urls] == [1, 2]
    assert http.urls[0][0].endswith("/anime/21355/episodes")
    assert await meta.jikan_episodes(None) == []


async def test_jikan_stops_at_four_pages(monkeypatch):
    monkeypatch.setattr("anime_sh.infra.metadata.anilist._JIKAN_PAGE_GAP_S", 0)
    page = {"data": [{"mal_id": 1, "title": "A"}], "pagination": {"has_next_page": True}}
    http = _Http([page] * 10)
    await AniListMetadata(http=http).jikan_episodes(5)
    assert len(http.urls) == 4


async def test_root_without_start_date_borrows_it_from_a_back_edge():
    bare = replace(CAST[S1], start_date=None)      # as loaded from the library
    fr = await svc().build(bare)
    assert names(fr)[0] == "Re:ZERO S1"
    assert fr.current.anime.start_date == date(2016, 4, 4)
