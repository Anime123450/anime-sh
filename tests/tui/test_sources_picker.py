"""The source picker only appears when there is a real choice to make."""
from __future__ import annotations

from anime_sh.domain.models import Audio, SourceOption
from anime_sh.tui.screens.sources import _all_alike


def _src(provider, title, eps):
    return SourceOption(provider=provider, anime_key=f"{provider}-1", title=title,
                        episode_count=eps, audio=Audio.SUB)


def test_the_same_entry_on_two_providers_is_not_a_choice():
    assert _all_alike([
        _src("hianime", "Frieren: Beyond Journey\u2019s End", 28),
        _src("anikoto", "Frieren: Beyond Journey's End", 28),
    ])


def test_a_different_episode_count_is_a_choice():
    assert not _all_alike([
        _src("hianime", "Frieren", 28),
        _src("anikoto", "Frieren", 10),
    ])


def test_a_mini_batch_beside_the_tv_run_is_a_choice():
    assert not _all_alike([
        _src("anikoto", "[Mini] Smoking Behind the Supermarket with You", 12),
        _src("anikoto", "Smoking Behind the Supermarket with You", 12),
    ])
