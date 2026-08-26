# anime-sh engineering standards

Every rule here exists because something broke. No rule is included on general
principle — each one names the bug that motivated it, so you can judge whether
it still applies rather than obeying it out of habit.

Read this alongside [`architecture.md`](architecture.md) (the design) and
[`../CONTRIBUTING.md`](../CONTRIBUTING.md) (how to work on it).

---

## 1. Bug taxonomy — what actually went wrong

### 1.1 Identity: providers mistaken for identities

**What failed.** Opening season 1 of a show listed season 2's provider entry as
a source for it. Choosing that — the season the user was actually watching —
streamed season 2's episodes while progress was written against **season 1's**
AniList id.

**Root cause.** `list_sources` returned every provider search hit unfiltered. A
provider search for one season also returns its sequels, and a sequel's title is
nearly identical to its prequel's.

**How it escaped.** Every layer was individually correct. The bug lived in the
*relationship* between an AniList id and a provider entry, which no unit test
covered because no unit owned it.

**What found it.** A user screenshot showing `4/12 episodes available` — the
number that only makes sense if a 4-episode source is attached to a 12-episode
show. Three rounds of fixing the *symptoms* (labels, badges, sort order)
preceded it. See §8.1.

### 1.2 Concurrency: check-then-act across `await`

**What failed.** `Couldn't load this season: database is locked`, and — very
likely — the repeated SQLite corruption that ate watch history for days.

**Root cause.** `Database.connect()` tested `self._conn is None` and *then*
awaited. The home screen fans out ~20 metadata fetches at once, so all twenty
saw no connection and each opened its own. The surplus connections fought over
SQLite's single writer lock, were never closed by `close()`, and held the file
open while recovery renamed and replaced it underneath them.

**Corroboration.** `cache.db` — the database taking those concurrent
first-connects — corrupted far more often than `anime.db`.

**What found it.** Chasing a user-visible "database is locked" toast to its
origin instead of treating it as transient.

### 1.3 Recovery that destroys what it recovers

**What failed.** Watch history silently reverted from 64 rows to 16, repeatedly,
across days.

**Root cause.** The corruption-salvage table scan abandoned the *entire rest of
a table* when it hit a damaged page. The newest rows have the highest rowids and
live at the end — so the salvage preserved old data and discarded exactly the
recent history the user cared about. Separately, when salvage failed outright the
app started from an empty database.

**How it escaped.** The self-heal was itself a fix, written and tested for the
happy path (a corrupt *index* over intact rows). Nobody tested a corrupt page
*in the middle of* a table.

**What found it.** Reading the actual row counts in every `.corrupt-*` backup on
disk and noticing one held four times more history than the live database.

### 1.4 Unicode: normalising too late, or not at all

**What failed.** (a) `ＮＡＲＵＴＯ` matched nothing. (b) `The Misfit of Demon
King Academy Ⅱ` was read as season 1, so it could be offered as a source for its
own prequel — a fresh instance of §1.1 that the §1.1 fix did not cover.

**Root cause.** Normalisation stripped every non-ASCII character (`[^a-z0-9]`)
instead of folding it first. `Ⅱ` is U+2162, not the letters `I`,`I`, and it
vanished silently.

**What found it.** Ranking 500 real AniList titles against themselves — a
*measurement*, not a test. The Ⅱ case was 1 of 4 failures in 500.

### 1.5 Ranking: strict tiers cannot express a real preference

**What failed.** `Your Name` returned a soft-drink commercial above the film.
`JoJo` returned an 89-view short above the 470,000-view series. `Nisekoi:`
returned `Nisekoi`.

**Root cause.** Three separate mistakes, found one after another:
1. Results were returned in the metadata source's order and never ranked at all.
2. Ranking used strict tiers — an exact match always beat a prefix match — so a
   crowd-sourced alias on an obscure entry buried the obvious answer.
3. Punctuation-only differences (`Nisekoi:` vs `Nisekoi`) folded together, so
   popularity decided which season you got.

