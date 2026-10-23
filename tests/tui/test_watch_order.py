from anime_sh.domain.models import Anime, AnimeId, Format, Title
from anime_sh.tui.screens.watch_order import build_watch_order


def anime(id, title, *, year=None, fmt=Format.TV):
    return Anime(
        id=AnimeId(anilist=id), title=Title(romaji=title), year=year,
        format=fmt,
    )


def test_watch_order_positions_prequels_and_sequels_around_current_title():
    current = anime(10, "Season Two", year=2022)
    relations = [
        ("SEQUEL", anime(12, "Season Three", year=2023)),
        ("SIDE_STORY", anime(14, "Side Story Movie", year=2021, fmt=Format.MOVIE)),
        ("PREQUEL", anime(9, "Season One", year=2020)),
    ]

    rows = build_watch_order(current, relations)

    assert [(label, entry.title.preferred) for label, entry in rows] == [
        ("Before this", "Season One"),
        ("Current", "Season Two"),
        ("After this", "Season Three"),
        ("Related · side story", "Side Story Movie"),
    ]


def test_watch_order_does_not_claim_side_story_movie_is_between_seasons():
    current = anime(10, "Season One", year=2020)
    movie = anime(11, "Franchise Movie", year=2021, fmt=Format.MOVIE)

    rows = build_watch_order(current, [("SIDE_STORY", movie)])

    assert rows[-1][0] == "Related · side story"


def test_watch_order_handles_no_known_relations():
    current = anime(10, "Standalone", year=2020)
    assert build_watch_order(current, []) == [("Current", current)]
