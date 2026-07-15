# anime-sh

The terminal-native anime client. You type a title; it plays. Providers,
mirrors, and resolvers are internal details you never have to think about.

```bash
anime "Frieren"
```

> **Status: M0 — skeleton.** The full architecture is wired and proven
> end-to-end against fakes, but no real streaming providers ship yet. Playback
> arrives in M1. See [`docs/architecture.md`](docs/architecture.md).

## What works today

```bash
anime version
anime doctor            # check player, ffmpeg, config, database, plugins
anime config path       # where your config lives
anime config validate
anime providers ls      # installed provider plugins (none in M0)
```

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