Requirements (2) and (3) are in direct tension: exactness must outrank
popularity for `Nisekoi:`, and must *not* for `JoJo`. Only a blended score can
hold both.

**What found it.** Running real queries and reading the results, then measuring
against 500 titles. Unit tests passed throughout.

### 1.6 Filesystem and Windows

**What failed.** In order: a UTF-8 BOM (written by Notepad, the default Windows
editor) made `config.toml` unreadable and the app refused to start; ffmpeg
failures reported `exited 4294967291` (an unsigned wrap of −5); and — the worst
one — truncating long titles to a length cap made two seasons of the same show
resolve to the **identical folder and filename**, so downloading one silently
overwrote the other.

**Root cause of the last one, which is the instructive one.** The cap was added
to avoid exceeding Windows' 260-character path limit. Truncation removes the end
of a string; the season marker lives at the end. The fix for one platform bug
created a data-loss bug.

**How it escaped.** It shipped in 0.2.37 and survived a full "engineering audit"
*and* a "maintainer audit", both of which reviewed the codebase as a whole
rather than attacking recent changes specifically.

**What found it.** A hostile review whose explicit assumption was "one of your
own fixes introduced a regression".

### 1.7 Rate limiting: three bugs in the same code, in sequence

**What failed.**
1. A 429 fell into the generic "4xx → give up" path and was never retried, so a
   large `sync push` failed almost immediately.
2. Fixing that by honouring `Retry-After` made an interactive search **sit
   silent for 61 seconds** — indistinguishable from a hang.
3. Fixing *that* left every subsequent request failing for the rest of the
   session, because each rejected request still spends the server's quota.

**Root cause.** Retry policy was written once for one caller and applied to all
of them. Batch work should wait out a rate-limit window; anything a human is
watching must not.

**What found it.** Timing a sweep of 33 real queries and noticing two took 22 s
and 61 s. Then fuzzing, which showed 11 consecutive failures after one 429.

### 1.8 Resource ownership

**What failed.** Six HTTP clients leaked per run (both providers, four
resolvers); the container's shutdown ran only when the user pressed `q`, so
Ctrl-C or a crash skipped it entirely; a cancelled download left `ffmpeg`
running headless, still writing to disk after anime-sh exited; and the metadata
cache never pruned — a real install sat at 96% dead rows.

**Root cause.** `domain.ports` *declared* `aclose()` for providers and
resolvers. No concrete plugin implemented it and nothing called it. A declared
interface nobody exercises is documentation, not a contract.

### 1.9 Failure handling in the UI

**What failed.** A momentarily locked database crashed the entire TUI with a
traceback on launch. A bad `config.toml` printed a Python stack trace. Ctrl-C
during a search looked like a crash.

**Root cause.** Textual worker exceptions terminate the app. Four workers
awaited the database and providers with no handler. The CLI called into its
command framework unguarded.

### 1.10 Alerting

**What failed.** The nightly canary filed *"anizone provider is failing"* every
night against a provider that works. Cloudflare challenges GitHub's datacenter
IPs; home connections are fine.

**Why it matters.** An alert that always fires is an alert everyone learns to
ignore — which costs you the one that would have mattered.

### 1.11 Dependencies and documentation drift

**What failed.** `cryptography` — a large native package — was a hard dependency
of every install, used only by a provider deleted months earlier. A `discord`
extra installed `pypresence` for a feature that was never written. Five shipped
commands were absent from the README. The search module's own docstring promised
"no reordering" long after reordering was added.

---

## 2. Prevention rules

Each rule names the bug it would have prevented.

**R1 — A source is never an identity.** Anything mapping an AniList id to a
provider entry must verify they describe the *same season*, and must fall back
to the unfiltered set rather than leaving a show unplayable. *(§1.1)*

**R2 — Fix root causes, not the screen.** If a UI label is wrong, ask why the
data reaching it is wrong before changing the label. Three releases were spent
on badges and wording that were compensating for §1.1. *(§8.1)*

