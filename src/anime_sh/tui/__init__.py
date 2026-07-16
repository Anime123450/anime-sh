"""Textual TUI — the second adapter onto the app services (bare ``anime``).

Like the CLI, this layer holds no domain logic: screens call app services and
render domain models. It receives its services by injection from the
composition root, so it never imports the CLI or a concrete infra adapter.
"""

from .app import AnimeShApp, TuiServices, run_tui

__all__ = ["AnimeShApp", "TuiServices", "run_tui"]
