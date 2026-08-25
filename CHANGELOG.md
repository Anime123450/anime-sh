# Changelog

All notable changes to anime-sh. Format loosely follows Keep a Changelog.

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
