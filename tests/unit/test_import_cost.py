"""The layering has to hold at runtime, not only on paper.

`import-linter` checks *declared* imports inside each layer, and those were
clean. What it cannot see is `anime_sh/__init__.py`, which runs on any access to
the package: a `from .cli.main import main` there meant that importing
`anime_sh.domain.models` — the layer whose entire contract is that it depends on
nothing — built the whole CLI. typer, pydantic, rich, httpx, 531 modules and
672 ms, to read a dataclass, while the architecture contract reported three
contracts kept.

These run in a subprocess deliberately. `sys.modules` is process-wide, so by the
time any other test has run, half the codebase is already imported and an
in-process assertion would be meaningless.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

# Heavy third-party packages the pure layers have no business loading.
FORBIDDEN = ("typer", "pydantic", "httpx", "rich", "curl_cffi", "textual")


def _import_in_subprocess(module: str) -> set[str]:
    """Import `module` in a clean interpreter, return the third-party packages
    it dragged in."""
    code = textwrap.dedent(f"""
        import sys
        import {module}
        loaded = [n for n in {FORBIDDEN!r} if n in sys.modules]
        print(",".join(loaded))
    """)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return {n for n in out.stdout.strip().split(",") if n}


def test_importing_the_domain_layer_does_not_build_the_cli():
    """The regression test for the 672 ms dataclass.

    Reverting `anime_sh/__init__.py` to import `main` eagerly fails this with
    every name in FORBIDDEN except curl_cffi.
    """
    pulled = _import_in_subprocess("anime_sh.domain.models")
    assert not pulled, (
        f"importing the domain layer loaded {sorted(pulled)} — "
        f"anime_sh/__init__.py is eagerly importing something again"
    )


def test_importing_the_package_root_stays_cheap():
    """`import anime_sh` on its own should commit to nothing. Anything that
    needs the CLI asks for it by name."""
    pulled = _import_in_subprocess("anime_sh")
    assert not pulled, f"`import anime_sh` loaded {sorted(pulled)}"


def test_the_public_names_still_resolve():
    """The laziness must be invisible: both spellings that worked before have to
    keep working, or this is an API break rather than an optimisation."""
    code = textwrap.dedent("""
        import anime_sh
        from anime_sh import main
        assert callable(main), "from anime_sh import main"
        assert isinstance(anime_sh.__version__, str), "anime_sh.__version__"
        assert anime_sh.__version__, "version is empty"
        assert {"main", "__version__"} <= set(dir(anime_sh)), "dir() lost the names"
        try:
            anime_sh.nonexistent
        except AttributeError:
            pass
        else:
            raise AssertionError("a missing attribute must still raise AttributeError")
        print("ok")
    """)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"
