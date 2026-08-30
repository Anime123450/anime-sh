"""Themes, and the properties the home screen needs one to have.

The screen's depth comes entirely from three background tiers and its focus
marker from the accent, so a palette can break the design without breaking
anything that raises. These tests are what stops a new theme doing that.
"""

from __future__ import annotations

import pytest
from rich.color import Color

from anime_sh.theme_names import ALL_THEMES, BUILTIN_THEMES, OWN_THEMES
from anime_sh.tui.themes import THEMES


def _luminance(value: str) -> float:
    c = Color.parse(value).get_truecolor()
    return 0.2126 * c.red + 0.7152 * c.green + 0.0722 * c.blue


def _distance(a: str, b: str) -> float:
    x, y = Color.parse(a).get_truecolor(), Color.parse(b).get_truecolor()
    return abs(x.red - y.red) + abs(x.green - y.green) + abs(x.blue - y.blue)


def test_the_name_list_and_the_themes_cannot_drift():
    """The names live apart from the themes on purpose — the config schema has
    to validate a theme name, and importing the module that builds them dragged
    142 Textual modules into every CLI command. Two lists means they can
    disagree, so this is the thing that says they must not."""
    assert set(OWN_THEMES) == set(THEMES), (
        "anime_sh.theme_names.OWN_THEMES and tui.themes.THEMES disagree"
    )


@pytest.mark.parametrize("name", sorted(THEMES))
def test_a_theme_has_three_distinct_background_tiers(name):
    """`$background` → `$surface` → `$panel`. The rows sit on a plate and the
    plate sits on the base; a theme whose tiers are equal flattens the screen
    into the wall of text this design exists to stop being."""
    t = THEMES[name]
    tiers = [_luminance(t.background), _luminance(t.surface), _luminance(t.panel)]
    # A light theme steps the other way; what matters is that it steps at all,
    # consistently, and by enough to see.
    ordered = tiers == sorted(tiers) if t.dark else tiers == sorted(tiers, reverse=True)
    assert ordered, f"{name}: tiers are not ordered away from the base: {tiers}"
    for lower, upper in zip(tiers, tiers[1:]):
        assert abs(upper - lower) >= 5, (
            f"{name}: two tiers are too close to tell apart ({lower:.0f} vs {upper:.0f})"
        )


@pytest.mark.parametrize("name", sorted(THEMES))
def test_the_accent_stands_out_from_the_row_it_marks(name):
    """`$accent` is the focused row's marker and means nothing else. Against a
    near neighbour of `$panel` it stops being findable at a glance, which is the
    whole job."""
    t = THEMES[name]
    assert _distance(t.accent, t.panel) >= 120, (
        f"{name}: the focus accent is too close to the panel it sits on"
    )


@pytest.mark.parametrize("name", sorted(THEMES))
def test_text_is_legible_on_every_tier(name):
    """A palette that reads on the base but not on the plate is a palette that
    fails exactly where the content is."""
    t = THEMES[name]
    for tier in ("background", "surface", "panel"):
        assert _distance(t.foreground, getattr(t, tier)) >= 180, (
            f"{name}: foreground is too close to {tier}"
        )


def test_every_offered_theme_can_actually_be_applied():
    """A name in the list that nothing can apply reaches the picker as an entry
    that fails when chosen — and reaches `config set` as a value that validates
    and then silently does nothing."""
    from anime_sh.tui import AnimeShApp, TuiServices

    from .test_app import FakeLibrary, FakeMetadata, FakePlayback, FakeSearch, _noop

    app = AnimeShApp(
        TuiServices(search=FakeSearch(), metadata=FakeMetadata(),
                    library=FakeLibrary(), playback=FakePlayback(), aclose=_noop)
    )
    from anime_sh.tui.themes import register

    register(app)
    missing = [n for n in ALL_THEMES if n not in app.available_themes]
    assert not missing, f"offered but not registered: {missing}"


def test_the_config_rejects_a_theme_nothing_will_apply():
    """It used to look the value up in a dict and, on a miss, leave the default
    in place — so `config set ui.theme drakula` reported success and changed
    nothing, which is indistinguishable from the setting not working."""
    from pydantic import ValidationError

    from anime_sh.config.schema import UiConfig

    assert UiConfig(theme="midnight").theme == "midnight"
    with pytest.raises(ValidationError):
        UiConfig(theme="drakula")


