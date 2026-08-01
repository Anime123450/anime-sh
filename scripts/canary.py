"""Provider canary — hits real providers and reports which still work.

For each installed provider it runs the full read path (match → episodes →
candidates) against a known title, and, where possible, tries to resolve one
candidate to a playable stream. Writes ``provider-status.json`` and exits
non-zero if any *checked* provider is broken (candidates empty / errored) — host
flakiness alone (candidates OK but nothing resolves) is reported, not failed,
because that is the resolver chain's job, not a provider regression. A
Cloudflare challenge is reported as ``blocked`` for the same reason: it is a
property of the IP the check ran from, not of the provider.

Run locally:   uv run python scripts/canary.py
One provider:  uv run python scripts/canary.py --provider anikoto
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone

from anime_sh.domain.models import Audio
from anime_sh.infra import registry
from anime_sh.infra.http import CloudflareChallenge
from anime_sh.infra.metadata import AniListMetadata

# A title each provider should carry. Extend as providers are added.
CHECK_TITLE = "Frieren"


async def check_provider(name, provider, metadata, resolvers) -> dict:
    started = time.monotonic()
    result = {"status": "unknown", "detail": "", "playable": False, "candidates": 0}
    try:
        hits = await metadata.search(CHECK_TITLE, limit=1)
        if not hits:
            raise RuntimeError("metadata search returned nothing")
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

        # Best-effort: can any host actually resolve? Informational only.
        for cand in candidates:
            resolver = next((r for r in resolvers if r.handles(cand)), None)
            if resolver is None:
                continue
            try:
                streams = await resolver.resolve(cand)
            except Exception:
                continue
            if streams:
                result["playable"] = True
                break

        result.update(
            status="ok",
            detail=f"{len(episodes)} eps, {len(candidates)} hosts"
            + ("" if result["playable"] else ", no host resolved (hosts flaky)"),
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
        await metadata.aclose()
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", help="check only this provider")
    ap.add_argument("--output", default="provider-status.json")
    args = ap.parse_args()

    report = asyncio.run(run(args.provider))
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "providers": report,
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    for name, r in report.items():
        mark = {"ok": "OK  ", "fail": "FAIL", "blocked": "BLKD"}.get(r["status"], "????")
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
    if broken:
        print(f"\nBROKEN: {', '.join(broken)}", file=sys.stderr)
        return 1
    print("\nAll checked providers healthy.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
