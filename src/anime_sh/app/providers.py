"""ProviderManager — parallel fan-out over providers, done safely.

Guarantees the rest of the app relies on:

* One slow or dead provider can never slow down or crash an operation. Every
  provider call is wrapped in a per-provider timeout + exception guard.
* Identity comes from metadata, never from a provider. Providers are consulted
  only to *attach availability* to an already-known :class:`Anime`.
* A repeatedly-failing provider trips its circuit breaker and is skipped (and
  deprioritised) until a cooldown probe succeeds — so a dead site stops costing
  every search its timeout. Breaker state is persisted, so it survives restarts.

When no :class:`HealthStore` is wired the manager behaves exactly as before
(attempt all in priority order) — the breaker is purely additive.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from ..domain.errors import ProviderError
from ..domain.health import CircuitBreaker
from ..domain.models import (
    Anime,
    Audio,
    Episode,
    ProviderHealth,
    ProviderRef,
    SourceOption,
    StreamCandidate,
)
from ..domain.ports import HealthStore, Provider

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ProviderManager:
    def __init__(
        self,
        providers: list[Provider],
        *,
        parallel: int = 5,
        match_timeout_s: float = 4.0,
        candidates_timeout_s: float = 8.0,
        health_store: HealthStore | None = None,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        # Highest priority first.
        self._providers = sorted(providers, key=lambda p: -p.priority)
        self._parallel = parallel
        self._match_timeout = match_timeout_s
        self._candidates_timeout = candidates_timeout_s
        self._health = health_store
        self._breaker = breaker or CircuitBreaker()

    @property
    def providers(self) -> list[Provider]:
        return list(self._providers)

    async def resolve_sources(
        self, anime: Anime, audio: Audio = Audio.SUB
    ) -> list[ProviderRef]:
        """Fan out to providers to map ``anime`` to provider-native refs.

        Providers whose breaker is open (and still cooling down) are skipped;
        the rest are tried healthiest-first, then by priority. A provider that
        times out, errors, or returns ``None`` contributes no ref.
        """
        if self._health is None:
            selected = self._providers[: self._parallel]
            results = await asyncio.gather(
                *(self._match_guarded(p, anime, audio) for p in selected)
            )
            return [ref for ref in results if ref is not None]

        now = _now()
        healths = {p.name: await self._health_for(p.name) for p in self._providers}
        eligible = [
            p for p in self._providers
            if self._breaker.should_attempt(healths[p.name], now)
        ]
        eligible.sort(
            key=lambda p: (self._breaker.rank(healths[p.name], now), -p.priority)
        )
        selected = eligible[: self._parallel]
        results = await asyncio.gather(
            *(
                self._match_recorded(p, anime, audio, healths[p.name], now)
                for p in selected
            )
        )
        return [ref for ref in results if ref is not None]

    async def candidates_for(self, episode: Episode) -> list[StreamCandidate]:
        """Ordered stream candidates for an episode from its owning provider."""
        provider = self._by_name(episode.provider_ref.provider)
        if provider is None:
            return []
        return await self._candidates_guarded(provider, episode)

    async def list_sources(
        self, anime: Anime, audio: Audio = Audio.SUB
    ) -> list[SourceOption]:
        """Every matching entry across all providers (each provider best-first,
        providers in priority order) — the pool the source picker shows."""
        results = await asyncio.gather(
            *(self._sources_guarded(p, anime, audio) for p in self._providers)
        )
        out: list[SourceOption] = []
        for group in results:
            out.extend(group)
        return out

    async def _sources_guarded(
        self, provider: Provider, anime: Anime, audio: Audio
    ) -> list[SourceOption]:
        finder = getattr(provider, "find_sources", None)
        try:
            async with asyncio.timeout(self._match_timeout):
                if finder is not None:
                    return await finder(anime, audio)
                ref = await provider.match(anime, audio)
                return (
                    [SourceOption(ref.provider, ref.anime_key,
                                  anime.title.preferred, anime.episode_count,
                                  ref.audio, ref.confidence)]
                    if ref else []
                )
        except (TimeoutError, ProviderError) as e:
            log.debug("provider %s find_sources failed: %s", provider.name, e)
            return []
        except Exception as e:
            log.warning("provider %s find_sources crashed: %s", provider.name, e)
            return []

    async def health_snapshot(self) -> list[dict]:
        """Per-provider breaker status, for ``anime providers health``."""
        now = _now()
        out: list[dict] = []
        for p in self._providers:
            health = await self._health_for(p.name)
            out.append(
                {
                    "provider": p.name,
                    "priority": p.priority,
                    "status": self._breaker.status(health, now),
                    "consecutive_failures": health.consecutive_failures,
                    "opened_at": health.opened_at.isoformat() if health.opened_at else None,
                }
            )
        return out

    # -- health helpers ----------------------------------------------------- #
    async def _health_for(self, name: str) -> ProviderHealth:
        if self._health is None:
            return ProviderHealth(provider=name)
        return await self._health.get(name) or ProviderHealth(provider=name)

    async def _match_recorded(
        self, provider: Provider, anime: Anime, audio: Audio,
        health: ProviderHealth, now: datetime,
    ) -> ProviderRef | None:
        ref, ok = await self._match_outcome(provider, anime, audio)
        updated = (
            self._breaker.record_success(health, now)
            if ok
            else self._breaker.record_failure(health, now)
        )
        if updated != health and self._health is not None:
            await self._health.save(updated)
        return ref

    async def _match_outcome(
        self, provider: Provider, anime: Anime, audio: Audio
    ) -> tuple[ProviderRef | None, bool]:
        """Return (ref-or-None, provider_is_healthy). A provider that responds
        without an error is healthy even when it has no match for this show;
        only timeouts/errors count against the breaker."""
        try:
            async with asyncio.timeout(self._match_timeout):
                ref = await provider.match(anime, audio)
            return ref, True
        except (TimeoutError, ProviderError) as e:
            log.debug("provider %s match failed: %s", provider.name, e)
            return None, False
        except Exception as e:  # a plugin bug must never crash a search
            log.warning("provider %s match crashed: %s", provider.name, e)
            return None, False

    # -- guards (no-health path) -------------------------------------------- #
    async def _match_guarded(
        self, provider: Provider, anime: Anime, audio: Audio
    ) -> ProviderRef | None:
        ref, _ = await self._match_outcome(provider, anime, audio)
        return ref

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
