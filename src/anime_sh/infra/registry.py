"""Plugin discovery via Python entry points.

`pip install anime-provider-foo` → restart → it works. Bundled providers and
third-party ones use the exact same mechanism, so the plugin path can never
silently rot.

Three invariants:

1. A bad plugin can never crash the app — load failures are logged and skipped.
2. The interface is versioned; a plugin built against a different
   ``api_version`` is refused with a clear message rather than half-loaded.
3. Discovery is pure of policy: the caller decides what to disable.
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points
from typing import Iterable, TypeVar

from ..domain.ports import API_VERSION, Provider, Resolver

log = logging.getLogger(__name__)

PROVIDER_GROUP = "anime_sh.providers"
RESOLVER_GROUP = "anime_sh.resolvers"

T = TypeVar("T")


def _load_group(
    group: str, *, disabled: Iterable[str] = (), **kwargs
) -> list:
    disabled_set = set(disabled)
    loaded: list = []
    for ep in entry_points(group=group):
        if ep.name in disabled_set:
            log.debug("plugin %s disabled by config", ep.name)
            continue
        try:
            cls = ep.load()
            instance = cls(**kwargs) if kwargs else cls()
        except Exception as e:  # import error, bad signature, anything
            log.warning("plugin %s failed to load: %s", ep.name, e)
            continue

        declared = getattr(instance, "api_version", None)
        if declared != API_VERSION:
            log.warning(
                "plugin %s targets api_version %s but this build is %s; skipping",
                ep.name,
                declared,
                API_VERSION,
            )
            continue
        loaded.append(instance)
    return loaded


def load_providers(*, disabled: Iterable[str] = (), **kwargs) -> list[Provider]:
    providers = _load_group(PROVIDER_GROUP, disabled=disabled, **kwargs)
    return sorted(providers, key=lambda p: -getattr(p, "priority", 0))


def load_resolvers(*, disabled: Iterable[str] = (), **kwargs) -> list[Resolver]:
    return _load_group(RESOLVER_GROUP, disabled=disabled, **kwargs)
