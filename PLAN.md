# anime-sh — engineering plan

**Audience:** an agent or contributor picking this up cold, with no access to
the conversation that produced it.
**Written:** 2026-08-24, against `master` at version **0.2.41**.
**Repo state when this was written:** 316 tests passing, 3 skipped;
`lint-imports` green with 3 contracts kept; 9,918 lines of Python across 79
files; `pyproject.toml` at 0.2.41, PyPI at 0.2.40 pending tag.

Read §0 and §1 before touching anything. Every task from §4 onward is written to
be picked up independently and states its own acceptance criteria.

---

## 0. Orientation — what this project is

anime-sh is a terminal-native anime client. You type a title, it plays. It fans
out across streaming providers, resolves a stream, drives `mpv` over JSON IPC,
auto-skips intros, auto-advances episodes, keeps a local library, syncs to
AniList, and downloads with `ffmpeg`. There is a Typer CLI and a Textual TUI.

### Setup

```bash
uv sync --extra dev --extra tui
uv run pytest -q            # 316 tests, no network, ~70s
uv run lint-imports         # architecture contracts — must stay green
uv run python -m anime_sh doctor
```

### The five rules that are not negotiable

These are enforced by `import-linter` in CI or by hard-won experience. Breaking
one is not a style disagreement — it is a build failure, or a repeat of a bug
that already shipped. Long form with the bug history:
[`docs/ENGINEERING_STANDARDS.md`](docs/ENGINEERING_STANDARDS.md).

1. **`domain/` imports nothing else in the package.** Pure models, ports, logic.
2. **`app/` imports only `domain`** and reaches the outside world exclusively
   through `domain.ports`. Never import `infra`/`providers`/`resolvers` there.
3. **`cli/container.py` is the sole composition root.** The TUI receives its
   services from there and never imports `cli`.
4. **The AniList id is the identity spine.** Every show is keyed by its AniList
   id; providers are *sources attached to a known identity*, never the source of
   identity. Violating this once caused episodes to play from season 2 while
   progress was recorded against season 1, and produced four separate visible
   symptoms that three releases chased before anyone found the cause.
5. **A failing plugin is never fatal.** Bad import, exploding constructor, wrong
   `api_version` — skipped with a warning, never a crash.

### Definition of done for any task here

- A regression test that **you verified fails when the fix is reverted**. Not
  "should fail" — run it both ways.
- `uv run pytest -q` and `uv run lint-imports` both green.
- Comments explain *why*, naming the real constraint that forced the choice.
  Most comments in this codebase name the bug they prevent; that is deliberate,
  and it is what stops the next person simplifying a fix back into the bug.
- `CHANGELOG.md` entry written **for users** — what broke, what it looked like,
  what to expect now — under a new version heading, with `pyproject.toml` bumped
  to match. The release workflow now fails if those two disagree.
- One focused PR. This repo's history is one change per PR, merged with a merge
  commit.

### What not to do

- Do not "modernise" or reformat code you are not changing. The diff is the unit
  of review here.
- Do not add a dependency without a stated reason and a check that it is
  actually imported. Two dependencies were removed in 0.2.39 for exactly this.
- Do not fix a display when the data reaching it is wrong. Verify the data
  first. This is retrospective §8.1, and it cost three releases once.
- Do not touch `.github/workflows/release.yml` casually. It holds publish rights
  to PyPI via OIDC trusted publishing.

---

## 1. What was just done (0.2.41) — do not redo

A repo-plumbing pass landed on branch `github-polish`. It is listed here so you
do not repeat it, and so you know the workflows changed underneath you.

| Fixed | What it was |
|---|---|
| `[project.urls]` in `pyproject.toml` | Pointed at `github.com/animesh/anime-sh` — the wrong account. All four PyPI sidebar links 404'd. The docs link also referenced a `main` branch that does not exist. |
| `ci.yml` trigger | `push: branches: [main]` while the default branch is `master`. CI had **never** run on a push to the default branch; every green run in this repo's history is a `pull_request` run. |
| `release.yml` | Published to PyPI and stopped. PyPI reached 0.2.40 while the GitHub Releases page still showed 0.2.1. It now also cuts the GitHub Release, with notes taken verbatim from `CHANGELOG.md`. |

