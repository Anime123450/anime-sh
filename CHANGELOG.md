# Changelog

All notable changes to anime-sh. Format loosely follows Keep a Changelog.

## [0.2.69] - 2026-08-31

### Fixed

- **`anime mark` could silently destroy your AniList progress.** It *sets*
  progress rather than advancing it, so marking below where you already were
  overwrote the higher number with no warning and no undo. Marking a special
  numbered 5.5 on a finished 28-episode show reported episode 5 to AniList and
  dropped the entry from "completed" back to "watching".

  Three separate faults, all found by running the command with odd numbers and
  then checking what had actually changed on the account:

  - `--single` now never touches AniList. It means "this one episode"; AniList
    only understands "N episodes finished", and sending one as the other is a
    category error that a float episode number makes obvious.
  - A catch-up that would *lower* your AniList progress is refused, naming both
    numbers, unless you pass `--force`. Checked before anything is written, so
    refusing leaves nothing half-done.
  - The confirmation reports the episodes actually written rather than the
    number you asked for. The two were allowed to differ, and the message
    believed the request - which is how it came to print "episodes 1-0 watched".

- **`anime mark -e 0` marked nothing and said it had.** `1..0` is an empty
  range, so no rows were written, yet the command reported success and pushed
  the 0 to AniList. Zero or less is now refused for a catch-up. A `--single`
  mark of episode 0 is still allowed, because some shows really do number a
  prologue that way.

- **`anime mark -e 99999` ran for six minutes and left 99,971 junk rows behind.**
  Progress is one committed write per episode, and the episode count was never
  checked against the show. A catch-up past the end of a show is now refused and
  names the real length; when the length is unknown, as on an ongoing series,
  the ceiling is 2000 - well past One Piece, and short of a typo.

- **`anime download -e 1-999999999` allocated gigabytes before touching the
  network.** The range is expanded into one list entry per episode up front, so
  an unbounded one is an out-of-memory bug rather than a slow download. Ranges
  are now capped at 2000 episodes, counted across the whole selector rather than
  per token, and the refusal says how many episodes were actually asked for.

- **`anime download -e -5` went looking for episode minus five.** `-5` parsed as
  the number, and `1--5` as a range starting at -5. Negative episodes are
  refused; episode 0 still works, because some shows number a prologue that way.

- A refused episode selector now says why. The specific reason was being
  swallowed for one generic line about the format, which made a rejected range
  look like a syntax error.

- **`--limit 0` printed thirty rows and `history --limit -1` printed
  everything.** Non-positive counts were passed straight through: AniList
  ignores one and returns a full page, and SQLite reads `LIMIT -1` as *no*
  limit, so the flag quietly did the opposite of what it said in both
  directions. Every `--limit` now has a floor of 1, and `--days` is bounded to a
  year — 9999 used to reach AniList and come back a 400.

## [0.2.68] - 2026-08-31

### Fixed

- **The checksum published beside the Windows executable named a file that was
  not in the release**, so nothing ever verified against it. The binary was
  hashed as `anime.exe` and only renamed to `anime-sh-<version>-windows-x64.exe`
  at upload time, and `sha256sum` writes the name it hashed into the file.

  Scoop's excavator reads that file to learn the hash of a new version and looks
  inside it for the published filename. It never matched, so every automatic
  update fell back to downloading the whole 21 MB executable and hashing it
  itself - which works, but means the manifest recorded a hash of whatever it
  had just downloaded, cross-checked against nothing. Verified against a real
  `scoop checkver -u` run, before and after.

  The binary now gets its release name before it is hashed, and the checksum
  ships as `anime-sh-<version>-windows-x64.exe.sha256`.

- **`scoop install anime-sh` could not work on a machine that had just installed
  Scoop.** The manifest declared `mpv` as a dependency, and `mpv` is not in
  Scoop's `main` bucket - it is in `extras`, which a fresh Scoop does not have.
  The install aborted with `Couldn't find manifest for 'mpv'` and no indication
  of which bucket was missing, on precisely the clean machine the README's
  instructions are written for.

  The dependency is now `extras/mpv`, so Scoop names the bucket it needs, and
  the README adds `extras` before it can come up. Verified by installing from
  the manifest rather than by reading it.

## [0.2.67] — 2026-08-30

### Fixed

- **Tab now moves between sections, which is what it was documented to do.**
  Both the README and the `?` sheet said "next section"; Textual's default focus
  chain walks every focusable widget instead. On this screen that meant Tab also
  landed on the body and the Coming Up rail — scroll containers that take focus,
  draw no cursor, and turn the arrow keys into panel scrolling. Three presses
  behaved as described and the fourth left you somewhere with nothing selected
  and no way to tell why.

  It cycles the lists that are on screen and have rows in them, wraps round at
  the end, and `Shift+Tab` goes back the other way. Empty and hidden sections are
  skipped — landing on Favorites before you have starred anything, or on a browse
  list hidden behind search results, is landing nowhere.

- The installation instructions on a machine without Scoop were incomplete: they
  opened with `scoop bucket add`, which needs Scoop. Both of Scoop's own
  quickstart lines are included now, verbatim — including the execution-policy
  one, without which Windows refuses the install with a message about running
  scripts being disabled.

## [0.2.66] — 2026-08-30

### Added

- **Every release now carries a standalone Windows executable.** One file,
  about 30 MB, with Python and every dependency inside it —
  `anime-sh-<version>-windows-x64.exe` on the release page, with its checksum
  beside it. The build existed before but had to be run by hand and was never
  published; it is built, *verified* and attached automatically now.

  The verification matters: a bundle missing its package metadata builds
  perfectly, runs, and reports version `0.0.0+unknown` with no providers — an
  app that cannot play anything, released green. The release now runs the
  executable and refuses to publish one that cannot see its own plugins.

- **Package manifests for WinGet and Scoop**, in `packaging/`, plus
  `scripts/make_manifests.py` to fill in the version, URL and checksum from the
  built artifact. Those are the two fields that must never be typed by hand: get
  either wrong and every install fails on a checksum mismatch.

### Changed

- **`anime doctor` now says how to fix what it found.** "mpv: not found on PATH"
  is a diagnosis, not help — especially for someone who downloaded a single
  executable precisely so they would not have to think about this. It now names
  a package manager you actually have (`winget` / `scoop` / `choco` / `brew`),
  and the package name that manager really publishes.
- The packaging notes said the bundle was 145 MB and that one build flag was
  load-bearing. Both were out of date; re-measured and re-checked, and corrected.

## [0.2.65] — 2026-08-30

### Fixed

- **anime-sh still opened the theme picker by itself on launch.** The previous
  release fixed half of it.

  `textual-image` asks the terminal two separate questions, and only one of them
  is asked when the cover-art module is first loaded. The other — the cell-size
  query, whose reply ends in the literal character `t` — is deferred until the
  *bitmap* rendering path is used. Since posters started appearing on the home
  screen, that happens a moment after launch, with the app already reading the
  keyboard: the terminal's reply arrives as key presses, and the last one opens
  the theme picker.

  Clearing the keyboard buffer beforehand could never have helped, because the
  question had not been asked yet. Both questions are now asked before the app
  starts. And if either somehow is not, the bitmap path declines rather than
  asking mid-run — a slightly softer poster is a far better outcome than the app
  typing at itself.

## [0.2.64] — 2026-08-30

### Fixed

- **anime-sh opened the theme picker by itself on every launch.** Nothing was
  wrong with the picker — it was simply the first thing ever bound to `t`, and
  a `t` was arriving on its own.

  The cover-art code asks the terminal for its cell size with `CSI 16 t` and
  waits a tenth of a second for a reply that ends in the literal character `t`.
  Windows Terminal answers more slowly than that, by which point the probe has
  already swallowed the `ESC [` that marked the reply as a reply — so the rest
  of it (`6;20;10t`) lands after the app has started, with nothing left to
  identify it, and is read as key presses.

  Anything left over from the probe is now cleared before the app starts. It
  stops as soon as it has swallowed the reply's terminator, so a terminal that
  answers costs a few milliseconds and only one that never answers waits.

### Added

- **`anime themes`** — lists the themes and marks the one in use; `--set` to
  change it, `--json` for scripts. The picker inside the app is still the good
  way to choose, because it previews live; this is for naming what is available
  without launching it, or setting it from a dotfile.

### Changed

