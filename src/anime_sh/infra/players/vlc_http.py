"""A tiny client for VLC's HTTP control interface (VLC 3.0 ``requests/status.json``).

Loopback only, by construction: the constructor refuses any host but
127.0.0.1 / ::1, and requests bypass any configured proxy so a system or
environment proxy never sees the control traffic or its password.

The password lives on this object for the life of one playback and nowhere
else: it is not part of ``repr``, error messages or logs.
"""

from __future__ import annotations

import asyncio
import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from ...domain.errors import PlayerError

_LOOPBACK = frozenset({"127.0.0.1", "::1"})


class VlcControlError(PlayerError):
    """VLC's control interface answered with something we can't use."""


class VlcAuthRejected(VlcControlError):
    """Something on our port refused our password — not our VLC (or not yet)."""


class VlcUnreachable(VlcControlError):
    """Nothing is listening: VLC hasn't started its interface, or has exited."""


def _as_int(value: Any) -> int:
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return 0
    return max(n, 0)


def _as_float(value: Any) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(f, 0.0)


@dataclass(frozen=True, slots=True)
class VlcStatus:
    """The parts of a status reply playback cares about.

    VLC reports -1 for an unknown time/length and sends numbers as strings on
    some builds; both are normalised here so callers only ever see ``>= 0``.
    """

    state: str
    time_s: int
    length_s: int
    position: float

    @classmethod
    def from_json(cls, data: Any) -> "VlcStatus":
        if not isinstance(data, dict):
            raise VlcControlError("VLC's status reply was not a JSON object")
        return cls(
            state=str(data.get("state") or "stopped"),
            time_s=_as_int(data.get("time")),
            length_s=_as_int(data.get("length")),
            position=_as_float(data.get("position")),
        )


class VlcHttpClient:
    """Authenticated status / pause / resume / seek / stop for one VLC."""

    def __init__(
        self, port: int, password: str, *, host: str = "127.0.0.1",
        timeout: float = 2.0,
    ) -> None:
        if host not in _LOOPBACK:
            raise ValueError(f"VLC control must stay on loopback, not {host!r}")
        self.port = port
        self.host = host
        self.timeout = timeout
        self._auth = "Basic " + base64.b64encode(f":{password}".encode()).decode()
        netloc = f"[{host}]" if ":" in host else host
        self._base = f"http://{netloc}:{port}/requests/status.json"
        # No ProxyHandler entries: HTTP(S)_PROXY must never route loopback
        # control traffic (urllib honours them for 127.0.0.1 otherwise).
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def __repr__(self) -> str:
        return f"VlcHttpClient(host={self.host!r}, port={self.port})"

    # -- transport ----------------------------------------------------------- #
    def _get(self, params: dict[str, str]) -> VlcStatus:
        url = self._base + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, headers={"Authorization": self._auth})
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise VlcAuthRejected(
                    f"the control port {self.port} refused our password") from None
            raise VlcControlError(
                f"VLC's control interface returned HTTP {e.code}") from None
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            reason = getattr(e, "reason", e)
            raise VlcUnreachable(
                f"VLC's control interface on port {self.port} is unreachable "
                f"({type(reason).__name__})") from None
        try:
            data = json.loads(body.decode("utf-8", errors="replace"))
        except ValueError:
            raise VlcControlError(
                f"port {self.port} answered, but not with VLC's status JSON") from None
        return VlcStatus.from_json(data)

    async def _call(self, **params: str) -> VlcStatus:
        return await asyncio.to_thread(self._get, params)

    # -- commands ----------------------------------------------------------- #
    async def status(self) -> VlcStatus:
        return await self._call()

    async def pause(self) -> None:
        await self._call(command="pl_forcepause")

    async def resume(self) -> None:
        await self._call(command="pl_forceresume")

    async def seek(self, seconds: int) -> None:
        await self._call(command="seek", val=str(max(int(seconds), 0)))

    async def stop(self) -> None:
        await self._call(command="pl_stop")
