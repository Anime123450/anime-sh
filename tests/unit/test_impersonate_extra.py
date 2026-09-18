"""curl-cffi is an extra, and its absence must be a sentence, not a traceback.

It is a C extension that fetches a libcurl-impersonate binary while it builds —
which is weight in every install, and the single hardest thing to package for
Homebrew and the AUR. Nothing bundled here asks for impersonation:
``HttpClient(impersonate=...)`` is the only way in, and no provider passes it.
"""

from __future__ import annotations

import builtins

import pytest

from anime_sh.infra.http.client import HttpClient, HttpError


async def test_the_normal_path_never_touches_curl_cffi(monkeypatch):
    """Every bundled provider goes through httpx, so a machine without
    curl-cffi installed must be completely unaffected."""
    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name.startswith("curl_cffi"):
            raise AssertionError("curl_cffi must not be imported on the httpx path")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)

    async def fake_send(method, url, params, json, headers):
        return 200, '{"ok": true}', None

    client = HttpClient(retries=0)
    monkeypatch.setattr(client, "_send_httpx", fake_send)
    assert await client.get_json("https://example.test/x") == {"ok": True}


async def test_asking_for_impersonation_without_the_extra_says_what_to_install(
    monkeypatch,
):
    real_import = builtins.__import__

    def missing(name, *args, **kwargs):
        if name.startswith("curl_cffi"):
            raise ImportError("No module named 'curl_cffi'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)

    client = HttpClient(impersonate="chrome", retries=0)
    with pytest.raises(HttpError) as ei:
        await client.get_text("https://example.test/x")
    message = str(ei.value)
    assert "curl-cffi" in message
    assert "anime-sh[impersonate]" in message, "say the command, not just the cause"


def test_the_extra_is_declared():
    """A message telling people to install `anime-sh[impersonate]` is only
    useful if that extra exists."""
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]
    assert any("curl-cffi" in dep for dep in extras["impersonate"])
    # And it must not have crept back into the always-installed set.
    assert not any("curl-cffi" in dep for dep in data["project"]["dependencies"])
