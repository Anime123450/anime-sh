"""Commands driven through Typer, the way a person runs them.

Every other CLI test calls the inner coroutine directly — `asyncio.run(_trending(...))`
— which skips the command function Typer actually invokes. That left the wrapper
itself untested, and it is not a trivial layer: deferring `asyncio` to keep
`anime version` fast routed all 32 command bodies through one `_run` helper, and
a bad edit made that helper call *itself*.

Every command that does any work recursed until the stack blew. The full suite
passed: 416 tests, nothing red, because not one of them entered a command
through Typer.

These do.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from anime_sh.cli import main as cli_main
from anime_sh.cli.main import app

from .fakes import make_anime

runner = CliRunner()


class _Metadata:
    async def trending(self, *, limit=30):
        return [make_anime(1, "Frieren: Beyond Journey's End"),
                make_anime(2, "BOCCHI THE ROCK!")]

    async def seasonal(self, season, year):
        return [make_anime(3, "New Show")]


class _Container:
    def __init__(self):
        self.metadata = _Metadata()
        self.closed = False

    async def aclose(self):
        self.closed = True


@pytest.fixture
def container(monkeypatch):
    fake = _Container()
    monkeypatch.setattr(cli_main, "build_container", lambda *a, **k: fake)
    return fake


def test_a_command_that_runs_a_coroutine_actually_completes(container):
    """The regression test for the recursing `_run`.

    Any command doing real work would do, because they all funnel through the
    same helper — which is exactly why one broken helper took out the entire CLI
    while the unit tests stayed green.
    """
    result = runner.invoke(app, ["trending", "--limit", "2"])

    assert result.exit_code == 0, result.output
    assert "Frieren" in result.output
    assert container.closed, "the container was left open"


def test_the_container_is_closed_even_when_a_command_fails(container):
    """`aclose` releases HTTP clients and the database handle. A command that
    exits non-zero still has to give them back."""

    class _Boom:
        async def trending(self, *, limit=30):
            raise RuntimeError("provider exploded")

    container.metadata = _Boom()
    runner.invoke(app, ["trending"])

    assert container.closed


def test_version_prints_a_version_without_building_anything():
    """`version` reads `__version__` through a deferred import now. If that
    lookup breaks, this is the only thing that notices."""
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0, result.output
    assert result.output.strip().startswith("anime-sh ")
    assert any(ch.isdigit() for ch in result.output)


def test_the_version_flag_agrees_with_the_version_command():
    """Two code paths read the version; they used to share a module global and
    now share a function, so it is worth asserting they still agree."""
    flag = runner.invoke(app, ["--version"])
    command = runner.invoke(app, ["version"])

    assert flag.exit_code == 0, flag.output
    assert flag.output.strip() == command.output.strip()


def test_providers_ls_resolves_its_deferred_registry_import():
    """`registry` moved to a function-local import. A typo there would surface
    only when someone ran the command."""
    result = runner.invoke(app, ["providers", "ls"])

    assert result.exit_code == 0, result.output
    assert "anizone" in result.output or "anikoto" in result.output


def test_help_still_lists_the_commands():
    """Deferring imports must not cost a command its registration — Typer builds
    help from the decorated functions, and those all still have to exist."""
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in ("play", "search", "download", "trending", "providers"):
        assert command in result.output, f"{command} vanished from --help"
