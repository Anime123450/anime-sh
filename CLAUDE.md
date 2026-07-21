# anime-sh — working notes

Terminal-native anime client. Layered `cli > tui > app > domain`, with
`infra` / `providers` / `resolvers` as adapters behind `domain.ports`.

## Commands

```bash
uv sync --extra dev --extra tui   # set up
uv run pytest -q                  # unit + contract + tui tests (no network)
uv run lint-imports               # architecture contracts — must stay green
uv run anime doctor               # player/ffmpeg/db/plugins check
ANIME_SH_LIVE=1 uv run pytest tests/integration   # gated live provider/mpv tests
```

`uv` lives at `C:\Users\anime\.local\bin` (prepend to PATH). mpv/ffmpeg via scoop.
On Windows, `lint-imports`' native DLL is occasionally blocked by Application
Control — just retry.

## Architecture rules (enforced by import-linter)

- `domain/` imports nothing else in the package (pure models + ports + logic).
- `app/` imports only `domain` — it talks to the outside only through
  `domain.ports`. Never import `infra`/`providers`/`resolvers` from `app`.
- The composition root is `cli/container.py` (the one place that wires concretes
  to services). The TUI gets its services injected from there, never imports cli.
- Identity spine: every `Anime` is keyed by its AniList id. Providers are
  *sources* attached to a known identity, not the source of identity.

## Layout

- `domain/` — models, ports, ranking, health (circuit breaker), errors.
- `app/` — services: search, catalog(seasonal/calendar via metadata), playback
  (fallback chain + skip + auto-next), providers (fan-out + breakers), library,
  download.
- `infra/` — http, metadata (AniList), db (sqlite: library/health/downloads +
  migrations), players (mpv over IPC, null), downloader (ffmpeg), registry.
- `providers/` — allanime, anikoto (entry-point plugins).
- `resolvers/` — allanime-clock, mp4upload, vidtube→megaplay, generic.
- `cli/`, `tui/` — the two adapters. Tests in `tests/{unit,contract,integration,tui}`.

## Adding a provider/resolver

Implement the `Provider`/`Resolver` port, register it as an entry point in
`pyproject.toml`, add fixtures + a live gated test, and it must pass the
registry-wide contract suite (`tests/contract/`). A bad plugin is skipped at
load, never fatal.

## Provider notes (change often — canary tracks them)

- **AllAnime** (rebranded to `mkissa.to`): Firefox UA. Search/episodes are plain
  POST GraphQL. Sources are gated — the crypto (rotating `epoch`, per-build AES
  key, persisted-query text+hash) is derived from the live site's JS bundle in
  `keygen.py`. The sources GET carries `Origin: mkissa.to` (the old
  `youtu-chan.com` origin now yields `AA_CRYPTO_STALE`), the full persisted-query
  text (hash-only alone → `PersistedQueryNotFound` on cold instances), and an
  `extensions.aaReq` AES-256-GCM token (`build_aareq`) or the API returns
  `AA_CRYPTO_MISSING`. The reply is an AES-256-GCM `tobeparsed` blob (per-build
  key, legacy sha256("Xot36i3lK3:v1") fallback), then XOR-0x38 per source URL.
  Playback referer stays `youtu-chan.com` (the clock/CDN, not the API).
- **anikoto**: HiAnime-family. `/search` → `/ajax/episode/list/<id>` →
  `/ajax/server/list?servers=<data-ids>` → `/ajax/server?get=<link-id>`. Streams
  on megaplay clones (vidtube.site/megaplay.buzz/vidwish.live): read `cidu` from
  the embed page, then `getSources?id=<cidu>` (needs `X-Requested-With`).

## Status

M0–M5 done. Two live providers, circuit breakers, nightly canary, Textual TUI,
auto-skip/auto-next, ffmpeg downloads. Next: M6 (docs site, plugin cookiecutter,
PyPI). See `docs/architecture.md`.
