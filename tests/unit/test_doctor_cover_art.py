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


def _cells(monkeypatch, width, height):
    """Pin what textual-image thinks a character cell measures."""
    from types import SimpleNamespace

    monkeypatch.setattr(
        "textual_image._terminal.get_cell_size",
        lambda: SimpleNamespace(width=width, height=height),
    )


def _primed(monkeypatch, *, answered: bool = True):
    """Stub the probe: it did or did not leave a finished-probe marker behind."""
    monkeypatch.setattr("anime_sh.tui.coverart.prime_graphics", lambda: None)
    monkeypatch.setattr(
        "anime_sh.tui.coverart._terminal_questions_all_answered", lambda: answered
    )


def test_a_real_terminal_gets_an_actual_answer(monkeypatch):
    monkeypatch.setattr(sys, "__stdout__", _Tty())
    monkeypatch.setattr(doctor, "prime_graphics", lambda: None, raising=False)
    monkeypatch.setattr(
        "anime_sh.tui.coverart.graphics_protocol_active", lambda: True
    )
    _primed(monkeypatch)
    _cells(monkeypatch, 9, 19)

    assert "true bitmap" in doctor._check_cover_art().detail


def test_an_unfinished_probe_is_reported_instead_of_claiming_sharp(monkeypatch):
    """The failure doctor could not see.

    The TUI needs *two* things: a graphics protocol, and a capability probe that
    finished before Textual took the keyboard. Doctor checked only the first, so
    on a textual-image that had moved where it records the second, doctor said
    "true bitmap — sharp" while every poster came out as blocks. A check that
    confidently describes something the app will not do is worse than no check:
    it is what sent the first investigation of this after the wrong cause.
    """
    monkeypatch.setattr(sys, "__stdout__", _Tty())
    monkeypatch.setattr(
        "anime_sh.tui.coverart.graphics_protocol_active", lambda: True
    )
    _primed(monkeypatch, answered=False)

    check = doctor._check_cover_art()
    assert not check.ok, "a refused bitmap is a fault, not a preference"
    assert "unicode blocks" in check.detail
    assert "true bitmap" not in check.detail
    assert "textual-image" in check.detail, "must name what to reinstall"


# -- the cell size a bitmap poster is scaled against ------------------------- #
# The last remaining way a poster comes out soft while every check above says
# "sharp": textual-image asks the terminal for its cell size and assumes a
# VT340's 10x20 when nothing answers, so a wrong assumption has the terminal
# resampling the image. The two cases look identical and need opposite fixes.
def test_a_measured_cell_size_is_reported_as_measured(monkeypatch):
    _cells(monkeypatch, 9, 19)
    detail = doctor._cell_size()
    assert "9x19px cells" in detail and "measured" in detail
    assert "assumed" not in detail


def test_the_vt340_default_is_reported_as_an_assumption(monkeypatch):
    """10x20 is the library's fallback, so it cannot be told apart from a real
    measurement of 10x20 — and claiming a measurement we did not take is the
    worse of the two mistakes, since it sends someone looking elsewhere."""
    _cells(monkeypatch, 10, 20)
    detail = doctor._cell_size()
    assert "assumed" in detail and "CSI 16 t" in detail


def test_an_unreadable_cell_size_says_so_rather_than_guessing(monkeypatch):
    def _boom():
        raise RuntimeError("no terminal")

    monkeypatch.setattr("textual_image._terminal.get_cell_size", _boom)
    assert doctor._cell_size() == "cell size unknown"


def test_a_terminal_without_graphics_is_named_as_such(monkeypatch):
    monkeypatch.setattr(sys, "__stdout__", _Tty())
    monkeypatch.setattr(
        "anime_sh.tui.coverart.graphics_protocol_active", lambda: False
    )
    _primed(monkeypatch)

    check = doctor._check_cover_art()
    assert check.ok, "a terminal with no graphics is not a fault"
    assert "unicode blocks" in check.detail
    assert "Sixel" in check.detail
    assert "textual-image" not in check.detail, (
        "nothing to reinstall here — the probe finished and said no"
    )


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
