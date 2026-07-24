"""AniZone provider: HTML parsing (search / episodes / stream / subtitles) and
the candidate pipeline — offline via an injected fake HTTP client. Fixtures
mirror the real anizone.to markup (Livewire cards, unquoted <track src>)."""

from __future__ import annotations

from anime_sh.domain.models import Anime, AnimeId, Audio, ProviderRef, Title
from anime_sh.providers.anizone.provider import (
    AnizoneProvider,
    parse_episode_numbers,
    parse_search,
    parse_stream_url,
    parse_subtitles,
)

SEARCH_HTML = """
<div meTitle() { return window.getTitle(this.anmTitles, 'Sousou no Frieren'); } }"
     wire:key="a-mdkytdqp" class="card"><a href="https://anizone.to/anime/mdkytdqp"></a></div>
<div meTitle() { return window.getTitle(this.anmTitles, 'Bocchi the Rock!'); } }"
     wire:key="a-y25twzsk" class="card"><a href="https://anizone.to/anime/y25twzsk"></a></div>
"""

SHOW_HTML = """
<a href="https://anizone.to/anime/mdkytdqp/1">Ep 1</a>
<a href="https://anizone.to/anime/mdkytdqp/2">Ep 2</a>
<a href="https://anizone.to/anime/mdkytdqp/3">Ep 3</a>
<a href="https://anizone.to/anime/OTHER/9">unrelated</a>
"""

EPISODE_HTML = """
<media-player wire:ignore src="https://seiryuu.vid-cdn.xyz/abc/master.m3u8" keep-alive>
  <track src=https://seiryuu.vid-cdn.xyz/abc/subtitles/0_ar.ass data-type="ass" kind="subtitles" label="Arabic" srclang="ar" />
  <track src=https://seiryuu.vid-cdn.xyz/abc/subtitles/3_en.ass data-type="ass" kind="subtitles" label="English (US)" srclang="en" default />
  <track src=https://seiryuu.vid-cdn.xyz/abc/subtitles/4_en.ass data-type="ass" kind="subtitles" label="English (CC)" srclang="en" />
</media-player>
"""


def test_parse_search_pairs_title_and_id():
    items = parse_search(SEARCH_HTML)
    assert [(i["id"], i["title"]) for i in items] == [
        ("mdkytdqp", "Sousou no Frieren"),
        ("y25twzsk", "Bocchi the Rock!"),
    ]


def test_parse_episode_numbers_scoped_to_show():
    assert parse_episode_numbers(SHOW_HTML, "mdkytdqp") == [1.0, 2.0, 3.0]  # not OTHER/9


def test_parse_stream_url():
    assert parse_stream_url(EPISODE_HTML) == "https://seiryuu.vid-cdn.xyz/abc/master.m3u8"
    assert parse_stream_url("<p>no player</p>") is None


def test_parse_subtitles_keeps_english_and_flags_default():
    subs = parse_subtitles(EPISODE_HTML)
    assert [s.lang for s in subs] == ["en", "en"]  # Arabic dropped
    assert subs[0].label == "English (US)" and subs[0].default is True
    assert subs[0].url.endswith("3_en.ass")


# -- provider over a fake HTTP ---------------------------------------------- #
class _FakeHttp:
    def __init__(self, pages: dict[str, str]):
        self._pages = pages

    async def get_text(self, url, *, params=None, headers=None):
        if params and "search" in params:
            return self._pages["search"]
        for key, body in self._pages.items():
            if key != "search" and key in url:
                return body
        raise AssertionError(f"unexpected fetch: {url} {params}")


def _anime():
    return Anime(id=AnimeId(anilist=1),
                 title=Title(romaji="Sousou no Frieren",
                             english="Frieren: Beyond Journey's End"))


async def test_dub_request_is_declined():
    # AniZone is sub-only; a dub request must yield nothing so the fan-out can
    # fall through to a dub-capable provider instead of getting sub content.
    http = _FakeHttp({"search": SEARCH_HTML})
    prov = AnizoneProvider(http=http)
    assert await prov.find_sources(_anime(), Audio.DUB) == []
    assert await prov.match(_anime(), Audio.DUB) is None


async def test_provider_pipeline_end_to_end():
    http = _FakeHttp({
        "search": SEARCH_HTML,
        "/anime/mdkytdqp/1": EPISODE_HTML,
        "/anime/mdkytdqp": SHOW_HTML,
    })
    prov = AnizoneProvider(http=http)
    sources = await prov.find_sources(_anime(), Audio.SUB)
    assert sources[0].anime_key == "mdkytdqp" and sources[0].confidence == 1.0

    eps = await prov.episodes(sources[0].ref(), AnimeId(anilist=1))
    assert [e.number for e in eps] == [1.0, 2.0, 3.0]

    cands = await prov.candidates(eps[0])
    assert len(cands) == 1
    c = cands[0]
    assert c.host == "anizone" and c.url.endswith("master.m3u8")
    assert len(c.subtitles) == 2 and c.subtitles[0].default
