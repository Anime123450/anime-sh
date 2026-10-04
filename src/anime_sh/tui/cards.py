"""Poster cards and the horizontal shelves that hold them.

The composition this exists for: a cinematic hero over horizontal media
shelves, which is what a person means by "an anime app" before they have seen a
terminal one. The first redesign kept vertical lists and a side panel — tidier
than what came before, but structurally the same thing, and the same thing was
what it was asked to move away from.

A poster is the point. A row of text that happens to name a show is a database
listing; a wall of cover art is a library you browse. Terminals can draw cover
art — `render_cover` turns an image into sextant blocks — so the only real
question is how many cells a poster earns, and the answer is "enough to
recognise the show from across the desk".

Textual has no horizontal list widget, so `Shelf` carries its own cursor. That
is the one piece of machinery here; everything else is a render.
"""

from __future__ import annotations

from rich.console import Group
from rich.text import Text
from textual.binding import Binding
from textual.containers import HorizontalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Static

from .coverart import cover_rows, render_cover
from .rows import fit

#: A poster's width in cells, and the rows that implies. `render_cover` keeps
#: the aspect itself — 14 cells comes back 10 rows tall — but the card has to
#: reserve the space *before* the image arrives or every shelf jumps downward
#: as covers land one by one.
CARD_COLS = 14
CARD_ART_ROWS = cover_rows(CARD_COLS)
#: Art, the selection rule, the title and one line of state — the whole card,
#: so a shelf knows its height without measuring a child. `app.tcss` sets the
#: same number on `PosterCard`; `test_cards` fails if the two disagree, because
#: a card one row taller than its box silently loses its caption.
CARD_ROWS = CARD_ART_ROWS + 3


def placeholder(cols: int = CARD_COLS, rows: int = CARD_ART_ROWS) -> Text:
    """What fills a card until its cover arrives.

    Deliberately not a spinner and not blank. A blank card reads as a broken
    card, and a spinner per card turns a shelf into a slot machine — twelve of
    them animating is the "RGB gamer UI" the brief ruled out. A flat plate in the
    panel tone reads as "a poster goes here", which is true, and it is the exact
    size the poster will be, so nothing moves when it lands.
    """
    body = Text()
    for i in range(rows):
        # `grey` rather than `$panel`: this is a Rich renderable (see
        # `PosterCard._colour`), and a Textual variable in a Rich style resolves
        # to nothing at all.
        body.append("░" * cols, style="grey30")
        if i < rows - 1:
            body.append("\n")
    return body


class PosterCard(Static):
    """One show: its cover, its title, and the one fact that matters here.

    The fact is chosen by the shelf that built the card, not by the card —
    Continue Watching wants a resume percentage, This Season wants a countdown,
    and a card that tried to decide for itself would need to know which shelf it
    was on, which is exactly the coupling that makes widgets unreusable.
    """

    #: Selection is drawn, not implied by scroll position: with eleven cards
    #: visible there is no "the middle one is selected" convention to lean on.
    selected: reactive[bool] = reactive(False)

    def __init__(self, anime, caption: str = "", *,
                 resume_episode: float | None = None,
                 fraction: float = 0.0, **kwargs) -> None:
        self.anime = anime
        self.caption = caption
        self.resume_episode = resume_episode
        self.fraction = fraction
        self._art: Text | None = None
        super().__init__(**kwargs)

    def set_caption(self, caption: str) -> None:
        """Re-cut the state line. Used by the minute tick for countdowns."""
        if caption != self.caption:
            self.caption = caption
            self.refresh()

    def set_cover(self, data: bytes | None) -> None:
        """Hand the card its cover bytes. Safe to call with None."""
        if not data:
            return
        art = render_cover(data, cols=CARD_COLS)
        if art is not None:
            self._art = art
            self.refresh()

    def watch_selected(self, value: bool) -> None:
        self.set_class(value, "-on")

    def _colour(self, name: str, fallback: str) -> str:
        """A theme colour as a literal, for the parts drawn with Rich.

        The art is a `rich.text.Text` of per-cell colours, so the whole card has
        to be a Rich renderable rather than Textual markup — and Rich does not
        know what `$accent` is. It does not complain either: the style parses,
        resolves to nothing, and the accent rule comes out in the ordinary
        foreground, which is the one colour it must not be.
        """
        try:
            return self.app.get_css_variables().get(name) or fallback
        except Exception:
            return fallback

    def render(self):
        art = self._art if self._art is not None else placeholder()
        title = fit(self.anime.title.preferred, CARD_COLS).rstrip()
        # The selected card says so in two ways at once: an accent rule under
        # the art, and its title at full strength while the others sit dim. The
        # rule is a shape, so the distinction survives a terminal with no
        # colour — the same bargain the focused row makes everywhere else.
        rule = Text(
            "▔" * CARD_COLS,
            style=self._colour("accent", "cyan") if self.selected
            else self._colour("surface", "grey23"),
        )
        name = Text(title, style="bold" if self.selected else "dim")
        caption = Text(fit(self.caption, CARD_COLS).rstrip(),
                       style=self._colour("accent", "cyan") if self.selected
                       else "dim")
        return Group(art, rule, name, caption)


