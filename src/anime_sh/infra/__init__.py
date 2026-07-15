"""Infrastructure adapters: concrete implementations of the domain ports.

HTTP, SQLite, AniList, players, plugin discovery. Nothing in `app` or `domain`
may import from here — the composition root (CLI) wires these in.
"""