Also added: a CI `package` job that builds the wheel, installs it into a clean
venv, runs the console script and loads every entry point; Python 3.13 in the
matrix (it was in the trove classifiers, untested); release-tag guards that fail
if the tag disagrees with `pyproject.toml` or has no changelog section;
`scripts/changelog_section.py`; issue templates (bug / provider-broken /
feature); a PR template built from the standards checklist; `SECURITY.md` with
the real threat boundary; `CODE_OF_CONDUCT.md`; `CODEOWNERS`; Dependabot.

---

## 2. Measured facts you can build on

Do not re-derive these. Do re-measure after you change them.

| Measurement | Value | How it was taken |
|---|---|---|
| `anime --version`, end to end | **~700 ms** (median of 5, min 692) | `subprocess.run` around `python -m anime_sh --version` |
| Bare interpreter start, same machine | 84 ms | `python -c pass` |
| Importing `anime_sh.cli.main` | **574 ms cumulative** | `python -X importtime -c "import anime_sh.cli.main"` |
| — of which `anime_sh.config.schema` | 126 ms | same |
| — `importlib.metadata` (plugin discovery) | 119 ms | same |
| — `anime_sh.cli.container` | 113 ms | same |
| — `asyncio` | 74 ms | same |
| — `httpx` | 69 ms | same |
| `src/anime_sh/cli/main.py` | **1,557 lines, 39 commands, 6 sub-apps** | `wc -l`, `grep -c` |
| Test distribution | unit 256 · tui 28 · contract 5 · integration 2 | `grep 'def test_'` per directory |
| Merged branches still alive on the remote | **38** | `git branch -r --merged origin/master` |
| Git tags | 42 | `git tag \| wc -l` |
| GitHub Releases published | **2** (v0.2.0, v0.2.1) | `gh release list` |

---

## 3. Priority order

Work top-down. Within a phase, tasks are independent unless noted.

| Phase | Theme | Why this order |
|---|---|---|
| **A** | Finish the GitHub surface (§4) | Cheap, visible, and several items need the maintainer's own hands — start them early so they are not blocking at the end. |
| **B** | Quality gates (§5) | Every later phase is safer with a linter and a type checker in place. Land these before any large refactor. |
| **C** | Startup latency (§6) | Self-contained, measurable, and the most user-noticeable defect fully within our control. |
| **D** | Break up `cli/main.py` (§7) | Needs B first. Large, mechanical, high regression risk without the gates. |
| **E** | Correctness backlog (§8) | The items `ENGINEERING_STANDARDS` §9 explicitly parked. |
| **F** | TUI legibility (§9) | Independent of everything else; can run in parallel. |
| **G** | Docs site and plugin ergonomics (§10) | The remaining M6 milestone. |

---

## 4. Phase A — finish the GitHub surface

### A1. Backfill the missing GitHub Releases *(maintainer action, scripted)*

**Problem.** 42 tags exist; 2 Releases exist. Every version from v0.2.2 to
v0.2.41 already has changelog notes written and no Release page carrying them.

**Change.** For each tag with a changelog section, create the Release from that
section. `scripts/changelog_section.py` already does the extraction.

```bash
for tag in $(git tag --sort=v:refname); do
  gh release view "$tag" >/dev/null 2>&1 && continue
  notes=$(uv run python scripts/changelog_section.py "$tag" 2>/dev/null) || continue
  gh release create "$tag" --title "anime-sh ${tag#v}" --notes "$notes"
done
```

Then mark the newest as latest:

```bash
gh release edit "$(git tag --sort=v:refname | tail -1)" --latest
```

**Care required.** This publishes ~39 public pages, each emitting a notification
to watchers. Do a dry run first — drop the `gh release create` line and `echo`
the tag and the first line of its notes — and read the list before committing to
it. Tags with no changelog section are skipped rather than getting empty notes.

**Acceptance.** `gh release list` shows every tag; the newest is marked Latest;
spot-check three release pages against `CHANGELOG.md`.

### A2. Prune the 38 merged remote branches *(maintainer action)*

**Problem.** The branch dropdown has 40+ entries, all but one merged and dead.
It makes the repo read as abandoned mid-work.

```bash
git branch -r --merged origin/master \
  | grep -vE 'origin/(HEAD|master)' \
  | sed 's|origin/||' \
  | xargs -n1 -I{} git push origin --delete {}
git fetch --prune
```

