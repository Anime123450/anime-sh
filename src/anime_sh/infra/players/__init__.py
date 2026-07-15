"""Player adapters. mpv (over JSON IPC) is the reference; NullPlayer is for
wiring/tests. Real players land in M1."""

from .null import NullPlayer

__all__ = ["NullPlayer"]
