# projects.md — collaboration guide (for Antigravity)

Living handoff doc. Maintained by the Tech Lead. Read this before starting a task.

## Roles & workflow
- **Claude** = Tech Lead / Reviewer / **the only one who pushes to GitHub**.
  Repo: `C:\Users\anime\dev\anime-sh` → origin `Anime123450/anime-sh` (**PRIVATE**).
- **Antigravity** = writes code in a **separate, strictly local copy**;
  **never pushes to GitHub**.
- Flow: Antigravity implements locally → user pings Claude → Claude reviews the
  files, runs tests, fixes issues → commits in Claude's repo → pushes.

## Project in one paragraph
Terminal-native anime client. Layers: `cli > tui > app > domain`; adapters
(`infra`/`providers`/`resolvers`) sit behind `domain.ports`. `app` imports only
`domain`; `domain` imports nothing else in the package. Identity spine = AniList
id. Full detail in `CLAUDE.md` and `docs/architecture.md`.
Current release: **0.2.6**, published on public PyPI (`pip install "anime-sh[tui]"`).

## Build / test / run (this Windows machine)
- Setup: `uv sync --extra dev --extra tui`
- Tests: `uv run python -m pytest -q`  (use the **module** form — the `pytest.exe`
  shim is unreliable under Smart App Control)
- Run the app: `python -m anime_sh <cmd>` (the `anime` / `anime-sh` `.exe` shims
  are SAC-blocked; use the module form or the `.bat` launchers in Python Scripts)
- `lint-imports` **cannot run here** (SAC blocks grimp's native DLL). CI runs it on
  Linux — rely on CI. To self-check layering: grep that `app/` imports only
  stdlib + `anime_sh.domain`, and `domain/` imports nothing else in the package.

## Conventions (IMPORTANT)
- **No AI attribution anywhere**: never add `Co-Authored-By: Claude`, "Generated
  with Claude Code", or any AI mention to commits, PRs, code, or docs.
- Keep the test suite green; add tests for new behavior.
- Release = bump `version` in `pyproject.toml` + date `CHANGELOG.md` + push a
  `v<version>` git tag → `.github/workflows/release.yml` publishes to PyPI.

## Recent changes (last session, 0.2.2 → 0.2.6)
- **Search** (`app/search.py`, `infra/metadata/anilist.py`): a lazy, day-cached
  local index of the ~500 most popular anime (`popular()`) rescues queries
  AniList's strict word-search drops (`the`, `fri`, `onepiece`). Fast path
  (a query AniList answers) is untouched — do not add reordering there.
- **Continue Watching** (`infra/db/library.py`, `tui/format.py:continue_row`):
  keyed to the *furthest* episode watched; keeps caught-up/between-episode shows;
  drops only finished-and-fully-watched series. States: resume / up-next /
  caught-up (greyed + countdown) / dropped.
- **Detail screen** (`tui/screens/detail.py`): refreshes ✓ marks in place after
  playback; re-fetches fresh metadata on open (so cached rows still show
  synopsis/airing/studio/score); per-episode air countdowns
  (`tui/format.py:episode_air_label`); header polish.
- **Cover art** (`tui/coverart.py`): 2×2 quadrant cells coloured by the
  least-error two-colour split — sharp, not muddy.
- **TUI search** (`tui/screens/home.py`): clearing the box cancels in-flight
  searches so stale results don't flash back.

## Gotchas learned
- **Smart App Control (SAC)** on this machine blocks: uv venv trampolines,
  generated `.exe` console shims (`anime`, `anime-sh`, `pytest`), and grimp's
  `_rustgrimp` DLL. Workarounds: run modules (`python -m ...`), `.bat` launchers,
  and `uv run python -m pytest`.
- After every local `pip install --upgrade`, pip recreates the SAC-blocked
  `anime`/`anime-sh` `.exe` shims — replace them with `.bat` launchers that call
  `python -m anime_sh` (see `CLAUDE.md` SAC note).
- The GitHub repo is private, so distribution to others is **via PyPI only**.

## For Antigravity: how to hand off a task
- Leave a short note of what changed and why (files touched, intent), and whether
  you added/updated tests. The Tech Lead will review, run the suite, fix, and push.
