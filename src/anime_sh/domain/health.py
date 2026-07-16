"""Circuit breaker — pure, I/O-free decision logic over ProviderHealth.

A provider that fails ``threshold`` times in a row trips OPEN for ``cooldown``
seconds; the manager stops calling it and stops paying its timeout. Once the
cooldown elapses, one *half-open* probe is allowed (derived from OPEN + elapsed
time, not a stored state): if it succeeds the breaker closes, otherwise it
re-opens. This is what delivers "the user never thinks about providers" — a dead
site is skipped instead of slowing every search.

Kept pure so it is trivially unit-testable and deterministic.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .models import BreakerState, ProviderHealth


class CircuitBreaker:
    def __init__(self, *, threshold: int = 3, cooldown_s: float = 600.0) -> None:
        self._threshold = threshold
        self._cooldown = timedelta(seconds=cooldown_s)

    def should_attempt(self, health: ProviderHealth, now: datetime) -> bool:
        """Whether to call this provider now. Closed → yes; open → only once the
        cooldown has elapsed (the half-open probe)."""
        if health.state is BreakerState.CLOSED:
            return True
        if health.opened_at is None:
            return True
        return (now - health.opened_at) >= self._cooldown

    def is_probe(self, health: ProviderHealth, now: datetime) -> bool:
        """True when this attempt is a half-open probe of an open breaker."""
        return health.state is BreakerState.OPEN and self.should_attempt(health, now)

    def record_success(self, health: ProviderHealth, now: datetime) -> ProviderHealth:
        return ProviderHealth(
            provider=health.provider,
            state=BreakerState.CLOSED,
            consecutive_failures=0,
            opened_at=None,
        )

    def record_failure(self, health: ProviderHealth, now: datetime) -> ProviderHealth:
        failures = health.consecutive_failures + 1
        if failures >= self._threshold:
            return ProviderHealth(
                provider=health.provider,
                state=BreakerState.OPEN,
                consecutive_failures=failures,
                opened_at=now,
            )
        return ProviderHealth(
            provider=health.provider,
            state=health.state,
            consecutive_failures=failures,
            opened_at=health.opened_at,
        )

    def status(self, health: ProviderHealth, now: datetime) -> str:
        """Human/UX-facing state including the derived half-open probe."""
        if health.state is BreakerState.CLOSED:
            return "closed"
        return "half-open" if self.should_attempt(health, now) else "open"

    def rank(self, health: ProviderHealth, now: datetime) -> int:
        """Ordering key (lower = try first): healthy providers before probes
        before still-open ones."""
        status = self.status(health, now)
        return {"closed": 0, "half-open": 1, "open": 2}[status]