- **The preview panel places a show before it summarises one.** Genres and the
  studio sit under the title now: they answer "is this for me" faster than a
  paragraph of plot, and the panel had the room.
- An unknown theme name given to `anime themes --set` is refused with a sentence
  and the list of real ones, instead of a raw validation error with a link to
  pydantic's website attached.

## [0.2.63] — 2026-08-30

### Added

- **Themes, and a picker to change them.** Press `t`. Moving the cursor applies
  the theme to the whole app straight away, so you are choosing by looking at
  anime-sh rather than at a list of names; Enter keeps the one you are on and
  saves it, and Escape puts back the one you arrived with, so browsing costs
  nothing.

- **Three new themes**, and a light one at last:

  - **midnight** (the new default) — deep blue-black, teal, with a warm amber
    accent that is the only warm thing in the palette, so the focused row is
    findable at a glance rather than by reading.
  - **ember** — warm and dark, amber with a cool accent for the same reason in
    reverse.
  - **paper** — light. Terminals get used in daylight, and every option here was
    previously dark, which made "change the theme" a choice between four
    variations on the same brightness.

  Textual's own themes are still offered alongside them.

### Changed

- The default theme is now **midnight** rather than tokyo-night.

### Fixed

- **`config set ui.theme <typo>` used to report success and change nothing.**
  The value was looked up in a table and, on a miss, the default was quietly
  left in place — which is indistinguishable from the setting not working. A
  theme name is validated now, and an unknown one is rejected with the list of
  real ones. This also means a theme this app defines itself can finally be set
  from the config file; the old table only knew Textual's.

## [0.2.62] — 2026-08-30

### Added

- **The right-hand panel now shows the show your cursor is on — with its
  poster.** It was a second list of upcoming episodes and nothing else, which
  left the home screen as three lists beside a fourth, with no focal point and
  not one image on it — in a client for a visual medium.

  It leads with the highlighted show: cover art, what the show is, how far
  through the episode you are, when the next one airs, a few lines of synopsis,
  and what pressing Enter will actually do. The schedule keeps the space
  underneath.

  The art renders as unicode block sextants, so it needs nothing but a
  truecolor terminal; where a real graphics protocol is available (Sixel, kitty,
  iTerm) it uses that instead and comes out sharper.

  Posters are fetched only for the row you *stop* on — holding an arrow key
  walks the cursor through a dozen rows a second, and a request per row would be
  the launch-time rate-limit storm again with a new trigger. Each is fetched at
  most once, including the ones that fail.

### Changed

- **Section headings are anchored.** A heading now runs a rule out to the edge
  of the rows beneath it and puts the count at the far end, so it reads as the
  lid of its section and the count is in the same place every time instead of
  floating wherever the name happens to stop.

## [0.2.61] — 2026-08-30

### Changed

- **The home screen has been redesigned.** It had become a wall of text: four
  sections set in the same weight on one flat background, with the loudest thing
  on screen — bright orange headings — carrying the least information.

  - **The rows sit on a plate now.** Depth comes from three tiers of background
    rather than from boxes, which would have cost two terminal rows per section
    to say what a shade of colour says for free. The stylesheet had been setting
    the screen itself to the *middle* tier, so there was nowhere left to go up
    and everything came out the same colour.
  - **Every section shares one column grid.** Each list used to size itself to
    its own titles, which put Continue Watching's episode column at 76 and
    Airing This Season's at 70, with Trending somewhere else again — three grids
    stacked down one screen, so there was no vertical line for the eye to
    follow.
  - **Titles lead and metadata recedes.** The episode number is set one step
    down from the title it belongs to, and "new episode" — the same three words
    repeated down eight consecutive rows — no longer competes with the names
    beside it.
  - **Headings are structure, not decoration.** They are set in the foreground
    colour; the accent is reserved for one thing only, which is where the
    keyboard is.
  - **The rail has a shape.** A single hairline was separating it from the rows,
    which read as a leftover strip rather than a panel. Its days are set apart
    from its episodes, and it grows with the terminal instead of stopping at 42
    columns.

- The resume bar's fill ends in a half-width tip. Whole cells gave a seven-cell
  bar seven positions, which cannot tell 18% from 25% — both rounded to one
  cell, so the one row on screen whose job is to say *how far in* drew the same
  bar for two different places in an episode.

### Fixed

- **Rows could be drawn wider than the widget holding them, losing their last
  column silently.** The width available to a row was computed from a constant
  that tried to mirror paddings living in the stylesheet, and the real figure is
  not even fixed — a list long enough to scroll gives up two columns to its
  scrollbar and a short one does not. When it was wrong the row overflowed its
  label, Textual wrapped the overflow onto a second line, and a one-line row
  height hid it: "new episode" rendered as "new". Rows are measured from the
  widget now, and cut to the narrowest list on the screen.

## [0.2.60] — 2026-08-30

### Changed

- **Continue Watching keeps only the episodes you can actually play.** The rows
  for shows you are caught up on — dimmed, unplayable, carrying nothing but a
  countdown to the next episode — were saying exactly what the Coming Up rail
  says, grouped by day and easier to read. Six of twenty rows were on screen
  twice at once. Dimming them was already an admission they are not actionable;
  now that something else says it better, they leave the list.

  They stay when the rail is not there to take them: below 120 columns there is
  no rail at all, and a show airing beyond the rail's week-long horizon never
  reaches it. In both cases the dimmed row is the only countdown on screen.

- **A wide terminal is spent instead of left empty.** On a 200-column window the
  rows stopped at 96 cells and the rail sat at a fixed 42, leaving 54 empty
  columns between two regions that were *both* ellipsizing titles — the list cut
  four of them, and the rail cut every single one at 27 characters.

  The rail grows with the terminal now, and takes the width the rows turn out
  not to need, so the leftover ends up at the edge as a margin instead of in the
  middle as a gutter. The rows' own measure cap no longer trims titles a wide
  terminal has room to print. The title column follows the bulk of a list's
  titles rather than its longest, so one very long name cannot push the episode
  column far to the right of where the short ones end.

  Measured at 200 columns: the gap between the two regions falls from 54 columns
  to 20, the rail's room for a title goes from 27 characters to 53, and one row
  is ellipsized where four were.

- My List sizes its rows to their contents too, like every list on the home
  screen.

### Fixed

- Rows were being measured against the width of the *terminal* while living in a
  column the rail had already shortened. On a 120-column terminal that laid out
  96-cell rows inside a 78-cell body — wider than the thing holding them.

## [0.2.59] — 2026-08-29

### Added

- **Episodes you already have are marked in the grid.** A small `⤓` beside the
  number, and "· on disk" on the detail line beneath — so you can see at a
  glance what is watchable with no network. It is asked of the playback service
  rather than the filesystem, so the mark and the thing that actually plays
  cannot disagree; a screen claiming an episode is available offline while
  playback goes to the network would be worse than not marking it at all.

  Being on disk is independent of having watched it, so it gets a slot of its
  own rather than sharing the state glyph — and the slot is a space when empty,
  so every cell stays the same width and the grid keeps its columns.

### Changed

- **The synopsis no longer runs to the full width of the terminal.** At 1fr it
  reached 112 columns on a 150-column window, well past the 45–75 characters a
  line of prose is comfortably read at. It is capped at 80 now — the rows beside
  it have always capped themselves at a measure for the same reason, and this
  was the one place that did not.

## [0.2.58] — 2026-08-29

### Changed

- **Episodes are a grid.** The detail screen listed one episode per line — 28
  stacked rows for Frieren on a 150-column terminal, and 1175 for ONE PIECE,
  which is not a list anyone can use. A list of numbers is the one thing that
  genuinely wants to tile, and tiling it turns 28 rows into three.

  Each cell is a state glyph and the number, right-aligned to a common width so
  9 and 1175 sit in the same grid without ragging the columns. The sentence that
  used to live in the row — the air countdown, "not on this source", the
  progress bar — moved to a line beneath the grid describing whichever episode
  the cursor is on. Nothing was dropped to make room.

- **The cursor moves like a grid.** Up and Down (and `j`/`k`) step a whole row;
  Left and Right step a single episode. It clamps at the edges rather than
  wrapping, which in a grid would throw the cursor corner to corner instead of
  moving it a step.

- **The facts line on the detail screen is spaced like the genres line below
  it** — `TV · Finished · 28 eps` rather than `TV  ·  Finished  ·  28 eps`. They
  read as one block and were set two different ways.

