# Contributing to anime-sh

Thanks for looking. This file covers the things that are easy to get wrong and
hard to guess.

## Setup

```bash
uv sync --extra dev --extra tui
uv run pytest -q            # unit + contract + TUI tests, no network
uv run lint-imports         # architecture contracts — must stay green
```

Tests never touch the network. The gated live tests do:

```bash
ANIME_SH_LIVE=1 uv run pytest tests/integration
uv run python scripts/canary.py --provider anikoto   # probe a real provider
```

## The rules that actually matter

**The layering is enforced, not aspirational.** `cli > tui > app > domain`, with
`infra` / `providers` / `resolvers` as adapters behind `domain.ports`.
`import-linter` fails the build if you cross a boundary:

- `domain/` imports nothing else in the package — pure models, ports and logic.
- `app/` imports only `domain`, and reaches the outside world exclusively
  through `domain.ports`. If a service needs HTTP or SQLite, it takes a port.
- `cli/container.py` is the single composition root — the one place concretes
  are wired to services. The TUI receives its services from there and never
  imports `cli`.

**AniList ids are the identity spine.** Every show is keyed by its AniList id.
Providers are *sources attached to a known identity*, never the source of
identity. Getting this wrong is not theoretical: a sequel once got matched as a
source for its own prequel, so episodes played from season 2 while progress was
recorded against season 1 — and every visible symptom (wrong row in Continue
Watching, "4/12 episodes available", episodes of a finished season appearing
unreleased) came from that single mismatch.

**A failing plugin must never be fatal.** Providers and resolvers are
third-party code. Anything that fails to import, raises in its constructor, or
targets the wrong `api_version` is skipped with a warning.

## Adding a provider or resolver

1. Implement the `Provider` / `Resolver` port in `domain/ports.py` — including
   `aclose()` if you build your own HTTP client, or you leak it.
2. Register it as an entry point in `pyproject.toml`.
3. Add fixtures plus a gated live test.
4. It must pass the registry-wide contract suite in `tests/contract/`, which is
   parametrized over every installed plugin, yours included.

See `docs/plugins.md` for the full walkthrough and `docs/architecture.md` for
the design.

## Style

Match the surrounding code. Comments explain *why*, especially where the
non-obvious choice was forced by something real — a provider quirk, a platform
rule, a bug that bit us. Those comments are why the next person doesn't
reintroduce the bug.

Tests are named after the behaviour they protect and carry a docstring saying
what breaks without them.

## Pull requests

Keep them focused, explain the reasoning, and make sure `pytest` and
`lint-imports` pass. If you fixed a bug, add the regression test that would have
caught it.
