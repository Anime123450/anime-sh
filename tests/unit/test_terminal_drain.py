"""Leftover terminal-probe bytes must never reach the app as key presses.

anime-sh opened its theme picker on every launch. Nothing in the picker was
wrong — it was simply the first thing ever bound to `t`, and `t` was arriving on
its own.

`textual-image` asks the terminal for its cell size with `CSI 16 t` and waits
0.1s for a reply ending in the literal character `t`. Windows Terminal answers
more slowly than that, by which point the probe has already consumed and thrown
away the `ESC [` that marked the reply as a reply. The rest — `6;20;10t` —
arrives after Textual has started, with no escape prefix left to identify it, so
Textual delivers it as keys. The last one opens the theme picker.
"""

from __future__ import annotations

import anime_sh.tui.coverart as coverart


class _Tty:
    """A stdin that claims to be a terminal, so the drain does not opt out."""

    @staticmethod
    def isatty():
        return True


def test_the_probe_is_always_followed_by_a_drain():
    """Including when it fails. A probe that raised is *more* likely to have
    left half an answer in the buffer, not less."""
    calls: list[str] = []
    original = coverart.drain_terminal_replies
    coverart.drain_terminal_replies = lambda *a, **k: calls.append("drained") or 0
    try:
        coverart.prime_graphics()
    finally:
        coverart.drain_terminal_replies = original
    assert calls == ["drained"], "the probe ran without clearing up after itself"


def test_a_failing_probe_still_drains(monkeypatch):
    calls: list[str] = []
    original = coverart.drain_terminal_replies
    coverart.drain_terminal_replies = lambda *a, **k: calls.append("drained") or 0
    # Make the probe import blow up the way a missing/py-incompatible
    # textual-image would.
    import builtins

    real_import = builtins.__import__

    def _boom(name, *args, **kwargs):
        if name.startswith("textual_image"):
            raise RuntimeError("probe exploded")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _boom)
    try:
        coverart.prime_graphics()
    finally:
        coverart.drain_terminal_replies = original
    assert calls == ["drained"]


def test_the_drain_is_skipped_when_there_is_no_terminal():
    """Under pytest, in a pipe, or in CI, stdin is not a tty — there is no
    probe reply to clear and nothing to wait for. Polling there would slow every
    test run down for nothing."""
    assert coverart.drain_terminal_replies(budget_s=0) == 0


def test_the_drain_never_stops_the_app_starting(monkeypatch):
    """It is tidy-up. A terminal that behaves oddly enough to break it must
    still get an app."""
    monkeypatch.setattr(coverart, "_read_pending",
                        lambda: (_ for _ in ()).throw(OSError("no console")))
    monkeypatch.setattr("sys.__stdin__", _Tty())
    assert coverart.drain_terminal_replies(budget_s=0) == 0


def test_graphics_disabled_skips_the_probe_entirely(monkeypatch):
    """`ANIME_SH_NO_GRAPHICS=1` is the escape hatch for a terminal that
    mishandles the probe; it has to skip the query, not just the rendering."""
    calls: list[str] = []
    original = coverart.drain_terminal_replies
    coverart.drain_terminal_replies = lambda *a, **k: calls.append("drained") or 0
    monkeypatch.setenv("ANIME_SH_NO_GRAPHICS", "1")
    try:
        coverart.prime_graphics()
    finally:
        coverart.drain_terminal_replies = original
    assert calls == [], "the probe ran despite graphics being switched off"


def test_the_drain_stops_as_soon_as_it_swallows_the_terminator(monkeypatch):
    """A terminal that answers should cost milliseconds, not the whole budget.
    The reply ends in `t`; once that is consumed there is nothing left to wait
    for."""
    chunks = iter(["6;20;10t", "should never be read"])
    monkeypatch.setattr(coverart, "_read_pending", lambda: next(chunks))
    monkeypatch.setattr("sys.__stdin__", _Tty())

    assert coverart.drain_terminal_replies(budget_s=5, poll_s=0) == len("6;20;10t")


def test_the_drain_gives_up_when_no_reply_ever_comes(monkeypatch):
    """A terminal that does not support the query answers nothing at all, and
    the app still has to start."""
    monkeypatch.setattr(coverart, "_read_pending", lambda: "")
    monkeypatch.setattr("sys.__stdin__", _Tty())

    assert coverart.drain_terminal_replies(budget_s=0.05, poll_s=0.01) == 0


def test_the_stray_t_never_reaches_the_app(monkeypatch):
    """The bug, stated as the thing that actually went wrong: the probe's late
    reply ends in `t`, `t` opens the theme picker, so anime-sh opened it on
    launch. Whatever else the drain does, that character must be gone."""
    reply = "6;20;10t"
    seen: list[str] = []

    def _read():
        if not seen:
            seen.append(reply)
            return reply
        return ""

    monkeypatch.setattr(coverart, "_read_pending", _read)
    monkeypatch.setattr("sys.__stdin__", _Tty())

    dropped = coverart.drain_terminal_replies(budget_s=1, poll_s=0)
    assert dropped == len(reply), "the probe reply was left for Textual to read"
