"""What the canary's exit code is allowed to mean.

The nightly run went red every night because anikoto is dead and cannot be
fixed — its hosts moved to an encrypted payload. So the providers badge was
permanently red over an expected casualty, which is the same failure the
Cloudflare classification already fixed once: an alert that always fires is an
alert everyone learns to ignore, and it made a project that works look broken
to anyone who landed on the README.

One provider dying is the normal operating state. The question worth gating on
is the one a user actually has — can anime-sh still play anything?
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))

import canary  # noqa: E402


def _statuses(tmp_path: pathlib.Path, **by_provider: str) -> pathlib.Path:
    """Lay out per-provider status files the way download-artifact does —
    one directory per artifact, not one flat folder."""
    root = tmp_path / "probe-results"
    for name, status in by_provider.items():
        directory = root / f"provider-status-{name}"
        directory.mkdir(parents=True)
        (directory / f"provider-status-{name}.json").write_text(
            json.dumps(
                {
                    "generated_at": "2026-09-23T03:00:00+00:00",
                    "providers": {
                        name: {
                            "status": status,
                            "detail": "probe",
                            "playable": status == "ok",
                            "latency_ms": 11,
                            "resolvable_hosts": [],
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
    return root


def _run(monkeypatch, root: pathlib.Path, out: pathlib.Path, *flags: str) -> int:
    argv = [
        "canary.py",
        "--merge", str(root / "**" / "*.json"),
        "--output", str(out),
        *flags,
    ]
    monkeypatch.setattr(sys, "argv", argv)
    return canary.main()


# -- the gate --------------------------------------------------------------- #
def test_a_dead_provider_alone_does_not_fail_the_run(tmp_path, monkeypatch):
    """Last night's actual result: anikoto unplayable, the other two fine."""
    root = _statuses(tmp_path, anizone="ok", hianime="ok", anikoto="degraded")
    assert _run(monkeypatch, root, tmp_path / "s.json", "--require-any") == 0


def test_no_provider_able_to_play_is_an_outage(tmp_path, monkeypatch):
    root = _statuses(tmp_path, anizone="fail", hianime="degraded", anikoto="degraded")
    assert _run(monkeypatch, root, tmp_path / "s.json", "--require-any") == 1


def test_a_blocked_ip_is_not_a_healthy_provider(tmp_path, monkeypatch):
    """`blocked` says the runner's IP got a Cloudflare page, which is no
    evidence the provider can serve anyone. It must not hold the gate open on
    its own."""
    root = _statuses(tmp_path, anizone="blocked", anikoto="degraded")
    out = tmp_path / "s.json"
    assert _run(monkeypatch, root, out, "--require-any") == 1


def test_checking_nothing_is_a_failure_not_a_pass(tmp_path, monkeypatch):
    """A glob that matches nothing, a renamed artifact, a matrix that filtered
    everything out — the run has stopped checking, and the one thing it must
    not do is keep reporting green."""
    empty = tmp_path / "nothing"
    empty.mkdir()
    for flags in ([], ["--require-any"]):
        assert _run(monkeypatch, empty, tmp_path / "s.json", *flags) == 1


def test_without_the_gate_one_broken_provider_still_fails(tmp_path, monkeypatch):
    """The default is still the strict question, for running it by hand."""
    root = _statuses(tmp_path, anizone="ok", anikoto="degraded")
    assert _run(monkeypatch, root, tmp_path / "s.json") == 1


# -- merging ---------------------------------------------------------------- #
def test_merge_reads_every_providers_file(tmp_path, monkeypatch):
    root = _statuses(tmp_path, anizone="ok", hianime="ok", anikoto="degraded")
    out = tmp_path / "s.json"
    _run(monkeypatch, root, out, "--require-any")
    merged = json.loads(out.read_text(encoding="utf-8"))["providers"]
    assert set(merged) == {"anizone", "hianime", "anikoto"}
    assert merged["anikoto"]["status"] == "degraded"


def test_one_unreadable_file_does_not_lose_the_others(tmp_path, monkeypatch):
    root = _statuses(tmp_path, anizone="ok", anikoto="degraded")
    truncated = root / "provider-status-hianime"
    truncated.mkdir()
    (truncated / "provider-status-hianime.json").write_text("{not json", encoding="utf-8")
    out = tmp_path / "s.json"
    assert _run(monkeypatch, root, out, "--require-any") == 0
    assert set(json.loads(out.read_text(encoding="utf-8"))["providers"]) == {
        "anizone", "anikoto",
    }


def test_the_status_file_is_written_even_on_an_outage(tmp_path, monkeypatch):
    """The client reads this artifact to pre-deprioritise dead providers, and
    an outage is exactly when it most wants to know."""
    root = _statuses(tmp_path, anizone="fail", anikoto="degraded")
    out = tmp_path / "s.json"
    assert _run(monkeypatch, root, out, "--require-any") == 1
    assert json.loads(out.read_text(encoding="utf-8"))["providers"], "no status written"
