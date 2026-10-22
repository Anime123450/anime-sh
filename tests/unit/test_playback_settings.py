"""Settings are fixed per playback session, and VLC rides the same playback flow
(resume, progress, skips, completion, auto-next) as mpv."""

from __future__ import annotations

from anime_sh.app.playback import PlaybackService, PlaybackSettings
from anime_sh.app.providers import ProviderManager
from anime_sh.domain.models import Anime, AnimeId, Title

from .fakes import FakeLibrary, FakeProvider, FakeResolver, make_anime, resume_at
from .test_skip_autonext import _Ev, _ScriptedPlayer, _SkipResolver
from .test_vlc_player import READY, STOPPED, Harness, _flag, _playing


def _anime(eps: int) -> Anime:
    return Anime(id=AnimeId(anilist=1), title=Title(romaji="Frieren"), episode_count=eps)


def _service(player, *, resolvers=None, library=None, **kw) -> PlaybackService:
    return PlaybackService(
        providers=ProviderManager([FakeProvider("p", candidate_hosts=["h"])]),
        resolvers=resolvers or [FakeResolver("h", host="h")],
        player=player,
        library=library or FakeLibrary(),
        **kw,
    )


DONE = [_Ev(1390, 1400, False, True, reason="eof")]


async def test_settings_change_applies_to_next_playback_not_active_session():
    first, second = _ScriptedPlayer([DONE, DONE]), _ScriptedPlayer([DONE])
    current = {"player": first, "auto_next": True}
    reads = []

    def source():
        reads.append(dict(current))
        return PlaybackSettings(player=current["player"], auto_next=current["auto_next"])

    svc = _service(_ScriptedPlayer([]), auto_next=False)
    svc.set_session_source(source)
    # Saved mid-episode, as from the Settings screen while VLC/mpv is open.
    svc.set_on_event(lambda msg: current.update(player=second, auto_next=False))

    await svc.play_and_track(_anime(2), 1.0)
    assert len(first.handles) == 2, "auto-next switched player mid-session"
    assert second.handles == []
    assert len(reads) == 1, "settings re-read during a session"

    await svc.play_and_track(_anime(2), 1.0)
    assert len(second.handles) == 1 and len(first.handles) == 2
    assert len(reads) == 2


async def test_unreadable_settings_fall_back_to_launch_settings():
    launch = _ScriptedPlayer([DONE])
    svc = _service(launch, auto_next=False)
    notes = []
    svc.set_on_event(notes.append)

    def broken():
        raise ValueError("config.toml: invalid TOML")

    svc.set_session_source(broken)
    await svc.play_and_track(_anime(3), 1.0)
    assert len(launch.handles) == 1
    assert any("settings" in n for n in notes)


async def test_vlc_progress_resume_and_skip_use_shared_playback_flow():
    h = Harness([[READY, _playing(301), _playing(320), _playing(1430), STOPPED]])
    # One op range 5..90 isn't hit after a resume at 300, so add an early one:
    h2 = Harness([[READY, _playing(10), _playing(95), _playing(1430), STOPPED]])
    lib = FakeLibrary(progress=resume_at(300, episode=1.0))

    svc = _service(h.player(), library=lib, auto_next=False)
    await svc.play_and_track(make_anime(), 1.0)
    assert _flag(h.spawned[0], "start-time") == ["300"], "resume position not passed"
    assert lib.saved and lib.saved[-1].position_s == 1430
    assert lib.saved[-1].completed
    assert lib.history[-1][1] == 1.0 and lib.history[-1][3] == 1430

    svc = _service(h2.player(), resolvers=[_SkipResolver()], skip_intro=True,
                   auto_next=False)
    await svc.play_and_track(make_anime(), 1.0)
    assert ("seek", 90) in h2.clients[0].calls, "intro skip didn't reach VLC"
    assert h2.made_procs[0].terminated, "our VLC was left running"


async def test_vlc_eof_uses_near_end_threshold():
    # 25 s left of 24 minutes: inside max(5 s, 2 %) -> a natural end -> next ep.
    h = Harness([[READY, _playing(1415), STOPPED], [READY, _playing(1435), STOPPED]])
    await _service(h.player(), auto_next=True).play_and_track(_anime(2), 1.0)
    assert len(h.spawned) == 2

    # 30 s left: just outside -> a stop, even though 90 % was watched.
    h = Harness([[READY, _playing(1410), STOPPED]])
    await _service(h.player(), auto_next=True).play_and_track(_anime(2), 1.0)
    assert len(h.spawned) == 1


async def test_unknown_duration_never_autonexts():
    h = Harness([[READY, _playing(1430, length=0), STOPPED]])
    lib = FakeLibrary()
    await _service(h.player(), library=lib, auto_next=True).play_and_track(_anime(3), 1.0)
    assert len(h.spawned) == 1
    assert lib.saved and not lib.saved[-1].completed


async def test_stop_outside_threshold_does_not_autonext():
    h = Harness([[READY, _playing(600), STOPPED]])
    lib = FakeLibrary()
    await _service(h.player(), library=lib, auto_next=True).play_and_track(_anime(3), 1.0)
    assert len(h.spawned) == 1
    assert lib.saved[-1].position_s == 600 and not lib.saved[-1].completed

    from anime_sh.infra.players.vlc_http import VlcUnreachable

    h = Harness([[READY, _playing(1435), VlcUnreachable("window closed")]])
    await _service(h.player(), auto_next=True).play_and_track(_anime(3), 1.0)
    assert len(h.spawned) == 1, "closing the window rolled on to the next episode"


async def test_manual_stop_near_end_is_best_effort():
    """Documented limitation: VLC's HTTP interface can't tell a stop inside the
    last max(5 s, 2 %) from the episode ending, so that stop rolls on."""
    h = Harness([[READY, _playing(1420), STOPPED], [READY, _playing(5), STOPPED]])
    await _service(h.player(), auto_next=True).play_and_track(_anime(2), 1.0)
    assert len(h.spawned) == 2


def test_container_builds_the_session_player_from_current_settings(tmp_path):
    from anime_sh.app.settings import SettingsService
    from anime_sh.cli.container import Container
    from anime_sh.config.loader import load_config
    from anime_sh.infra.players import UnavailablePlayer, VlcPlayer

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("")
    settings = SettingsService(config_path=cfg_path)
    c = Container(load_config(cfg_path), settings=settings)
    assert c._session_settings().player is c.player, "unchanged player rebuilt"

    fake_vlc = tmp_path / "vlc.exe"
    fake_vlc.write_text("")
    settings.update("player.vlc_path", str(fake_vlc))
    settings.update("player.name", "vlc")
    settings.update("playback.quality", "720p")
    s = c._session_settings()
    assert isinstance(s.player, VlcPlayer) and s.player.executable == str(fake_vlc)
    assert s.quality == "720p"

    settings.update("player.vlc_path", str(tmp_path / "gone" / "vlc.exe"))
    assert isinstance(c._session_settings().player, UnavailablePlayer)


def test_cli_container_keeps_its_launch_config(tmp_path):
    from anime_sh.cli.container import Container
    from anime_sh.config.loader import load_config

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("")
    c = Container(load_config(cfg_path))
    assert c.settings is None
    assert c.download._dir_source == c.config.downloads.dir
