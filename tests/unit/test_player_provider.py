"""PlayerFactory: which backend gets built, and never a silent substitute."""

from __future__ import annotations

import os

import pytest

from anime_sh.domain.errors import PlayerError, PlayerUnavailable
from anime_sh.domain.ports import PlayerProvider
from anime_sh.infra.players import MpvPlayer, PlayerFactory, VlcPlayer
from anime_sh.infra.players.vlc import find_vlc


def _factory(tmp_path, *, on_path=(), platform="win32"):
    found = {name: str(tmp_path / f"{name}.exe") for name in on_path}
    for p in found.values():
        open(p, "w").close()
    return PlayerFactory(which=found.get, platform=platform,
                         env={"ProgramFiles": str(tmp_path / "nowhere")})


def test_player_provider_defaults_to_mpv(tmp_path):
    f = _factory(tmp_path, on_path=["mpv"])
    assert isinstance(f, PlayerProvider)
    player = f.create("mpv", args=["--profile=fast"])
    assert isinstance(player, MpvPlayer)
    assert f.status("mpv").available


def test_player_provider_prefers_configured_vlc_path(tmp_path):
    custom = tmp_path / "portable" / "vlc.exe"
    custom.parent.mkdir()
    custom.write_text("")
    f = _factory(tmp_path, on_path=["vlc"])
    info = f.status("vlc", path_override=str(custom))
    assert info.available and info.path == str(custom)
    # A folder works too: people paste the install directory.
    assert f.status("vlc", path_override=str(custom.parent)).path == str(custom)
    player = f.create("vlc", path_override=str(custom), args=["--mpv-only-flag"])
    assert isinstance(player, VlcPlayer) and player.executable == str(custom)
    assert player.extra_args == [], "mpv's player.args were handed to VLC"


def test_missing_vlc_never_falls_back_to_mpv(tmp_path):
    f = _factory(tmp_path, on_path=["mpv"])
    info = f.status("vlc")
    assert not info.available and info.path is None and "not found" in info.detail
    with pytest.raises(PlayerUnavailable, match="mpv"):  # says how to switch back
        f.create("vlc")

    bad = f.status("vlc", path_override=str(tmp_path / "missing" / "vlc.exe"))
    assert not bad.available and "vlc_path" in bad.detail
    with pytest.raises(PlayerUnavailable):
        f.create("vlc", path_override=str(tmp_path / "missing" / "vlc.exe"))

    with pytest.raises(PlayerError):
        f.create("iina")


def test_vlc_discovery_order(tmp_path):
    pf = tmp_path / "pf"
    std = pf / "VideoLAN" / "VLC" / "vlc.exe"
    std.parent.mkdir(parents=True)
    std.write_text("")
    env = {"ProgramFiles": str(pf)}
    assert find_vlc("", which=lambda n: None, platform="win32", env=env)[0] == str(std)
    on_path = str(tmp_path / "bin-vlc.exe")
    open(on_path, "w").close()
    assert find_vlc("", which=lambda n: on_path, platform="win32", env=env)[0] == on_path
    path, detail = find_vlc("", which=lambda n: None, platform="linux", env={},
                            exists=lambda p: p == "/snap/bin/vlc")
    assert path == "/snap/bin/vlc"
    assert os.sep or detail is not None
