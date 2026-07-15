"""Player adapters. mpv (over JSON IPC) is the real one; NullPlayer is for
wiring/tests."""

from .null import NullPlayer
from .mpv import MpvPlayer

__all__ = ["NullPlayer", "MpvPlayer"]
