from anime_sh.domain.models import Stream, StreamKind, Subtitle
from anime_sh.infra.players.mpv import MpvPlayer


def test_mpv_selects_preferred_external_subtitle_and_sets_language():
    stream = Stream(
        url="https://cdn.example/episode.m3u8",
        kind=StreamKind.HLS,
        subtitles=(
            Subtitle("https://subs.example/fr.vtt", "fr", default=True),
            Subtitle("https://subs.example/en.vtt", "English (US)"),
        ),
        preferred_subtitle_language="en",
    )
    args = MpvPlayer()._build_args(
        "mpv", stream, "Series - Episode 1", 42, "/tmp/anime-sh.sock"
    )
    assert "--tls-verify=no" not in args
    assert "--slang=eng,en" in args
    assert "--sub-file=https://subs.example/en.vtt" in args
    assert "--sub-file=https://subs.example/fr.vtt" not in args
    assert args[-1] == stream.url