**R3 — No check-then-act across `await`.** Lazy initialisation of anything
shared must use a double-checked lock. The codebase already had a correct
example in `SearchService._catalog`; `Database.connect` was the outlier. *(§1.2)*

**R4 — Recovery may never silently discard data.** Any repair path must prefer
partial preservation, must never quietly start empty, and must name the backup
it kept. *(§1.3)*

**R5 — NFKC-fold before parsing or comparing any human-supplied text.** Folding
happens *before* character-class filtering, never instead of it. *(§1.4)*

**R6 — Never truncate a name without preserving uniqueness.** If a filesystem
component must be shortened, append a digest of the full value. *(§1.6)*

**R7 — Retry policy is per-caller.** Interactive paths fail fast with a
time estimate; batch paths wait. A server-imposed back-off must be remembered so
later calls fail locally instead of spending more quota. *(§1.7)*

**R8 — Whoever creates a resource closes it.** If a port declares `aclose()`,
every implementation provides it and the composition root calls it — defensively,
so a third-party plugin cannot block shutdown. Shutdown runs from a `finally`,
not from one keybinding. *(§1.8)*

**R9 — A background task may not crash the application.** Every TUI worker and
the CLI entry point catch broadly and degrade to a message. *(§1.9)*

**R10 — Caches prune themselves.** Any store with a TTL sweeps on a bounded
cadence. A maintenance command is not a strategy. *(§1.8)*

**R11 — Parse defensively at trust boundaries.** A malformed record is skipped,
not fatal; one bad row in a batch must not lose the batch. *(a missing `id`
raised `KeyError` out of an entire search; one rejected row aborted an entire
`sync push`)*

**R12 — Numeric and enumerated config values are bounded.** `providers.parallel`
accepted `-3`, which sliced the provider list to empty and silently disabled all
playback. Validate on write; stay lenient on read so a bad file cannot brick
startup. *(§1.6, config)*

**R13 — Read config as `utf-8-sig`, write as `utf-8`.** *(§1.6)*

**R14 — Normalise subprocess exit codes before showing them.** *(§1.6)*

**R15 — An alert that cannot be acted on is a bug.** Distinguish "broken" from
"blocked by this environment". *(§1.10)*

**R16 — A dependency with no import is deleted; an extra with no feature is
deleted.** Do it before 1.0, while it is not a breaking change. *(§1.11)*

**R17 — Docstrings that describe behaviour are part of the behaviour.** Changing
what a module does means changing what it claims. *(§1.11)*

---

## 3. Testing standards

**Unit tests — always.** Every fix ships with a test named for the behaviour it
protects, whose docstring says what breaks without it.

**Mutation check — for every bug fix.** Revert the fix locally and confirm the
new test *fails*. A test that passes either way is false confidence. This is
cheap (two commands) and it is how the download-collision test was validated.

**Contract tests — for every plugin surface.** `tests/contract/` is parametrized
over the live registry, so a new plugin is held to the same structural contract
automatically. This is what will catch the next missing `aclose()`. *(§1.8)*

**Measurement, not just assertion — for ranking and matching.** Ranking is not
correct because a test passes; it is correct because 500 real titles each rank
first for their own name. Keep the measurement runnable. **Beware sampling
bias:** the first version of that measurement drew only from the *popularity*
index, so it could not have detected popularity burying obscure shows. It was
re-run against deliberately obscure titles afterward. *(§1.5)*

**Fuzzing — for anything parsing user or network input.** Empty, huge, emoji,
fullwidth, CJK, control characters, SQL-like and shell-like strings. This is how
the rate-limit session-poisoning was found. *(§1.4, §1.7)*

**Soak — before a release.** Hundreds of iterations watching RSS, handle count
and per-iteration latency. Flat is the pass condition.

**Manual execution — for every feature, at least once, before 1.0.** Playback
and downloads were "verified by inspection" for months. When finally executed,
playback produced `AV: 00:00:05 / 00:25:59` and a download produced a valid
20.08 s MP4 — but until that moment, nobody actually knew.

