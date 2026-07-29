"""AniSkip — community intro/outro timestamps.

Providers only sometimes ship op/ed skip data with a stream (megaplay does,
AniZone doesn't). AniSkip fills the gap: given a show's
MAL id + episode number, it returns crowd-sourced intro/outro intervals so
auto-skip works on every provider, not just the lucky ones.

Best-effort by contract: any miss (no MAL id, no data for the episode, network
error) returns ``None`` and playback proceeds without skips — never an error.
"""

from __future__ import annotations

import logging

from ...domain.models import SkipRange, SkipTimes
from ..http import HttpClient, HttpError

API = "https://api.aniskip.com/v2"

log = logging.getLogger(__name__)


class AniSkipSource:
    name = "aniskip"

    def __init__(self, http: HttpClient | None = None) -> None:
        self._http = http or HttpClient(headers={"Accept": "application/json"})

    async def aclose(self) -> None:
        await self._http.aclose()

    async def for_episode(
        self, mal_id: int | None, episode: float, *, episode_length: int
    ) -> SkipTimes | None:
        """Intro/outro intervals for one episode, or None if unavailable."""
        if not mal_id:
            return None
        try:
            data = await self._http.get_json(
                f"{API}/skip-times/{mal_id}/{episode:g}",
                params={"types": ["op", "ed"], "episodeLength": int(episode_length)},
            )
        except HttpError as e:
            # 404 = no data for this episode; anything else = a hiccup. Either
            # way, no skips — never disturb playback over it.
            log.debug("aniskip miss for mal %s ep %g: %s", mal_id, episode, e)
            return None
        return _parse(data)


def _parse(data) -> SkipTimes | None:
    if not data or not data.get("found"):
        return None
    op = ed = None
    for result in data.get("results") or []:
        interval = result.get("interval") or {}
        rng = SkipRange(
            start_s=int(interval.get("startTime", 0)),
            end_s=int(interval.get("endTime", 0)),
        )
        if rng.end_s <= rng.start_s:
            continue
        kind = result.get("skipType")
        if kind == "op":
            op = rng
        elif kind == "ed":
            ed = rng
    if op is None and ed is None:
        return None
    return SkipTimes(op=op, ed=ed)
