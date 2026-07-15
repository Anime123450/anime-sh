"""Bundled provider plugins.

These are discovered through the same entry-point mechanism as third-party
plugins (see ``pyproject.toml``), so the plugin path can never silently rot.
A provider knows how to find shows and episodes for a known identity; it never
returns a playable video URL — that is a resolver's job.
"""
