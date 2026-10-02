"""`anime doctor` has to report what the breaker already knows.

The `providers` check says which plugins *load*. That is a different question
from whether any of them work, and on 02/10/2026 the difference was the whole
story: doctor printed `[OK] providers: anikoto, hianime` while anikoto's breaker
sat half-open on three straight failures and the provider could not play a single
episode, because every host it offers had moved to an encrypted payload.

Doctor reporting OK about something that does not work is the same defect two
other checks in this file's module were fixed for the same day. It is also free
to avoid: the breaker state is already written down, so no site is contacted.
"""

from __future__ import annotations

import anime_sh.cli.doctor as doctor


class _Container:
    def __init__(self, snapshot):
        self.provider_manager = _Manager(snapshot)
        self.closed = False

    async def aclose(self):
        self.closed = True


class _Manager:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    async def health_snapshot(self):
        return self._snapshot


def _row(provider, status, failures=0, priority=50):
    return {
        "provider": provider,
        "priority": priority,
        "status": status,
        "consecutive_failures": failures,
        "opened_at": None,
    }


def _wire(monkeypatch, snapshot):
    """Hand doctor a container with this breaker state. Returns it, so a test can
    check doctor closed it -- a doctor that leaks a database connection is a
    diagnostic that creates its own symptom."""
    container = _Container(snapshot)
    monkeypatch.setattr(
        "anime_sh.cli.container.build_container", lambda *a, **k: container
    )
    return container


async def test_a_half_open_provider_is_named_rather_than_reported_healthy(monkeypatch):
    container = _wire(monkeypatch, [
        _row("hianime", "closed", priority=80),
        _row("anikoto", "half-open", failures=3, priority=70),
    ])

    check = await doctor._check_provider_health()

    assert "anikoto" in check.detail
    assert "half-open" in check.detail
    assert "3 failures" in check.detail
    assert container.closed, "doctor must close what it opened"


async def test_one_dead_provider_is_not_a_fault_when_another_works(monkeypatch):
    """A provider dying is the normal operating state here, and the fan-out
    routes around it. Failing the command for that would make `doctor` red
    permanently, which is how a check stops being read."""
    _wire(monkeypatch, [
        _row("hianime", "closed"),
        _row("anikoto", "open", failures=9),
    ])

    check = await doctor._check_provider_health()

    assert check.ok
    assert "hianime" in check.detail, "say what is left that does work"


async def test_nothing_working_is_a_fault(monkeypatch):
    """The case worth failing on: no provider is known to work, so the next play
    has nowhere to go. That is not flakiness, it is an install that cannot watch
    anything."""
    _wire(monkeypatch, [
        _row("hianime", "open", failures=5),
        _row("anikoto", "half-open", failures=3),
    ])

    check = await doctor._check_provider_health()

    assert not check.ok
    assert "no provider" in check.detail
    assert "--streams" in check.detail, "point at the check that tries for real"


async def test_all_healthy_says_so_without_listing_a_problem(monkeypatch):
    _wire(monkeypatch, [_row("hianime", "closed"), _row("anikoto", "closed")])

    check = await doctor._check_provider_health()

    assert check.ok
    assert "all healthy" in check.detail
    assert "half-open" not in check.detail and "open" not in check.detail


async def test_a_container_that_will_not_build_is_not_a_verdict(monkeypatch):
    """The trap the cover-art check already fell into once: reporting "providers
    are failing" when the check never got far enough to ask. A fresh install with
    no database yet must not be told its providers are broken."""
    def _boom(*a, **k):
        raise RuntimeError("no database yet")

    monkeypatch.setattr("anime_sh.cli.container.build_container", _boom)

    check = await doctor._check_provider_health()

    assert check.ok, "this is a fact about the check, not about the providers"
    assert "not recorded yet" in check.detail
    assert "failure" not in check.detail


async def test_no_providers_installed_is_left_to_the_providers_check(monkeypatch):
    """`providers` already fails with "none installed — anime-sh cannot find
    episodes", so saying it twice would just be noise under a second heading."""
    _wire(monkeypatch, [])

    check = await doctor._check_provider_health()

    assert check.ok
    assert "no providers installed" in check.detail
