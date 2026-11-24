"""Blocking ``fetch(url, headers) -> (status, text)`` for the decryptors.

The crypto modules are synchronous pure functions over a fetch callback (that is
what keeps them testable from fixtures); resolvers run them in a worker thread.
"""

from __future__ import annotations

import urllib.error
import urllib.request

AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0"


def urllib_fetch(url: str, headers: dict) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
