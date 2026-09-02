"""The AniList token is a credential sitting in a file.

No OS keyring is bundled, so the file *is* the store. That puts the whole
weight of protecting it on how the file is created — which is worth pinning
down, because the mode is invisible in normal use and nothing else would ever
notice it being wrong.
"""

from __future__ import annotations

import os
import stat

import pytest

from anime_sh.infra.tracker.tokens import (
    clear_token,
    load_client_id,
    load_token,
    save_token,
)

posix_only = pytest.mark.skipif(
    os.name != "posix", reason="file modes are close to meaningless on Windows"
)


def test_token_round_trips(tmp_path):
    path = tmp_path / "nested" / "token.json"
    save_token("secret-abc", client_id="4242", path=path)
    assert load_token(path) == "secret-abc"
    assert load_client_id(path) == "4242"


def test_saving_again_replaces_rather_than_merges(tmp_path):
    """The file is truncated, so a client id from a previous login cannot
    survive into a session that did not mint one."""
    path = tmp_path / "token.json"
    save_token("first", client_id="1111", path=path)
    save_token("second", path=path)
    assert load_token(path) == "second"
    assert load_client_id(path) is None


def test_the_token_file_is_created_restricted_not_widened_then_narrowed(
    tmp_path, monkeypatch
):
    """The bug was a window, not an end state.

    The old code wrote the file with default permissions and chmod-ed it
    afterwards, so an access token sat on disk world-readable for the moment in
    between. Asserting the *final* mode cannot catch that — the chmod ran
    either way, so the end state was already 0600 and such a test passes
    against the bug. What has to be true is that the file is never created
    permissive in the first place, so this pins the mode creation is asked for.
    """
    seen = {}
    real_open = os.open

    def spy(path, flags, mode=0o777, *a, **kw):
        if str(path).endswith("token.json"):
            seen["flags"], seen["mode"] = flags, mode
        return real_open(path, flags, mode, *a, **kw)

    # Via monkeypatch, not by hand: `tokens.os` *is* the os module, so a manual
    # swap edits it process-wide and a failed assertion would leave it swapped.
    monkeypatch.setattr(os, "open", spy)
    save_token("secret-abc", path=tmp_path / "token.json")

    assert seen, "the token file was not created through os.open with a mode"
    assert seen["mode"] == 0o600, f"created as {oct(seen['mode'])}"
    assert seen["flags"] & os.O_CREAT, "not the call that creates the file"


@posix_only
def test_the_token_file_ends_up_private(tmp_path):
    """The end state, which the chmod already guaranteed — kept so that
    removing the chmod (which is what repairs a file an older build left
    loose) cannot pass unnoticed."""
    path = tmp_path / "token.json"
    path.write_text("{}", encoding="utf-8")
    os.chmod(path, 0o644)
    save_token("secret-abc", path=path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_clearing_a_token_that_is_not_there_is_not_an_error(tmp_path):
    assert clear_token(tmp_path / "nope.json") is False
    path = tmp_path / "token.json"
    save_token("x", path=path)
    assert clear_token(path) is True
    assert load_token(path) is None
