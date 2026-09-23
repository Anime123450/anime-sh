"""`playback.audio` has to reach the screen you actually watch from.

The setting reached every CLI command — `play`, `download`, `sources`,
`prefetch` all read it — and none of the TUI, which passed `Audio.SUB` as a
literal. So `anime config set playback.audio dub` changed `anime play` and left
the full-screen app, the default way in, playing subs.
"""

from __future__ import annotations

from anime_sh.domain.models import Anime, AnimeId, Audio, Title
from anime_sh.tui import AnimeShApp, TuiServices
from anime_sh.tui.screens.detail import DetailScreen

from .test_app import FakeLibrary, FakeMetadata, FakePlayback, FakeSearch, _noop

SHOW = Anime(id=AnimeId(anilist=1), title=Title(romaji="Frieren"), episode_count=12)


class _RecordingPlayback(FakePlayback):
    """Remembers the audio each call was made with."""

    def __init__(self):
        super().__init__()
        self.play_audio: list[Audio | None] = []
        self.list_audio: list[Audio | None] = []

    async def play_and_track(self, anime, number, *, audio=None, source=None):
        self.play_audio.append(audio)
        await super().play_and_track(anime, number, audio=audio, source=source)

    async def list_sources(self, anime, *, audio=None):
        self.list_audio.append(audio)
        return []


def _app(audio=None):
    playback = _RecordingPlayback()
    services = TuiServices(
        search=FakeSearch(), metadata=FakeMetadata(),
        library=FakeLibrary(), playback=playback, aclose=_noop, tracker=None,
    )
    kwargs = {} if audio is None else {"audio": audio}
    return AnimeShApp(services, theme="tokyo-night", **kwargs), playback


def test_the_app_defaults_to_sub():
    app, _ = _app()
    assert app.audio is Audio.SUB


def test_the_app_carries_the_configured_audio():
    app, _ = _app(Audio.DUB)
    assert app.audio is Audio.DUB


async def test_playing_from_the_detail_screen_uses_the_configured_audio():
    """The regression: this call passed a literal Audio.SUB, so a dub viewer
    watching from the TUI got subs no matter what the config said."""
    app, playback = _app(Audio.DUB)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = DetailScreen(SHOW)
        await app.push_screen(screen)
        await pilot.pause()
        # `_play` is a @work method: it schedules, it does not await.
        screen._play(1.0)
        await app.workers.wait_for_complete()
        await pilot.pause()
    assert playback.play_audio == [Audio.DUB]


async def test_a_sub_viewer_is_unaffected():
    app, playback = _app(Audio.SUB)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = DetailScreen(SHOW)
        await app.push_screen(screen)
        await pilot.pause()
        # `_play` is a @work method: it schedules, it does not await.
        screen._play(1.0)
        await app.workers.wait_for_complete()
        await pilot.pause()
    assert playback.play_audio == [Audio.SUB]


def test_the_cli_maps_the_setting_the_same_way_everywhere():
    """`_launch_tui` reads `playback.audio` exactly as every other command
    does; a second spelling of that rule is how they drift apart again."""
    import inspect

    from anime_sh.cli import main as cli_main

    source = inspect.getsource(cli_main._launch_tui)
    assert 'config.playback.audio == "dub"' in source
    assert "audio=audio" in source