**Honesty about test harnesses.** Two "bugs" during this project were artifacts
of the harness: `--vo=null --ao=null` stalls mpv (reported as broken IPC), and
`limit=1` leaves nothing to re-rank (reported as a ranking regression). Before
filing a finding against the product, ask what your instrumentation changed.

---

## 4. Pull request checklist

- [ ] Regression test added, and **verified to fail when the fix is reverted**
- [ ] Does this bug class exist elsewhere? (searched, and either fixed or ruled out)
- [ ] Docstrings/comments describing changed behaviour updated *(R17)*
- [ ] README / `--help` updated if a command, option or default changed
- [ ] User-facing strings say what happened **and** what to do next
- [ ] New config keys validated on write, lenient on read *(R12)*
- [ ] Text from users or the network is NFKC-folded before parsing *(R5)*
- [ ] Any generated filesystem name tested for collisions and length *(R6)*
- [ ] New resources have an owner that closes them *(R8)*
- [ ] `uv run pytest -q` and `uv run lint-imports` pass

## 5. Release checklist

1. Full suite green on all supported platforms in CI.
2. **Devil's advocate pass aimed only at what changed since the last release.**
   This is the single highest-yield step: every audit that reviewed "the
   codebase" missed the 0.2.37 download regression; the audit that assumed "one
   of my own recent fixes is broken" found it in minutes.
3. Dependency audit: every declared dependency is imported; every extra maps to
   a real feature. *(R16)*
4. Documentation audit: diff the README's command list against `--help`.
5. **Real playback**, executed, with evidence.
6. **Real download**, executed, verified with `ffprobe`.
7. Clean-room install (`uv venv` + `uv pip install ".[tui]"`) and a live provider
   call from it.
8. Upgrade check: run the previous version's database through the new
   migrations.
9. Changelog written for users — what broke, what it looked like, what to expect
   now.
10. Publish, then install from the published artifact and run `doctor`.

---

## 6. Coding standards

**Filenames.** Strip separators and control characters; escape Windows reserved
device names; strip leading dashes (a leading `-` reads as a flag in an argv
position); cap length *with a uniquifying digest*. *(R6)*

**Unicode.** NFKC-fold, then filter. Never the reverse. *(R5)*

**Subprocesses.** Argument lists only, never `shell=True`. Kill children in a
`finally`. Normalise exit codes. *(§1.6, §1.8)*

**Async.** No check-then-act across `await` on shared state. Blocking I/O in an
async path needs a justification in a comment (there is exactly one:
`_migrate` reading small `.sql` files at startup). *(R3)*

**Database.** One connection per `Database`. Single-statement writes plus an
immediate commit — that is why interleaved commits on a shared connection cannot
tear a logical unit. Set `busy_timeout`. Migrations are forward-only and
numbered.

**Metadata parsing.** Required fields are validated, not assumed. Skip a bad
record; never fail the batch. *(R11)*

**Errors.** Say what happened, and what to do next. `rate limited — try again in
about 42s` beats a URL and a status code. Reserve tracebacks for genuine bugs.

**Comments.** Explain *why*, especially where the non-obvious choice was forced
by something real. Most comments added during this work name the bug they
prevent; that is deliberate, and it is what stops the next person from
"simplifying" the fix back into the bug.

---

## 7. Architecture invariants

These are enforced by `import-linter` in CI, and verified as holding with zero
violations.

1. **The AniList id is the identity spine.** Providers are *sources attached to
   a known identity*, never the source of identity. Violating this caused §1.1.
2. **`domain/` imports nothing else in the package.** It stays pure models,
   ports and logic.
3. **`app/` imports only `domain`**, reaching the outside world exclusively
   through `domain.ports`.
4. **`cli/container.py` is the sole composition root.** The TUI receives its
   services from there and never imports `cli`.
5. **A failing plugin is never fatal.** Bad import, exploding constructor, wrong
   `api_version` — skipped with a warning.