## [0.2.57] — 2026-08-29

### Fixed

- **The `?` cheat-sheet documents the keys the app actually has.** `j`, `k`, `g`
  and `G` were added in 0.2.55 and appeared nowhere — not in the footer (kept out
  deliberately, four motion entries would crowd out `q`, `/`, `l` and `?`) and not
  in the help either, so the only way to find them was to read the source. The
  sheet is now grouped into *Move* and *Act*, and a test refuses to let a bound
  key go undocumented again — or the reverse, a documented key that isn't bound.

### Changed

- **Italic is no longer used anywhere in the interface.** It is not one of the
  four text treatments the rest of the UI keeps to, and a fair number of
  terminals render it as reverse video or drop it silently. The two places that
  used it — an alternate title on the detail screen, and the search term in the
  no-matches notice — are dim instead.

## [0.2.56] — 2026-08-29

### Added

- **A "Coming Up" rail on wide terminals.** Rows cap themselves at a readable
  measure, so on a 190-column window the list stopped around column 96 and the
  right-hand half of the screen was empty. That space now shows the one thing the
  rows cannot: episodes of the shows you are watching that **have not aired yet**,
  grouped by the day they land, in local time, dimmed so they never compete with
  the list you actually act on.

  It appears at 120 columns and widens at 160 — below that the rows alone fill
  the window and a rail would be taking space from them, so a small terminal is
  exactly as it was.

  Built entirely from show data Continue Watching has already loaded: **it makes
  no requests of its own**, and it ticks with the same one-minute timer that
  updates the countdowns.

## [0.2.55] — 2026-08-29

### Fixed

- **The TUI opens with the keyboard on a list, not in the search box.** Arrow
  keys did nothing on launch and no row was selected. `on_mount` did try to focus
  a browse list, but at that point every list is still empty — focusing an empty
  one does not stick, and the rows arrive later from workers that clear and
  rebuild the list. The attempt sat in a bare `try/except`, so it failed
  silently and the comment above it described behaviour the app did not have.

  Focus is now taken once a section actually has rows, and settles on Continue
  Watching rather than whichever worker returned first — sections finished
  loading in a different order each launch, so the keyboard landed somewhere
  different every time. Once you move, or start typing a search, nothing touches
  focus again.

- **The selected row is visible again.** Textual marks it with `-highlight`, one
  dash; the stylesheet said `--highlight`, so the rule matched nothing and had
  never styled anything. A dead CSS selector raises no error and renders no
  complaint.

### Added

- **Vim motions in the TUI: `j` / `k` to move, `g` / `G` for top and bottom.**
  Bound on the screen rather than globally, so typing a title containing those
  letters into the search box still types them.

- **The focused list is now distinguishable from the others.** Each list keeps
  its own cursor so you do not lose your place moving between sections, but only
  the focused one gets the full-strength bar and an accent stripe down its left
  edge. The stripe is a shape, not just a colour, so the distinction survives a
  monochrome terminal.

## [0.2.54] — 2026-08-28

### Fixed

- **A sequel named by subtitle is no longer offered as a source for its
  prequel.** Season matching read the number out of a title, so
  "Attack on Titan Season 2" was correctly told apart from "Attack on Titan" —
  but "Attack on Titan: **Final Season**" was not, because it carries no number
  and read as season 1, exactly like the show it continues. The same held for
  "JoJo's Bizarre Adventure: Stone Ocean", "Demon Slayer: Entertainment District
  Arc" and "Made in Abyss: The Golden City of the Devouring Sun" — four of seven
  real cases checked.

  Since providers rank a sequel's near-identical title first, the wrong season
  could be picked automatically: you watched one season while your progress was
  written against another. This is the identity-spine failure described in
  §1.1 of the engineering standards, reached by a route the original fix did not
  cover.

  Titles that merely differ in *wording* — "Attack on Titan" against "Shingeki
  no Kyojin", or a "(Dub)" tag — are deliberately still accepted. Only a title
  whose words are a strict superset of the other's counts as a different entry,
  because rejecting the rest would throw away legitimate matches to fix a
  narrower problem.

## [0.2.53] — 2026-08-28

### Fixed

- **A search that matches nothing now says so.** Searching hides Continue
  Watching, This Season and Trending to make room for results, so a query
  AniList could not match left the whole screen blank under a "Results" heading
  — with no way to tell whether it was still loading, had broken, or simply had
  no answer. AniList's search is strict about word boundaries, so this was not a
  rare state. It now names what was searched for and suggests what to try.

- **`esc` closes a search.** It was bound app-wide to "go back", which on the
  home screen has nothing to pop and so did nothing at all — the only way out of
  a search was to select the box and delete it by hand.

## [0.2.52] — 2026-08-28

### Added

- **`anime cache info`** — how many entries are cached, how many have gone
  stale, what the file costs on disk, and how much of that is free space waiting
  to be reclaimed. "3 entries, 1.5 MB" looks broken until you can see that 1.4 MB
  of it is empty.

- **`anime cache prune`**, the new name for `cache purge`: drop the entries that
  have expired, and nothing you are still using. `cache purge` still works and
  points at the new name.

### Changed

- **`anime cache clear` says what it is about to throw away, and asks.** `clear`
  and `purge` are synonyms in English, and neither name said which one discarded
  data you were still using. Rather than swap their meanings — which would
  silently change what an existing `cache clear` does in someone's script — the
  safe operation got an unambiguous name and the destructive one now reports
  how many entries are still current before doing anything. `--yes` skips the
  prompt.

- **Clearing the cache gives the disk space back.** `DELETE` leaves the freed
  pages inside the SQLite file, and in WAL mode a `VACUUM` alone just moves them
  into `cache.db-wal`, so an emptied 1.5 MB cache stayed 1.5 MB — reporting
  "cleared" beside an unchanged file size. Pruning deliberately still does not
  do this: it also runs as housekeeping after ordinary cache writes, where a
  VACUUM would be absurd.

## [0.2.51] — 2026-08-27

### Fixed

- **`anime config set` no longer refuses a value that starts with `--`.**
  `anime config set player.args "--cache=yes"` failed with
  `No such option: --cache`, because the *value* was parsed as an option of
  anime-sh's own. `player.args` is the one setting whose values always begin
  with `--`, so the most likely use of the command was the broken one. The
  `anime config set -- player.args "..."` escape still works; it is simply no
  longer required.

### Changed

- **`anime downloads` now describes the disk, not just the database.** The
  download table is written when an episode is fetched and never revisited, so
  an episode you deleted to free space was still listed as `done` — exactly when
  you are trying to work out where your disk went. Each row now shows its file
  size, a deleted file reads `gone from disk` instead of `done`, and the listing
  ends with how many files are actually present and what they add up to.
  `--json` gains `on_disk` and `size_bytes`.

## [0.2.50] — 2026-08-27

### Fixed

- **Playback no longer stalls on a connection with bandwidth to spare.** Three
  causes, all on the path every anikoto episode takes:

  - **mpv was given no buffering settings at all**, so it ran on its default
    `--demuxer-readahead-secs=1`. One second of lookahead, for a stream arriving
    in multi-second segments, means any upstream hiccup is a visible stall.
    anime-sh now starts mpv with 20 s of readahead and a 120 s cache. Anything in
    `player.args` is still applied afterwards and still wins.

  - **The de-obfuscating proxy downloaded each segment whole before sending a
    single byte.** Every anikoto segment arrives PNG-disguised and has to pass
    through it, so this added a full segment's download to every segment's
    latency. Stripping the decoy header only needs the first ~96 KB, so the rest
    is now relayed as it arrives. A response the proxy cannot frame — no
    `Content-Length` — still falls back to the old buffered path rather than
    guessing a length.

  - **The proxy spoke HTTP/1.0**, which closes the socket after every response,
    so mpv paid a fresh TCP handshake for every segment of the episode. It is
    HTTP/1.1 with keep-alive now.

## [0.2.49] — 2026-08-26

### Fixed

- **`doctor` and `providers ls` no longer report a provider you switched off as
  though it were in use.** Both read the installed plugins and ignored
  `providers.disabled` in your config, so with `disabled = ["anizone"]` doctor
  printed `providers: anizone, anikoto` — naming a provider that nothing would
  ever call. That is the exact question `doctor` exists to answer for a bug
  report, and it answered it wrongly.

  Disabled plugins are still *listed*, because hiding them turns "why is anizone
  never used?" into a mystery, but they are now marked as disabled.

