"""Contract tests every plugin must pass — the guarantee that providers and
resolvers are actually interchangeable.

Parametrized over the live registry, so a newly-installed plugin (bundled or
third-party) is held to the same structural contract automatically. No network:
plugins construct their HTTP clients lazily, so instantiation is offline.
"""

from __future__ import annotations

import inspect

import pytest

from anime_sh.domain.ports import API_VERSION, Provider, Resolver
from anime_sh.infra import registry

PROVIDERS = registry.load_providers()
RESOLVERS = registry.load_resolvers()


def _ids(objs):
    return [getattr(o, "name", type(o).__name__) for o in objs]


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("provider", PROVIDERS, ids=_ids(PROVIDERS))
def test_provider_conforms(provider):
    assert isinstance(provider.name, str) and provider.name
    assert isinstance(provider.priority, int)
    assert provider.api_version == API_VERSION
    assert isinstance(provider, Provider)  # structural (runtime_checkable)

    for method in ("match", "find_sources", "episodes", "candidates"):
        fn = getattr(provider, method)
        assert inspect.iscoroutinefunction(fn), f"{provider.name}.{method} must be async"

    # arity: match(anime, audio), episodes(ref, anime_id), candidates(episode)
    assert _param_count(provider.match) == 2
    assert _param_count(provider.find_sources) == 2
    assert _param_count(provider.episodes) == 2
    assert _param_count(provider.candidates) == 1


@pytest.mark.parametrize("provider", PROVIDERS, ids=_ids(PROVIDERS))
def test_provider_names_unique(provider):
    names = [p.name for p in PROVIDERS]
    assert names.count(provider.name) == 1, f"duplicate provider name {provider.name!r}"


# --------------------------------------------------------------------------- #
# Resolvers
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("resolver", RESOLVERS, ids=_ids(RESOLVERS))
def test_resolver_conforms(resolver):
    assert isinstance(resolver.name, str) and resolver.name
    assert resolver.api_version == API_VERSION
    assert isinstance(resolver, Resolver)

    assert not inspect.iscoroutinefunction(resolver.handles), "handles must be sync"
    assert inspect.iscoroutinefunction(resolver.resolve), "resolve must be async"
    assert _param_count(resolver.handles) == 1
    assert _param_count(resolver.resolve) == 1


def test_at_least_the_bundled_plugins_load():
    assert {"anizone", "anikoto"} <= {p.name for p in PROVIDERS}
    assert {"megaplay", "generic"} <= {r.name for r in RESOLVERS}


def _param_count(fn) -> int:
    """Positional params excluding self/bound (bound methods drop self)."""
    sig = inspect.signature(fn)
    return sum(
        1
        for p in sig.parameters.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    )


# --------------------------------------------------------------------------- #
# Shutdown
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "plugin", [*PROVIDERS, *RESOLVERS], ids=_ids([*PROVIDERS, *RESOLVERS])
)
async def test_plugin_owning_an_http_client_can_be_closed(plugin):
    """A plugin that builds its own HTTP client must be able to release it.

    The container closes every provider and resolver on shutdown. Before that,
    each run leaked these clients — six of them — and printed "unclosed client"
    noise on exit.
    """
    if getattr(plugin, "_http", None) is None:
        pytest.skip("holds no HTTP client of its own")
    assert hasattr(plugin, "aclose"), "owns an HTTP client but exposes no aclose()"
    await plugin.aclose()
    await plugin.aclose()  # closing twice must stay harmless
