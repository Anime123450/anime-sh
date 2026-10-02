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


def test_every_terminal_query_is_asked_before_the_app_starts():
    """The bug that survived the first fix.

    `textual-image` asks the terminal two separate questions. Importing
    `renderable` asks about Sixel/kitty support. Importing `widget` asks for the
    **cell size** — `CSI 16 t`, the one whose reply ends in the literal `t`.

    The first fix primed only `renderable`, so the cell-size query stayed
    deferred and fired later from `graphics_cover_widget` — which, now that
    posters render on the home screen, runs while Textual is reading stdin. The
    reply came back as key presses and the trailing `t` reopened the picker.

    Draining is useless against a question that has not been asked yet, so the
    property worth asserting is not "stdin is clear" but "nothing is left to
    ask".

    Run in a subprocess because the thing being measured is an *import side
    effect*: once `textual_image.widget` is in `sys.modules`, importing it again
    does nothing, and clearing the cache by hand tests only the clearing. The
    first version of this test did exactly that and failed against the fix.
    """
    import subprocess
    import sys

    code = (
        "from anime_sh.tui.coverart import prime_graphics;"
        "prime_graphics();"
        "from textual_image._terminal import get_cell_size;"
        "print(hasattr(get_cell_size, '_result'))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    if out.returncode != 0 and "textual_image" in out.stderr:
        __import__("pytest").skip("textual-image not installed")
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "True", (
        "prime_graphics left the cell-size query to fire later, from inside the "
        "running app"
    )


def test_the_bitmap_path_refuses_to_ask_the_terminal_anything_mid_run():
    """The structural half of the fix.

    Priming is a promise that every query has already been made; this is what
    happens when the promise is broken. Rather than import the module that asks
    the terminal for its cell size — while Textual is reading stdin, so the
    reply arrives as key presses — the bitmap path declines and the caller falls
    back to the unicode render. A slightly softer poster beats the app typing at
    itself.
    """
    original = coverart._terminal_questions_all_answered
    coverart._terminal_questions_all_answered = lambda: False
    try:
        assert coverart.graphics_cover_widget(b"whatever", 20) is None
    finally:
        coverart._terminal_questions_all_answered = original


# --------------------------------------------------------------------------- #
# The probe has to be given long enough to answer
# --------------------------------------------------------------------------- #
def _budgets_through_patient_probes(calls):
    """What `_patient_probes` hands the library for each (end_marker, timeout)."""
    from textual_image import _terminal

    seen: list[float | None] = []
    original = _terminal.capture_terminal_response
    _terminal.capture_terminal_response = lambda s, e, timeout=None: seen.append(timeout)
    try:
        with coverart._patient_probes():
            for end_marker, timeout in calls:
                _terminal.capture_terminal_response("a", end_marker, timeout)
    finally:
        _terminal.capture_terminal_response = original
    return seen


def test_the_query_that_decides_sixel_gets_the_full_budget():
    """Device attributes -- the `ESC [ ? … c` reply -- is what says whether Sixel
    is on offer, and every terminal answers it, so waiting longer there costs
    nothing and buys the whole fix."""
    assert _budgets_through_patient_probes([("c", 0.1)]) == [coverart._PROBE_TIMEOUT_S]


def test_the_queries_terminals_ignore_get_a_smaller_rise():
    """The kitty and cell-size queries go unanswered on plenty of terminals, and
    an unanswered probe waits out its whole budget. They are still raised above
    the 0.1s that caused this, just not as far -- otherwise a terminal that draws
    no images pays a second and a half at every launch for the privilege."""
    budgets = _budgets_through_patient_probes([("t", 0.1), ("S", 0.1)])
    assert budgets == [coverart._QUIET_PROBE_TIMEOUT_S] * 2
    assert coverart._QUIET_PROBE_TIMEOUT_S > 0.1, "no better than the budget that failed"
    assert coverart._QUIET_PROBE_TIMEOUT_S < coverart._PROBE_TIMEOUT_S


def test_the_timeout_is_only_ever_widened():
    """A library asking for longer, or deliberately asking for no timeout at all,
    gets what it asked for."""
    assert _budgets_through_patient_probes([("c", 9.0), ("t", None)]) == [9.0, None]


def test_the_probe_timeout_is_put_back_afterwards():
    """It is widened for the one moment anime-sh owns the terminal, not for the
    life of the process."""
    from textual_image import _terminal

    before = _terminal.capture_terminal_response
    with coverart._patient_probes():
        assert _terminal.capture_terminal_response is not before
    assert _terminal.capture_terminal_response is before


def test_a_broken_textual_image_does_not_break_priming():
    """`_patient_probes` runs before the imports it is widening, so it cannot
    assume the package is importable."""
    import builtins

    real_import = builtins.__import__

    def _boom(name, *args, **kwargs):
        if name.startswith("textual_image"):
            raise RuntimeError("not installed")
        return real_import(name, *args, **kwargs)

    builtins.__import__ = _boom
    try:
        with coverart._patient_probes():
            pass  # must not raise
    finally:
        builtins.__import__ = real_import


def test_the_terminal_gets_longer_than_a_tenth_of_a_second_to_answer():
    """The regression this exists for: a sharp poster turning blocky.

    textual-image hardcodes 0.1s per byte for its capability probes, and this
    module's own `drain_terminal_replies` already records Windows Terminal
    answering slower than that. A probe that times out is indistinguishable from
    "this terminal has no Sixel", so the library picks half-cell rendering,
    `graphics_protocol_active` says no, and the cover falls back to the unicode
    block render -- at 2x3 pixels per character cell, which is a 44x51 image
    stretched across a 22-cell poster.

    Subprocess, and stdout faked into claiming to be a terminal, because the
    renderer is chosen once as an import side effect and skipped entirely when
    there is no tty -- which under pytest there never is.
    """
    import subprocess
    import sys

    code = (
        "import io, sys\n"
        "class _Tty(io.StringIO):\n"
        "    def isatty(self): return True\n"
        "sys.__stdout__ = _Tty(); sys.__stdin__ = _Tty()\n"
        "import textual_image._terminal as t\n"
        "seen = []\n"
        "def fake(start, end, timeout=None):\n"
        "    seen.append((end, timeout))\n"
        "    raise t.TerminalError('this terminal is slow')\n"
        "t.capture_terminal_response = fake\n"
        "from anime_sh.tui.coverart import prime_graphics\n"
        "prime_graphics()\n"
        # Only `sys.__stdout__` was faked, so `sys.stdout` is still the pipe --
        # and the library logs its own warnings to stderr, so this arrives clean.
        # The device-attributes query is the one that decides Sixel; report what
        # it alone was given.
        "da = [x[1] for x in seen if x[0] == 'c' and x[1] is not None]\n"
        # Only `sys.__stdout__` was faked, so `sys.stdout` is still the pipe --
        # and the library logs its own warnings to stderr, so this arrives clean.
        "sys.stdout.write('BUDGETS=' + repr(da))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    if "textual_image" in out.stderr and "ModuleNotFoundError" in out.stderr:
        __import__("pytest").skip("textual-image not installed")
    assert out.returncode == 0, out.stderr

    assert "BUDGETS=" in out.stdout, out.stdout + out.stderr
    budgets = [
        float(x) for x in
        out.stdout.split("BUDGETS=", 1)[1].strip().strip("[]").split(",") if x.strip()
    ]
    assert budgets, "the Sixel capability probe never ran, so this proves nothing"
    assert min(budgets) >= coverart._PROBE_TIMEOUT_S, (
        f"the terminal was given {min(budgets)}s to say whether it does Sixel; a "
        f"slow one times out and its support is read as absent"
    )