class Shelf(HorizontalScroll):
    """A row of poster cards with a cursor of its own.

    Textual's list widgets are vertical, so this keeps its own index. It is a
    thin thing deliberately: it owns which card is current, keeps that card in
    view, and says when one is chosen. Everything about *what* the cards say
    belongs to whoever built them.
    """

    BINDINGS = [
        Binding("left", "cursor_left", "Previous", show=False),
        Binding("right", "cursor_right", "Next", show=False),
        Binding("home", "cursor_first", "First", show=False),
        Binding("end", "cursor_last", "Last", show=False),
        Binding("enter", "choose", "Open", show=False),
        # Up and down are bound here, not only on the screen. `HorizontalScroll`
        # inherits bindings for them from `ScrollableContainer` and would
        # consume both to scroll vertically — in a container exactly one card
        # tall, so the key did nothing at all and never reached the screen.
        Binding("up", "leave(-1)", "Previous shelf", show=False),
        Binding("down", "leave(1)", "Next shelf", show=False),
        Binding("k", "leave(-1)", "Previous shelf", show=False),
        Binding("j", "leave(1)", "Next shelf", show=False),
        # `h` only. `l` opens My List app-wide, and a shelf that stole it would
        # make one key mean two things depending on where the cursor was.
        Binding("h", "cursor_left", "Previous", show=False),
    ]

    can_focus = True
    index: reactive[int] = reactive(0)

    class Chosen(Message):
        """Enter, or a click, on a card."""

        def __init__(self, shelf: "Shelf", card: PosterCard) -> None:
            self.shelf = shelf
            self.card = card
            super().__init__()

    class Exited(Message):
        """Up or down: the cursor is leaving this shelf.

        A message rather than the shelf reaching for the screen, so `Shelf`
        stays a widget that knows about cards and nothing about what is above or
        below it.
        """

        def __init__(self, shelf: "Shelf", delta: int) -> None:
            self.shelf = shelf
            self.delta = delta
            super().__init__()

    class Moved(Message):
        """The cursor landed on a different card."""

        def __init__(self, shelf: "Shelf", card: PosterCard) -> None:
            self.shelf = shelf
            self.card = card
            super().__init__()

    @property
    def all_cards(self) -> list[PosterCard]:
        """Every card on the shelf, shown or not.

        Only for whoever is deciding what to show: This Season hides the shows
        already in Continue Watching, and reading the filtered list to make that
        decision would mean a card could never be un-hidden again.
        """
        return [c for c in self.children if isinstance(c, PosterCard)]

    @property
    def cards(self) -> list[PosterCard]:
        """The cards the cursor can reach.

        Filtered, because `display = False` takes a card out of the layout but
        not out of `children` — so a cursor indexing the children could stop on
        a card nobody can see, and then the arrow key appeared to do nothing
        while the hero changed to a show that was not on screen.
        """
        return [c for c in self.all_cards if c.display]

    def reselect(self) -> None:
        """Re-run the selection pass after cards have been shown or hidden.

        Assigning the same index back would not do it: `index` is a reactive and
        a write of the value it already holds does not call the watcher, which is
        where the selection actually moves.
        """
        cards = self.cards
        if not cards:
            return
        i = max(0, min(len(cards) - 1, self.index))
        if i != self.index:
            self.index = i
            return
        for n, card in enumerate(cards):
            card.selected = n == i

    def watch_index(self, value: int) -> None:
        cards = self.cards
        if not cards:
            return
        value = max(0, min(len(cards) - 1, value))
        for i, card in enumerate(cards):
            card.selected = i == value
        # Keep the cursor on screen. `immediate` because a shelf that eases to
        # the next card makes held-down arrow keys feel like wading.
        self.scroll_to_widget(cards[value], animate=False, immediate=True)
        self.post_message(self.Moved(self, cards[value]))

    def _step(self, delta: int) -> None:
        cards = self.cards
        if not cards:
            return
        # Clamped, not wrapped: jumping from the last poster back to the first
        # throws the eye the whole width of the screen for one keypress.
        self.index = max(0, min(len(cards) - 1, self.index + delta))

    def action_cursor_left(self) -> None:
        self._step(-1)

    def action_cursor_right(self) -> None:
        self._step(1)

    def action_cursor_first(self) -> None:
        self.index = 0

    def action_cursor_last(self) -> None:
        self.index = max(0, len(self.cards) - 1)

    def action_leave(self, delta: int) -> None:
        self.post_message(self.Exited(self, delta))

    def action_choose(self) -> None:
        cards = self.cards
        if cards:
            self.post_message(self.Chosen(self, cards[min(self.index, len(cards) - 1)]))

    def on_click(self, event) -> None:
        """Clicking a card selects it; clicking the selected card opens it.

        Two-step rather than open-on-first-click, because a poster wall is
        something you point at to *look* at — the hero above updates as you
        move — and a single stray click launching a video player is a worse
        mistake than one extra click.
        """
        card = event.widget
        while card is not None and not isinstance(card, PosterCard):
            card = card.parent
        if card is None:
            return
        cards = self.cards
        if card not in cards:
            return
        self.focus()
        i = cards.index(card)
        if i == self.index:
            self.post_message(self.Chosen(self, card))
        else:
            self.index = i
