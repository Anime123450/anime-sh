"""List-item widget rendering — titles with markup-significant characters.

Provider/AniList titles routinely contain square brackets ("[Mini]" batches,
"[Oshi no Ko]"). Textual parses label strings as markup, so an unescaped
bracket makes the text vanish — the regression that made the "[Mini]" source
look identical to the plain TV entry in the picker.
"""

from __future__ import annotations

from textual.app import App
from textual.content import Content
from textual.widgets import Label

from anime_sh.domain.models import Anime, AnimeId, Audio, SourceOption, Title
from anime_sh.tui.widgets import AnimeItem, SourceItem, _lit


def test_lit_keeps_brackets_literal():
    assert Content.from_markup(_lit("[Mini] X")).plain == "[Mini] X"
    assert Content.from_markup(_lit("[Oshi no Ko]")).plain == "[Oshi no Ko]"


async def _mounted_plain(item) -> str:
    """Mount a list-item in a throwaway app and return its Label's rendered
    plain text (markup applied) — the actual on-screen characters."""

    class _App(App):
        def compose(self):
            yield item

    async with _App().run_test():
        label = item.query_one(Label)
        content = label.content  # the raw markup string passed to the Label
        if isinstance(content, str):
            content = Content.from_markup(content)
        return content.plain


async def test_source_item_shows_mini_prefix():
    src = SourceOption(
        provider="anikoto", anime_key="8851",
        title="[Mini] Smoking Behind the Supermarket with You",
        episode_count=12, audio=Audio.SUB,
    )
    plain = await _mounted_plain(SourceItem(src))
    assert plain.startswith("[Mini] Smoking Behind the Supermarket with You")
    assert "anikoto" in plain and "12 eps" in plain


async def test_anime_item_keeps_bracketed_title():
    anime = Anime(id=AnimeId(anilist=1), title=Title(romaji="[Oshi no Ko]"))
    plain = await _mounted_plain(AnimeItem(anime, subtitle="TV · 2023"))
    assert plain.startswith("[Oshi no Ko]")
