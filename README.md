# anime-sh

The terminal-native anime client. You type a title; it plays. Providers,
mirrors, and resolvers are internal details you never have to think about.

```bash
anime "Frieren"
```

> **Status: M2 — persistence & resume.** Real adapters run through every layer
> (AniList metadata, AllAnime provider, resolvers, mpv over JSON IPC), and your
> library persists: resume, history, and favorites. `anime "Frieren"` searches,
> matches, resolves, and plays — on networks where the provider isn't
> Cloudflare-challenged. See [`docs/architecture.md`](docs/architecture.md).

## What works today

```bash
anime "Frieren"              # search + best match + play episode 1
anime play "Frieren" -e 18   # a specific episode (add --dub, -q 1080p)
anime search "frieren"       # AniList search (instant; no providers touched)
anime trending

anime continue               # episodes you started but didn't finish
anime resume                 # jump back into the most recent one
anime history                # what you've watched
anime favorite add "Frieren" # ★  (also: favorite ls / rm)

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

## Install (dev)

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). An external media
player (`mpv` recommended) and `ffmpeg` are needed for playback/downloads —
`anime doctor` tells you what is missing.

```bash
git clone <repo> anime-sh && cd anime-sh
uv sync --extra dev
uv run anime doctor
```

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
