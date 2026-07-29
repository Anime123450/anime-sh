# Changelog

All notable changes to anime-sh. Format loosely follows Keep a Changelog.

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
