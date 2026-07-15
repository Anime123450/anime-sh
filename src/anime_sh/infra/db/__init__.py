"""SQLite persistence.

Two physically separate databases, by design:

* ``anime.db``  — sacred user state (progress, favorites, history, provider
  mappings). Never auto-expired, backed up, exported.
* ``cache.db``  — disposable (metadata, search, candidate lists). Safe to wipe.

``anime cache clear`` can only ever touch ``cache.db``; there is no code path
from cache maintenance to the user store.
"""
