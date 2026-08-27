"""The container builds what a command asks for, and nothing else.

`anime continue` reads two tables out of SQLite. It used to construct the whole
object graph first — an HTTP client, a plugin scan, an mpv lookup, an AniList
tracker — because `build_container` wired everything eagerly. Measured by module
count, every command loaded the same 588 modules regardless of what it did.

Module counts rather than timings on purpose: wall-clock on a loaded developer
machine drifted by 40% between runs of an unchanged command here, which is more
than the effect being measured. What is imported is deterministic.

Subprocesses for the same reason as `test_import_cost.py` — `sys.modules` is
process-wide, so once any other test has run, an in-process assertion is
meaningless.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

# Anything that only a network- or playback-bound command should need.
HEAVY = ("httpx", "curl_cffi")


def _load(expr: str) -> tuple[set[str], int]:
    """Build a container, touch `expr`, report the heavy modules and the total."""
    code = textwrap.dedent(f"""
        import sys
        from anime_sh.cli.container import build_container
        c = build_container()
        {expr}
        # Prefixed so the line is never empty — a bare empty line is eaten
        # by .strip() below, and the *good* result is the empty one.
        print("HEAVY:" + ",".join(m for m in {HEAVY!r} if m in sys.modules))
        print("TOTAL:" + str(len(sys.modules)))
    """)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=180)
    assert out.returncode == 0, out.stderr
    heavy, total = out.stdout.strip().splitlines()
    heavy = heavy.removeprefix("HEAVY:")
    return {m for m in heavy.split(",") if m}, int(total.removeprefix("TOTAL:"))


def test_reading_the_library_does_not_open_the_network_stack():
    """`anime continue`, `history` and `stats` are pure SQLite reads."""
    heavy, _ = _load("c.library_service")
    assert not heavy, f"reading the library imported {sorted(heavy)}"


def test_listing_downloads_does_not_build_the_resolve_chain():
    """`anime downloads` lists rows from a table. It needed the playback service,
    a stream proxy and an ffmpeg downloader to do it, because DownloadService
    took them as constructed objects — they are factories now, resolved only by
    an actual download."""
    heavy, _ = _load("c.download")
    assert not heavy, f"listing downloads imported {sorted(heavy)}"


def test_a_download_still_resolves_its_dependencies():
    """The other half of the contract: deferred means deferred, not absent. A
    factory left unresolved would fail at the moment someone downloads, which is
    the worst possible time to find out."""
    code = textwrap.dedent("""
        from anime_sh.cli.container import build_container
        c = build_container()
        d = c.download
        assert d._playback is not None, "playback factory did not resolve"
        assert d._playback is c.playback, "resolved a different playback service"
        assert d._stream_proxy is c.stream_proxy, "resolved a different proxy"
        # Resolving twice must reuse, not rebuild.
        assert d._playback is d._playback
        print("ok")
    """)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=180)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"


def test_playing_still_pays_for_what_it_needs():
    """Laziness must not be mistaken for having removed anything: `anime play`
    genuinely needs the network stack, and should load it."""
    heavy, total = _load("c.playback")
    assert "httpx" in heavy, "playback lost its HTTP stack"
    assert total > 500, f"playback only loaded {total} modules — is it still wired?"


def test_closing_an_untouched_container_builds_nothing():
    """Shutdown ran over every field, so closing a container that had only read
    the library would *construct* an HTTP probe and start a proxy thread purely
    in order to close them."""
    code = textwrap.dedent("""
        import asyncio, sys
        from anime_sh.cli.container import build_container
        c = build_container()
        c.library_service
        asyncio.run(c.aclose())
        assert "httpx" not in sys.modules, "aclose built the network stack"
        asyncio.run(c.aclose())  # twice must stay harmless
        print("ok")
    """)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=180)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"
