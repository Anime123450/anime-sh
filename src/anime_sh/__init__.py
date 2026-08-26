"""anime-sh — the terminal-native anime client.

Layered architecture: cli/tui → app (services) → domain (models + ports) with
infra/providers/resolvers as swappable adapters behind the ports. See
``docs/architecture.md``.

Nothing is imported eagerly here, and that is load-bearing rather than tidiness.
This module runs on *any* access to the package, so a plain
``from .cli.main import main`` at the top meant that importing
``anime_sh.domain.models`` — the layer whose entire contract is that it depends
on nothing — built the whole CLI: typer, pydantic, rich, httpx, 531 modules and
672 ms, to read a dataclass. ``import-linter`` cannot catch that, because the
declared import lives here rather than in ``domain/``, so the architecture
contract passed while the runtime told a different story.

``__version__`` is deferred for the same reason: ``importlib.metadata`` costs
around 240 ms by itself, and most imports of this package never read a version.

PEP 562 keeps both spellings working — ``from anime_sh import main`` and
``anime_sh.__version__`` behave exactly as before; they just pay for what they
name, at the moment they name it.
"""

from typing import Any

__all__ = ["main", "__version__"]


def __getattr__(name: str) -> Any:
    if name == "__version__":
        from importlib.metadata import PackageNotFoundError, version

        try:
            # Single source of truth: the version declared in the installed dist.
            return version("anime-sh")
        except PackageNotFoundError:
            # Running from a raw checkout that was never installed.
            return "0.0.0+unknown"
    if name == "main":
        from .cli.main import main

        return main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    # Without this, `dir()` and tab-completion lose the lazy names entirely.
    return sorted({*globals(), *__all__})
