"""The package version resolves from installed metadata (single source of truth)."""

from __future__ import annotations

from importlib.metadata import version

import anime_sh


def test_version_matches_installed_metadata():
    # __version__ must come from the dist metadata, not a hard-coded literal that
    # can drift from pyproject.
    assert anime_sh.__version__ == version("anime-sh")
    assert anime_sh.__version__ != "0.0.0+unknown"