Then enable **Settings → General → Automatically delete head branches** so it
does not accumulate again.

**Care required.** Irreversible for anything not genuinely merged. The
`--merged origin/master` filter is what makes it safe — do not widen it. Print
the list and read it before piping to `xargs`.

### A3. Branch protection on `master` *(maintainer action)*

Meaningful for the first time, because the `ci.yml` trigger fix in §1 means CI
now actually runs on `master`. Require: a pull request before merge; status
checks `test (ubuntu-latest · py3.12)` and `package` green; branches up to date;
conversation resolution.

### A4. Repository labels to match the issue templates

The templates reference `bug`, `provider-broken`, `enhancement`, `dependencies`
and `canary`. Only `provider-broken` and `canary` exist.

```bash
gh label create bug          --color d73a4a --description "Something behaves wrongly" --force
gh label create enhancement  --color a2eeef --description "Proposed new behaviour"    --force
gh label create dependencies --color 0366d6 --description "Dependency updates"        --force
```

### A5. A README hero image

The README has no screenshot. This is a TUI — the most persuasive thing it can
possibly show is a picture of itself. Capture the home screen with Continue
Watching and Airing This Season populated, save as `docs/media/home.png`, and
place it directly under the one-line `anime "Frieren"` block:

```markdown
<p align="center">
  <img src="docs/media/home.png" width="900"
       alt="The anime-sh home screen: Continue Watching and Airing This Season">
</p>
```

Then set the same image as the repository's social preview under
**Settings → General → Social preview**, so links unfurl with the app instead of
a generic GitHub card. Keep the terminal at ~120 columns on a dark theme; crop
out the window chrome and any personal path in the prompt.

### A6. Fix the build-backend pin

`pyproject.toml` declares `requires = ["uv_build>=0.11.28,<0.12.0"]` while the
installed uv is 0.12.5, so every single build prints:

> warning: `build_system.requires` does not contain the current uv version

Widen to `>=0.11.28,<0.13.0` and confirm `uv build` is warning-free. Small, but
it is noise on every build and it becomes a hard failure eventually.

---

## 5. Phase B — quality gates

There is currently **no linter, no formatter, no type checker and no coverage
measurement** in this project. Everything below is additive and must not change
runtime behaviour.

### B1. Ruff, introduced without a 9,918-line reformat

**The trap:** enabling ruff with default rules produces a diff touching every
file, which destroys `git blame` and buries every real change after it.

**Do this instead:**

1. Add to `pyproject.toml`:

   ```toml
   [tool.ruff]
   line-length = 100
   target-version = "py311"

   [tool.ruff.lint]
   select = ["E", "F", "W", "I", "UP", "B", "SIM", "RUF"]
   ignore = ["E501"]   # length is the formatter's job, not the linter's
   ```

2. Run `uv run ruff check --statistics` and put that report in the PR
   description — not the fixes.
3. Fix only `F` (unused imports, undefined names) and `B` (mutable defaults,
   swallowed exceptions). Those are correctness. Land as its own commit.
4. Everything else goes into `[tool.ruff.lint.per-file-ignores]` with a `# TODO`
   naming the file, burned down file-by-file as those files are touched for
   other reasons.
5. Add a **separate** `lint` job to `ci.yml` — not a step inside `test`, so a
   style failure cannot mask a test failure.

**Acceptance.** `ruff check` exits 0 on a clean tree; the behaviour-changing
subset (`F`, `B`) is genuinely fixed rather than ignored; no reformat commit
exists.

### B2. Type checking, `domain` and `app` first

`domain/` is pure and `app/` only touches ports — the two layers where types pay
off immediately and where the annotations largely already exist.

```toml
[tool.mypy]
python_version = "3.11"
files = ["src/anime_sh"]
ignore_missing_imports = true

[[tool.mypy.overrides]]
module = ["anime_sh.domain.*", "anime_sh.app.*"]
disallow_untyped_defs = true
warn_return_any = true
strict_equality = true
```

Everything outside those two modules stays lenient for now. Add `infra` next,
`cli`/`tui` last — Textual and Typer both lean on decorators that generate
noise. Wire into the `lint` job.

### B3. Coverage, as information rather than a gate

