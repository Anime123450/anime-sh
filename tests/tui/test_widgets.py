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
from anime_sh.tui.rows import Row, columns_for
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
    """Titles like "[Oshi no Ko]" are markup as far as the renderer is concerned,
    and an unescaped one vanishes from the row entirely."""
    anime = Anime(id=AnimeId(anilist=1), title=Title(romaji="[Oshi no Ko]"))
    row = Row(title="[Oshi no Ko]", position="TV", status="2023")
    plain = await _mounted_plain(AnimeItem(anime, row, columns_for(100)))
    # Three cells in: the state glyph plus the gap that separates it.
    assert plain.startswith("   [Oshi no Ko]")


async def test_anime_item_set_status_updates_in_place():
    """Countdowns tick every minute. Rebuilding the list to show that would
    throw away whatever row the user had selected, once a minute."""
    anime = Anime(id=AnimeId(anilist=1), title=Title(romaji="Frieren"))
    item = AnimeItem(anime, Row(title="Frieren", position="2/12", status="…"),
                     columns_for(100))

    class _App(App):
        def compose(self):
            yield item

    async with _App().run_test():
        item.set_status("Ep 3 in 4d 6h")
        content = item.query_one(Label).content
        plain = Content.from_markup(content).plain if isinstance(content, str) else content.plain
        assert "Frieren" in plain and "Ep 3 in 4d 6h" in plain


async def test_anime_item_relayout_keeps_columns_aligned_after_a_resize():
    """Rows are padded to widths computed from the terminal, so a resize that
    did not re-render would leave the grid aligned to the old width."""
    anime = Anime(id=AnimeId(anilist=1), title=Title(romaji="Frieren"))
    item = AnimeItem(anime, Row(title="Frieren", position="2/12", status="soon"),
                     columns_for(120))

    class _App(App):
        def compose(self):
            yield item

    async with _App().run_test():
        wide = Content.from_markup(item.query_one(Label).content).plain
        item.relayout(columns_for(70))
        narrow = Content.from_markup(item.query_one(Label).content).plain
        assert len(narrow) < len(wide)
        assert "Frieren" in narrow and "soon" in narrow
