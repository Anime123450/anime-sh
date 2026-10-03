# anime-sh — interface design

The visual language of the TUI, and the reasoning behind it. Written to be
falsifiable: most of the rules here are asserted by `tests/tui/test_home_design.py`
and `tests/tui/test_themes.py`, so a change that breaks one fails the suite
rather than quietly degrading the screen.

Reference: [monospace-design-tui](https://github.com/coreyt/monospace-design-tui),
whose breakpoints, spacing scale and four-treatment typography this follows.

---

## 1. What was wrong

Measured on a 120×40 render of a real-sized library, the previous home screen:

- showed **four lists at once** (18 + 2 + 19 + 10 rows) with equal visual weight,
  so nothing led and Trending never appeared above the fold;
- repeated the words **"new episode" on 13 consecutive rows** — a third of the
  screen spent saying the same thing in the same dim grey;
- **truncated 10 of 18 titles** at ~34 cells while the context rail beside them
  broke a synopsis mid-word (`Studi…`, then `his` alone on a line);
- drew **three rule-filled headers** (`Continue Watching ─────── 18`), which is
  decoration carrying one integer;
- had **no focal point**: the only image on screen was 22 cells wide in a corner.

The diagnosis is not "the colours are wrong". It is that everything was
permanently visible at the same volume.

## 2. Principles

1. **One thing leads.** The screen answers "what am I in the middle of?" before
   it answers anything else.
2. **Hierarchy before decoration.** Depth comes from three background tiers and
   from weight, never from borders. A border costs two rows to say what a shade
   already says.
3. **Progressive disclosure.** A shelf shows a sample and says how much more
   there is. The full list is one keypress away, not permanently on screen.
4. **Every glyph earns its cells.** If a dot already means "ready to watch", the
   words "new episode" are thirteen lines of noise.
5. **Colour never carries meaning alone.** Every state has a shape as well as a
   hue, so the screen survives a monochrome terminal.

## 3. Composition

```
┌──────────────────────────────────────────────────────────────┐
│  ▞ anime-sh    Home                          /  search       │  TopBar
├─────┬──────────────────────────────────┬─────────────────────┤
│ nav │  CONTINUE WATCHING          6 ▸  │   ┌────────────┐    │
│     │   ▸ Skeleton Knight …  Ep 8  18% │   │            │    │
│  ⌂  │   ● Solo Leveling …    Ep 8/13   │   │   poster   │    │  Hero
│  ✦  │                                  │   │            │    │
│  ◷  │  THIS SEASON               19 ▸  │   └────────────┘    │
│  ♥  │   ○ Black Clover S2    in 1h 59m │   SKELETON KNIGHT   │
│     │                                  │   TV · 12 eps · ★72 │
│     │  TRENDING                  10 ▸  │   Action · Comedy   │
│     │   ○ Apothecary Diaries  in 6d    │   ━━━╸────── 18%    │
│     │                                  │   ▶ Resume ep 8     │
├─────┴──────────────────────────────────┴─────────────────────┤
│  ↵ open   ␣ play   f favourite   / search   ? help           │  ActionBar
└──────────────────────────────────────────────────────────────┘
```

Three regions, fixed in place, so spatial memory does the navigating:

- **Nav rail** — the sections, always in the same order. Selecting one expands
  it to the full height and hides the others. This is what turns four competing
  lists into one.
- **Content** — the active shelf or shelves.
- **Hero** — whatever the cursor is on, as a poster-led panel rather than a
  cramped info strip. It is the only part of the screen that is meant to be
  *looked at* rather than read.

The hero is a column, not a top band, deliberately. At 120×40 the scarce
resource is rows, not columns: a cinematic band across the top would cost twelve
rows of content to show one show. A column spends width, which is what the
terminal has going spare.

## 4. Breakpoints

Width, following the monospace standard:

| Cells | Name | Nav | Hero | Content |
|---|---|---|---|---|
| < 80 | compact | hidden (keys only) | hidden | one column, status dropped |
| 80–119 | standard | digit + icon, 5 cells | hidden | one column |
| 120–159 | expanded | digit + icon, 5 cells | ~40 cells | remainder |
| ≥ 160 | wide | icon + label + count, 20 | 46–72 cells | remainder |

Labels only in the `wide` band, and that was measured rather than guessed. A
labelled rail was tried at 120: the rail took 16 cells, the hero 40, and the
title column fell from 34 cells to 18 — every row ellipsized to make room for
four words that never change. Orientation is worth five cells there, not
sixteen. Twenty at `wide` rather than eighteen because eighteen leaves ten cells
for the label and wrote "This Season" as `This Seas…`; a rail that cannot spell
its own destinations is worse than one with no labels.

Height is **not** a table of fixed row counts. Those were tried twice and both
versions left the column short:

- a fixed table under-filled by ten rows at 120×40 — four shelves of six on a
  screen with room for seven;
- a strictly proportional split that kept its remainder under-filled by
  twenty-three rows at 200×60, because a shelf clamped to its own length hands
  nothing back.

What ships instead: every shelf is seeded with `_SHELF_MIN` rows, and the rest
of the column is handed out in **rounds weighted by `Section.weight`** until
nothing can take any more. Continue Watching carries `weight=3`, so it takes
three fifths of every round and stays visibly the largest thing — hierarchy has
to be legible in *size*, not only in order, or four shelves read as four equal
blocks and the screen is a dashboard again. Weighted is load-bearing: an even
redistribution was tried and flattened it, with This Season drawing level at
13 rows each. Both failures are pinned by `tests/tui/test_shelf_caps.py`.

Measured result, same library: 5/2/4/3 rows at 80×24, 14/2/7/7 at 120×40,
18/2/19/10 at 200×60 — every size filling to within a row of the window.

Extra width past 160 becomes margin, never more columns of data. An interface
that fills a 240-cell terminal with data is not using the space, it is
spilling into it. The row grid obeys this already by cutting its title column
to the 85th percentile of the titles it actually holds, so a wide terminal gets
a longer measure, not a stretched one.

## 5. Spacing

The scale is `0 1 2 3 4 6 8` cells. No other value appears. Vertical gaps come
in ones: a shelf is `label, rows, blank`. Horizontal padding inside a plate is
`2`, which is what makes a background change read as a surface rather than as a
highlight.

## 6. Typography

Four treatments, never more than two attributes on one span:

| Role | Treatment | Used for |
|---|---|---|
| Display | bold, uppercase, letterspaced | shelf labels, hero title |
| Title | bold | row titles, the focused row |
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
| `$panel` | lifted off the plate | highlighted row in an unfocused list |
| `$accent` | **the keyboard is here** — nothing else | focused row marker only |
| `$primary` | identity and progress | logo, progress fill, resume glyph |
| `$success` | watched, complete | ✓ |
| `$warning` | airing soon, degraded | countdowns under an hour |
| `$error` | failed | unavailable sections |
| `$text` / `$text-muted` | body / label | — |

`$accent` meaning exactly one thing is the rule that makes focus unambiguous.
It is why shelf labels are foreground rather than accent: a heading is
structure, not interaction.

## 8. State and focus

Exactly one element holds focus. Four row states, each with a shape as well as a
colour, so none depends on hue:

| State | Shape | Colour | Meaning |
|---|---|---|---|
| resume | `▸` | primary | part-way through this episode |
| ready | `●` | success | an aired episode waiting |
| waiting | `○` | dim | caught up, counting down |
| watched | `✓` | dim | nothing to do |

The focused row additionally takes a thick left border in `$accent` and trades
one cell of its padding for it, so its width never changes as focus moves.

## 9. Motion

Borrowed wholesale from the standard: nothing over 500ms, panel transitions
150–300ms, feedback within 100ms. In practice this app animates three things —
the hero cross-fading as the cursor moves, the shelf expanding when a nav item
is chosen, and loading spinners. Everything else is instant, because a terminal
redrawing a grid is instant and pretending otherwise feels broken.

Poster fetches are debounced by 350ms, so walking a list never touches the
network; only the row you stop on is fetched.

## 10. Components

| Component | File | Does |
|---|---|---|
| `TopBar` | `tui/shell.py` | identity, current section, search hint |
| `NavRail` | `tui/shell.py` | the sections; the one place navigation lives |
| `ActionBar` | `tui/shell.py` | keys for what is focused *now* |
| `Hero` | `tui/hero.py` | poster, title, metadata, progress, primary action |
| `Shelf` | `screens/home.py` | label + capped list + "more" affordance |
| `MediaRow` | `tui/rows.py` | one row's grid geometry |
| `EmptyState` | `tui/shell.py` | what to do when a section has nothing |

## 11. What is deliberately not here

- **Borders around cards.** Tried; a terminal card grid spends two rows and two
  columns per item on line art, and at poster sizes only four fit on screen.
- **A top hero band.** Costs twelve rows at the size most people run.
- **Horizontal poster carousels.** A poster is ~15 rows tall at a readable
  width; one strip would be a third of the screen for five items, and
  left/right scrolling in a terminal fights the list navigation people already
  know.
- **Per-section colour.** Four accent hues was the previous design's mistake in
  a different form: it makes the loudest thing on screen the part carrying the
  least information.

## 12. The other screens

The home screen is the argument; these are the same rules applied.

**Search** is a *mode*, not a screen. The box is hidden until `/` — an always-on
field spent four rows showing a placeholder that named the key which opens it,
on a screen whose scarcest resource is rows, and the top bar says `/ search`
for free. Entering it puts the whole shell into search mode:

- the nav rail **empties but keeps its column**, because all four shelves are
  hidden and a rail mapping four destinations that do not exist is decoration
  with a broken keybinding behind it — but hiding it outright shifted the whole
  content column five cells left, twenty at 160, on the first character typed,
  so the result list landed somewhere the shelves had never been. Spatial
  consistency is the navigation in an interface this dense: panels may go quiet,
  but they do not move. An empty gutter reads as margin; a list that jumps
  sideways as you type reads as a different screen;
- the schedule goes, because "what is on tonight" is a home-screen answer and
  sixteen rows is a lot to spend on a question nobody asked;
- the action bar swaps `tab next shelf` — a key that does nothing when there is
  one shelf — for `esc back`;
- the hero takes the room the short list frees, up to 24 synopsis lines. Four
  lines regardless was fine beside a full home screen and absurd beside a
  two-result search, where it left thirty blank rows under a paragraph cut off
  mid-sentence. The space a short result list frees belongs to the one result
  you are looking at; that is the whole argument for having a hero.

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

Every defect listed in §1 and §4 was found this way, and two were found in the
harness itself: its search fixture fell back to eight rows on a miss, so the one
screen with an explicit empty state was the one screen that could never be
photographed; and its only synopsis was four lines long, so it could not show
whether the hero used the room a short result list frees. A QA harness whose
fixtures do not exercise the layout is how the dead space got in.