Add `pytest-cov`, publish the number, and **do not** set a failure threshold
yet — a threshold picked before you know the baseline just gets lowered later.

```bash
uv run pytest -q --cov=anime_sh --cov-report=term-missing --cov-report=xml
```

Put the per-module table in the PR. The expected finding, given the test
distribution in §2, is that `cli/` and `tui/` are the thin spots. Use the result
to aim §7 and §9 — not as a number to game.

### B4. A pre-commit config

`ruff check` and `ruff format --check` on changed files only, plus `uv lock
--check`. Cheap, and it stops B1's carefully-avoided mega-diff from arriving by
accident later.

---

## 6. Phase C — startup latency

**Problem, measured.** `anime --version` takes ~700 ms. 84 ms of that is the
interpreter. **574 ms is importing `anime_sh.cli.main`**, which eagerly pulls in
the whole composition root: the pydantic settings schema (126 ms), plugin
discovery via `importlib.metadata` (119 ms), `httpx` (69 ms), `asyncio` (74 ms).
Printing a version string needs none of it.

This is the project's most visible self-inflicted defect. It is paid on every
shell completion, every `--help`, and every scripted invocation.

**Target:** `anime --version` and `anime --help` under **150 ms**. Commands that
genuinely need the network keep their current cost.

**Approach.**

1. **Move the container out of import time.** `cli/main.py` builds or imports
   the container at module scope. Make service construction lazy — a
   `get_services()` called inside command bodies, not a module-level object.
   This alone should remove the 113 ms container cost and most of what it drags
   in behind it.
2. **Defer plugin discovery.** `importlib.metadata.entry_points()` costs 119 ms
   and is only needed when a command actually resolves a stream. `anime
   providers ls` and playback need it; `--version` does not.
3. **Defer `httpx`.** Import it inside `infra/http/client.py` at call time, or
   have the container import the client module lazily.
4. **Short-circuit `--version`** before any other import runs — a literal in
   `anime_sh/__init__.py`, or `importlib.metadata.version("anime-sh")` at worst.
5. **Consider lazy Typer command registration** if 1–4 miss the target. Typer
   imports every command's module to build its help; a `TyperGroup.get_command`
   override that imports on demand is the standard fix.

**Guard rail — add this test; it is the entire point of the exercise:**

```python
def test_version_does_not_import_the_world():
    """`anime --version` must not import httpx, the container, or the plugin
    registry. Startup was ~700ms because it imported all three; without this
    test the cost silently returns the first time someone adds a module-level
    import to cli/main.py."""
    out = subprocess.run(
        [sys.executable, "-X", "importtime", "-m", "anime_sh", "--version"],
        capture_output=True, text=True,
    ).stderr
    for forbidden in ("httpx", "anime_sh.cli.container", "anime_sh.infra"):
        assert forbidden not in out, f"{forbidden} imported for --version"
```

**Acceptance.** That test passes; re-measure with the §2 method and record the
new number in the changelog entry; `anime doctor`, `anime play` and the TUI all
still work — the lazy path is exactly where this breaks.

---

## 7. Phase D — break up `cli/main.py`

**Problem.** 1,557 lines, 39 commands, 6 sub-apps in one module. It is the
largest file in the project by a factor of three, it is why Phase C is awkward,
and every command change collides in review.

**Do this only after B1 and B2 land**, so the move is checked mechanically.

**Target layout** — mirrors how the commands already group, and each sub-app
already exists as a `typer.Typer()` in the current file:

```
cli/
  main.py          # app assembly, global options, callbacks   (~150 lines)
  commands/
    watch.py       # play, continue, resume, next, sources, download, downloads
    discover.py    # search, trending, seasonal, calendar, random, recommend, related
    library.py     # mark, unmark, history, favorite, stats, rate, status
    tracking.py    # auth, sync, list
    admin.py       # doctor, config, providers, cache, version
  container.py     # unchanged — still the sole composition root
  doctor.py        # unchanged
```

**Rules for the move.**

- **Pure moves first, one commit per module**, with no behaviour edits mixed in.
  A refactor commit that also fixes a bug is unreviewable.
- Command names, options, defaults and help text must be byte-identical
  afterwards. Capture `anime --help` and every `anime <cmd> --help` before and
  after and diff them. That is the acceptance test — and it is worth landing as
  a real snapshot test so the *next* split cannot silently change a flag.
