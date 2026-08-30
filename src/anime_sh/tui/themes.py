"""The themes anime-sh ships, and the rules a theme has to satisfy.

Textual builds most colours by derivation — `$text`, `$text-muted`, `$boost` and
the hover and focus tints all come out of a handful of named slots. Only a few
of those slots are load-bearing for this app's design, and they are load-bearing
in a way a palette can silently break:

* **Three background tiers.** `$background` → `$surface` → `$panel`, each a step
  lighter. The home screen gets its depth entirely from these — rows sit on a
  plate, the plate sits on the base — so a theme whose tiers are equal (or in
  the wrong order) flattens the whole screen back into a wall of text. That is
  not hypothetical: the stylesheet itself once pointed the screen at the middle
  tier and lost the layering that way.
* **`$accent` means "the keyboard is here"** and nothing else. It is the focused
  row's marker, so it has to be legible against `$panel` rather than a near
  neighbour of it.

`test_themes.py` asserts both for every theme in `THEMES`, which is the point of
keeping them here rather than scattered through the app.
"""

from __future__ import annotations

from textual.theme import Theme

from ..theme_names import BUILTIN_THEMES

# Ani asked for something that is not purple; the default at the time was
# tokyo-night, whose primary is #BB9AF7. These two go opposite ways from it — one
# cool, one warm — so the picker offers a real choice rather than two shades of
# the same idea.

MIDNIGHT = Theme(
    name="midnight",
    # A deep blue-black that stays blue: pure greys make the covers, which are
    # the only saturated thing on the screen, look like a mistake.
    background="#0E1420",
    surface="#161E2E",
    panel="#26314A",
    primary="#5CC8D7",
    secondary="#7FB3E8",
    # Warm, and the only warm thing in the palette — which is what makes the
    # focused row findable at a glance rather than by reading.
    accent="#F2B45C",
    foreground="#C6D0E0",
    success="#7FCB8F",
    warning="#E8C070",
    error="#E8737D",
    dark=True,
)

EMBER = Theme(
    name="ember",
    background="#17120F",
    surface="#211A16",
    panel="#372A22",
    primary="#E8944A",
    secondary="#D57B62",
    # Cool against a warm palette, for the same reason midnight's accent is warm
    # against a cool one.
    accent="#6FC0B4",
    foreground="#E2D6CC",
    success="#9DBF6E",
    warning="#E5B45C",
    error="#DB6A5E",
    dark=True,
)

PAPER = Theme(
    name="paper",
    # The one light theme. Terminals get used in daylight, and every built-in
    # option here was dark — which made "change the theme" a choice between
    # four variations on the same brightness.
    background="#F2EFE7",
    surface="#E7E2D6",
    panel="#D2CABA",
    primary="#2F6F7E",
    secondary="#4A7A99",
    accent="#B4552A",
    foreground="#2E2A24",
    success="#3F7A45",
    warning="#8A6215",
    error="#A83232",
    dark=False,
)

#: Themes this app defines, by name.
THEMES: dict[str, Theme] = {t.name: t for t in (MIDNIGHT, EMBER, PAPER)}

#: Textual's own themes offered alongside ours. The list lives in
#: `anime_sh.theme_names` so the config schema can validate a theme name without
#: importing Textual — see that module.
BUILTIN_NAMES = BUILTIN_THEMES


def register(app) -> None:
    """Make this module's themes selectable on ``app``."""
    for theme in THEMES.values():
        app.register_theme(theme)


def available(app) -> list[str]:
    """Theme names to offer, ours first.

    Filtered against what the app actually has registered: a built-in named here
    that a future Textual drops would otherwise reach the picker as an entry
    that fails when chosen.
    """
    known = set(app.available_themes)
    ours = [name for name in THEMES if name in known]
    return ours + [name for name in BUILTIN_NAMES if name in known]
