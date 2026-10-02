"""The "is the probe done?" gate has to survive textual-image's refactors.

`graphics_cover_widget` refuses to build a bitmap widget unless
`_terminal_questions_all_answered()` says the terminal has nothing pending —
because asking after Textual owns stdin turns the reply into key presses. That
check read a *private* cache attribute, `get_cell_size._result`.

textual-image 0.14 batched the TGP, cell-size and device-attributes queries into
one `probe_terminal()` and moved the cache onto that function. `get_cell_size`
delegates to it and never gains `_result` again — so the gate answered False
forever and every terminal got the unicode-block fallback, however good its
graphics support was.

The repo's lock resolves 0.12/0.13, which still cache on `get_cell_size`, so the
whole suite passed while an actual `uv tool install` resolved 0.14.1 and drew
blocks. These tests pin the gate against both layouts, and the last one asks the
really installed library which layout it is, so the next move fails here instead
of in someone's terminal.
"""

from __future__ import annotations

import sys
import types

from anime_sh.tui import coverart


def _fake_textual_image(monkeypatch, **attrs):
    """Stand in for `textual_image._terminal` with the given module attributes."""
    pkg = types.ModuleType("textual_image")
    terminal = types.ModuleType("textual_image._terminal")
    for name, value in attrs.items():
        setattr(terminal, name, value)
    pkg._terminal = terminal
    monkeypatch.setitem(sys.modules, "textual_image", pkg)
    monkeypatch.setitem(sys.modules, "textual_image._terminal", terminal)


def _probed() -> object:
    def fn() -> None: ...
    fn._result = object()
    return fn


def _unprobed() -> object:
    def fn() -> None: ...
    return fn


def test_the_0_14_layout_is_recognised(monkeypatch):
    """The bug: a primed 0.14 terminal reported nothing as answered.

    `get_cell_size` is present and never gains `_result`, exactly as 0.14 leaves
    it after a successful probe.
    """
    _fake_textual_image(
        monkeypatch, get_cell_size=_unprobed(), probe_terminal=_probed()
    )
    assert coverart._terminal_questions_all_answered() is True


def test_the_older_layout_still_works(monkeypatch):
    """0.13 and earlier have no `probe_terminal` at all."""
    _fake_textual_image(monkeypatch, get_cell_size=_probed())
    assert coverart._terminal_questions_all_answered() is True


def test_an_unprobed_terminal_is_still_refused(monkeypatch):
    """The gate has to keep saying no, or it stops protecting anything."""
    _fake_textual_image(
        monkeypatch, get_cell_size=_unprobed(), probe_terminal=_unprobed()
    )
    assert coverart._terminal_questions_all_answered() is False


def test_a_library_with_neither_marker_is_refused(monkeypatch):
    """A future version that moves the cache again falls back to blocks rather
    than asking the terminal a question at the wrong moment."""
    _fake_textual_image(monkeypatch)
    assert coverart._terminal_questions_all_answered() is False


def test_the_installed_textual_image_exposes_a_marker_we_know():
    """The check that would have caught this.

    Asks the real installed library, after a real prime, whether either marker
    appeared. A new version that moves the cache a third time fails here — in
    CI, on the version people actually get — instead of silently costing every
    user their cover art.
    """
    try:
        import textual_image  # noqa: F401
    except Exception:
        __import__("pytest").skip("textual-image not installed")

    coverart.prime_graphics()
    from textual_image import _terminal

    markers = {
        name: hasattr(getattr(_terminal, name, None), "_result")
        for name in ("probe_terminal", "get_cell_size")
    }
    assert any(markers.values()), (
        f"neither cache marker appeared after priming ({markers}) — textual-image "
        f"has moved it again, and `_terminal_questions_all_answered` now refuses "
        f"every bitmap cover. Find the new one and add it to the tuple."
    )
