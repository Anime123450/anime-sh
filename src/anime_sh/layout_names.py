"""The episode layouts anime-sh offers — and nothing else.

Dependency-free and separate from the widgets that implement them, for the same
reason `theme_names` is separate from `tui.themes`: config validation has to
check a layout name, and importing the TUI to do it pulls Textual into every
`anime` CLI command that has one set in its config file.
"""

from __future__ import annotations

#: How the detail screen arranges an episode list.
#:
#: ``grid``    tiled cells, a season readable as one block
#: ``list``    one episode per line, carrying its own state sentence
#: ``compact`` the grid with the gutter closed up, for very long series
EPISODE_LAYOUTS: tuple[str, ...] = ("grid", "list", "compact")

DEFAULT_EPISODE_LAYOUT = "grid"


#: How much room the home screen gives its chrome.
#:
#: ``comfortable``  the plate's padding and the space between sections
#: ``compact``      both closed up, for a terminal that is short rather than wide
DENSITIES: tuple[str, ...] = ("comfortable", "compact")

DEFAULT_DENSITY = "comfortable"
