"""Player adapters: mpv (over JSON IPC, the default), VLC (over its loopback
HTTP interface), the factory that picks between them, and NullPlayer for
wiring/tests."""

from .factory import PlayerFactory, UnavailablePlayer
from .mpv import MpvPlayer
from .null import NullPlayer
from .vlc import VlcPlayer

__all__ = ["MpvPlayer", "NullPlayer", "PlayerFactory", "UnavailablePlayer", "VlcPlayer"]
