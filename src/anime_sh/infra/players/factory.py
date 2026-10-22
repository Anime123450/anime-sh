"""PlayerFactory: the `PlayerProvider` the composition root hands out.

Builds the backend a playback session asked for — mpv or VLC — and reports
whether each is installed, without starting anything. It never substitutes one
backend for another: asking for VLC when VLC is missing is an error that says
how to fix it, not a quiet mpv window.
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import Callable, Mapping

from ...domain.errors import PlayerError, PlayerUnavailable
from ...domain.models import PlayerInfo, Stream
from .mpv import MpvPlayer
from .vlc import VlcPlayer, find_vlc

KNOWN = ("mpv", "vlc")


class UnavailablePlayer:
    """Stands in for a chosen backend that is not installed.

    ``play`` raises the reason, so the failure lands where playback starts —
    with an actionable message — instead of the whole app refusing to launch.
    """

    def __init__(self, name: str, error: PlayerError) -> None:
        self.name = name
        self._error = error

    def available(self) -> bool:
        return False

    async def play(self, stream: Stream, *, title: str, start_s: int = 0):
        raise self._error


class PlayerFactory:
    def __init__(
        self, *, which: Callable[[str], str | None] = shutil.which,
        platform: str = sys.platform, env: Mapping[str, str] | None = None,
        exists: Callable[[str], bool] = os.path.isfile,
    ) -> None:
        self._which = which
        self._platform = platform
        self._env = env
        self._exists = exists

    def _mpv_path(self) -> str | None:
        found = self._which("mpv")
        if found and found.lower().endswith(".com"):
            # Same rule as resolve_binary: the console build may never open a window.
            exe = found[:-4] + ".exe"
            if self._exists(exe):
                return exe
        return found

    def status(self, name: str, *, path_override: str = "") -> PlayerInfo:
        name = name.strip().lower()
        if name == "mpv":
            path = self._mpv_path()
            if path:
                return PlayerInfo("mpv", True, path, "found on PATH")
            return PlayerInfo("mpv", False, None,
                              "mpv not found on PATH — install it from https://mpv.io")
        if name == "vlc":
            path, detail = find_vlc(path_override, which=self._which,
                                    platform=self._platform, env=self._env,
                                    exists=self._exists)
            return PlayerInfo("vlc", path is not None, path, detail)
        return PlayerInfo(name, False, None,
                          f"unknown player {name!r}; choose one of: {', '.join(KNOWN)}")

    def create(self, name: str, *, path_override: str = "",
               args: list[str] | None = None):
        name = name.strip().lower()
        if name == "mpv":
            # mpv keeps its own lookup at play time (and its own error).
            return MpvPlayer(binary=self._mpv_path() or "mpv", extra_args=args)
        if name == "vlc":
            info = self.status("vlc", path_override=path_override)
            if not info.available or info.path is None:
                raise PlayerUnavailable(
                    f"{info.detail}. To keep using mpv instead: "
                    "`anime config set player.name mpv`.")
            # player.args are mpv options; VLC would reject or misread them.
            return VlcPlayer(info.path)
        raise PlayerError(f"unknown player {name!r}; choose one of: {', '.join(KNOWN)}")