def test_validating_a_theme_name_does_not_import_textual():
    """Config is loaded by every CLI command; Textual is the TUI's dependency.
    Reaching for `tui.themes` here pulled 142 modules into `anime search`."""
    import subprocess
    import sys

    code = (
        "import sys;"
        "from anime_sh.config.schema import UiConfig;"
        "UiConfig(theme='nord');"
        "print(sum(1 for m in sys.modules if m.startswith('textual')))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "0", (
        f"validating a theme name imported {out.stdout.strip()} Textual modules"
    )


def test_builtin_names_are_offered_in_a_stable_order():
    """The picker is navigated by muscle memory; a set would reorder it between
    runs."""
    assert isinstance(BUILTIN_THEMES, tuple) and isinstance(OWN_THEMES, tuple)
    assert ALL_THEMES == OWN_THEMES + BUILTIN_THEMES


# --------------------------------------------------------------------------- #
# The picker
# --------------------------------------------------------------------------- #
def _themed_app():
    from anime_sh.tui import AnimeShApp, TuiServices

    from .test_app import FakeLibrary, FakeMetadata, FakePlayback, FakeSearch, _noop

    return AnimeShApp(
        TuiServices(search=FakeSearch(), metadata=FakeMetadata(),
                    library=FakeLibrary(), playback=FakePlayback(), aclose=_noop),
        theme="midnight",
    )


async def _open_picker(app, pilot):
    from anime_sh.tui.screens.themes import ThemesScreen

    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.press("t")
    await pilot.pause()
    assert isinstance(app.screen, ThemesScreen), (
        f"pressing t opened {type(app.screen).__name__}"
    )
    return app.screen


async def test_moving_the_cursor_applies_the_theme():
    """The preview is the app itself. A list of names tells you nothing about
    what a theme does to a screen full of rows and a poster."""
    app = _themed_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await _open_picker(app, pilot)
        before = app.theme
        await pilot.press("down")
        await pilot.pause()
        assert app.theme != before, "moving the cursor did not change the theme"


async def test_escape_puts_back_the_theme_you_arrived_with():
    """Browsing has to be free, or the picker is a trap: every look costs you
    the setting you had."""
    app = _themed_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await _open_picker(app, pilot)
        original = "midnight"
        await pilot.press("down")
        await pilot.press("down")
        await pilot.pause()
        assert app.theme != original  # premise: we previewed something else
        await pilot.press("escape")
        await pilot.pause()
        assert app.theme == original, "cancelling kept the previewed theme"


async def test_choosing_a_theme_writes_it_to_the_config():
    """A theme chosen in the app and gone again next launch reads as the setting
    not having worked."""
    import anime_sh.tui.screens.themes as picker

    saved: list[tuple[str, str]] = []

    async def _run():
        app = _themed_app()
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await _open_picker(app, pilot)
            screen._persist = lambda name: saved.append(("ui.theme", name))
            await pilot.press("down")
            await pilot.pause()
            chosen = app.theme
            await pilot.press("enter")
            await pilot.pause()
            return chosen

    chosen = await _run()
    assert saved == [("ui.theme", chosen)], f"config writes were {saved}"
    assert picker  # silence the unused import in the failure path


async def test_the_picker_offers_every_theme():
    from textual.widgets import ListView

    app = _themed_app()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_picker(app, pilot)
        lv = screen.query_one("#theme-list", ListView)
        names = [item.theme_name for item in lv.children]
        assert names == list(ALL_THEMES), f"picker showed {names}"


async def test_the_picker_lets_the_preview_show_through():
    """The live preview is the point, and it is invisible behind an opaque
    modal. This was broken once already and looked fine in every test: the
    screen's own DEFAULT_CSS asked for translucency and `app.tcss`'s app-level
    `Screen` rule silently outranked it."""
    app = _themed_app()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_picker(app, pilot)
        assert screen.styles.background.a < 1.0, (
            "the theme picker is opaque, so nothing it previews can be seen"
        )
