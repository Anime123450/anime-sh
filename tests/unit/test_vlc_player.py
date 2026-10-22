"""VlcPlayer and its handle, with the process and the HTTP client faked out."""

from __future__ import annotations

import logging

import pytest

from anime_sh.domain.errors import PlayerError
from anime_sh.domain.models import Stream, StreamKind, Subtitle
from anime_sh.infra.players.vlc import (
    VlcPlayer,
    _free_port,
    reached_end,
)
from anime_sh.infra.players.vlc_http import (
    VlcAuthRejected,
    VlcStatus,
    VlcUnreachable,
)


class FakeProc:
    def __init__(self, exit_code=None) -> None:
        self.exit_code = exit_code
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.exit_code

    def terminate(self):
        self.terminated = True
        self.exit_code = 1

    def kill(self):
        self.killed = True
        self.exit_code = -9

    def wait(self, timeout=None):
        return self.exit_code


class FakeClient:
    """Plays back a script of statuses (or exceptions), last one repeating."""

    def __init__(self, port, password, script) -> None:
        self.port, self.password = port, password
        self.script = list(script)
        self.calls: list[tuple] = []

    async def status(self):
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, BaseException):
            raise item
        return item

    async def seek(self, seconds):
        self.calls.append(("seek", seconds))

    async def pause(self):
        self.calls.append(("pause",))

    async def resume(self):
        self.calls.append(("resume",))

    async def stop(self):
        self.calls.append(("stop",))


def _playing(t, length=1440, state="playing"):
    return VlcStatus(state=state, time_s=t, length_s=length, position=0.0)


STOPPED = VlcStatus(state="stopped", time_s=0, length_s=0, position=0.0)
#: What VLC reports before the stream opens. The readiness probe in play()
#: consumes the first status of every script, so scripts start with this.
READY = STOPPED


class Harness:
    def __init__(self, scripts, procs=None, ports=None) -> None:
        self.scripts = list(scripts)
        self.procs = list(procs or [])
        self.ports = list(ports or [])
        self.spawned: list[list[str]] = []
        self.made_procs: list[FakeProc] = []
        self.clients: list[FakeClient] = []

    def spawn(self, argv):
        self.spawned.append(list(argv))
        proc = self.procs.pop(0) if self.procs else FakeProc()
        self.made_procs.append(proc)
        return proc

    def client(self, port, password):
        c = FakeClient(port, password, self.scripts.pop(0) if self.scripts else [STOPPED])
        self.clients.append(c)
        return c

    def pick_port(self):
        return self.ports.pop(0) if self.ports else 54321

    def player(self, **kw):
        return VlcPlayer(
            r"C:\Program Files\VideoLAN\VLC\vlc.exe",
            spawn=self.spawn, client_factory=self.client, pick_port=self.pick_port,
            ready_timeout=kw.pop("ready_timeout", 2.0),
            poll_interval=kw.pop("poll_interval", 0.0),
            **kw,
        )


def _stream(**kw):
    return Stream(url="https://cdn.example/ep1.m3u8", kind=StreamKind.HLS, **kw)


def _flag(argv, name):
    return [a.split("=", 1)[1] for a in argv if a.startswith(f"--{name}=")]


def _input_opt(argv, name):
    """Per-input ``:name=value`` options, which only count after the URL."""
    url_at = next(i for i, a in enumerate(argv) if a.startswith("https://cdn."))
    return [a.split("=", 1)[1] for a in argv[url_at + 1:] if a.startswith(f":{name}=")]


async def test_vlc_loopback_auth_uses_ephemeral_port(caplog):
    caplog.set_level(logging.DEBUG)
    h = Harness([[_playing(1)], [_playing(1)]], ports=[50111, 50222])
    player = h.player()
    await player.play(_stream(), title="Show - Episode 1", start_s=0)
    await player.play(_stream(), title="Show - Episode 2", start_s=0)

    first, second = h.spawned
    assert first[0].endswith("vlc.exe")
    for argv, port in ((first, 50111), (second, 50222)):
        assert "--no-one-instance" in argv, "could hand the media to a running VLC"
        assert "--extraintf=http" in argv
        assert _flag(argv, "http-host") == ["127.0.0.1"]
        assert _flag(argv, "http-port") == [str(port)]
        assert "--play-and-exit" not in argv
    pw1, pw2 = _flag(first, "http-password")[0], _flag(second, "http-password")[0]
    assert pw1 != pw2 and len(pw1) >= 32, "password is not per run"
    assert h.clients[0].password == pw1 and h.clients[0].port == 50111
    assert pw1 not in caplog.text and pw2 not in caplog.text

    port = _free_port()
    assert 1024 < port < 65536 and port != 8080


async def test_vlc_port_collision_retries():
    # Someone else answered on our port (wrong auth): kill only our child, try again.
    taken = VlcAuthRejected("401")
    h = Harness([[taken], [_playing(3)]], ports=[50001, 50002])
    handle = await h.player().play(_stream(), title="t")
    assert len(h.spawned) == 2
    assert h.made_procs[0].terminated and not h.made_procs[1].terminated
    assert _flag(h.spawned[1], "http-port") == ["50002"]
    assert handle is not None

    h = Harness([[taken]] * 5, ports=[1, 2, 3, 4, 5])
    with pytest.raises(PlayerError, match="port"):
        await h.player().play(_stream(), title="t")
    assert len(h.spawned) == 3, "retried more than three times"
    assert all(p.terminated for p in h.made_procs)


