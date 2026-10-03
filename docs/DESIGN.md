# anime-sh — interface design

The visual language of the TUI, and the reasoning behind it. Written to be
falsifiable: most of the rules here are asserted by `tests/tui/test_home_design.py`
and `tests/tui/test_themes.py`, so a change that breaks one fails the suite
rather than quietly degrading the screen.

Reference: [monospace-design-tui](https://github.com/coreyt/monospace-design-tui),
whose breakpoints, spacing scale and four-treatment typography this follows.

---

## 1. What was wrong

This screen has been got wrong twice, in opposite directions, and both diagnoses
are worth keeping because the second one is the reason the current design exists.

**The original.** Measured on a 120x40 render of a real-sized library:

- **four lists at once** (18 + 2 + 19 + 10 rows) with equal visual weight, so
  nothing led and Trending never appeared above the fold;
- the words **"new episode" on 13 consecutive rows** — a third of the screen
  spent saying the same thing in the same dim grey;
- **10 of 18 titles truncated** at ~34 cells while the panel beside them broke
  a synopsis mid-word (`Studi...`, then `his` alone on a line);
- **three rule-filled headers** (`Continue Watching ------- 18`), decoration
  carrying one integer;
- **no focal point**: the only image on screen was 22 cells wide in a corner.

**The first redesign**, which fixed all five of those and was still wrong. It
kept stacked vertical lists and added an information panel down the right-hand
side. Tidier, better proportioned, legible — and structurally the same screen,
which is the one thing the brief had asked it not to be. Rows are an efficient
way to present a database. Anime is not a database: the poster *is* the
metadata, and a person recognises a show from a thumbnail faster than from its
name set in a column. A client for a visual medium whose main screen is a wall
of text has misidentified what it is showing.

The argument that produced it is recorded in [S11](#11-what-was-rejected-and-why)
below, because it was made carefully, in numbers, and was wrong anyway.

## 2. Principles

1. **One thing leads.** The screen answers "what am I in the middle of?" before
   it answers anything else.
2. **The poster is the data.** Cover art is not decoration on top of a list; it
   is the fastest identifier the medium has. Spend cells on it.
3. **Hierarchy before decoration.** Depth comes from three background tiers and
   from weight, never from borders. A border costs two rows to say what a shade
   already says.
4. **Progressive disclosure.** Detail is on screen for exactly one show — the
   one the cursor is on — and never for all of them at once.
5. **Nothing moves that was not asked to move.** A card reserves its full size
   before its cover arrives, panels go quiet rather than disappearing, and a
   shelf keeps its height whether it is full or nearly empty.
6. **Colour never carries meaning alone.** Every state has a shape as well as a
   hue, so the screen survives a monochrome terminal.

## 3. Composition

```
+--------------------------------------------------------------------------+
|  anime-sh  .  Continue                           / search   ? help       |  TopBar
|                                                                          |
|  +----------+   SKELETON KNIGHT IN ANOTHER WORLD SEASON 2                |
|  |          |   TV . 12 eps . 2026   * 72%                               |
|  |          |   Action . Adventure . Comedy . Studio KAI                 |
|  |  poster  |                                                            |  Hero
|  |    24    |   o airing   Ep 9 in 1d 4h                                 |
|  |  cells   |   ====------------------  18%                              |
|  |          |                                                            |
|  |          |   Arc returns! After waking to find he's become a ...      |
|  +----------+    > resume episode 8                                      |
|                                                                          |
|   C O N T I N U E   W A T C H I N G   19                                 |
|   +------+  +------+  +------+  +------+  +------+  +------+  +------+   |
|   | 14c  |  |      |  |      |  |      |  |      |  |      |  |      |   |  Shelf
|   +======+  +------+  +------+  +------+  +------+  +------+  +------+   |
|   Skeleton.  Lord of.  BOCCHI .  The Kin.  Ghost i.  ONE PIE.  The Ex.   |
|   ep 8 . 18%  ep 1 . 5%  ep 3 . 50% ...                                  |
|                                                                          |
|   F A V O U R I T E S   2                                                |
|   +------+  +------+                                                     |  Shelf
|                                                                          |
|  ^ resume   l my list   / search   ? help   q quit                       |  ActionBar
+--------------------------------------------------------------------------+
```

- **TopBar** — one row: identity, which shelf you are on, how to search.
- **Hero** — one show, large: a 24-cell poster beside everything known about it,
  ending in a filled accent bar naming what Enter does. It follows the cursor,
  so it is never stale and never describes more than one thing.
- **Shelves** — horizontal rows of 14-cell posters, each with its title and the
  one piece of state that matters under it. Arrow keys walk a shelf; up and down
  change shelves. About eleven cards fit across at 190 columns.
- **ActionBar** — one row: what the card under the cursor can do, then the
  global keys.

The hero sits **outside** the scrolling region. It describes whatever the cursor
is on, so scrolling it away would leave the shelves annotating something that is
no longer on screen.

The cost, stated plainly because it is real: a shelf is 15 rows where a list of
rows was one. At 190x50 this is the hero and about two shelves; the old screen
fitted four lists in the same space. Fewer things, bigger, is the trade — and
`v` (compact density) buys two rows a shelf back for people who would rather
have the fourth shelf.

## 4. Breakpoints

Width, following the monospace standard:

| Cells | Name | Hero poster | Hero | Shelves |
|---|---|---|---|---|
| < 80 | compact | none | hidden | ~4 cards across |
| 80-119 | standard | 16 cells (11 rows) | shown | ~5 cards |
| 120-159 | expanded | 20 cells (14 rows) | shown | ~7 cards |
| >= 160 | wide | 24 cells (17 rows) | shown | 9-12 cards |

A card is a fixed 14 cells wide at every size. Extra width becomes *more
posters*, not wider ones: a poster you can recognise a show from is the point,
and 14 cells is where that happens — below it the art is mud, above it the
shelf holds fewer shows for no gain.

The hero's **height is derived, not tabled**: it is however many rows its poster
comes back as, from `coverart.cover_rows`, floored at 12 so the text beside it
always has room for a line or two of synopsis. Pinning it to the widest band's
17 rows (which shipped, briefly) made a 120-column terminal reserve 17 rows for
a 14-row poster and leave three blank between the hero and the first shelf —
three rows is a quarter of a card, taken off the shelves to pad a gap.

Height below 30 rows drops the hero entirely. At 24 rows it would leave six for
the shelves, which is less than one card, so the screen would be a hero and
nothing else — a detail screen with extra steps. What ships at 80x24 is two
shelves and the two bars.

There are no shelf **caps** any more, and the machinery that computed them is
gone with them: a horizontal shelf holds everything it is given and scrolls, so
there is nothing hidden for a "6 of 18" to be honest about and nothing for `z`
to expand. The two failed attempts at height distribution (a fixed table that
under-filled by ten rows; a proportional split that kept its remainder and
under-filled by twenty-three) are recorded here only so neither is tried again.

## 5. Spacing

The scale is `0 1 2 3 4 6 8` cells. No other value appears. Vertical gaps come
in ones: a shelf is `blank, label, cards, blank`. Horizontal padding inside a
plate is `1`-`2`, which is what makes a background change read as a surface
rather than as a tint stopping flush against the art.

A shelf is 15 rows, not 14, and the extra row is not spacing: the horizontal
scrollbar is drawn *inside* the content area, so a shelf with more posters than
fit lost the bottom row of every card to it — the state caption, silently, on
exactly the shelves long enough to need scrolling. The row is kept on the short
shelves too, because a shelf that changes height depending on how full it is
makes every shelf below it move.

## 6. Typography

Four treatments, never more than two attributes on one span:

| Role | Treatment | Used for |
|---|---|---|
| Display | bold, uppercase, letterspaced | shelf labels, hero title |
| Title | bold | card titles, the selected card |
| Body | normal | metadata, counts |
| Label | dim | secondary facts, hints, timestamps |

Letterspacing is a single space between characters (`C O N T I N U E`) and is
reserved for shelf labels. It is what lets a label read as structure without a
rule under it — the thing the old `───────` fills were doing with 40 cells of
line art.

## 7. Colour

Roles, mapped onto the theme slots that already exist so every bundled and
built-in theme keeps working:

| Slot | Means | Appears on |
|---|---|---|
| `$background` | the base | screen |
| `$surface` | a plate content sits on | shelves, hero |
| `$panel` | lifted off the plate | the selected card, cover placeholders |
| `$accent` | **the keyboard is here** — nothing else | the selected card's rule and caption |
| `$primary` | identity and progress | logo, progress fill |
| `$success` | watched, complete | ✓ |
| `$warning` | airing soon, degraded | countdowns under an hour |
| `$error` | failed | unavailable shelves |
| `$text` / `$text-muted` | body / label | — |

`$accent` meaning exactly one thing is the rule that makes focus unambiguous.
It is why shelf labels are foreground rather than accent: a heading is
structure, not interaction.

## 8. State and focus

Exactly one element holds focus, and exactly one card on it is selected.

The selected card says so **twice**: an accent rule drawn under its art, and its
title at full strength while its neighbours sit dim. The rule is a shape, so the
distinction survives a terminal with no colour. Only the *focused* shelf's
selected card also lifts to `$panel` — every shelf keeps its place so you do not
lose it moving between them, but `$accent` and the lift both mean "the keyboard
is here", and four shelves claiming that at once means none of them do.

A card's caption is the one line of state the poster cannot carry:

| Caption | Means |
|---|---|
| `ep 8 . 18%` | part-way through episode 8 |
| `resume ep 8` | stopped in episode 8, duration unknown |
| `3 eps ready` | aired and unwatched — the state the screen exists to surface |
| `ep 9 in 1d 4h` | caught up, counting down |
| `12 eps` / `2024` | not tracked; how much of it exists |

Deliberately *not* the row status re-used at a smaller width. That string is
built around a seven-cell progress bar and a column grid, neither of which
exists under a card, and cutting it to fourteen cells produced `____ 4` — not a
shorter version of the information, just damage.

A card that has nothing to say shows a blank line rather than `?` or `TBA`. The
row is reserved either way; inventing a fact nobody has is worse than a gap.

## 9. Motion

Borrowed wholesale from the standard: nothing over 500ms, panel transitions
150-300ms, feedback within 100ms. In practice almost nothing here animates.
Shelf scrolling is explicitly **not** eased — a shelf that glides to the next
card makes a held-down arrow key feel like wading — and a terminal redrawing a
grid is instant anyway, so pretending otherwise reads as lag.

Covers are the one thing that arrives late, and the rule there is that nothing
moves when one lands:

- every card draws a flat plate in the panel tone at the exact size its poster
  will be, so the shelf is laid out before a single image exists. Not a spinner:
  twelve spinners animating is a slot machine, and a blank card reads as broken;
- a cover already on disk is read **synchronously**, while the shelf is being
  built, so on every launch after the first the whole wall paints complete in
  the first frame;
- only genuine misses go to the network, six at a time, and each is fetched once
  per show however many shelves show it. A fetch that *fails* is recorded too,
  or a show whose poster 404s sends a fresh request forever, quietly, since
  nothing appears either way.

There is no cursor debounce any more and no need for one: every cover a shelf
wants is asked for when the shelf is built, so by the time the cursor can move
there is nothing left to ask for.

## 10. Components

| Component | File | Does |
|---|---|---|
| `TopBar` | `tui/shell.py` | identity, current shelf, search hint |
| `ActionBar` | `tui/shell.py` | keys for what is selected *now* |
| `PosterCard` | `tui/cards.py` | one show: art, title, one line of state |
| `Shelf` | `tui/cards.py` | a horizontal row of cards with its own cursor |
| Hero | `tui/preview.py` | the block beside the big poster |
| `render_cover` | `tui/coverart.py` | image bytes to sextant blocks |
| `cover_rows` | `tui/coverart.py` | how tall a poster *will* be, for reserving |
| `AnimeItem` | `tui/widgets.py` | one text row — My List only, now |
| `EmptyState` | `tui/shell.py` | what to do when a shelf has nothing |

`Shelf` carries its own cursor because Textual has no horizontal list widget.
That is the only real machinery in `cards.py`; everything else is a render. It
binds up and down itself as well as left and right — `HorizontalScroll` inherits
bindings for them from `ScrollableContainer` and would swallow both to scroll
vertically, in a container exactly one card tall, so the key did nothing at all
and never reached the screen.

## 11. What was rejected, and why

Two of the entries in this section were **wrong**, and the record of them is
more useful than quietly deleting them would be. What they have in common is
that each counted the rows a thing costs without weighing what it buys — and a
row-count argument always favours text, because text is what fits in one row.

> ~~**A top hero band.** Costs twelve rows at the size most people run.~~
>
> It costs 15-18, which is worse than the estimate, and it is the right call
> anyway. The hero is the only part of the screen meant to be *looked* at rather
> than read, and a 24-cell poster is the difference between recognising a show
> and reading its name. The rows come out of how many shelves fit, which is a
> trade, not a loss.

> ~~**Horizontal poster carousels.** A poster is ~15 rows tall at a readable
> width; one strip would be a third of the screen for five items, and
> left/right scrolling in a terminal fights the list navigation people already
> know.~~
>
> The "five items" was the error: at 14 cells a strip holds eleven across at 190
> columns and seven at 120, which is more shows visible than a capped vertical
> list of eight ever showed — and each one recognisable at a glance instead of
> read. The navigation objection was simply wrong: left/right along a shelf and
> up/down between shelves is the convention every streaming client on every
> screen already uses.

Still rejected, and these have held up:

- **Borders around cards.** A terminal card grid spends two rows and two columns
  per item on line art. At poster width that is a seventh of the card's cells
  spent outlining it, and the plate underneath already groups them.
- **Per-section colour.** Four accent hues makes the loudest thing on screen the
  part carrying the least information, and breaks the one rule that keeps focus
  unambiguous.
- **A nav rail.** It shipped, and it went with the stacked lists it mapped.
  Twenty columns of permanent labels is orientation worth having beside three
  tall lists of text; beside horizontal shelves it is twenty columns taken off
  every shelf to repeat what the shelf headings already say, and a shelf is
  measured in posters. The digits `1`-`4` still jump.
- **Shelf caps and `z`.** Same reason: a shelf that holds everything it was
  given has nothing to expand.

## 12. The other screens

The home screen is the argument; these are the same rules applied.

**Search** is a *mode*, not a screen. The box is hidden until `/` — an always-on
field spent rows showing a placeholder that named the key which opens it, on a
screen whose scarcest resource is rows, and the top bar says `/ search` for
free. Entering it puts the whole shell into search mode:

- every browse shelf and its heading is swapped for the results shelf, which is
  a shelf of posters like any other;
- the top bar says `Search` rather than naming a shelf you are no longer on;
- the action bar swaps `l my list` for `esc back`, because the one row
  guaranteed to be visible should offer the way out of the mode it is in.

No matches hides the heading *and* its plate — "Results" over an empty plate
over a notice saying there are none is the same fact three times — and the hero
switches to an `EmptyState`, because it had been confidently detailing whichever
show the cursor sat on before the search, which is by definition not among the
results.

**Detail** wears the same two bars. It was the last screen still carrying
Textual's `Header` and `Footer` — a centred title with a clock above a row of
nine global keys in fixed order — and so was also the only screen that still
looked like the app before this redesign. Its metadata panel lost its `round`
border: two rows and eighty cells of line art to say "these belong together",
which the plate underneath already says, in the one idiom this redesign set out
to remove. The title now appears once, in the bar, rather than there *and* as
the panel's first line eight rows below. The call to action is the same filled
accent bar the hero uses, and drops its "— press Enter" because the action bar
at the foot now says it.

**Modals** are the one place a border is right: a thing floating over another
thing needs an edge. They are framed in `$panel`, never `$accent` — accent means
"the keyboard is here", and the help sheet takes no input at all. Help is
bounded at `max-height: 100%` and scrolls, because a modal that runs off the
bottom of the screen carries its own instructions for closing it over the edge.
It is keys only; three closing paragraphs of prose made it forty-four rows tall
in a forty-row terminal.

## 13. Visual QA

`scripts/tui_shots.py` renders any screen at any size headlessly and writes both
an SVG and **a plain-text dump of the same compositor output**. The text is the
instrument that matters: a screenshot shows whether something looks wrong, the
text shows exactly which column every cell starts in, which is what "clipped by
one cell" and "the count wrapped onto its own line" look like.

```bash
uv run python scripts/tui_shots.py OUT --screen home
uv run python scripts/tui_shots.py OUT --screen search --query zzz   # empty state
```

Every defect listed in §1 and §4 was found this way, and three were found in the
harness itself. Its search fixture fell back to eight rows on a miss, so the one
screen with an explicit empty state was the one screen that could never be
photographed. Its only synopsis was four lines long, so it could not show
whether the hero used the room a short result list frees. And none of its shows
had a cover URL, so every shot was a wall of placeholder plates — a picture of
the loading state rather than of the screen, on a design whose whole argument is
what a shelf of *posters* looks like. It generates synthetic covers with Pillow
now, patched in at the disk-cache read so the app takes the same path it does on
a warm launch; the shapes are fake and the layout is real.

A QA harness whose fixtures do not exercise the layout is how the dead space got
in, twice.
