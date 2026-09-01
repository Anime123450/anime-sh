"""`anime <query>` sugar must not swallow mistyped commands.

Anything unrecognised in the first argument position is rewritten to
`anime play <that>`. That is the headline feature — `anime "Frieren"` plays
Frieren — but it also meant a guessed or mistyped *command* silently became a
search: `anime plugins` went off to resolve a stream for a show called
"plugins" instead of saying it did not recognise the command.

The risk in fixing it runs the other way: flag too eagerly and anime-sh refuses
to play real shows. So the guard is deliberately narrow, and the tests below pin
both edges — the typos it must catch, and the titles it must never touch.
"""

from __future__ import annotations

import difflib

import pytest

from anime_sh.cli.main import (
    _NOT_A_COMMAND,
    _TYPO_CUTOFF,
    _command_suggestion,
    _known_commands,
)

KNOWN = _known_commands()


# One-word titles a user would reasonably type bare. `bleach` is the dangerous
# one: it scores 0.67 against `search`, the highest any real title reaches.
REAL_TITLES = [
    "frieren", "monster", "bleach", "naruto", "clannad", "gintama", "haikyuu",
    "toradora", "nichijou", "kaguya", "dandadan", "vinland", "mushishi",
    "hyouka", "erased", "baccano", "durarara", "psycho", "trigun", "berserk",
    "claymore", "noragami", "kakegurui", "overlord", "planetes", "shirobako",
    "barakamon", "mononoke", "steins", "serial",
]


def test_known_commands_were_actually_discovered():
    """Every test here is vacuous if the command list comes back empty — which
    is exactly what the bare `except` in `_known_commands` can produce."""
    assert len(KNOWN) > 20
    assert {"play", "search", "providers", "download"} <= KNOWN


# --------------------------------------------------------------------------- #
# The reported bug
# --------------------------------------------------------------------------- #
def test_plugins_is_not_treated_as_a_show_to_play():
    """The original report. `docs/plugins.md` exists, so this is a reasonable
    thing to type — and it used to start resolving a stream."""
    hint = _command_suggestion("plugins", KNOWN)
    assert hint is not None
    assert "providers ls" in hint


def test_the_suggestion_says_how_to_force_a_search_anyway():
    """Refusing without an escape hatch would make a legitimately-named show
    unplayable."""
    hint = _command_suggestion("plugins", KNOWN)
    assert 'anime play "plugins"' in hint


@pytest.mark.parametrize("word", sorted(_NOT_A_COMMAND))
def test_every_known_non_command_word_is_caught(word):
    """These are guesses, not typos: `plugins` scores 0.55 against the nearest
    real command, below where fuzzy matching can help. They only work because
    they are listed explicitly, so the list has to stay wired up."""
    assert _command_suggestion(word, KNOWN) is not None


# --------------------------------------------------------------------------- #
# Typos
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "typo,expected",
    [
        ("serach", "search"),
        ("doctro", "doctor"),
        ("donwload", "download"),
        ("downlaod", "download"),
        ("provider", "providers"),
        ("configs", "config"),
        ("statuses", "status"),
    ],
)
def test_a_mistyped_command_suggests_the_real_one(typo, expected):
    hint = _command_suggestion(typo, KNOWN)
    assert hint is not None, f"{typo} should have been recognised as a typo"
    assert expected in hint


# --------------------------------------------------------------------------- #
# The false-positive edge — the expensive half to get wrong
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("title", REAL_TITLES)
def test_a_real_one_word_title_still_plays(title):
    """None of these may be intercepted. `anime bleach` has to play Bleach."""
    assert _command_suggestion(title, KNOWN) is None, (
        f"{title!r} was mistaken for a command — the sugar is the headline "
        f"feature and this breaks it"
    )


def test_the_cutoff_sits_above_where_real_titles_reach():
    """Documents why the threshold is what it is, and fails if someone lowers it
    to catch one more typo without checking what it costs.

    Measured over the real command list: the worst-case legitimate title scores
    0.67, genuine typos start around 0.80.
    """
    worst = max(
        max(difflib.SequenceMatcher(None, t, k).ratio() for k in KNOWN)
        for t in REAL_TITLES
    )
    assert worst < _TYPO_CUTOFF, (
        f"a real title scores {worst:.2f} but the cutoff is {_TYPO_CUTOFF} — "
        f"that title would stop playing"
    )


# --------------------------------------------------------------------------- #
# Everything else must pass straight through
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("command", ["play", "search", "doctor", "trending"])
def test_real_commands_are_never_second_guessed(command):
    """The guard only ever sees words that failed the known-command check, but
    an exact match must be inert even if it does."""
    assert _command_suggestion(command, KNOWN) is None


def test_a_multiword_title_is_left_alone():
    """Only the first bare argument is ever considered, and a phrase is
    obviously a title."""
    assert _command_suggestion("attack on titan", KNOWN) is None


def test_every_count_option_has_a_floor():
    """A non-positive `--limit` was passed straight through to whatever was
    behind it. AniList ignores one and returns a full page; SQLite reads
    `LIMIT -1` as *no* limit. So `--limit 0` printed thirty rows and
    `history --limit -1` printed everything — the flag doing the opposite of
    what it says, quietly, in both directions.

    Asserted over the parsed commands rather than one example, so a `--limit`
    added later cannot be the unguarded one.
    """
    import click
    import typer

    from anime_sh.cli.main import app

    group = typer.main.get_command(app)
    ctx = click.Context(group)
    checked = 0
    for name in group.list_commands(ctx):
        for param in group.get_command(ctx, name).params:
            if not set(param.opts) & {"--limit", "--days"}:
                continue
            checked += 1
            # Duck-typed rather than isinstance: typer hands back its own
            # range class, so checking the type would pass for the wrong
            # reason today and fail for the wrong reason later.
            low = getattr(param.type, "min", None)
            assert low is not None and low >= 1, (
                f"{name} {param.opts}: unbounded, so 0 and negatives get through"
            )
    assert checked >= 6, f"only found {checked} count options; did they move?"