async def test_vlc_startup_timeout_cleans_up_owned_process():
    h = Harness([[VlcUnreachable("refused")]])
    with pytest.raises(PlayerError, match="control interface"):
        await h.player(ready_timeout=0.2, poll_interval=0.02).play(_stream(), title="t")
    (proc,) = h.made_procs
    assert proc.terminated, "left our VLC running after giving up"

    # VLC that dies on its own is reported straight away, with its exit code.
    h = Harness([[VlcUnreachable("refused")]], procs=[FakeProc(exit_code=3)])
    with pytest.raises(PlayerError, match="code 3"):
        await h.player(ready_timeout=5).play(_stream(), title="t")


async def test_vlc_preserves_supported_headers_and_subtitles():
    h = Harness([[_playing(1)]])
    subs = (Subtitle(url="https://subs.example/fr.vtt", lang="fr"),
            Subtitle(url="https://subs.example/en.vtt", lang="en", default=True))
    stream = _stream(headers={"User-Agent": "UA/1.0", "referer": "https://site.example/"},
                     subtitles=subs)
    await h.player().play(stream, title="Show - Episode 3", start_s=125)
    argv = h.spawned[0]
    assert _input_opt(argv, "http-user-agent") == ["UA/1.0"]
    assert not _flag(argv, "http-user-agent"), "vlc.exe overrides the global form"
    assert _flag(argv, "http-referrer") == ["https://site.example/"]
    assert _flag(argv, "sub-file") == ["https://subs.example/en.vtt"], "default track first"
    assert _flag(argv, "input-slave") == ["https://subs.example/fr.vtt"]
    assert _flag(argv, "start-time") == ["125"]
    assert _flag(argv, "meta-title") == ["Show - Episode 3"]
    assert argv[-2] == "https://cdn.example/ep1.m3u8"

    h = Harness([[_playing(1)]])
    await h.player().play(_stream(), title="t")
    assert _input_opt(h.spawned[0], "http-user-agent")[0].startswith("Mozilla/"), \
        "no browser UA by default"
    assert not _flag(h.spawned[0], "start-time")


async def test_unsupported_custom_headers_fail_before_launch():
    h = Harness([[_playing(1)]])
    stream = _stream(headers={"Referer": "https://a/", "Origin": "https://a",
                              "X-Token": "abc"})
    with pytest.raises(PlayerError) as err:
        await h.player().play(stream, title="t")
    assert "Origin" in str(err.value) and "X-Token" in str(err.value)
    assert "mpv" in str(err.value), "the error should say what would work"
    assert h.spawned == [], "VLC was launched before the refusal"


async def _collect(handle):
    return [ev async for ev in handle.events()]


async def test_events_map_status_and_detect_natural_end():
    h = Harness([[_playing(0, state="stopped"), _playing(10), _playing(11, state="paused"),
                  _playing(1430), STOPPED]])
    handle = await h.player().play(_stream(), title="t")
    evs = await _collect(handle)
    assert [(e.position_s, e.duration_s, e.paused, e.eof) for e in evs[:3]] == [
        (10, 1440, False, False), (11, 1440, True, False), (1430, 1440, False, False)]
    assert evs[-1].eof and evs[-1].reason == "eof" and evs[-1].position_s == 1430


@pytest.mark.parametrize("last, length, reason", [
    (1300, 1440, "stop"),   # stopped well before the end
    (1430, 0, "stop"),      # unknown duration never counts as finished
])
async def test_stops_that_are_not_natural_ends(last, length, reason):
    h = Harness([[READY, _playing(last, length=length), STOPPED]])
    evs = await _collect(await h.player().play(_stream(), title="t"))
    assert evs[-1].eof and evs[-1].reason == reason


async def test_window_closed_is_a_disconnect_not_an_end():
    h = Harness([[READY, _playing(1435), VlcUnreachable("gone")]])
    evs = await _collect(await h.player().play(_stream(), title="t"))
    assert evs[-1].eof and evs[-1].reason == "disconnect"
    assert evs[-1].position_s == 1435, "closing the window must keep the last position"


async def test_seek_and_stop_drive_only_our_process():
    h = Harness([[_playing(5)]])
    handle = await h.player().play(_stream(), title="t")
    await handle.seek(90)
    await handle.pause()
    await handle.resume()
    assert h.clients[0].calls == [("seek", 90), ("pause",), ("resume",)]
    await handle.stop()
    assert h.made_procs[0].terminated
    evs = await _collect(handle)
    assert evs[-1].reason == "stop", "our own stop must never read as an EOF"


def test_near_end_threshold():
    assert reached_end(1440 - 28, 1440)        # inside 2% of 24 minutes
    assert not reached_end(1440 - 30, 1440)
    assert reached_end(116, 120)               # short clip: the 5 s floor
    assert not reached_end(114, 120)
    assert not reached_end(100, 0)


async def test_manual_stop_near_end_is_best_effort():
    """The documented limitation: VLC's HTTP interface has no end-of-file event,
    so a stop the user makes inside the near-end window looks like a natural
    end. Pinned here so the threshold can't drift without someone noticing."""
    h = Harness([[READY, _playing(1415), STOPPED]])  # 25 s left of 24 min
    evs = await _collect(await h.player().play(_stream(), title="t"))
    assert evs[-1].reason == "eof"


async def test_a_stream_that_never_starts_does_not_end_by_itself():
    """Before anything plays VLC reports `stopped`; that is not an ending."""
    h = Harness([[READY, READY, READY, _playing(2), STOPPED]])
    evs = await _collect(await h.player().play(_stream(), title="t"))
    assert [e.position_s for e in evs] == [2, 2]
    assert evs[-1].reason == "stop"