- `lint-imports` must stay green. Add a contract asserting `cli.commands.*` may
  import `app` and `domain` but never `infra` directly.
- This is also the right moment to make each command module import lazily (§6.5).

**Acceptance.** Help-output diff is empty; suite green; `main.py` under 200
lines; a help-snapshot test exists.

---

## 8. Phase E — the parked correctness backlog

These are `docs/ENGINEERING_STANDARDS.md` §9, recorded there precisely so they
would not have to be rediscovered. Each is a real user-visible gap.

### E1. Sequels that carry no season marker

**Problem.** Title parsing identifies sequels by markers like "Season 2" or
"II". `Attack on Titan: Final Season` and subtitle-only sequels (`JoJo's Bizarre
Adventure: Stone Ocean`) have none, so they are invisible to the season logic
that protects invariant §0.4 — the same logic whose failure once recorded
season 2 progress against season 1.

**Change.** Use AniList `relations` (`PREQUEL` / `SEQUEL` edges) as the
authoritative ordering, with title parsing demoted to a fallback for shows with
no relation data. `infra/metadata/anilist.py` already speaks GraphQL; relations
is a field addition to the existing media query.

**Acceptance.** A test with recorded AniList fixtures for AoT Final Season and
Stone Ocean asserting the correct prequel chain; `anime next "Attack on Titan"`
reaches Final Season; `tests/unit/test_season_sources.py` still passes.

### E2. `cache clear` vs `cache purge`

Two distinct behaviours behind near-synonyms; only `--help` disambiguates, and a
user reaching for the destructive one by accident has no way to tell. Rename
before 1.0 — it is a breaking change and it only gets more expensive.

Suggested: keep `anime cache clear` (the safe, common one) and rename the other
to `anime cache delete`, with the old name kept as a hidden deprecated alias
that prints what it did and what to use instead.

### E3. `sync push` has never been verified by execution

It mutates a real AniList account, so no test runs it. It is the last untested
user-facing feature. Create a throwaway AniList account, store its token as a
repository secret, and add a **manually-dispatched** workflow — not scheduled,
never on `pull_request`, since a token in a PR-triggered workflow is a
credential leak — that pushes a known small list and reads it back.

### E4. Tighten the AniList token file write

`infra/tracker/tokens.py::save_token` calls `path.write_text(...)` and *then*
`os.chmod(path, 0o600)`. Between those two calls the token exists on disk with
default permissions. Create the file with
`os.open(path, O_CREAT | O_WRONLY | O_TRUNC, 0o600)` and write through that
handle so it is never world-readable. On Windows `chmod` is a no-op regardless —
say so honestly in the docstring; `SECURITY.md` already documents the limit.

### E5. macOS and Linux are CI-verified only

Nobody has used anime-sh by hand on either. CI proves the tests pass; it does not
prove `mpv` IPC, terminal cover art, or the config paths behave. One manual
session on each, with notes, is worth more here than another hundred unit tests.

---

## 9. Phase F — TUI legibility

Grounded in the current home screen. `tui/widgets.py::AnimeItem._compose_label`
builds each row as one flowing markup string:
`title [(badge)]  [dim]subtitle[/dim]  [bar]`.

### F1. The metadata column is ragged, and it is the main readability cost

