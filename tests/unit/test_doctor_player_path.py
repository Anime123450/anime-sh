"""`anime doctor` must name the player binary playback will actually launch.

On Windows `shutil.which("mpv")` finds `mpv.COM`, the console build, and
playback deliberately switches to the `mpv.exe` beside it -- the console one can
attach to the parent terminal and never open a video window. Doctor used to
report the `.COM`, which is the one binary that cannot be the cause of the
symptom people run doctor about, and doctor output is what goes into a bug
report.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from anime_sh.cli.doctor import _check_player
from anime_sh.infra.players.mpv import MpvPlayer, resolve_binary


def _fake_which(mapping: dict[str, str]):
    return lambda name: mapping.get(name)


def test_doctor_reports_the_exe_not_the_console_shim(tmp_path: Path, monkeypatch):
    com = tmp_path / "mpv.COM"
    exe = tmp_path / "mpv.exe"
    com.write_text("", encoding="utf-8")
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", _fake_which({"mpv": str(com)}))

    check = _check_player("mpv")
    assert check.ok
    assert check.detail == str(exe)
    # The point of the fix: doctor and playback agree on one binary.
    assert check.detail == resolve_binary("mpv")


def test_the_shim_is_still_reported_when_there_is_no_exe_beside_it(
    tmp_path: Path, monkeypatch
):
    """No sibling means no better answer, and naming nothing would be worse."""
    com = tmp_path / "mpv.COM"
    com.write_text("", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", _fake_which({"mpv": str(com)}))

    assert _check_player("mpv").detail == str(com)


def test_a_missing_player_still_fails_with_an_install_hint(monkeypatch):
    monkeypatch.setattr(shutil, "which", _fake_which({}))

    check = _check_player("mpv")
    assert not check.ok
    assert "not found on PATH" in check.detail


def test_playback_and_doctor_resolve_through_the_same_function(tmp_path, monkeypatch):
    """Guards the regression rather than the symptom: if `MpvPlayer` stops using
    `resolve_binary`, or doctor goes back to `shutil.which`, the two drift apart
    again and nothing else here would notice."""
    com = tmp_path / "mpv.COM"
    exe = tmp_path / "mpv.exe"
    com.write_text("", encoding="utf-8")
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", _fake_which({"mpv": str(com)}))

    assert MpvPlayer("mpv").available()
    assert _check_player("mpv").detail == resolve_binary("mpv") == str(exe)
