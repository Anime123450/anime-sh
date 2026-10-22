"""The VLC HTTP control client, against a real loopback server that checks auth."""

from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from anime_sh.domain.errors import PlayerError
from anime_sh.infra.players.vlc_http import (
    VlcAuthRejected,
    VlcControlError,
    VlcHttpClient,
    VlcStatus,
    VlcUnreachable,
)

PASSWORD = "s3cret-per-run"


class _FakeVlc:
    """Speaks just enough of VLC 3.0's requests/status.json."""

    def __init__(self, *, body=None, raw: bytes | None = None) -> None:
        self.body = body or {"state": "playing", "time": 61, "length": 1440,
                             "position": 0.0424}
        self.raw = raw
        self.requests: list[dict] = []
        self.auth_headers: list[str | None] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # keep test output clean
                pass

            def do_GET(self):
                fake.auth_headers.append(self.headers.get("Authorization"))
                expected = "Basic " + base64.b64encode(
                    f":{PASSWORD}".encode()).decode()
                if self.headers.get("Authorization") != expected:
                    self.send_response(401)
                    self.end_headers()
                    return
                url = urlparse(self.path)
                fake.requests.append({"path": url.path,
                                      **{k: v[0] for k, v in parse_qs(url.query).items()}})
                data = fake.raw if fake.raw is not None else json.dumps(fake.body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


async def test_vlc_http_auth_and_status_parsing(monkeypatch):
    # A system proxy must never see loopback control traffic (or its password).
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    with _FakeVlc() as vlc:
        client = VlcHttpClient(vlc.port, PASSWORD)
        st = await client.status()
        assert st == VlcStatus(state="playing", time_s=61, length_s=1440,
                               position=pytest.approx(0.0424))
        assert vlc.requests[-1]["path"] == "/requests/status.json"

        await client.pause()
        await client.resume()
        await client.seek(90)
        await client.stop()
        commands = [(r.get("command"), r.get("val")) for r in vlc.requests[1:]]
        assert commands == [("pl_forcepause", None), ("pl_forceresume", None),
                            ("seek", "90"), ("pl_stop", None)]

        wrong = VlcHttpClient(vlc.port, "not-it")
        with pytest.raises(VlcAuthRejected):
            await wrong.status()

    assert PASSWORD not in repr(client)


def test_status_parsing_tolerates_vlc_quirks():
    st = VlcStatus.from_json({"state": "stopped", "time": -1, "length": -1})
    assert (st.state, st.time_s, st.length_s, st.position) == ("stopped", 0, 0, 0.0)
    st = VlcStatus.from_json({"state": "paused", "time": "12", "length": 99.7,
                              "position": "0.5"})
    assert (st.state, st.time_s, st.length_s, st.position) == ("paused", 12, 99, 0.5)
    with pytest.raises(VlcControlError):
        VlcStatus.from_json(["not", "a", "status"])


async def test_non_vlc_responses_and_dead_ports_are_distinguished():
    with _FakeVlc(raw=b"<html>someone else's server</html>") as other:
        with pytest.raises(VlcControlError):
            await VlcHttpClient(other.port, PASSWORD).status()

    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    dead = s.getsockname()[1]
    s.close()
    with pytest.raises(VlcUnreachable) as err:
        await VlcHttpClient(dead, PASSWORD, timeout=0.5).status()
    assert isinstance(err.value, PlayerError)
    assert PASSWORD not in str(err.value)


def test_control_client_is_loopback_only():
    for host in ("0.0.0.0", "192.168.1.5", "example.com"):
        with pytest.raises(ValueError):
            VlcHttpClient(8080, PASSWORD, host=host)
    VlcHttpClient(8080, PASSWORD, host="127.0.0.1")
    VlcHttpClient(8080, PASSWORD, host="::1")