- **`doctor` no longer reports a healthy install when nothing can play.** With
  every provider disabled or none installed, it printed "anime-sh core looks
  healthy" and exited 0. Providers and resolvers are now critical checks: no
  active provider means no episode can ever be found, which is precisely the
  situation the command is for.

### Added

- **`anime providers enable <name>` and `anime providers disable <name>`.**
  Switching a provider off previously meant restating the whole list by hand
  (`anime config set providers.disabled "a,b"`), where a slip silently disables
  one you meant to keep. The name is checked against what is actually installed,
  so a typo is refused rather than written to the config to do nothing for ever,
  and disabling your last provider is refused outright.

### Changed

- **Commands only build what they use.** Every command constructed the entire
  object graph first — an HTTP client, a plugin scan, an mpv lookup, an AniList
  tracker — so `anime continue`, which reads two SQLite tables, paid for all of
  it. Measured by modules loaded, on a fixed machine:

  | | before | after |
  |---|---|---|
  | `continue`, `stats`, `history` | 588 | 335 |
  | `downloads` | 588 | 341 |
  | `search`, `trending` | 588 | 469 |
  | `play` | 588 | 580 |

  `play` is unchanged on purpose: it genuinely needs all of it. `httpx` is no
  longer imported at all for the library and download-listing commands.

## [0.2.48] — 2026-08-26

### Added

- **Offline playback actually works offline now.** 0.2.47 let a downloaded
  episode play from disk without touching a provider — but naming the show
  still went to AniList, so an episode sitting on your drive was unplayable on
  a train purely because nobody could look its title up.

  When AniList cannot be reached, `anime play` falls back to the shows already
  saved on this machine. That is exactly the right set: every show you have
  played or downloaded is in there, which is the same set that could have a
  file waiting.

  ```
  AniList is unreachable (transport error: All connection attempts failed)
  Using your local library: BOCCHI THE ROCK!
  ▶ BOCCHI THE ROCK! — Episode 1 (sub)
  Playing your download — no provider needed.
  ```

  Verified with HTTP and HTTPS pointed at a dead port, against a real library
  and a real download, with the resume position preserved.

  A title you have never touched still reports the real network error rather
  than a vague "no anime found" — the fallback looks on your machine first, it
  does not swallow the outage.

## [0.2.47] — 2026-08-26

### Added

- **Episodes you have downloaded now play from disk.** Downloads were
  write-only: you could fetch an episode and anime-sh would still stream it
  from the internet the next time you asked for it — slower, dependent on a CDN
  that may since have died, and impossible without a connection.

  `anime play` now prefers a copy you already have. No provider fan-out, no
  resolver, no network: it says *"Playing your download — no provider needed"*
  and starts. Verified with HTTP and HTTPS pointed at a dead port — it plays.
  (The title lookup still reads AniList, so a show whose metadata was never
  cached will not resolve offline; anything you have played before will.)

  Progress, resume position, intro skipping, auto-next and AniList sync all
  behave exactly as they do when streaming, because the local file is
  introduced as an ordinary stream candidate that simply happens to be first.
  Watch history records it as `downloads` rather than crediting whichever
  provider originally fetched it.

  Files count whether or not anime-sh has a record of them. `anime download`
  skips an episode it finds on disk without writing a database row, and
  anything downloaded before this release has no row either, so a lookup keyed
  only on the downloads table would have missed nearly every file anyone
  actually has.

- **`anime play --stream`** fetches from a provider even when a local copy
  exists — for when the file on disk is the suspect. `playback.prefer_downloads`
  in the config turns the whole behaviour off.

## [0.2.46] — 2026-08-26

### Changed

- **The CLI starts in a third of the time.** `anime version` took ~665 ms
  because `cli/main.py` imported the container, the config schema, `asyncio`
  and `importlib.metadata` at module level — the entire application constructed
  before it could print one line. A command that prints a version string, or
  the shell asking for tab-completions, paid for httpx and pydantic every time.

  | | before | after |
  |---|---|---|
  | `anime version` | 665 ms | **245 ms** |
  | `anime --help` | ~700 ms | **303 ms** |

  Commands that do real work import what they need when they need it, so they
  are no slower — the cost moved rather than grew. What remains at startup is
  typer and rich, which having a CLI at all costs.

## [0.2.45] — 2026-08-26

### Fixed

- **One rate limit no longer disables fuzzy search for the rest of the session.**
  Typing `fri` for *Frieren* or `onepiece` for *One Piece* matches against a
  local popularity index, built on first need from ten AniList catalog pages
  fetched at once. AniList rate-limits below ten concurrent requests, so a
  failure there was the normal outcome, not an exceptional one — and two
  separate pieces of code treated it as permanent:

  - one failed page threw away the nine that had succeeded, discarding 450
    usable titles because the tenth was refused;
  - the caller then stored that failure as *"there is no index"*, and the
    fast-path check returned it unconditionally ever after, so no retry was
    ever attempted.

  Together, a single bad minute silently switched off fuzzy search until the app
  was restarted, with nothing shown to say so. Pages that arrive are now kept
  (they are popularity-ordered, so what survives is the part that matters most),
  a partial catalog is used for the session without being cached for the day,
  and a failed build is retried rather than remembered as an answer.

- **Importing the domain layer no longer builds the entire CLI.** `anime_sh`'s
  package `__init__` imported the CLI eagerly, and that module runs on any
  access to the package — so reading a dataclass out of `anime_sh.domain.models`
  loaded typer, pydantic, rich and httpx: 531 modules and 672 ms. It is 85
  modules and 62 ms now. `import-linter` could not catch this, because the
  declared import lived in `__init__.py` rather than in `domain/`, so all three
  architecture contracts passed while the runtime disagreed. A test now asserts
  the layering in a subprocess, where `sys.modules` is still honest.

  This does not speed up `anime <command>`, which loads the CLI either way;
  startup cost there is still open.

## [0.2.44] — 2026-08-26

### Changed

- **The home screen is a grid.** Rows used to be a title glued to its metadata
  with two spaces, so where "which episode am I on" appeared depended entirely
  on how long the title was. With titles running from "BLACK TORCH" to "Rich
  Girl Caretaker: I'm Secretly the Caregiver of the Most Popular Girl in This
  Rich Kid School", the answer landed in a different column on every line and
  finding it meant reading each row to its end.

  Every row now has fixed columns — state, title, episode, status — so the eye
  travels down one column instead of across forty lines:

  ```
  ▸  The King's Avatar        Ep 2       ━━━━━──  68%
  ●  The Ogre's Bride         Ep 8/12    new episode
  ○  Slime Season 4           Ep 20      in 4d 0h
  ```

  Widths come from the terminal, not from constants: the layout drops its least
  load-bearing column rather than wrapping in a narrow shell, and caps its
  measure rather than sprawling across a 200-column one. Titles are measured in
  terminal cells, so a Japanese title stays aligned with everything else.

- **Continue Watching is ordered by what you can act on.** The episode you are
  part-way through leads, then episodes waiting unwatched, then shows you are
  caught up on — dimmed, at the bottom. A half-watched episode previously sat
  below three shows that merely had a new episode out.

- **State reads as shape before it reads as words.** `▸` resume, `●` an episode
  waiting, `○` caught up and nothing to do.

### Fixed

- **Launching no longer rate-limits itself.** The home screen fired one AniList
  query per Continue-Watching row — twenty at once — on top of seasonal,
  trending and the AniList sync. AniList rate-limits well below that, so a
  normal launch earned a 429, and because the limiter is shared the next thing
  you typed failed too: *"Search failed: AniList is rate-limiting requests: rate
  limited — try again in about 41s"*, twice, stacked over the list.

  Almost none of those requests could return anything new. A show that has
  finished airing has no schedule left to change, and a show whose next episode
  is still in the future already has everything the row needs — the countdown
  ticks locally. Only rows whose next episode has aired since they were cached
  are refreshed, a few at a time. A real library that previously made twenty
  requests on launch now makes **none**.

- **A repeated error no longer stacks a second toast** over the rows behind it.

- **`anime` no longer says the same thing twice** in a rate-limit message. It
  read "AniList is rate-limiting requests: rate limited — try again in about
  41s".

- **A show you are already watching no longer appears twice.** Four titles were
  listed in both Continue Watching and Airing This Season, with different
  metadata in each, which read as two different shows.

