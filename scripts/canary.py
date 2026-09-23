"""Provider canary — hits real providers and reports which still work.

For each installed provider it runs the full read path (match → episodes →
candidates) against a known title, resolves a candidate, and fetches what that
resolves to. Writes ``provider-status.json`` and exits non-zero when a provider
is ``fail`` (candidates empty / errored) or ``degraded``.

``degraded`` means the read path works but nothing watchable came out of it:
every host failed to resolve, no installed resolver handles the hosts on offer,
or a stream resolved and then would not load. That used to be reported as
healthy on the grounds that hosts are flaky and that is the resolver chain's
problem — until anikoto spent five days offering episodes it could not play and
every morning's run said "All checked providers healthy". One flaky host among
several is noise; nothing playable at all is the outage this exists to catch.

A Cloudflare challenge is still ``blocked``, not a failure: it is a property of
the IP the check ran from, not of the provider.

Exit codes say which question you asked. By default a single unhealthy provider
is a failure, which is what you want at a terminal. ``--require-any`` asks the
other question — "can anime-sh still play anything at all?" — and fails only
when no provider is ``ok``. That is the one CI gates on, because a provider
dying is the normal operating state here and a badge that is permanently red
over an expected casualty is a badge everyone learns to ignore.

Run locally:   uv run python scripts/canary.py
One provider:  uv run python scripts/canary.py --provider anikoto
Merge in CI:   uv run python scripts/canary.py --merge "artifacts/**/*.json" --require-any
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import sys
import time
from datetime import datetime, timezone

from anime_sh.domain.models import Audio
from anime_sh.infra import registry
from anime_sh.infra.http import CloudflareChallenge, HttpClient, HttpError
from anime_sh.infra.metadata import AniListMetadata

# A title each provider should carry. Extend as providers are added.
CHECK_TITLE = "Frieren"


async def _playlist_serves(stream) -> str | None:
    """``None`` if the stream actually loads, else why it did not.

    A resolver returning a URL is not the same as a stream a user can watch.
    On 17/09/2026 hianime resolved perfectly while every one of its playlists
    answered HTTP 522 — the CDN behind it was down — and the canary called that
    provider "playable" because it had a string in its hand. One request is a
    small price for the difference between a URL and a stream.
    """
    http = HttpClient(headers=dict(stream.headers), retries=0)
    try:
        body = await http.get_text(stream.url)
    except HttpError as e:
        # The status is the actionable part, and a URL this long buries it.
        # 522 is Cloudflare failing to reach the origin — the stream host is
        # down, which is a different problem from a 403 aimed at us.
        return f"HTTP {e.status}" if e.status else f"unreachable ({type(e).__name__})"
    except Exception as e:
        return f"{type(e).__name__}"
    finally:
        await http.aclose()
    if ".m3u8" in stream.url and "#EXTM3U" not in body[:64]:
        return "not a playlist"
    return None


async def check_provider(name, provider, metadata, resolvers) -> dict:
    started = time.monotonic()
    result = {
        "status": "unknown", "detail": "", "playable": False,
        "candidates": 0, "resolvable_hosts": 0,
    }
    try:
        # The identity lookup is a *precondition* of the probe, not part of it.
        # Failing it means the provider was never contacted, so it cannot be a
        # verdict on the provider — and reporting one anyway is not a harmless
        # over-report: on 07/09/2026 AniList disabled its own public API and this
        # canary filed "anikoto provider is failing" and "anizone provider is
        # failing" against a repo whose providers were both fine.
        try:
            hits = await metadata.search(CHECK_TITLE, limit=1)
            if not hits:
                raise RuntimeError("metadata search returned nothing")
        except Exception as e:
            result.update(
                status="blocked",
                detail=f"metadata unavailable, provider not reached: {e}",
            )
            return _timed(result, started)
        anime = hits[0]

        ref = await provider.match(anime, Audio.SUB)
        if ref is None:
            result.update(status="fail", detail=f"no match for {CHECK_TITLE!r}")
            return _timed(result, started)

        episodes = await provider.episodes(ref, anime.id)
        if not episodes:
            result.update(status="fail", detail="no episodes")
            return _timed(result, started)

        candidates = await provider.candidates(episodes[0])
        result["candidates"] = len(candidates)
        if not candidates:
            result.update(status="fail", detail="no stream candidates")
            return _timed(result, started)

        # Can any host actually resolve, and does what it resolves to load?
        # That is the difference between a provider that works and one that
        # merely answers.
        tried = 0
        resolved = False
        dead_stream = ""
        for cand in candidates:
            resolver = next((r for r in resolvers if r.handles(cand)), None)
            if resolver is None:
                continue
            tried += 1
            try:
                streams = await resolver.resolve(cand)
            except Exception:
                continue
            if not streams:
                continue
            resolved = True
            why = await _playlist_serves(streams[0])
            if why is None:
                result["playable"] = True
                break
            dead_stream = why
        result["resolvable_hosts"] = tried

        detail = f"{len(episodes)} eps, {len(candidates)} hosts"
        if result["playable"]:
            result.update(status="ok", detail=detail)
        elif resolved:
            # Resolved fine, but nothing came back down the wire. The provider's
            # own protocol is healthy; the stream host behind it is not.
            result.update(
                status="degraded",
                detail=f"{detail}, resolved but the stream did not load ({dead_stream})",
            )
        elif tried == 0:
            # Every host is one no installed resolver claims. Not flakiness: the
            # provider has moved to hosts this build cannot read at all.
            result.update(
                status="degraded",
                detail=f"{detail}, no resolver handles any of them",
            )
        else:
            # This was reported as `ok` with "(hosts flaky)". One failing host
            # among several is flakiness; *every* host failing is a provider
            # nobody can watch. anikoto sat in exactly this state from
            # 13/09/2026 — its megaplay hosts moved to an encrypted payload —
            # and the canary said "All checked providers healthy" for five
            # mornings while nothing it offered could be played.
            result.update(
                status="degraded",
                detail=f"{detail}, 0 of {tried} resolved — nothing playable",
            )
    except Exception as e:
        if _is_cloudflare(e):
            # A datacenter IP meeting a Cloudflare interstitial says nothing
            # about the provider's protocol: anizone answers home connections
            # fine and blocks CI runners. Reporting that as "broken" every night
            # files an issue nobody can act on and trains everyone to ignore the
            # canary — which costs us the one alert that would have mattered.
            result.update(status="blocked", detail=f"Cloudflare challenge from this IP: {e}")
        else:
            result.update(status="fail", detail=f"{type(e).__name__}: {e}")
    return _timed(result, started)


def _is_cloudflare(exc: BaseException) -> bool:
    """True if a Cloudflare challenge caused this, however it was re-raised.

    Providers wrap transport errors in ProviderUnavailable, so the type alone is
    not enough — walk the cause chain, then fall back to the message.
    """
    seen: BaseException | None = exc
    while seen is not None:
        if isinstance(seen, CloudflareChallenge):
            return True
        seen = seen.__cause__ or seen.__context__
    return "cloudflare challenge" in str(exc).lower()


def _timed(result: dict, started: float) -> dict:
    result["latency_ms"] = round((time.monotonic() - started) * 1000)
    result["checked_at"] = datetime.now(timezone.utc).isoformat()
    return result


async def run(only: str | None) -> dict:
    metadata = AniListMetadata()
    providers = registry.load_providers()
    resolvers = registry.load_resolvers()
    if only:
        providers = [p for p in providers if p.name == only]

    report: dict[str, dict] = {}
    try:
        for provider in providers:
            report[provider.name] = await check_provider(
                provider.name, provider, metadata, resolvers
            )
    finally:
        # Providers and resolvers each own an HTTP client too; only metadata was
        # being closed. The process exits straight afterwards so nothing leaked
        # for long, but an unclosed session is the kind of thing that prints a
        # warning into a log someone is trying to read a verdict out of.
        await _close_all([metadata, *providers, *resolvers])
    return report


async def _close_all(things) -> None:
    for thing in things:
        close = getattr(thing, "aclose", None)
        if close is None:
            continue  # a plugin need not hold anything that closes
        try:
            await close()
        except Exception as e:  # never let cleanup lose the report
            print(f"  (closing {thing!r} failed: {e})", file=sys.stderr)


def _merge(pattern: str) -> dict:
    """Combine per-provider status files written by the probe matrix.

    The matrix has already hit every site once. Probing a second time to build
    the aggregate doubled the requests these providers get from us each night,
    and let a flake in the second pass contradict the run that actually
    reported."""
    report: dict = {}
    for path in sorted(glob.glob(pattern, recursive=True)):
        try:
            with open(path, encoding="utf-8") as f:
                report.update(json.load(f).get("providers") or {})
        except (OSError, ValueError) as e:
            print(f"  (skipping unreadable {path}: {e})", file=sys.stderr)
    if not report:
        print(f"no status files matched {pattern!r}", file=sys.stderr)
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", help="check only this provider")
    ap.add_argument("--output", default="provider-status.json")
    ap.add_argument(
        "--merge",
        help="glob of per-provider status files to combine instead of probing "
             "again; the matrix already hit every site once",
    )
    ap.add_argument(
        "--require-any",
        action="store_true",
        help="fail only when no provider is healthy, rather than when any is "
             "unhealthy",
    )
    args = ap.parse_args()

    report = _merge(args.merge) if args.merge else asyncio.run(run(args.provider))
    if not report:
        # Zero providers checked is not zero providers broken. Reporting "all
        # checked providers healthy" on an empty report is how a canary that
        # has stopped checking anything — a renamed artifact, a glob that
        # matches nothing, every provider filtered out — goes on looking green.
        print(
            "\nNO PROVIDERS WERE CHECKED. This is a broken canary, not a "
            "healthy one.",
            file=sys.stderr,
        )
        return 1
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "providers": report,
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    for name, r in report.items():
        mark = {
            "ok": "OK  ", "fail": "FAIL", "blocked": "BLKD", "degraded": "DEGR",
        }.get(r["status"], "????")
        play = "playable" if r["playable"] else "unresolved"
        print(f"[{mark}] {name}: {r['detail']} ({play}, {r['latency_ms']}ms)", file=sys.stderr)

    blocked = [n for n, r in report.items() if r["status"] == "blocked"]
    if blocked:
        joined = ", ".join(blocked)
        print("", file=sys.stderr)
        print(
            f"BLOCKED (environment, not a provider regression): {joined}",
            file=sys.stderr,
        )
    broken = [n for n, r in report.items() if r["status"] == "fail"]
    unplayable = [n for n, r in report.items() if r["status"] == "degraded"]
    if args.require_any:
        healthy = [n for n, r in report.items() if r["status"] == "ok"]
        if healthy:
            others = broken + unplayable
            note = f" ({', '.join(others)} still broken)" if others else ""
            print(f"\nPlayable via: {', '.join(healthy)}{note}", file=sys.stderr)
            return 0
        # `blocked` is deliberately not counted as healthy: it says this IP got
        # a Cloudflare page, which is no evidence the provider can serve
        # anyone. It is still worth naming, because "nothing here could be
        # checked" and "nothing here works" want different responses.
        if blocked:
            print(
                "\nNOTHING PLAYABLE, and the only providers not already broken "
                f"were blocked from this IP ({', '.join(blocked)}) — rerun from "
                "somewhere else before treating this as an outage.",
                file=sys.stderr,
            )
        else:
            print("\nNO PROVIDER CAN PLAY ANYTHING — this one is an outage.",
                  file=sys.stderr)
        return 1
    if broken or unplayable:
        if broken:
            print(f"\nBROKEN: {', '.join(broken)}", file=sys.stderr)
        if unplayable:
            # Worth a tracking issue even though the read path is fine: from
            # where the user sits, a provider that can never produce a stream is
            # broken, whatever its search endpoint says.
            print(
                f"\nUNPLAYABLE (episodes found, no stream): {', '.join(unplayable)}",
                file=sys.stderr,
            )
        return 1
    print("\nAll checked providers healthy.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
