"""Pure CLI formatting helpers."""

from __future__ import annotations

from anime_sh.cli.main import _ep_list


def test_ep_list_collapses_contiguous_runs():
    assert _ep_list([1.0, 2.0, 3.0, 4.0]) == "1–4"


def test_ep_list_keeps_gaps_and_specials_explicit():
    assert _ep_list([1.0, 2.0, 3.0, 5.0, 13.5]) == "1–3, 5, 13.5"


def test_ep_list_single_episode():
    assert _ep_list([1.0]) == "1"
