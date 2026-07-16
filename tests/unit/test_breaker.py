"""Circuit breaker: pure state-machine logic + persisted health round-trip +
ProviderManager gating/ordering/recording."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from anime_sh.app.providers import ProviderManager
from anime_sh.domain.health import CircuitBreaker
from anime_sh.domain.models import Audio, BreakerState, ProviderHealth
from anime_sh.infra.db.database import Database
from anime_sh.infra.db.health import SqliteHealthStore

from .fakes import FakeProvider, make_anime

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


# -- pure state machine ----------------------------------------------------- #
def test_closed_attempts_and_stays_closed_on_success():
    b = CircuitBreaker(threshold=3)
    h = ProviderHealth(provider="p")
    assert b.should_attempt(h, NOW)
    h2 = b.record_success(h, NOW)
    assert h2.state is BreakerState.CLOSED and h2.consecutive_failures == 0


def test_trips_open_after_threshold():
    b = CircuitBreaker(threshold=3)
    h = ProviderHealth(provider="p")
    for _ in range(2):
        h = b.record_failure(h, NOW)
        assert h.state is BreakerState.CLOSED
    h = b.record_failure(h, NOW)
    assert h.state is BreakerState.OPEN
    assert h.opened_at == NOW
    assert not b.should_attempt(h, NOW)  # cooling down


def test_open_allows_probe_after_cooldown():
    b = CircuitBreaker(threshold=1, cooldown_s=600)
    h = b.record_failure(ProviderHealth(provider="p"), NOW)
    assert h.state is BreakerState.OPEN
    assert not b.should_attempt(h, NOW + timedelta(seconds=300))
    later = NOW + timedelta(seconds=601)
    assert b.should_attempt(h, later)  # half-open probe
    assert b.is_probe(h, later)
    assert b.status(h, later) == "half-open"
    # a successful probe closes it
    assert b.record_success(h, later).state is BreakerState.CLOSED


def test_rank_orders_healthy_first():
    b = CircuitBreaker(threshold=1, cooldown_s=600)
    closed = ProviderHealth(provider="a")
    open_h = b.record_failure(ProviderHealth(provider="b"), NOW)
    assert b.rank(closed, NOW) < b.rank(open_h, NOW)


# -- persistence ------------------------------------------------------------ #
@pytest.fixture
async def store(tmp_path: Path):
    db = Database(tmp_path / "anime.db", migrations_dir="migrations")
    await db.connect()
    yield SqliteHealthStore(db)
    await db.close()


async def test_health_round_trip(store):
    assert await store.get("allanime") is None
    h = ProviderHealth("allanime", BreakerState.OPEN, 4, NOW)
    await store.save(h)
    got = await store.get("allanime")
    assert got == h
    # upsert
    await store.save(ProviderHealth("allanime"))
    assert (await store.get("allanime")).state is BreakerState.CLOSED


# -- manager integration ---------------------------------------------------- #
class MemHealthStore:
    def __init__(self):
        self.data: dict[str, ProviderHealth] = {}
        self.saves = 0

    async def get(self, provider):
        return self.data.get(provider)

    async def save(self, health):
        self.data[health.provider] = health
        self.saves += 1

    async def all(self):
        return list(self.data.values())


async def test_manager_records_failure_and_opens_after_threshold():
    store = MemHealthStore()
    provider = FakeProvider("boom", priority=10, raise_on="match")
    mgr = ProviderManager(
        [provider], match_timeout_s=1, health_store=store,
        breaker=CircuitBreaker(threshold=2),
    )
    await mgr.resolve_sources(make_anime())  # failure 1
    assert store.data["boom"].state is BreakerState.CLOSED
    await mgr.resolve_sources(make_anime())  # failure 2 -> open
    assert store.data["boom"].state is BreakerState.OPEN


async def test_manager_skips_open_provider():
    store = MemHealthStore()
    # Pre-open the breaker within cooldown.
    store.data["dead"] = ProviderHealth(
        "dead", BreakerState.OPEN, 5,
        datetime.now(timezone.utc),
    )
    dead = FakeProvider("dead", priority=20, raise_on="match")
    alive = FakeProvider("alive", priority=10)
    mgr = ProviderManager([dead, alive], match_timeout_s=1, health_store=store)
    refs = await mgr.resolve_sources(make_anime())
    # 'dead' is skipped entirely; only 'alive' contributes.
    assert [r.provider for r in refs] == ["alive"]
    assert "match" not in dead.calls  # never called


async def test_manager_success_resets_failures():
    store = MemHealthStore()
    store.data["p"] = ProviderHealth("p", BreakerState.CLOSED, 1, None)
    mgr = ProviderManager([FakeProvider("p")], health_store=store)
    await mgr.resolve_sources(make_anime())
    assert store.data["p"].consecutive_failures == 0