- **Section counts are real counts.** Both headers read "20" because both had
  simply hit their fetch limit — a number that looked like data.

## [0.2.43] — 2026-08-24

### Fixed

- **A mistyped command is no longer searched for as if it were a show.**
  `anime <anything>` is sugar for `anime play <anything>`, which is the point of
  the tool — but it also meant that anything anime-sh did not recognise became a
  search. `anime plugins` went off to resolve a stream for a show called
  "plugins"; `anime serach frieren` searched for a show called "serach". You got
  provider fan-out and a "nothing playable" error instead of "no such command".

  anime-sh now recognises two kinds of near-miss and says so instead:

  - **Words that name a real concept here but are not commands** — `plugins`,
    `server`, `update`, `install`, `help`, `watch`. These are guesses rather
    than typos and are listed explicitly, because `plugins` is no closer to a
    real command name than an actual anime title is.
  - **Typos of real commands** — `serach` → `search`, `donwload` → `download`,
    `provider` → `providers`.

  Every message names what to run instead, and says how to force the search
  anyway (`anime play "plugins"`) in case you really did mean a show by that
  name.

  The threshold for "that's a typo" was chosen by measurement, not by feel:
  one-word anime titles reach at most 0.67 similarity against the command list
  (`bleach` against `search`), while real typos start around 0.80. A test pins
  that gap, so lowering the threshold to catch one more typo fails the build
  rather than quietly making `anime bleach` stop playing Bleach.

## [0.2.42] — 2026-08-24

### Fixed

- **A download that lost part of the episode was recorded as complete.** When a
  host drops an HLS segment mid-transfer, ffmpeg retries it, gives up, skips it
  and carries on to the end. It reports that at *warning* level and leaves the
  exit code at 0 — and anime-sh ran ffmpeg at `-loglevel error`, so nothing was
  printed at all. The only success criterion was the exit code, so a file
  missing a third of its runtime went into your downloads folder marked
  **done**. Reproduced by removing one segment of a three-segment playlist:
  exit 0, no output, 4.04 seconds of video where 6.06 were expected.

  anime-sh now asks ffmpeg for warnings, treats a skipped segment as a failed
  download, and deletes the partial file instead of leaving it to be mistaken
  for a good one. A partial episode is worse than a failed one — a failure
  retries, whereas a file sitting in the folder marked done is trusted, watched
  halfway, and only then discovered to be truncated.

  Downloads are also verified with `ffprobe` before being reported as finished:
  a file with no playable stream, or zero duration, is discarded with an error
  saying the host most likely returned an error page instead of the stream.

  A file is only ever deleted on a *positive* finding of damage. If `ffprobe`
  itself cannot be run — missing, broken, or too slow to answer — that is a
  verdict about `ffprobe`, not about your download, and the file is kept. The
  first version of this check did not draw that line, and a verified-good file
  was destroyed in testing by a prober that merely misbehaved.

## [0.2.41] — 2026-08-24

### Fixed

- **Every project link on the PyPI page was a 404.** `[project.urls]` pointed at
  `github.com/animesh/anime-sh` — the wrong account — and the docs link added a
  `main` branch that does not exist. Homepage, Repository, Issues and
  Documentation now resolve, and a Changelog link was added beside them.
- **CI had never run on a push to the default branch.** The `ci` workflow
  triggered on `push: branches: [main]` while the default branch is `master`, so
  every green run in this repository's history came from a pull request. A
  branch protection rule built on that would have been guarding nothing.
- **Releases are now published to GitHub, not only to PyPI.** PyPI had moved
  from 0.2.1 to 0.2.40 while the repository's Releases page still showed 0.2.1;
  the release workflow uploaded the wheel and stopped. It now cuts the matching
  GitHub Release with the notes taken verbatim from this file, and attaches the
  built sdist and wheel.

### Added

- **A packaging job in CI.** It builds the wheel, installs it into a clean venv,
  runs the console script, and loads every declared provider and resolver entry
  point. Tests run from the source tree and structurally cannot catch a module
  missing from the wheel or a broken entry point.
- **Release-tag guards.** A tag whose version disagrees with `pyproject.toml`,
  or that has no section in this changelog, now fails the release before
  anything is uploaded rather than after.
- **Python 3.13 in the test matrix.** It was advertised in the trove classifiers
  and never tested.
- **`scripts/changelog_section.py`** — extracts one version's notes from this
  file, so the tag, the changelog and the release page cannot drift apart.
  It writes UTF-8 regardless of what the console claims to be: this changelog
  describes provider request flows with real `→` arrows, and Python piped on
  Windows picks cp1252 and dies encoding them — which made the first run report
  "no section" for the nine versions that contain one. A release backfill
  driven by that output would have silently skipped exactly the versions whose
  notes are worth reading.
- **Issue and pull request templates**, a `SECURITY.md` with the actual threat
  boundary (provider HTML, subprocess arguments, generated filenames, the
  loopback proxy, the AniList token), a `CODE_OF_CONDUCT.md`, `CODEOWNERS`, and
  a monthly Dependabot schedule for actions and dependencies.

## [0.2.40] — 2026-08-01

### Fixed

- **Downloads of two long-titled seasons no longer overwrite each other.** The
  length cap added in 0.2.37 truncated titles from the end — which is exactly
  where the season marker lives — so "…Rich Kid School" and
  "…Rich Kid School Season 2" produced the same folder *and* file name, and
  downloading one silently replaced the other's episodes. Truncated names now
  carry a short digest of the full title, so distinct shows stay distinct.
  Titles short enough to keep whole are unchanged.

### Changed

- Corrected the search module's documentation, which still claimed results were
  returned in AniList's order "with no reordering". They have been re-ranked
  locally since 0.2.34.

## [0.2.39] — 2026-08-01

### Removed

- **The `cryptography` dependency.** It existed for AllAnime's source
  decryption, and AllAnime was removed in July — so every install has been
  pulling a large native package that nothing imports. Verified: a clean
  install no longer contains it, and both providers still work (anizone's TLS
  impersonation comes from `curl-cffi`, which stays).
- **The `discord` extra.** It installed `pypresence` for a Rich Presence
  feature that was never implemented, so `pip install anime-sh[discord]` cost
  you a dependency and gave you nothing. Also dropped from `[all]`.

### Added

- **`CONTRIBUTING.md`** — the enforced architecture rules, the identity-spine
  invariant, and what a provider or resolver has to satisfy.

## [0.2.38] — 2026-08-01

### Changed

- **The command reference now lists every command.** `calendar`, `random`,
  `seasonal`, `sources` and `unmark` shipped undocumented — findable only
  through `--help`, and invisible on the PyPI page.

## [0.2.37] — 2026-08-01

### Fixed

- **The metadata cache prunes itself.** Expired entries were only dropped when
  that exact key was read again, and a cache keyed by search query is mostly
  keys nobody types twice — a real install was found at 55 rows of which 53 were
  already expired. Nothing swept them except the manual `anime cache purge`.
  Writes now sweep on a fixed cadence.
- **Download names are safe on every platform.** Titles become a folder *and* a
  file name, and three gaps remained: Windows refuses reserved device names
  (`CON`, `NUL`, `COM1`…), a leading dash makes the path look like a flag to any
  tool receiving it, and long light-novel titles push the path toward Windows'
  260-character limit — a 96-character title already reaches 229 with the
  default folder, so a deeper `downloads.dir` tips it over mid-download.

## [0.2.36] — 2026-07-31

### Fixed

- **Obscure exact-title matches no longer bury the show you meant.** A 89-view
  short whose romaji title is literally "JoJo" outranked JoJo's Bizarre
  Adventure (470,000 views), because an exact title beat a prefix match no
  matter what. Ranking is now a blend — match strength, exactness and
  popularity all bounded — so a huge popularity gap can overcome a small
  exactness edge, while "Nisekoi:" still beats the more popular "Nisekoi".
- **Synonym matches rank below real titles.** AniList synonyms are
  crowd-sourced aliases, and treating them as equal to a show's own name is what
  let those obscure entries score a perfect match.

## [0.2.35] — 2026-07-31

### Fixed

- **A UTF-8 BOM no longer breaks your config.** Notepad writes one by default on
  Windows, so editing `config.toml` there made the app refuse to start with
  "could not read config".
- **`providers.parallel` must be at least 1.** A zero or negative value sliced
  the provider list down to nothing, so every playback attempt silently found no
  sources at all.
