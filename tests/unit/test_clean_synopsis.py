from anime_sh.tui.format import clean_synopsis


def test_strips_source_and_notes():
    raw = ("He can rewind death.<br><br>(Source: Anime News Network)<br><br>"
           "<i>Notes:<br>- The first episode aired with a runtime of ~50 minutes.</i>")
    assert clean_synopsis(raw) == "He can rewind death."


def test_strips_bare_source_line_and_tags():
    assert clean_synopsis("A <b>bold</b> [tale].\n\nSource: Crunchyroll") == "A bold (tale)."


def test_plain_text_kept_and_empty():
    assert clean_synopsis("Two  girls\n travel.") == "Two girls travel."
    assert clean_synopsis(None) == ""
