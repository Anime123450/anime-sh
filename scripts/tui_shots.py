"""Render the TUI at a set of terminal sizes and write SVG + an index page.

Visual QA for the TUI. Textual can paint a screen headlessly at an exact size
and export it as SVG, which is the only way to actually *look* at a terminal
layout at 80x24 and 200x60 without owning six terminals.

    uv run python scripts/tui_shots.py out/dir [--screen home]

The fake services below carry a library the size of a real one -- twenty-odd
continue-watching rows, a full season, long titles, a show at episode 1180 --
because every layout bug this is meant to catch only appears when the data is
big enough to overflow something.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from anime_sh.domain.models import (  # noqa: E402
    Anime,
    AnimeId,
    Audio,
    FavoriteItem,
    Format,
    ResumeItem,
    SearchResult,
    Status,
    Title,
    WatchProgress,
)
from anime_sh.tui import AnimeShApp, TuiServices  # noqa: E402

NOW = datetime.now(timezone.utc)

#: (width, height) terminals to render. The small end is the classic 80x24 that
#: everything must survive; the large end is a maximised modern terminal, where
#: the risk is not clipping but a layout that stretches into emptiness.
SIZES = [(80, 24), (100, 30), (120, 40), (160, 50), (200, 60)]


def _anime(
    anilist, title, *, eps=12, status=Status.FINISHED, next_ep=None, mins=None,
    year=2026, score=None, genres=(), english=None, studio=None, synopsis=None,
):
    return Anime(
        id=AnimeId(anilist=anilist),
        title=Title(romaji=title, english=english),
        format=Format.TV,
        status=status,
        episode_count=eps,
        year=year,
        genres=tuple(genres),
        average_score=score,
        studio=studio,
        synopsis=synopsis,
        cover_url=None,
        next_airing_episode=next_ep,
        next_airing_at=(NOW + timedelta(minutes=mins)) if mins is not None else None,
    )


# A library shaped like a real one: part-watched shows, caught-up airing shows,
# finished shows with an episode waiting, and titles long enough to truncate.
_CONTINUE = [
    (_anime(1, "Skeleton Knight in Another World Season 2", eps=12,
            status=Status.RELEASING, next_ep=9, mins=60 * 29,
            score=72, genres=("Action", "Adventure", "Comedy"), studio="Studio KAI",
            synopsis="Arc returns! After waking to find he's become a Skeleton "
                     "Knight in his favourite fantasy video game, Arc, Ariane, "
                     "Chiyome and Ponta band together to take on evil."),
     8.0, 262, 1429, False),
    (_anime(2, "Lord of Mysteries", eps=13, status=Status.RELEASING,
            next_ep=2, mins=60 * 5, score=84), 1.0, 70, 1400, False),
    (_anime(3, "BOCCHI THE ROCK!", eps=12, score=89), 3.0, 700, 1400, False),
    (_anime(4, "The King's Avatar", eps=12), 2.0, 952, 1400, False),
    (_anime(5, "Ghost in the Shell", eps=26), 1.0, 14, 1400, False),
    (_anime(6, "Pokemon", english="Pokémon", eps=276, status=Status.RELEASING,
            next_ep=3, mins=60 * 50), 2.0, 0, 0, True),
    (_anime(7, "Solo Leveling Season 2 -Arise from the Shadow-", eps=13,
            score=86), 7.0, 0, 0, True),
    (_anime(8, "That Time I Got Reincarnated as a Slime Season 4", eps=24),
     22.0, 0, 0, True),
    (_anime(9, "Rich Girl Caretaker: I'm Secretly the Caregiver of the Most "
               "Popular Girl in School", eps=12), 11.0, 0, 0, True),
    (_anime(10, "Trapped in a Dating Sim: The World of Otome Games is Tough "
                "for Mobs", eps=12), 12.0, 0, 0, True),
    (_anime(11, "The Ogre's Bride", eps=12), 9.0, 0, 0, True),
    (_anime(12, "The Exiled Heavy Knight Knows How to Game the System", eps=26,
            status=Status.RELEASING, next_ep=16, mins=60 * 70), 11.0, 0, 0, True),
    (_anime(13, "The World's Strongest Rearguard", eps=12), 7.0, 0, 0, True),
    (_anime(14, "The Duke's Son Claims He Won't Love Me Yet Showers Me with "
                "Adoration", eps=12), 7.0, 0, 0, True),
    (_anime(15, "The 100 Girlfriends Who Really, Really, Really, Really, "
                "REALLY Love You", eps=12), 10.0, 0, 0, True),
    (_anime(16, "The Insipid Prince's Furtive Grab for the Throne", eps=12),
     10.0, 0, 0, True),
    (_anime(17, "Tomb Raider King", eps=12), 7.0, 0, 0, True),
    (_anime(18, "The Angel Next Door Spoils Me Rotten2", eps=12), 8.0, 0, 0, True),
    (_anime(19, "Full-Time Magister S2", eps=12), 6.0, 0, 0, True),
    (_anime(20, "ONE PIECE", eps=None, status=Status.RELEASING, next_ep=1181,
            mins=60 * 100), 1180.0, 400, 1430, False),
]

_SEASONAL = [
    _anime(101, "A Tale of the Secret Saint", eps=12, status=Status.RELEASING,
           next_ep=4, mins=60 * 26),
    _anime(102, "Black Clover Season 2", eps=None, status=Status.RELEASING,
           next_ep=7, mins=60 * 2),
    _anime(103, "#I'm Looking For a Zombie", eps=12, status=Status.RELEASING,
           next_ep=2, mins=90),
    _anime(104, "Even the Student Council Has Its Holes!", eps=12,
           status=Status.RELEASING, next_ep=1, mins=60 * 20),
    _anime(105, "Ranma1/2 (2024) Season 3", eps=12, status=Status.RELEASING,
           next_ep=2, mins=60 * 21),
    _anime(106, "Magic Repo Man: Dumped by My Party, I'll Cash In With a Cute "
                "Supporter", eps=12, status=Status.RELEASING, next_ep=1, mins=60 * 22),
    _anime(107, "Blue Box Season 2", eps=24, status=Status.RELEASING, next_ep=5,
           mins=60 * 45),
    _anime(108, "Reborn as a Space Mercenary: I Woke Up Piloting the Strongest "
                "Starship", eps=12, status=Status.RELEASING, next_ep=3, mins=60 * 71),
    _anime(109, "Aoashi Season 2", eps=24, status=Status.RELEASING, next_ep=6,
           mins=60 * 95),
    _anime(110, "Overgeared", eps=12, status=Status.RELEASING, next_ep=2,
           mins=60 * 26),
    _anime(111, "As a Reincarnated Aristocrat, I'll Use My Appraisal Skill to "
                "Rise in the World", eps=12, status=Status.RELEASING, next_ep=2,
           mins=60 * 26),
    _anime(112, "My Girlfriend's Friend", eps=12, status=Status.RELEASING,
           next_ep=3, mins=60 * 120),
    _anime(113, "I'm Dating a Dark Summoner", eps=12),
    _anime(114, "Hello, I am a Witch and my Crush Wants me to Make a Love Potion!",
           eps=12),
    _anime(115, "PSYREN", eps=24),
    _anime(116, "Mahou Shoujo Ikusei Keikaku: restart", eps=12),
    _anime(117, "Nia Liston: The Merciless Maiden", eps=12),
    _anime(118, "The Cold Sato-san is Only Sweet to Me", eps=12),
    _anime(119, "The Laid-Off Cheat-Granting Mage Enjoys a Second Lease on Life",
           eps=12),
]

_TRENDING = [
    _anime(201, "The Apothecary Diaries Season 3", eps=12, status=Status.RELEASING,
           next_ep=2, mins=60 * 145, score=90, genres=("Drama", "Mystery")),
    _anime(202, "Ascendance of a Bookworm: Adopted Daughter of an Archduke",
           eps=24, score=85),
    _anime(203, "Honzuki no Gekokujou: Kizoku-in no Jishou Tosho Iin", eps=12),
    _anime(204, "Frieren: Beyond Journey's End", eps=28, score=92,
           genres=("Adventure", "Drama", "Fantasy"), studio="MADHOUSE",
           synopsis="The demon king has been defeated, and the victorious hero "
                    "party returns home before disbanding. The four finally "
                    "go their separate ways, and to the elven mage Frieren, "
                    "this feels like just a small moment out of her life. She "
                    "departs on a new journey to the north with a promise to "
                    "her companions that she will return to see them again. "
                    "Half a century passes, and Frieren arrives at the home of "
                    "the hero Himmel, only to find that the decades have not "
                    "been kind. Faced with the weight of a mortal life, she "
                    "resolves to learn what she never thought to ask: who "
                    "the people beside her really were."),
    _anime(205, "DAN DA DAN Season 2", eps=12, status=Status.RELEASING,
           next_ep=8, mins=60 * 33, score=88),
    _anime(206, "SAKAMOTO DAYS Part 2", eps=11, score=79),
    _anime(207, "Kaiju No. 8 Season 2", eps=12, score=81),
    _anime(208, "Wind Breaker Season 2", eps=12, score=80),
    _anime(209, "Gachiakuta", eps=24, score=83),
    _anime(210, "The Fragrant Flower Blooms with Dignity", eps=12, score=86),
]

_FAVORITES = [_anime(204, "Frieren: Beyond Journey's End", eps=28, score=92),
              _anime(3, "BOCCHI THE ROCK!", eps=12, score=89)]


class FakeSearch:
    async def search(self, query, *, limit=25):
        # A miss returns nothing, rather than falling back to the first eight of
        # the pool. The fallback meant the no-matches state could not be
        # rendered at all, so the one screen with an explicit empty state was
        # the one screen this harness could never photograph.
        pool = _TRENDING + _SEASONAL
        hits = [a for a in pool if query.lower() in a.title.preferred.lower()]
        return [SearchResult(anime=a) for a in hits]


class FakeMetadata:
    name = "anilist"

    async def trending(self, *, limit=20):
        return list(_TRENDING)[:limit]

    async def seasonal(self, season, year):
        return list(_SEASONAL)

    async def get(self, anime_id):
        return None

    async def sequel(self, anime_id):
        return None

    async def airing_schedule(self, start, end):
        return []


class FakeLibrary:
    async def continue_watching(self, *, limit=20):
        out = []
        for anime, ep, pos, dur, done in _CONTINUE[:limit]:
            out.append(ResumeItem(
                anime=anime,
                progress=WatchProgress(anime.id, ep, pos, dur, NOW, completed=done),
            ))
        return out

    async def favorites(self):
        return [FavoriteItem(anime=a, added_at=NOW) for a in _FAVORITES]

    async def progress_for(self, anime_id):
        return []


class FakePlayback:
    async def play_and_track(self, anime, number, *, audio=None, source=None):
        return None

    async def available_episodes(self, anime, *, audio=None, source=None):
        return [float(n) for n in range(1, 13)]

    async def list_sources(self, anime, *, audio=None):
        return []

    def set_on_event(self, cb):
        return None


async def _noop():
    return None


def make_app(theme: str = "midnight") -> AnimeShApp:
    services = TuiServices(
        search=FakeSearch(), metadata=FakeMetadata(), library=FakeLibrary(),
        playback=FakePlayback(), aclose=_noop, tracker=None, sync=None,
    )
    return AnimeShApp(services, theme=theme, audio=Audio.SUB)


def _with_intrinsic_size(svg: str) -> str:
    """Give the SVG the `width`/`height` its `viewBox` implies.

    Textual writes a `viewBox` and nothing else, which leaves the image with no
    intrinsic size: a browser falls back to the 300x150 default for a replaced
    element and upscales *that* to whatever the markup asks for, so a shot
    dropped into the README renders as an illegible smear. `test_readme_promises`
    fails the build over it, which is how this was found the first time.
    """
    box = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
    if not box or "width=" in svg[:200]:
        return svg
    w, h = (round(float(n)) for n in box.groups())
    return svg.replace("<svg ", f'<svg width="{w}" height="{h}" ', 1)


async def _shoot(out: Path, size, screen: str, theme: str, settle: float,
                 query: str = "frie") -> Path:
    w, h = size
    app = make_app(theme)
    async with app.run_test(size=(w, h)) as pilot:
        await pilot.pause()
        await asyncio.sleep(settle)
        await pilot.pause()
        if screen == "search":
            await pilot.press("/")
            for ch in query:
                await pilot.press(ch)
            await asyncio.sleep(0.8)
            await pilot.pause()
        elif screen == "detail":
            await pilot.press("enter")
            await asyncio.sleep(0.8)
            await pilot.pause()
        elif screen == "help":
            await pilot.press("question_mark")
            await asyncio.sleep(0.4)
            await pilot.pause()
        svg = _with_intrinsic_size(
            app.export_screenshot(title=f"anime-sh {w}x{h}"))
        text = _export_text(app)
    name = f"{screen}-{w}x{h}.svg"
    (out / name).write_text(svg, encoding="utf-8")
    # The character grid beside the picture. A screenshot shows whether it looks
    # right; the text shows exactly which column every cell starts in, which is
    # what alignment and truncation bugs actually live in.
    (out / f"{screen}-{w}x{h}.txt").write_text(text, encoding="utf-8")
    return out / name


def _export_text(app) -> str:
    """The rendered screen as plain text, the same way `export_screenshot` gets
    its SVG — same compositor render, recorded to a Rich console, exported as
    text instead of as a picture."""
    import io as _io

    from rich.console import Console

    w, h = app.size
    console = Console(
        width=w, height=h, file=_io.StringIO(), force_terminal=True,
        color_system="truecolor", record=True, legacy_windows=False, safe_box=False,
    )
    console.print(
        app.screen._compositor.render_update(
            full=True, screen_stack=app._background_screens, simplify=False
        )
    )
    return console.export_text()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--screen", default="home",
                    choices=["home", "search", "detail", "help"])
    ap.add_argument("--theme", default="midnight")
    ap.add_argument("--settle", type=float, default=1.2)
    # What `--screen search` types. `--query zzz` is how the no-matches state
    # gets photographed.
    ap.add_argument("--query", default="frie")
    ap.add_argument("--sizes", default="")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sizes = SIZES
    if args.sizes:
        sizes = [tuple(int(n) for n in s.split("x")) for s in args.sizes.split(",")]

    made = []
    for size in sizes:
        made.append(await _shoot(out, size, args.screen, args.theme, args.settle,
                                 args.query))
        print("wrote", made[-1].name)

    # One viewer page per shot, with the SVG blown up to the full viewport width.
    # An SVG served on its own renders at its natural size, which for a terminal
    # is a few hundred pixels in the corner of the window — too small to judge
    # alignment, contrast or truncation on.
    for p in made:
        (out / f"{p.stem}.html").write_text(
            "<!doctype html><meta charset=utf-8>"
            f"<title>{p.stem}</title>"
            "<style>html,body{margin:0;background:#07090e}"
            "img{display:block;width:100vw}</style>"
            f'<img src="{p.name}" alt="{p.stem}">',
            encoding="utf-8",
        )
    cards = "\n".join(
        f'<figure><figcaption><a href="{p.stem}.html">{p.stem}</a></figcaption>'
        f'<img src="{p.name}" alt="{p.stem}"></figure>'
        for p in made
    )
    (out / "index.html").write_text(
        "<!doctype html><meta charset=utf-8>"
        "<title>anime-sh TUI</title>"
        "<style>body{background:#0b0d12;color:#c7d0e0;font:13px/1.5 ui-monospace,"
        "monospace;margin:0;padding:24px}figure{margin:0 0 32px}"
        "figcaption{padding:6px 2px;color:#7f8ca3;letter-spacing:.08em;"
        "text-transform:uppercase;font-size:11px}"
        "img{display:block;width:100%;max-width:1500px;border-radius:8px}</style>"
        f"{cards}",
        encoding="utf-8",
    )
    print("index:", (out / "index.html").resolve().as_uri())


if __name__ == "__main__":
    asyncio.run(main())