Invariants 2–4 are why most bugs in this project were confined to one layer and
fixable without ripple. That is evidence the layering earns its cost.

---

## 8. Retrospective — why things were missed

### 8.1 Treating symptoms as the bug

The season-mismatch bug (§1.1) produced a wrong Continue Watching row, a
`4/12 episodes available` header, and episodes of a finished season looking
unreleased. Three releases went into labels, a year badge, and sort order — each
change *correct in isolation* and none touching the cause. The badge in
particular was compensation for a data bug, and was deleted when the real fix
landed.

**Change:** when a display is wrong, verify the data reaching it before editing
the display. *(R2)*

### 8.2 Auditing "the codebase" instead of the diff

The download-collision regression (§1.6) shipped in 0.2.37 and survived two
subsequent full audits. Both swept the whole project. The pass that found it
started from "assume one of your own recent fixes is broken".

**Change:** every release audit attacks the recent diff first. *(Release
checklist §5.2)*

### 8.3 Fixing one caller's problem for every caller

Rate limiting (§1.7) took three attempts because each fix was written for the
caller in front of me — batch sync, then interactive search — and applied
globally.

**Change:** when changing shared policy, enumerate the callers first. *(R7)*

### 8.4 Trusting my own instrumentation

Two findings were nearly filed against the product that were artifacts of my
test setup (`--vo=null` stalling mpv; `limit=1` leaving nothing to rank). Both
were caught only by re-running differently.

**Change:** reproduce a finding through a second, independent path before
believing it.

### 8.5 Measuring with a biased sample

The 500-title ranking measurement drew from the popularity index, so every
subject was popular — structurally incapable of detecting the failure mode most
worth detecting. It was only re-run against obscure titles during the hostile
pass.

**Change:** state what a measurement *cannot* detect, at the point of writing it.

### 8.6 Environment blind spot

Roughly an hour was lost to file writes that appeared to succeed but never
reached the real disk, producing "file not found" errors that looked like
application bugs.

**Change:** verify a write landed through a different tool than the one that
wrote it.

---

## 9. Future recommendations

Not implemented; listed so they are not rediscovered from scratch.

1. ~~**Startup cost.**~~ **Done (0.2.46.)** `anime version` was ~665 ms because
   `cli/main.py` imported the container, the config schema, `asyncio` and
   `importlib.metadata` at module level — the whole application constructed
   before printing one line. All 32 command bodies now reach `asyncio` through a
   single `_run` helper, and the rest are function-local imports behind
   same-named module-level shims, so no call site changed and the tests that
   monkeypatch `build_container` still work. **665 ms → 245 ms**, and importing
   `anime_sh.cli.main` went 406 ms → 167 ms; what is left is typer and rich,
   which declaring a Typer app costs unavoidably.

   Two lessons worth keeping. The refactor briefly made `_run` call *itself*,
   and **the entire suite stayed green** — every CLI test called the inner
   coroutine directly and none entered a command through Typer, so the wrapper
   had no coverage at all. `tests/unit/test_cli_commands_end_to_end.py` closes
   that. And the guard against regression is a subprocess assertion on
   `sys.modules`, because in-process it is worthless: by the time any other test
   has run, everything is imported already.
2. **Sequels without a season marker.** `Attack on Titan: Final Season` and
   subtitle-only sequels (`JoJo: Stone Ocean`) are invisible to §1.1's title
   parsing. AniList `relations` data would resolve them properly.
3. **`cache clear` vs `cache purge`.** Distinct behaviours, near-synonym names;
   only `--help` disambiguates. Renaming is cheapest before 1.0.
4. **`sync push` is unverified by execution** — it mutates a real AniList
   account. A throwaway account in CI would close the last untested feature.
5. **macOS and Linux are CI-verified only.** No one has used anime-sh by hand on
   either.
6. **Provider protocol drift is inevitable.** The canary is the early-warning
   system; keep it honest (§1.10) or it stops being one.