- **ffmpeg failures report a sane exit code.** Windows reports a negative status
  as 32-bit unsigned, so an ordinary failure read as "ffmpeg exited 4294967291".

- **One rate limit no longer poisons the whole session.** After a 429, every
  later request failed too — and each rejected request still spends the server's
  quota, so retrying through a limit is what keeps you inside it. The client now
  remembers the back-off window and fails fast and locally until it passes,
  telling you roughly how long is left.
- **Sequels written with Unicode roman numerals were read as season 1.** AniList
  titles some sequels "… Academy Ⅱ" (U+2162, not the letters I-I), which an
  ASCII pattern can't see — so such a sequel could still be offered as a source
  for its own prequel. Titles are NFKC-folded before parsing.
- **Fullwidth queries matched nothing.** Normalisation stripped every non-ASCII
  character, so "ＮＡＲＵＴＯ" folded away to an empty query.
- **An exact title now outranks a more popular near-match.** "Nisekoi:" and
  "Nisekoi" (and "Kaguya-sama: Love is War?" / "…War") differ only by
  punctuation, which folding erases, so the more popular season won whichever
  one you typed. Validated across 500 real titles: every show now ranks first
  for its own title (was 496/500).

## [0.2.34] — 2026-07-31

### Fixed

- **Search results are ranked, not just passed through.** When AniList returned
  anything at all, its ordering was used as-is — so "Your Name" put a soft-drink
  commercial above the film, and "JoJo" put a one-off short above the series.
  Results are now scored against what you typed, with popularity breaking ties.
  No extra requests: it re-orders rows already fetched.
- **A rate limit no longer freezes the app.** 0.2.28 taught the client to honour
  `Retry-After`, and AniList's is 60 seconds — so a search could sit silent for
  a full minute (measured: 61s). Interactive requests now give up after 5s and
  say "AniList is rate-limiting requests right now — wait a moment and try
  again"; batch work like `sync push` still waits out the window.
- **A malformed record no longer loses the whole search.** AniList occasionally
  returns a partial media row; reading its id raised a bare `KeyError` out of
  the entire call. Bad rows are skipped and the good results come back.
- **`config set` rejects values it doesn't understand.** `playback.quality` and
  `playback.audio` are plain strings in the schema, so a typo saved happily and
  then silently played at the wrong quality (an unknown target falls back to
  1080p). Loading an existing config stays lenient.

## [0.2.33] — 2026-07-31

### Fixed

- **Auto-play picks the right season too.** 0.2.32 tied the *source picker* to
  the season you opened, but playing without choosing a source by hand went
  through a separate path that still took whatever the provider ranked first —
  and a sequel's title is nearly identical to its prequel's, so that was
  regularly the wrong season. Both paths now agree.

## [0.2.32] — 2026-07-31

### Fixed

- **A sequel is no longer offered as a source for its prequel.** Provider
  searches return neighbouring seasons too, and nothing filtered them out — so
  opening season 1 listed season 2's entry as a source for it. Picking that (the
  season you were actually watching) played season 2's episodes while progress
  was recorded against season 1's AniList id. Everything downstream followed from
  that: the wrong show sat in Continue Watching, its episode list showed
  "4/12 available", and the remaining episodes looked unreleased. Sources are
  now matched to the season of the show you opened.

### Removed

- **The year badge in Continue Watching.** It was papering over the mis-matching
  above; with sources tied to the right season it is just noise.

## [0.2.31] — 2026-07-31

### Fixed

- **A source that stops early no longer looks like an unaired show.** Opening a
  12-episode season on a source that only carries 4 showed the rest as
  "not aired yet" — on a season that finished airing in 2025. Those episodes had
  aired; the chosen source just didn't have them. They now say
  "not on this source — press Esc to switch", and an episode that genuinely
  hasn't aired still shows its countdown.
- **The screen no longer offers an episode the source hasn't got.** The resume
  pin from Continue Watching skipped the availability check, so the call to
  action read "Play Episode 5 · up next" (with the cursor parked on it) while
  the list below marked episode 5 unavailable — pressing Enter could only fail.
- **Running out of episodes on a source now says so**, instead of leaving the
  action line blank, and points at Esc to pick another source.

## [0.2.30] — 2026-07-31

### Changed

- **Continue Watching says how many episodes a show has.** A finished season you
  are partway through now reads `up next · Ep 5 of 12`. On its own, `up next ·
  Ep 5` looks identical to an airing show's awaited next episode — which is how
  a 2025 season sitting above its 2026 sequel gets mistaken for a release that
  hasn't happened yet.
- **The year badge is parenthesised** — `From Old Country Bumpkin to Master
  Swordsman (2025)`. Bare, it read as part of the title.

## [0.2.29] — 2026-07-31

### Fixed

- **Errors and Ctrl-C no longer print a traceback.** The CLI called into its
  command framework unguarded, so a bad config file surfaced as a Python stack
  trace and pressing Ctrl-C during a search or download looked like a crash.
  Known failures now print their message (exit 2) and an interrupt exits quietly
  (exit 130).

## [0.2.28] — 2026-07-31

### Fixed

- **A background failure no longer takes the whole app down.** Continue
  Watching, the episode list, the watched-marks refresh and cover loading all
  ran unguarded in their workers, so a momentarily busy database or a provider
  hiccup raised straight out and crashed the TUI with a traceback — this is what
  killed the app on launch when the database was locked. Each now degrades to a
  message (or silently, for decoration) and leaves the rest usable.
- **Providers and resolvers are closed on shutdown.** Six of them build their own
  HTTP client and nothing ever closed them, so every run leaked those
  connections. The ports already declared `aclose()`; the implementations were
  missing and the container never called them.
- **Auto-next stops at the last aired episode.** `episode_count` is AniList's
  *planned* total, so for a currently-airing show it runs ahead of what has been
  released. Finishing the newest episode rolled straight into one that doesn't
  exist yet, then failed to find a stream for it.
- **Being rate-limited no longer fails the request outright.** A 429 fell
  through to the generic "4xx → give up" path with no retry, so brisk browsing
  or a large `anime sync push` walked straight into a hard failure against
  AniList's per-minute cap. 429s are retried now, waiting the server's own
  `Retry-After` when it sends one.
- **A rejected row no longer aborts `sync push`.** One bad media id (or a rate
  limit that outlasted its retries) ended the whole run and lost every row still
  queued behind it. Failures are counted as skipped and the push continues.
- **Abandoning a download no longer leaves ffmpeg running.** Cancelling a
  download (quitting, Ctrl-C) left the ffmpeg child alive and still writing to
  the destination file after anime-sh had exited. The child is now killed with
  its parent.
- **Quitting any way other than `q` now shuts down cleanly.** Ctrl-C, a crash or
  the terminal going away skipped the container's shutdown entirely, leaking
  clients and leaving the database without a clean close. Shutdown is idempotent
  and now runs from the TUI's own exit path.

## [0.2.27] — 2026-07-30

### Fixed

- **"Couldn't load this season / trending: database is locked".** Opening a
  database was a check-then-act race across an await: the home screen fans out
  around twenty metadata fetches at once, every one of them found no connection
  yet, and each opened its own. Those extra connections then fought over
  SQLite's single writer lock — so a background AniList sync writing ~70 rows
  made whatever else was loading fail outright. First-connect is serialized now,
  so there is exactly one connection per database.

  The same race is the likeliest source of the repeated database corruption:
  the surplus connections were never closed by ``close()``, leaving the file
  open while a recovery could be renaming and replacing it underneath them.
- **Brief write contention no longer fails a read.** Connections now set
  ``busy_timeout``, so a query waits out a busy writer instead of erroring.

## [0.2.26] — 2026-07-30

### Fixed

- **Seasons of the same show can be told apart.** Two entries reading
  "…Master Swordsman" and "…Master Swordsman II" sat next to each other in
  Continue Watching with nothing to distinguish them, so it was easy to open —
  and track progress against — last year's season instead of the one currently
  airing. Rows whose titles overlap now carry their year.
- **A show you're caught up on no longer offers an unaired episode.** The
  airing schedule wasn't stored locally, so a Continue Watching row painted from
  the cache had no idea when the next episode lands and said "up next · Ep N"
  until a live AniList fetch corrected it — and stayed wrong offline or when
  that fetch failed. The schedule is cached now (migration 0002), so the first
  paint shows the real countdown.

### Changed

- When a database is damaged beyond repair, the log now names the backup file
  holding your previous library, instead of only saying a backup was kept.

