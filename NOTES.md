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
- `providers/` — anikoto, anizone (entry-point plugins).
- `resolvers/` — mp4upload, vidtube→megaplay, filemoon, streamwish, generic.
- `cli/`, `tui/` — the two adapters. Tests in `tests/{unit,contract,integration,tui}`.

## Adding a provider/resolver

Implement the `Provider`/`Resolver` port, register it as an entry point in
`pyproject.toml`, add fixtures + a live gated test, and it must pass the
registry-wide contract suite (`tests/contract/`). A bad plugin is skipped at
load, never fatal.

## Provider notes (change often — canary tracks them)

- **AllAnime** was removed (2026-07-29): its streams came from third-party embed
  hosts that are frequently geo/ISP-blocked, and its source-crypto rotated every
  few days (per-build AES key + timed `epoch` scraped live) — an unsustainable
  maintenance burden for a source that rarely played. anikoto + anizone cover it.
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
- **hianime** (hianime.at, 10/09/2026): same family as anikoto but shorter —
  `/search?keyword=` (server-rendered `flw-item` cards) → `/api/theme/episode/
  list/<id>` → `/api/theme/episode/servers?episodeId=<id>`, each server
  carrying `data-hash`, base64 of the embed URL. Note `/api/theme/…`, **not**
  `/ajax/…` — those 404 here; the routes came off the site's own player.
  Its servers split two ways: **ZokoAnime** hands out clean, plaintext HLS
  (verified: `0x47` sync bytes, `video/mp2t`, no PNG decoy — so `obfuscated`
  stays False), while HD-1/Vidstream-2 (megaplay.buzz `s-2/<id>`) answer
  `getSources` with an `enc` blob instead of `sources.file` and are left
  unresolved on purpose. Both are still emitted; the fan-out absorbs it.
  The zoko player page is a 1.6 KB shell holding `window.__P` — base64 of the
  config JSON XOR'd with a repeating key (`otaku-embed-v1` today). The
  resolver **recovers the key from the payload** via the known `{"download_url"`
  prefix rather than hardcoding it, so a rotation costs nothing.
- Title matching for both HiAnime-family providers lives in
  `providers/_matching.py`. The airing-status inversion in `rank_items` is
  load-bearing — read its docstring before touching it.

## Status

M0–M5 done. Two live providers (anikoto, anizone), circuit breakers, nightly
canary, Textual TUI, auto-skip/auto-next, ffmpeg downloads, AniList sync. Next:
M6 (docs site, plugin cookiecutter, PyPI). See `docs/architecture.md`.
