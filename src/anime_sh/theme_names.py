"""The names of the themes anime-sh offers — and nothing else.

Deliberately dependency-free, and deliberately not in `tui.themes` where the
themes themselves live. Config validation has to check a theme name, and
`tui.themes` imports `textual.theme`; importing it from the config schema pulled
**142 Textual modules** into every `anime` CLI command that had a theme set in
its config file. That undoes the lazy-import work the startup path depends on,
for a list of six strings.

`tui.themes` is still the source of truth for what a theme *is*; a test asserts
that these names and the themes it builds cannot drift apart.
"""

from __future__ import annotations

#: Themes defined by this app.
OWN_THEMES: tuple[str, ...] = ("midnight", "ember", "paper")

#: Textual themes offered alongside them. Named explicitly rather than taking
#: whatever Textual registers, so the picker cannot fill with a future release's
#: additions.
BUILTIN_THEMES: tuple[str, ...] = (
    "tokyo-night",
    "nord",
    "gruvbox",
    "dracula",
    "catppuccin-mocha",
    "solarized-light",
)

#: Every selectable theme, ours first.
ALL_THEMES: tuple[str, ...] = OWN_THEMES + BUILTIN_THEMES
