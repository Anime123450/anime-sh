from anime_sh.config.schema import PlaybackConfig
from anime_sh.domain.models import Subtitle
from anime_sh.domain.subtitles import prioritize_subtitles, player_language_tags


def test_player_language_tag_accepts_english_bcp47_and_legacy_codes():
    assert player_language_tags("en-US") == "eng,en"
    assert player_language_tags("English") == "eng,en"
    assert player_language_tags("ja-JP") == "ja"
    assert player_language_tags("") == ""


def test_subtitles_default_to_english_preference():
    assert PlaybackConfig().subtitle_lang == "en"


def test_preferred_language_beats_provider_default_and_understands_english_labels():
    subtitles = (
        Subtitle("https://sub/fr.vtt", "fr", default=True),
        Subtitle("https://sub/en.vtt", "English (US)"),
        Subtitle("https://sub/eng.vtt", "eng"),
    )
    ordered = prioritize_subtitles(subtitles, "en")
    assert [sub.url for sub in ordered] == [
        "https://sub/en.vtt", "https://sub/eng.vtt", "https://sub/fr.vtt"
    ]


def test_missing_preferred_language_falls_back_to_source_default():
    subtitles = (
        Subtitle("https://sub/fr.vtt", "fr"),
        Subtitle("https://sub/ja.vtt", "ja", default=True),
    )
    ordered = prioritize_subtitles(subtitles, "en")
    assert ordered[0].url == "https://sub/ja.vtt"
