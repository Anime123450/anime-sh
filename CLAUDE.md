# anime-sh — working notes

Terminal-native anime client. Layered `cli > tui > app > domain`, with
`infra` / `providers` / `resolvers` as adapters behind `domain.ports`.

## Commands

```bash
uv sync --extra dev --extra tui   # set up
uv run pytest -q                  # unit + contract + tui tests (no network)
uv run lint-imports               # architecture contracts — must stay green
uv run python -m anime_sh doctor  # player/ffmpeg/db/plugins check (see SAC note)
ANIME_SH_LIVE=1 uv run pytest tests/integration   # gated live provider/mpv tests
```

`uv` lives at `C:\Users\anime\.local\bin` (prepend to PATH). mpv/ffmpeg via scoop.

Windows / Smart App Control: SAC is enforcing on this machine, and it blocks uv's
45 KB `python.exe` venv trampoline (`os error 4551` — "An Application Control
policy has blocked this file"). Retrying does not help. If `uv run` starts failing
with that error, rebuild the venv with stdlib `venv`, which copies the real
CPython binary SAC allows:

```bash
rm -rf .venv
"$APPDATA/uv/python/cpython-3.11-windows-x86_64-none/python.exe" -m venv .venv
uv pip install --python .venv/Scripts/python.exe -e ".[dev,tui]"
```

`uv run`/`uv sync` then reuse that interpreter instead of re-installing a
trampoline.

SAC judges each trampoline by its own hash, so the generated console shims are
hit-and-miss: `pytest.exe` and `lint-imports.exe` currently run, `anime.exe` is
blocked. Invoke the CLI as a module to sidestep the shim entirely:
`uv run python -m anime_sh <cmd>`. The user-facing install
(`~/.local/bin/anime-sh.exe`, via `uv tool install`) is unaffected.

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
- `providers/` — allanime, anikoto, anizone (entry-point plugins).
- `resolvers/` — allanime-clock, mp4upload, vidtube→megaplay, filemoon,
  streamwish, generic.
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
  Its segments come back PNG-disguised (`Content-Type: image/png`, real TS after
  a ~252-byte decoy header) off a **CDN whose hostname rotates** — seen as
  nekostream, now `vidtub.kotocdn.site`. The megaplay resolver therefore sets
  `Stream.obfuscated=True` and `DeobfuscatingProxy` keys off that flag, not the
  hostname; `_OBFUSCATED_HOSTS` is only a fallback for unflagged streams. If
  playback ever dies with "didn't play, trying next…" on every anikoto title,
  check that flag is still being set before touching the host list.

## Status

M0–M5 done. Three live providers, circuit breakers, nightly canary, Textual TUI,
auto-skip/auto-next, ffmpeg downloads. Next: M6 (docs site, plugin cookiecutter,
PyPI). See `docs/architecture.md`.
