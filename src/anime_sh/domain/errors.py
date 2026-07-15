"""Domain error hierarchy.

These are the *typed* failures the orchestration layer catches to drive the
resolver / provider fallback chains. A resolver raising :class:`ResolverError`
means "try the next host"; a provider raising :class:`ProviderError` means
"try the next provider". Neither should ever reach the user as a traceback.
"""

from __future__ import annotations


class AnimeShError(Exception):
    """Base class for everything anime-sh raises deliberately."""


class ConfigError(AnimeShError):
    """Invalid or unloadable configuration."""


class MetadataError(AnimeShError):
    """The metadata source (AniList) failed."""


class ProviderError(AnimeShError):
    """A provider failed to match, list episodes, or produce candidates."""


class ProviderTimeout(ProviderError):
    """A provider exceeded its per-call deadline. Not a hard failure."""


class ProviderUnavailable(ProviderError):
    """Provider circuit breaker is open; skip without calling."""


class ResolverError(AnimeShError):
    """A resolver could not turn a candidate into a playable stream."""


class NoStreamsFound(AnimeShError):
    """Every provider and resolver was exhausted without a playable stream."""


class PlayerError(AnimeShError):
    """The player could not be launched or crashed."""


class PlayerUnavailable(PlayerError):
    """The configured player binary is not installed / not found."""
