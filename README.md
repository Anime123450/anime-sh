# anime-sh

[![PyPI](https://img.shields.io/pypi/v/anime-sh)](https://pypi.org/project/anime-sh/)
[![Python](https://img.shields.io/pypi/pyversions/anime-sh)](https://pypi.org/project/anime-sh/)
[![CI](https://github.com/Anime123450/anime-sh/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/Anime123450/anime-sh/actions/workflows/ci.yml)
[![Providers](https://github.com/Anime123450/anime-sh/actions/workflows/canary.yml/badge.svg)](https://github.com/Anime123450/anime-sh/actions/workflows/canary.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**The terminal-native anime client.** You type a title; it plays. Providers,
mirrors, and resolvers are internal details you never have to think about.

```bash
anime "Frieren"
```

> The **Providers** badge is a nightly liveness probe of the real streaming
> sites. It goes red when a site blocks us, which happens routinely and is not a
> build failure — see [How it behaves](#how-it-behaves).

Under the hood: AniList metadata, live streaming providers fanned out with
circuit breakers, stream resolvers, `mpv` driven over JSON IPC, auto-skip of
intros/outros, auto-advance to the next episode, a persistent offline library
(resume / history / favorites), AniList two-way sync, and `ffmpeg` downloads —
all behind a keyboard-driven [Textual](https://textual.textualize.io) app.

---

## Contents

- [Requirements](#requirements)
- [Install](#install) — [PyPI (recommended)](#method-a--from-pypi-recommended) · [from source](#method-b--from-github-source)
- [First run](#first-run)
- [Sync across devices (AniList)](#sync-across-devices-anilist)
- [Command reference](#command-reference)
- [How it behaves](#how-it-behaves)
- [Updating & uninstalling](#updating--uninstalling)
- [Troubleshooting](#troubleshooting)
- [Develop](#develop) · [Design](#design) · [Legal](#legal)

---

## Requirements

| What | Why | Needed |
|------|-----|--------|
| **Python 3.11+** | runs the app | always |
| **[mpv](https://mpv.io)** | plays the video | for playback |
| **[ffmpeg](https://ffmpeg.org)** | saves downloads, fallback player | for `download` |

Install the two media tools with your OS package manager:

```bash
# Windows (scoop)          # Windows (winget)
scoop install mpv ffmpeg   winget install mpv.mpv ; winget install Gyan.FFmpeg

# macOS (Homebrew)         # Debian/Ubuntu
brew install mpv ffmpeg    sudo apt install mpv ffmpeg
```

After installing anime-sh, run **`anime doctor`** — it checks Python, `mpv`,
`ffmpeg`, the database, and the provider plugins, and tells you exactly what (if
anything) is missing.

---

## Install

### Easiest on Windows — one file, no terminal

Download this repo (green **Code** button → **Download ZIP**), unzip it, and
double-click **`run-anime.bat`**.

It installs what's missing and starts the app. You do **not** need Python — it
uses [uv](https://docs.astral.sh/uv/), a single standalone program that brings
its own. Running the file again later just launches anime-sh.

Prefer typing commands, or not on Windows? Use Method A.

### Method A — from PyPI (recommended)

**You do not need Python installed.** `uv` is a single standalone binary that
fetches its own Python, so this works from a clean machine. Copy the block for
your OS:

**Windows**

```powershell
winget install astral-sh.uv          # the installer (no Python needed)
winget install shinchiro.mpv         # the video player
uv tool install "anime-sh[tui]"      # anime-sh itself
anime doctor                         # check everything was found
```

**macOS**

```bash
brew install uv mpv
uv tool install "anime-sh[tui]"
anime doctor
```

**Linux**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # if you don't have uv
sudo apt install mpv                              # or your package manager
uv tool install "anime-sh[tui]"
anime doctor
```

Then run `anime` to launch it.

> **`anime` not recognised after installing?** The install worked; your shell
> just hasn't picked up the new folder. Run `uv tool update-shell`, then close
> and reopen the terminal.

**Already have Python?** `pipx install "anime-sh[tui]"` works too — but note
that pipx is itself a Python package, so it can't be the *first* thing you
install on a machine without Python.

The `[tui]` extra pulls in the interactive terminal UI and cover-art rendering.
`ffmpeg` is optional and only needed for `anime download`.

### Method B — from GitHub (source)

Use this to run the very latest code, hack on it, or if you don't want PyPI.

**Just want the latest build, installed as a tool:**

```bash
uv tool install "anime-sh[tui] @ git+https://github.com/Anime123450/anime-sh.git"
```

**Want the source to edit / develop:**

```bash
git clone https://github.com/Anime123450/anime-sh.git
cd anime-sh
uv sync --extra dev --extra tui   # create the venv + install everything
uv run anime doctor
uv run anime                      # launch the TUI (prefix commands with `uv run`)
```

> On this checkout, run the app as `uv run anime …`. The `uv tool install`
> methods above put a plain `anime` command on your PATH instead.

---

## First run

```bash
anime            # opens the TUI: Continue Watching, Airing This Season, Trending
```

- Type to **search** as you go (or press `/` to focus the search box).
- **Arrow keys** move, **Enter** opens a show, **Enter** on an episode plays it.
- Press **`?`** any time for the full key list, **`q`** to quit.

Prefer one-shot commands? `anime "Frieren"` searches, picks the best match, and
plays episode 1. `anime play "Frieren" -e 18 --dub -q 1080p` is fully explicit.

Turn on shell tab-completion once: `anime --install-completion`.

---

## Sync across devices (AniList)

anime-sh keeps your progress in a local database **and** can sync it with
[AniList](https://anilist.co), so what you watch here lines up with what you
watch anywhere else that tracks to AniList (phone apps, the website, etc.).

**Link your account once** — no password involved:

1. Create a free API client at
   [anilist.co/settings/developer](https://anilist.co/settings/developer) with
   redirect URL `https://anilist.co/api/v2/oauth/pin`.
2. Run `anime auth login` and paste the token it points you to.

Then:

```bash
anime sync pull      # import your AniList list (watching/planning/…) into anime-sh
anime sync push      # send your local watch history up to AniList
anime list --status watching   # view your AniList list by status
```

After linking, **finishing an episode automatically bumps your AniList
progress**. Run `anime sync pull` whenever you want to pull in progress you made
on another device.

---

## Command reference

```bash
# Watch
anime                        # launch the keyboard-driven TUI (needs [tui] extra)
anime "Frieren"              # search + best match + play episode 1
anime play "Frieren" -e 18   # a specific episode (add --dub, -q 1080p)
anime continue               # episodes you started but didn't finish
anime resume                 # jump back into the most recent one
anime next "Mob Psycho 100"  # find + play the next season (sequel)
anime sources "Frieren"      # every provider entry that matches, before playing

# Discover
anime search "frieren"       # AniList search (instant; no providers touched)
anime search --genre action --year 2024 --sort score   # browse with filters
anime trending
anime seasonal               # this season (or: --season fall --year 2025)
anime calendar --days 7      # what airs next, and when
anime random                 # surprise me, picked from what's trending
anime recommend "Frieren"    # shows for people who liked it (AniList)
anime related "Attack on Titan"  # prequels, sequels, side stories, movies

# Library & tracking
anime mark "Frieren" -e 12    # mark eps 1–12 watched (syncs to AniList)
anime unmark "Frieren"        # clear local progress for a show (undo a mark)
anime history                 # what you've watched
anime favorite add "Frieren"  # ★  (also: favorite ls / rm)
anime stats                   # episodes, hours, top genres & providers
anime rate "Frieren" 9        # set a score;  anime status "X" completed

# AniList
anime auth login              # link AniList (one-time); status / logout
anime sync pull | push        # import your list / send yours up
anime list --status watching  # your AniList list (also planning/completed…)

# Downloads
anime download "Frieren" -e 1-12  # save a range to disk (ffmpeg); resumes, skips done
anime download "Frieren" -e 1,3,5 # or a list;  also: anime downloads

# Housekeeping
anime doctor                  # player, ffmpeg, config, database, plugins
anime --version
anime config get              # dump settings;  config get playback.quality
anime config set playback.quality 1080p   # also: audio dub, ui.theme nord …
anime config path | validate
anime providers ls
anime cache clear             # wipe the disposable metadata cache (or: cache purge)
```

Add `--json` to `search`, `trending`, `play`, `continue`, `history`, and
`favorite ls` for machine-readable output (`play --json` resolves the stream
without launching a player).

---

## How it behaves

**Forgiving search.** You don't have to spell titles the way AniList stores
them — `dont toy with me`, `dukes son claims he wont love me`, even
`atack on titan` all find the right show. When AniList's strict search comes up
empty, anime-sh retries with apostrophes restored and the query's distinctive
words, then fuzzy-ranks the results against what you typed.

**Multiple providers, merged.** anime-sh fans out across streaming providers
(currently **anikoto + AniZone**) and falls through to whichever one actually
has your show — so a title missing from one source still plays from another,
with no action from you. AniZone serves a clean, un-obfuscated HLS stream with
soft English subs, so it plays where Cloudflare-gated sites can't.

> Streaming providers break and get Cloudflare-gated constantly — that's the
> normal operating state, not a bug. When a provider is unreachable, anime-sh
> degrades cleanly instead of crashing; metadata and your library keep working.

**Offline-friendly.** Your library (progress, history, favorites) lives in its
own `anime.db`, separate from a disposable `cache.db` of AniList responses.
Recently-seen pages still render with no network, and `anime cache clear` is
always safe — nothing user-owned lives in the cache.

---

## Updating & uninstalling

```bash
uv tool upgrade anime-sh      # or: pipx upgrade anime-sh
uv tool uninstall anime-sh    # or: pipx uninstall anime-sh
```

Your library and settings live outside the install (see `anime config path`), so
upgrading never touches them.

---

## Troubleshooting

- **`pipx` / `pip` "not recognised", or you don't have Python** — that's the
  wrong starting point on a clean machine: pipx and pip *are* Python packages.
  Use the `uv` block under [Install](#install) instead; uv is a standalone
  binary and brings its own Python.
- **`anime` not recognised right after installing** — the install succeeded,
  your shell just hasn't picked up the new folder. `uv tool update-shell`, then
  reopen the terminal.
- **`anime doctor` says mpv/ffmpeg not found** — install them (see
  [Requirements](#requirements)) and make sure they're on your PATH.
- **A show won't play / "trying next…" on every source** — providers get
  Cloudflare-gated or geo-blocked; try again later or a different title. Your
  library and search keep working regardless.
- **Windows: `anime` blocked by Smart App Control** — invoke it as a module:
  `python -m anime_sh <command>`.
- **Nothing in Continue Watching from your phone** — link AniList
  (`anime auth login`) and run `anime sync pull`; see
  [Sync across devices](#sync-across-devices-anilist).

---

## Develop

```bash
git clone https://github.com/Anime123450/anime-sh.git && cd anime-sh
uv sync --extra dev --extra tui
uv run python -m pytest -q   # fast unit + contract suite (no network)
uv run lint-imports          # architecture contracts (must stay green)
```

Add `ANIME_SH_LIVE=1` to run the gated live-provider tests. See
[`docs/plugins.md`](docs/plugins.md) to add a provider or resolver,
[`CONTRIBUTING.md`](CONTRIBUTING.md) before sending a change, and
[`docs/ENGINEERING_STANDARDS.md`](docs/ENGINEERING_STANDARDS.md) for the rules
this project learned the hard way — each one names the bug that caused it.

## Design

anime-sh is layered `cli/tui → app → domain`, with `infra`, `providers`, and
`resolvers` as swappable adapters behind ports. Dependencies point downward only
and that is enforced in CI. Identity comes from AniList (every show is keyed by
its AniList id), so adding a provider is attaching a source to a known identity,
not fuzzy-matching titles. Full write-up: [`docs/architecture.md`](docs/architecture.md).

## Legal

anime-sh is a **client**, not a content library. It bundles no media, mirrors
nothing, and bypasses no DRM. Providers read public pages and are expected to
break; a broken provider is a degraded experience, not an outage. Provider
plugins are separable from the core so the project survives any single one.

## Project

- [Changelog](CHANGELOG.md) — every release, written for users
- [Releases](https://github.com/Anime123450/anime-sh/releases) — tagged builds with notes
- [Contributing](CONTRIBUTING.md) · [Engineering standards](docs/ENGINEERING_STANDARDS.md) · [Architecture](docs/architecture.md) · [Writing a plugin](docs/plugins.md)
- [Security policy](SECURITY.md) · [Code of conduct](CODE_OF_CONDUCT.md)

## License

[MIT](LICENSE)