## [0.2.25] — 2026-07-30

### Fixed

- **Database recovery no longer discards your most recent watches.** When the
  local database is damaged (it can happen on Windows when another process
  touches the file mid-write), the self-heal rebuilds it from what's still
  readable. It used to abandon a whole table the moment a scan hit a corrupt
  page — and since the newest rows sit at the end of the table, that quietly
  reverted recent watch history and progress. The salvage now walks each table
  by row id and skips only the damaged rows, keeping everything that follows —
  so a bad page early in the file can't cost you the history after it.
- **A transient lock is no longer mistaken for corruption.** The integrity probe
  only triggers the (destructive) rebuild on genuine "malformed"/"not a
  database" errors now, not on a passing "database is locked" hiccup.

## [0.2.24] — 2026-07-30

### Fixed

- **Episode list no longer doubles after a series auto-completes.** Finishing a
  season fired several workers that re-rendered the episode list at the same
  time; their clear+append interleaved and the list showed every episode twice.
  Rendering is now serialized so only one rebuild runs at a time.

## [0.2.23] — 2026-07-30

### Fixed

- **The AniList sync no longer demotes shows you just watched here.** Continue
  Watching now orders by your local play history — which the sync never touches —
  so a show you watched on this device stays on top, instead of the sync bumping
  other shows above it by their AniList timestamps. (A missing AniList updatedAt
  also no longer gets stamped "now", which was inflating recency every sync.)

## [0.2.22] — 2026-07-30

### Fixed

- **Continue Watching refreshes when you return to Home.** After watching a show
  and pressing Esc, the list kept showing the state from launch — it only loaded
  once, on startup. It now rebuilds when you come back from a show, so what you
  just watched moves to the top and reflects your latest progress.

## [0.2.21] — 2026-07-30

### Fixed

- **App failed to launch with "database is locked" (regression in 0.2.20).** The
  new corruption self-heal opened a second connection to probe the database
  right before the main one, and on Windows that probe held the WAL lock long
  enough to break the real connection. The integrity check now runs on the main
  connection itself — no second connection, no lock.

## [0.2.20] — 2026-07-30

### Fixed

- **A corrupt database now self-heals instead of silently freezing the app.** If
  the SQLite file is damaged (a bad index from a crash/AV/disk hiccup), writes
  quietly fail on the bad pages and nothing updates — progress, Continue
  Watching, everything looks stuck. On launch the app now integrity-checks the
  DB and, if corrupt, salvages the rows into a rebuilt file (keeping the original
  as a `.corrupt-*` backup), so it recovers on its own.

## [0.2.19] — 2026-07-30

### Fixed

- **Continue Watching stays ordered by what you most recently watched.** The
  AniList pull stamped each show with the entry's (often older) updatedAt,
  overwriting the fresh timestamp from a show you just watched here and sinking
  it down the list. Progress recency now never moves backward, so the last thing
  you watched stays at the top.

## [0.2.18] — 2026-07-30

### Fixed

- **The cursor and "play next" line now advance after you finish an episode.**
  Opening a show from Continue Watching pinned the cursor to the episode you
  came to resume — and it stayed pinned there even after you finished it. The
  pin now drops once that episode is watched, so the highlight rolls on to the
  next one live.

## [0.2.17] — 2026-07-29

### Fixed

- **Watched ✓ marks now update live during auto-next.** Finishing an episode
  while the next one auto-plays kept the list frozen until the whole run ended
  (you had to leave and re-open the screen). The detail screen now refreshes its
  marks on each playback event, so a completed episode ticks over immediately.

## [0.2.16] — 2026-07-29

### Fixed

- **Watched ticks now update the moment you finish an episode.** mpv plays in
  its own window while the app idles in the background, so the refreshed marks
  never got painted until you left and re-opened the screen; the detail screen
  now forces a repaint when playback returns.
- **Cover art no longer blinks.** The resize handler was re-mounting the image
  and forcing a full repaint on every stray event; it now re-mounts only on a
  real size change, which also fixes covers that intermittently failed to show.

## [0.2.15] — 2026-07-29

### Added

- **A clear message when mpv isn't installed.** Playing an episode without the
  player used to fail with a cryptic error; it now says exactly what to install
  (scoop/brew/apt) and to run `anime doctor` — the most common first-run wall.

## [0.2.14] — 2026-07-29

### Added

- **A clear "what to do next" line on the detail screen** — "▶ Resume Episode 7 · 26%" / "▶ Start Episode 1" / "▶ Play Episode N" above the list, so the primary action is obvious.
- **Counts on the home sections** — "Continue Watching  14", "Trending  20".

### Fixed

- **The "needs extra deps" hint showed the wrong command.** Rich markup ate the
  `[tui]`, so it read `pip install anime-sh` (no extra) — the exact trap. It now
  shows the correct `anime-sh[tui]` install (uv and pip).

## [0.2.13] — 2026-07-29

### Changed

- **Slimmer progress bars.** The bars used solid block glyphs that read chunky;
  they now use thin horizontal rules (heavy = filled, light = track) for a sleek
  line instead of a thick block.

## [0.2.12] — 2026-07-29

### Fixed

- **Cover art no longer smears when you resize/maximize the window.** A Sixel
  bitmap doesn't reflow on its own, so resizing left stale pixels and a broken
  layout; the detail screen now re-mounts the cover and repaints on resize.
- **Narrower overall-progress bar** on the detail screen — it was too wide.

## [0.2.11] — 2026-07-29

### Added

- **Crisp cover art via graphics protocols.** On a terminal that supports Sixel
  (Windows Terminal ≥ 1.22), kitty, or iTerm2, the detail-screen poster now
  renders as a true bitmap instead of unicode blocks. Falls back to the block
  render everywhere else; set `ANIME_SH_NO_GRAPHICS=1` to force the fallback.

### Changed

- **Slimmer Continue Watching bars.** The little progress bars were too wide;
  trimmed so they sit neatly after the "Ep N · %".

## [0.2.10] — 2026-07-29

### Added

- **Mini progress bars in Continue Watching.** A show you're partway through now
  shows a little bar next to its "Ep 7 · 26%", matching the detail screen.
- **Loading spinners** on the This-Season and Trending sections, so the home
  screen shows it's working instead of looking empty while they fetch.

## [0.2.9] — 2026-07-29

### Changed

- **Continue Watching appears instantly.** It used to sit blank on launch while
  a dozen metadata lookups ran; now it paints from the local cache immediately
  and fills in airing countdowns in the background.
- **Smaller cover art.** The poster on the detail screen no longer dominates the
  view — it's a tidy accent beside the metadata (and reads a touch sharper).
- **Richer progress line.** The detail bar now shows episodes left and a rough
  time-to-finish, e.g. `6/12 · 50% · 6 left · ~2h`.

## [0.2.8] — 2026-07-29

### Fixed

- **AniList sync now marks every episode you've watched.** Pulling your list
  recorded only a single "up to episode N" row, so a show you'd watched 6
  episodes of elsewhere showed no ✓ marks at all. Watching is linear, so the
  furthest finished episode now implies every earlier one is watched — episodes
  1–N light up ✓, while a half-watched later episode keeps its own progress.

### Added

- **Progress bars on the detail screen.** An overall "watched X/Y · %" bar under
  the header, and per-episode mini-bars for anything in progress. Episodes now
  read at a glance: ✓ watched, ▸ in-progress (with bar), ▶ up-next, ○ unwatched.

## [0.2.7] — 2026-07-29

### Added

- **Cross-device progress.** When an AniList account is linked, the home screen
  pulls your list in the background on launch, so episodes watched on your phone
  or the web now show up in Continue Watching automatically.

### Changed

