"""A blank line inside a cue cut every subtitle after it.

hianime's English track for Magic Repo Man ep 3 has a stat-sheet cue at 13:20
whose text contains an empty line. WebVTT ends a cue at a blank line, so mpv's
parser hit a bare "Ability" line where a timestamp should be and dropped the
rest of the file: subtitles stopped at 13:20 of a 22-minute episode.
"""
from __future__ import annotations

import pathlib

from anime_sh.domain.models import Stream, StreamKind, Subtitle
from anime_sh.domain.subtitles import repair_webvtt
from anime_sh.infra.players import mpv as mpv_mod
from anime_sh.infra.players.mpv import MpvPlayer

BROKEN = (
    "WEBVTT\r\n\r\n"
    "13:17.710 --> 13:20.620\r\nMy parchment filled up with stats.\r\n\r\n"
    "13:20.250 --> 13:21.900\r\n<b>Loan Magic\r\n\r\nAbility\r\nAdjustment</b>\r\n\r\n"
    "intro\r\n13:20.620 --> 13:23.970\r\nYou can swipe to see more.\r\n\r\n"
    "NOTE a comment\r\n\r\n"
    "13:23.970 --> 13:25.950\r\nWhat?\r\n"
)


def _cues(text):
    return [b.strip() for b in text.split("\n\n") if "-->" in b]


def test_blank_lines_inside_a_cue_are_removed():
    fixed = repair_webvtt(BROKEN)
    cues = _cues(fixed)
    assert len(cues) == 4
    assert cues[1] == "13:20.250 --> 13:21.900\n<b>Loan Magic\nAbility\nAdjustment</b>"
    assert cues[2].startswith("intro\n13:20.620")  # cue identifiers survive
    assert "NOTE a comment" in fixed


def test_a_well_formed_file_is_unchanged():
    ok = "WEBVTT\n\n00:01.000 --> 00:02.000\nHi\n\n00:03.000 --> 00:04.000\nBye\n"
    assert repair_webvtt(ok) == ok


def test_mpv_gets_a_repaired_local_copy(monkeypatch, tmp_path):
    seen = {}

    def fake_fetch(url, headers):
        seen["url"], seen["referer"] = url, headers.get("Referer")
        return BROKEN.encode()

    monkeypatch.setattr(mpv_mod, "_fetch_subtitle", fake_fetch)
    monkeypatch.setattr(mpv_mod, "_subtitle_dir", lambda: tmp_path)
    stream = Stream(url="https://cdn.example/master.m3u8", kind=StreamKind.HLS,
                    headers={"Referer": "https://zokoanime.video/"},
                    subtitles=(Subtitle("https://subs.example/en.vtt", "English"),))
    local = mpv_mod._localize_subtitle(stream)
    assert local is not None and seen["referer"] == "https://zokoanime.video/"
    assert len(_cues(pathlib.Path(local).read_text(encoding="utf-8"))) == 4
    args = MpvPlayer()._build_args("mpv", stream, "t", 0, "p", subtitle_path=local)
    assert f"--sub-file={local}" in args
    assert "--sub-file=https://subs.example/en.vtt" not in args


def test_mpv_falls_back_to_the_url_when_the_download_fails(monkeypatch, tmp_path):
    def boom(url, headers):
        raise OSError("offline")

    monkeypatch.setattr(mpv_mod, "_fetch_subtitle", boom)
    monkeypatch.setattr(mpv_mod, "_subtitle_dir", lambda: tmp_path)
    stream = Stream(url="https://cdn.example/master.m3u8", kind=StreamKind.HLS,
                    subtitles=(Subtitle("https://subs.example/en.vtt", "English"),))
    assert mpv_mod._localize_subtitle(stream) is None
    args = MpvPlayer()._build_args("mpv", stream, "t", 0, "p", subtitle_path=None)
    assert "--sub-file=https://subs.example/en.vtt" in args
