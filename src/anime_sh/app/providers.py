"""ProviderManager — parallel fan-out over providers, done safely.

Two guarantees the rest of the app relies on:

* One slow or dead provider can never slow down or crash an operation. Every
  provider call is wrapped in a per-provider timeout + exception guard.
* Identity comes from metadata, never from a provider. Providers are consulted
  only to *attach availability* to an already-known :class:`Anime`.

Circuit breakers and health-based reordering (M3) will slot in at the two
``_guard`` call sites without changing this interface.
"""

from __future__ import annotations

import asyncio
import logging

from ..domain.errors import ProviderError
from ..domain.models import Anime, Audio, Episode, ProviderRef, StreamCandidate
from ..domain.ports import Provider

log = logging.getLogger(__name__)


class ProviderManager:
    def __init__(
        self,
        providers: list[Provider],
        *,
        parallel: int = 5,
        match_timeout_s: float = 4.0,
        candidates_timeout_s: float = 8.0,
    ) -> None:
        # Highest priority first.
        self._providers = sorted(providers, key=lambda p: -p.priority)
        self._parallel = parallel
        self._match_timeout = match_timeout_s
        self._candidates_timeout = candidates_timeout_s

    @property
    def providers(self) -> list[Provider]:
        return list(self._providers)

    async def resolve_sources(
        self, anime: Anime, audio: Audio = Audio.SUB
    ) -> list[ProviderRef]:
        """Fan out to providers to map ``anime`` to provider-native refs.

        A provider that times out, errors, or returns ``None`` simply does not
        contribute a ref. Results preserve provider-priority order.
        """
        selected = self._providers[: self._parallel]
        results = await asyncio.gather(
            *(self._match_guarded(p, anime, audio) for p in selected)
        )
        return [ref for ref in results if ref is not None]

    async def candidates_for(self, episode: Episode) -> list[StreamCandidate]:
        """Ordered stream candidates for an episode from its owning provider."""
        provider = self._by_name(episode.provider_ref.provider)
        if provider is None:
            return []
        return await self._candidates_guarded(provider, episode)

    # -- internal guards ---------------------------------------------------- #
    async def _match_guarded(
        self, provider: Provider, anime: Anime, audio: Audio
    ) -> ProviderRef | None:
        try:
            async with asyncio.timeout(self._match_timeout):
                return await provider.match(anime, audio)
        except (TimeoutError, ProviderError) as e:
            log.debug("provider %s match failed: %s", provider.name, e)
            return None
        except Exception as e:  # a plugin bug must never crash a search
            log.warning("provider %s match crashed: %s", provider.name, e)
            return None

    async def _candidates_guarded(
        self, provider: Provider, episode: Episode
    ) -> list[StreamCandidate]:
        try:
            async with asyncio.timeout(self._candidates_timeout):
                return await provider.candidates(episode)
        except (TimeoutError, ProviderError) as e:
            log.debug("provider %s candidates failed: %s", provider.name, e)
            return []
        except Exception as e:
            log.warning("provider %s candidates crashed: %s", provider.name, e)
            return []

    def _by_name(self, name: str) -> Provider | None:
        return next((p for p in self._providers if p.name == name), None)
