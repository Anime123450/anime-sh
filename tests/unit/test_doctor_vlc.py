"""`anime doctor` finds VLC where playback will, and says how to fix it."""

from __future__ import annotations

from anime_sh.cli import doctor


def test_doctor_reports_configured_vlc(tmp_path):
    exe = tmp_path / "vlc.exe"
    exe.write_text("")
    check = doctor._check_player("vlc", str(exe))
    assert check.ok and str(exe) in check.detail and "player.vlc_path" in check.detail


def test_doctor_reports_wrong_vlc_path_with_the_fix(tmp_path):
    check = doctor._check_player("vlc", str(tmp_path / "nope" / "vlc.exe"))
    assert not check.ok
    assert "player.vlc_path" in check.detail
    assert "player.name mpv" in check.detail


def test_doctor_mpv_check_unchanged(monkeypatch):
    monkeypatch.setattr(doctor, "resolve_binary", lambda name: None)
    check = doctor._check_player("mpv")
    assert not check.ok and "mpv" in check.detail
