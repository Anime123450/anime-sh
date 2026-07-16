# anime-sh

The terminal-native anime client. You type a title; it plays. Providers,
mirrors, and resolvers are internal details you never have to think about.

```bash
anime "Frieren"
```

> **Status: M5 — polish.** Bare `anime` launches a keyboard-driven Textual app;
> playback auto-skips intros and rolls into the next episode on its own. Under
> it: AniList metadata, two live providers (AllAnime + anikoto) fanned out with
> circuit breakers, resolvers, mpv over JSON IPC, a persistent library
> (resume/history/favorites), and ffmpeg downloads. See
> [`docs/architecture.md`](docs/architecture.md).

## What works today

```bash
anime                        # launch the keyboard-driven TUI (needs [tui] extra)
anime "Frieren"              # search + best match + play episode 1
anime play "Frieren" -e 18   # a specific episode (add --dub, -q 1080p)
anime search "frieren"       # AniList search (instant; no providers touched)
anime trending

anime continue               # episodes you started but didn't finish
anime resume                 # jump back into the most recent one
anime history                # what you've watched
anime favorite add "Frieren" # ★  (also: favorite ls / rm)
anime download "Frieren" -e 1  # save to disk (ffmpeg); also: anime downloads

anime doctor                 # player, ffmpeg, config, database, plugins
anime config path | validate
anime providers ls
```

Add `--json` to `search`, `trending`, `play`, `continue`, `history`, and
`favorite ls` for machine-readable output (`play --json` resolves the stream
without launching a player). Your library (progress, history, favorites) lives
in a separate `anime.db` from the disposable cache and renders offline.

> Streaming providers break and get Cloudflare-gated constantly — that's the
> normal operating state, not a bug. When a provider is unreachable, anime-sh
> degrades cleanly instead of crashing; metadata and your library keep working.

**Multiple providers, merged.** anime-sh fans out across providers (currently
AllAnime + anikoto) and falls through to whichever one actually has your show —
so a title missing from one source still plays from another, with no action
from you.

## Install

Needs Python 3.11+, plus an external media player (`mpv` recommended) and
`ffmpeg` for playback/downloads. `anime doctor` reports what's missing.

```bash
uv tool install "anime-sh[tui]"     # or: pipx install "anime-sh[tui]"
anime doctor
anime "Frieren"
```

### From source (dev)

```bash
git clone <repo> anime-sh && cd anime-sh
uv sync --extra dev --extra tui
uv run anime doctor
uv run anime            # launch the TUI
uv run pytest -q        # tests (no network); add ANIME_SH_LIVE=1 for live ones
```

See [`docs/plugins.md`](docs/plugins.md) to add a provider or resolver.

## Develop

```bash
uv run pytest          # fast unit suite — no network
uv run lint-imports    # architecture contracts (must stay green)
```

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

## License

MIT
