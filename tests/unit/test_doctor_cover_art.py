"""`anime doctor` has to say which cover renderer the TUI will actually use.

The difference is plainly visible and its cause is not: the sharp path needs the
terminal to answer a capability probe inside a fixed budget, so the same terminal
on the same machine can render a crisp poster one launch and a blocky one the
next. Without this line, that is a bug report reading "the images went
pixelated" with nothing underneath it.
"""

from __future__ import annotations

import io
import sys

import anime_sh.cli.doctor as doctor


class _Tty(io.StringIO):
    def isatty(self):
        return True


class _Pipe(io.StringIO):
    def isatty(self):
        return False


def test_a_pipe_is_reported_as_a_pipe_not_as_a_verdict(monkeypatch):
    """The trap this check could so easily have fallen into.

    Redirecting doctor's output means stdout is not a terminal, so the probe is
    skipped rather than answered. Reporting that as "no graphics here" would be a
    fact about the pipe -- and piping doctor into a bug report is exactly what
    people do.
    """
    monkeypatch.setattr(sys, "__stdout__", _Pipe())

    check = doctor._check_cover_art()
    assert check.ok
    assert "pipe" in check.detail
    assert "no Sixel" not in check.detail


def test_a_real_terminal_gets_an_actual_answer(monkeypatch):
    monkeypatch.setattr(sys, "__stdout__", _Tty())
    monkeypatch.setattr(doctor, "prime_graphics", lambda: None, raising=False)
    monkeypatch.setattr(
        "anime_sh.tui.coverart.graphics_protocol_active", lambda: True
    )
    monkeypatch.setattr("anime_sh.tui.coverart.prime_graphics", lambda: None)

    assert "true bitmap" in doctor._check_cover_art().detail


def test_a_terminal_without_graphics_is_named_as_such(monkeypatch):
    monkeypatch.setattr(sys, "__stdout__", _Tty())
    monkeypatch.setattr(
        "anime_sh.tui.coverart.graphics_protocol_active", lambda: False
    )
    monkeypatch.setattr("anime_sh.tui.coverart.prime_graphics", lambda: None)

    detail = doctor._check_cover_art().detail
    assert "unicode blocks" in detail
    assert "Sixel" in detail


def test_the_escape_hatch_is_reported_rather_than_looking_like_a_fault(monkeypatch):
    """`ANIME_SH_NO_GRAPHICS=1` is a choice. Reporting it as a terminal that
    cannot do graphics would send someone looking for a problem they caused."""
    monkeypatch.setattr(sys, "__stdout__", _Tty())
    monkeypatch.setenv("ANIME_SH_NO_GRAPHICS", "1")

    detail = doctor._check_cover_art().detail
    assert "ANIME_SH_NO_GRAPHICS" in detail


def test_a_missing_pillow_is_a_failure_with_the_command_to_fix_it(monkeypatch):
    """Without Pillow there is no poster at all, by either route -- and the
    cause is a missing extra, which is easy to end up without and gives no other
    symptom."""
    import builtins

    real_import = builtins.__import__

    def _no_pil(name, *args, **kwargs):
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError("no Pillow")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_pil)

    check = doctor._check_cover_art()
    assert not check.ok
    assert "anime-sh[tui]" in check.detail
