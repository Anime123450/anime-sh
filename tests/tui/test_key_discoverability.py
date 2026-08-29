"""A key nobody can find out about may as well not exist.

`j`/`k`/`g`/`G` were added and then shipped documented nowhere: not in the footer
(bound with `show=False`, correctly — four more entries would crowd out the ones
that matter) and not in the `?` cheat-sheet either. The research this UI follows
calls for progressive disclosure — the universal keys in the footer, everything
else discoverable through `?` — and silently failing that is exactly the sort of
thing a test should refuse to let happen twice.
"""

from __future__ import annotations

from anime_sh.tui.app import AnimeShApp
from anime_sh.tui.screens.help import _HELP
from anime_sh.tui.screens.home import HomeScreen

# How a key is written in the cheat-sheet, where that differs from its binding
# name. `escape` reads as "Esc" to a person, not as the string Textual uses.
_AS_WRITTEN = {
    "escape": "Esc",
    "question_mark": "?",
    "slash": "/",
}


def _documented(key: str) -> bool:
    return _AS_WRITTEN.get(key, key) in _HELP


def test_every_key_the_home_screen_binds_is_in_the_cheat_sheet():
    """The regression test: j, k, g and G were bound and documented nowhere."""
    missing = [
        b.key for b in HomeScreen.BINDINGS
        if not _documented(b.key)
    ]
    assert not missing, f"bound on the home screen but absent from `?`: {missing}"


def test_every_key_shown_in_the_footer_is_also_in_the_cheat_sheet():
    """The footer is a reminder, not the documentation. Anything prominent
    enough to occupy footer space has to be explained somewhere."""
    missing = [
        b.key for b in AnimeShApp.BINDINGS
        if getattr(b, "show", False) and not _documented(b.key)
    ]
    assert not missing, f"in the footer but absent from `?`: {missing}"


def test_the_cheat_sheet_does_not_promise_keys_that_do_not_exist():
    """The other direction, and the one this project keeps getting wrong: a hint
    naming a key that does nothing is worse than no hint. `Esc` was documented
    as "back" while the home screen had nothing to go back to."""
    bound = {b.key for b in HomeScreen.BINDINGS} | {b.key for b in AnimeShApp.BINDINGS}
    written = {_AS_WRITTEN.get(k, k) for k in bound}
    # Every single-character key the sheet marks up as a key must be real.
    import re

    for match in re.finditer(r"\[cyan\]([^\[]{1,5})\[/cyan\]", _HELP):
        token = match.group(1).strip()
        if len(token) == 1 and token.isalpha():
            assert token in written, f"`?` documents {token!r}, which is not bound"


def test_the_vim_motions_are_kept_out_of_the_footer():
    """Deliberate: the footer holds the universal keys, and four motion entries
    would crowd out `q`, `/`, `l` and `?`. They live in `?` instead — that is
    what makes the cheat-sheet test above load-bearing rather than decorative."""
    motions = {"j", "k", "g", "G"}
    shown = {b.key for b in HomeScreen.BINDINGS if getattr(b, "show", False)}
    assert not (motions & shown)