- **Sharper cover art again.** Covers now render with 2×3-pixel *sextant* cells
  (50% more vertical detail than 0.2.6's 2×2 quadrants), so posters read cleaner.
  A truecolor terminal is all that's needed; see the README for the even-sharper
  Sixel option.

### Removed

- **The AllAnime provider.** Its streams came from third-party embed hosts that
  were frequently geo/ISP-blocked, and its source crypto rotated every few days —
  an unsustainable upkeep for a source that rarely played. anikoto + anizone
  cover the same catalog.

## [0.2.6] — 2026-07-28

### Changed

- **Much sharper cover art.** 0.2.5's half-block render was only one pixel wide
  per character — coarse and blurry. Covers now use 2×2-pixel quadrant cells
  (double the horizontal detail) coloured by the *least-error* two-colour split
  of each cell, so edges stay crisp and smooth areas stay smooth instead of
  muddy. Rendered larger, too. Posters are now clearly legible.

## [0.2.5] — 2026-07-28

### Fixed

- **The detail screen refreshes after you watch.** Finishing an episode now
  updates its ✓ in place — no more leaving and re-opening the show to see what
  you've watched.
- **Every show renders full detail.** A show opened from Continue Watching or
  favorites used to show a sparse card (often no description, no airing info, no
  studio/score) because it rendered a cached row. The detail screen now
  re-fetches the show fresh on open, so the synopsis, schedule, studio and score
  are always there.

### Changed

- **Sharper cover art.** Covers now render as truecolor half-blocks — every
  pixel keeps its own colour instead of being averaged into muddy 2-colour
  blocks — at higher resolution. Much more legible posters.
- **Unreleased episodes show a countdown.** Instead of a flat "not available
  yet", an episode that hasn't aired shows when it will (`airs in 4d 3h`),
  projected weekly from the known schedule.
- Detail header polish: the alternate (romaji) title, cleaner genre line, and a
  longer synopsis.

## [0.2.4] — 2026-07-28

### Fixed

- **Continue Watching now keeps shows you're between episodes on.** It used to
  list a show only while an episode was *half-watched* — so the moment you
  finished the latest released episode, the show vanished until you started the
  next one. Shows you've caught up on (waiting for the next episode) disappeared
  entirely. Now a show stays in Continue Watching from when you start it until
  you've actually finished the whole series.

### Changed

- Continue Watching rows now describe where you are: **resume** a half-watched
  episode (`Ep 4 · 50%`), **up next** when the next episode is already out
  (`up next · Ep 6`), or **caught up** — greyed, with a countdown — when you're
  waiting on a still-airing show (`caught up · Ep 6 in 2d 3h`). Watchable shows
  sort above the ones you're waiting on; fully-finished series drop off.

## [0.2.3] — 2026-07-28

### Fixed

- **Clearing the search box no longer flashes stale results.** Emptying the
  field (backspace / select-all-delete) now cancels any search still in flight
  and drops late-arriving results for a query you've already cleared, instead of
  slamming random matches back onto the home screen.
- **Continue Watching now shows reliably.** The section was populated by a
  background worker that never re-showed it after the home screen hid it on
  load, so it often stayed invisible even when you had shows in progress. It now
  appears whenever you have something to continue.

### Added

- **"Caught up" state in Continue Watching.** For a show that's still airing,
  once you've watched the latest aired episode the row greys out and shows a live
  countdown to the next one (`caught up · Ep 6 in 2d 3h`). Shows you can actually
  watch stay bright and sort to the top; the ones you're waiting on sink to the
  bottom.

## [0.2.2] — 2026-07-28

### Fixed

- **Search no longer misses obvious titles.** AniList's search is strict
  whole-word matching, so common words (`the`, `a`), mid-word fragments (`fri`),
  and de-spaced spellings (`onepiece`) returned *nothing* — `the` came back
  empty even though dozens of titles contain it. Search now layers a local,
  day-cached snapshot of the most popular anime over AniList and matches it by
  prefix / substring / squashed-equality / fuzzy across every title field
  (romaji, english, native, synonyms). `the` → the popular shows that contain
  it, `fri` → *Frieren*, `onepiece` → *One Piece*, `one p` → *One Punch Man* /
  *One Piece*.

### Changed

- The forgiving fallback now also de-glues punctuation and camelCase
  (`ReZero` → `Re Zero`, `Dr.Stone` → `Dr Stone`) when retrying against AniList.
- The fast path is untouched: a query AniList answers is still returned in its
  own relevance order, with no extra requests and no index build — so nothing
  regresses for a query that already worked. The index is built lazily only when
  AniList returns nothing, memoised per run, and degrades to the old behaviour if
  it can't be fetched.

## [0.2.1] — 2026-07-24

Docs/metadata patch — republishes so the PyPI project page carries the corrected
README (0.2.0's page was built before the fix, and PyPI versions are immutable).

### Changed

- README leads with `uv tool install "anime-sh[tui]"`; added PyPI / Python /
  license badges. No code changes.

## [0.2.0] — 2026-07-24

Discovery, reliability, and a search that understands what you meant — the first
release on PyPI (`uv tool install "anime-sh[tui]"`).

### Added

- **Forgiving search** — when AniList's strict search returns nothing, retry with
  apostrophes restored (`dont` → `don't`) and the query's distinctive words, then
  fuzzy-rank the results against what you typed. `atack on titan` now finds it.
- **Discovery** — `anime recommend "<title>"` (AniList recommendations) and
  `anime related "<title>"` (prequels, sequels, side stories, movies).
- **Universal intro/outro skip** — AniSkip fills op/ed timestamps in when a
  provider ships none, so auto-skip works on every source.
- **Batch/season downloads** — `anime download -e 1-12` (and `1,3,5`), resumable
  (skips episodes already on disk) and robust to a single-episode failure.
- **Cached catalog** — AniList responses cached in a disposable `cache.db`;
  repeat browses are instant and recently-seen pages render offline.
  `anime cache clear` / `purge`.
- **Third provider** — AniZone (clean, un-obfuscated HLS with soft English subs).
- `providers.preferred` now orders the fan-out; `anime --version`; shell
  completion (`anime --install-completion`).

### Changed

- **Faster, more reliable playback** — a provider's candidate hosts are resolved
  concurrently (a slow/dead host no longer blocks the rest), and each resolved
  stream is pre-flighted so a dead CDN is dropped before the player is launched.
- **Reliable `--dub`** — AniZone (sub-only) no longer shadows dub requests, so the
  fan-out reaches a dub-capable provider.
- Browse commands (`trending`/`seasonal`/`calendar`) degrade gracefully instead
  of dumping a traceback when AniList is unreachable.
- Dropped config settings that were never wired (`resolvers.preferred_hosts`,
  `[tracking]`, `downloads.concurrency`).

### Fixed

- anikoto playback: de-obfuscation keys off the resolver, not a rotating CDN
  hostname, so it survives the CDN moving (nekostream → kotocdn → …).
- AllAnime: restored stream discovery after the mkissa.to crypto rewrite.

## [0.1.0] — unreleased

First end-to-end release. The full path — search → provider fan-out → resolver
fallback → mpv — works, with two live providers and a keyboard-driven TUI.

### Added

- **Domain core** — immutable models keyed on the AniList identity spine,
  `Protocol` ports, pure ranking, and a pure circuit-breaker state machine.
  Layering (`cli > tui > app > domain`) enforced in CI by import-linter.
- **Metadata** — AniList GraphQL source: search, trending, seasonal, airing
  schedule.
- **Providers** — AllAnime (ani-cli protocol: persisted-query + AES-CTR
  `tobeparsed` + XOR) and anikoto (HiAnime-family), both discovered via entry
  points. Parallel fan-out with per-provider timeouts and **persisted circuit
  breakers** + health-based reordering.
- **Resolvers** — AllAnime clock, mp4upload, the megaplay family
  (vidtube/megaplay.buzz/vidwish), and a generic HLS/MP4 passthrough, tried as a
  fallback chain.
- **Player** — mpv over JSON IPC (Windows named-pipe / Unix socket), with resume,
  automatic intro/outro skip, and auto-play-next.
- **Library** — SQLite, split into a sacred store (progress, history, favorites,
  cached metadata) and a disposable cache; numbered migrations from day one.
- **Downloads** — `anime download` via ffmpeg (stream copy) with DB tracking.
- **TUI** — bare `anime` launches a Textual app: search-as-you-type,
  continue-watching, trending → episodes → play.
- **CLI** — `search`, `play`, `trending`, `seasonal`, `calendar`, `random`,
  `continue`, `resume`, `history`, `favorite`, `download`, `downloads`,
  `providers`, `config`, `doctor` — all scriptable with `--json`.
- **Ops** — a nightly canary that probes each provider, publishes
  `provider-status.json`, and files a deduped issue on breakage; a registry-wide
  plugin contract suite; a PyPI trusted-publishing release workflow.

### Known limitations

- Some streaming hosts actively obstruct downloads (cross-origin segment
  redirects that strip the referer); those play but don't download.
- AniList write-sync and Discord Rich Presence are stubs for a later release.
