"""The hero synopsis is cut once, on a word, with a single ellipsis."""
from __future__ import annotations

import pytest
from rich.cells import cell_len

from anime_sh.tui.preview import _wrap

TEXT = ("The demon king has been defeated, and the victorious hero party returns "
        "home before disbanding. The four finally go their separate ways, and "
        "the elf mage watches them age.")


@pytest.mark.parametrize("width", range(10, 90))
@pytest.mark.parametrize("limit", range(1, 6))
def test_cut_synopsis_has_one_ellipsis_and_fits(width, limit):
    """A last line that already filled the width was cut by `fit` (adding an
    ellipsis) and then given a second one: "...go their separate ways, and ……"."""
    lines = _wrap(TEXT, width, limit)
    assert all(cell_len(line) <= width for line in lines), lines
    joined = " ".join(lines)
    assert "……" not in joined
    if lines and joined.endswith("…"):
        assert joined.count("…") == 1
        # cut on a word boundary: what's left is the start of the text
        stem = joined[:-1].rstrip(" ,;:")
        assert " ".join(TEXT.split()).startswith(stem), (stem, lines)


def test_text_that_fits_is_untouched():
    assert _wrap("A short synopsis.", 40, 3) == ["A short synopsis."]