Because the subtitle is concatenated straight after the title, `up next · Ep 8
of 12` starts at a different column on every row — titles in this list run from
9 to 90 characters ("BLACK TORCH" against "Rich Girl Caretaker: I'm Secretly the
Caregiver of the Most Popular Girl in This Rich Kid School"). The eye cannot
scan the status column, because there is no column.

**Change.** Give the row a fixed structure: truncate the title with an ellipsis
at a width derived from the container, and right-align the status. In Textual
that is a `Horizontal` holding a title `Label` with `text-overflow: ellipsis`
and a fixed-width status `Label`, rather than one composed string.

**Care required.** `_lit()` exists because titles contain square brackets that
Textual's markup parser eats — `[Oshi no Ko]` vanishes without it. Any
restructuring must keep that escaping on every user-supplied string.

**Acceptance.** A `tests/tui` test asserting a very long title truncates rather
than pushing the status off-screen, and that a bracketed title still renders its
brackets.

### F2. Section counts say `20` because 20 is the limit

`Continue Watching 20` and `Airing This Season 20` are both `limit=20`, not
totals. A count that is always the cap communicates nothing. Either show the
true total (`20 of 34`) or drop the number.

### F3. Shows appear in two sections at once

A currently-airing show you are watching appears in both Continue Watching and
Airing This Season. Not wrong, but it costs rows in the densest part of the
screen. Consider badging the duplicate in the seasonal list rather than
repeating it, or excluding rows already shown above.

### F4. Cover art is detail-screen only

`tui/coverart.py` and the `textual-image` extra already do sharp Sixel/kitty
rendering; the home screen is text-only. A thumbnail on the *focused* row — not
every row, which would be slow and noisy — would carry real recognition value.

**Care required.** Cover fetching has caused blink and resize bugs before; there
are branches named `fix-cover-resize`, `fix-marks-and-cover-blink` and
`sharper-covers`. Read those diffs first — the failure modes are known and
specific.

### F5. The `?` help screen and the footer drift apart

Verify that every binding the footer advertises exists, and that
`screens/help.py` lists every binding that exists. This drift is silent and
constant; a test that asserts the two agree costs almost nothing.

---

## 10. Phase G — docs site and plugin ergonomics

The remaining M6 milestone from `CLAUDE.md`.

### G1. A documentation site

Four substantial documents already exist (`architecture.md` 239 lines,
`ENGINEERING_STANDARDS.md` 452, `plugins.md` 115, `CONTRIBUTING.md` 82) and are
readable only as raw files on GitHub. MkDocs with Material, built and deployed by
a workflow on push to `master`, publishing to GitHub Pages. No content rewrite —
this is packaging what exists, plus a **generated CLI reference** so the command
list cannot drift from `--help`. Release checklist step 4 currently does that
diff by hand.

### G2. A plugin cookiecutter

`docs/plugins.md` describes writing a provider; there is no template. A
`create-anime-sh-plugin` template producing a working skeleton — port
implemented, entry point registered, fixtures, a gated live test, contract suite
wired up — would make "add a provider" a fifteen-minute task. This is the
project's actual extensibility story, and right now it is prose.

### G3. A third provider

Two providers is one bad week away from zero. The canary and breaker
infrastructure already exist to support more, and the contract suite in
`tests/contract/` is parametrized over every installed plugin, so a new provider
is testable the moment it is registered.

---

## 11. Release procedure

From `docs/ENGINEERING_STANDARDS.md` §5. Condensed; read the original before a
real release.

1. Full suite green on all platforms in CI.
2. **Devil's advocate pass aimed only at what changed since the last release.**
   The highest-yield step in the whole process: every audit that swept "the
   codebase" missed the 0.2.37 download regression; the pass that started from
   "assume one of my own recent fixes is broken" found it in minutes.
3. Dependency audit — every declared dependency imported, every extra mapping to
   a real feature.
4. Diff the README's command list against `--help`.
5. Real playback, executed, with evidence.
6. Real download, executed, verified with `ffprobe`.
7. Clean-room install, and a live provider call from it.
8. Run the previous version's database through the new migrations.
9. Changelog written for users.
10. Tag `vX.Y.Z`. The workflow now verifies the tag matches `pyproject.toml` and
    that a changelog section exists, then publishes to PyPI and cuts the GitHub
    Release from those notes.
11. Install from the published artifact and run `doctor`.

---

## 12. Quick reference

```bash
uv sync --extra dev --extra tui                     # setup
uv run pytest -q                                    # 316 tests, no network
uv run pytest tests/unit/test_search.py -q          # one file
ANIME_SH_LIVE=1 uv run pytest tests/integration     # gated, hits real providers
uv run lint-imports                                 # architecture contracts
uv run python -m anime_sh doctor                    # environment check
uv run python scripts/canary.py --provider anikoto  # probe a real provider
uv run python scripts/changelog_section.py 0.2.41   # release notes for a version
```

**Windows note (from `CLAUDE.md`).** Smart App Control blocks uv's generated
`anime.exe` trampoline on the maintainer's machine. Invoke the CLI as a module —
`uv run python -m anime_sh <cmd>` — to sidestep the shim. `pytest.exe` and
`lint-imports.exe` currently pass SAC; `anime.exe` does not. This affects the
development checkout only, not `uv tool install`.
