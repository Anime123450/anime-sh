from __future__ import annotations

from types import SimpleNamespace

from textual.geometry import Size

from anime_sh.domain.models import Anime, AnimeId, Title
from anime_sh.tui.screens.detail import DetailScreen


def test_resize_reflows_grid_shell_and_graphics_cover(monkeypatch):
    screen = DetailScreen(Anime(id=AnimeId(anilist=1), title=Title(romaji="Test")))
    calls: list[str] = []
    monkeypatch.setattr(screen, "_size_episode_grid", lambda: calls.append("grid"))
    monkeypatch.setattr(screen, "_paint_shell", lambda: calls.append("shell"))
    monkeypatch.setattr(screen, "_remount_cover", lambda: calls.append("cover"))
    screen._cover_data = b"cover"

    screen.on_resize(SimpleNamespace(size=Size(width=160, height=48)))

    assert calls == ["grid", "shell", "cover"]


def test_duplicate_resize_event_does_not_remount_cover(monkeypatch):
    screen = DetailScreen(Anime(id=AnimeId(anilist=1), title=Title(romaji="Test")))
    calls: list[str] = []
    monkeypatch.setattr(screen, "_size_episode_grid", lambda: calls.append("grid"))
    monkeypatch.setattr(screen, "_paint_shell", lambda: calls.append("shell"))
    monkeypatch.setattr(screen, "_remount_cover", lambda: calls.append("cover"))
    screen._cover_data = b"cover"
    event = SimpleNamespace(size=Size(width=160, height=48))

    screen.on_resize(event)
    calls.clear()
    screen.on_resize(event)

    assert calls == ["grid", "shell"]
