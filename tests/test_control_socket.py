import http.client
import json
import socket
import time
from pathlib import Path

from station_web.control_socket import ControlSocketServer


class _UnixHTTP(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost")
        self._path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self._path)


def _get(path, route):
    c = _UnixHTTP(str(path))
    c.request("GET", route)
    r = c.getresponse()
    status, body, ct = r.status, r.read().decode(), r.getheader("Content-Type")
    c.close()
    return status, body, ct


def test_endpoints(tmp_path):
    sock = tmp_path / "control.sock"
    srv = ControlSocketServer(sock, status_fn=lambda: {"ready": True, "products": {"L3_fusion_timing": "VALID"}},
                              group=None)
    srv.start()
    try:
        assert sock.exists()
        st, body, ct = _get(sock, "/healthz")
        assert st == 200 and json.loads(body) == {"ok": True}
        st, body, _ = _get(sock, "/readyz")
        assert st == 200 and json.loads(body)["ready"] is True
        st, body, _ = _get(sock, "/status")
        assert st == 200 and json.loads(body)["products"]["L3_fusion_timing"] == "VALID"
        st, body, ct = _get(sock, "/metrics")
        assert st == 200 and ct.startswith("text/plain")
        assert "station_web_up 1" in body
        st, _, _ = _get(sock, "/nope")
        assert st == 404
    finally:
        srv.stop()
    assert not sock.exists()


def test_readyz_503_when_not_ready(tmp_path):
    sock = tmp_path / "nr.sock"
    srv = ControlSocketServer(sock, status_fn=lambda: {"ready": False}, group=None)
    srv.start()
    try:
        st, body, _ = _get(sock, "/readyz")
        assert st == 503 and json.loads(body) == {"ready": False}
    finally:
        srv.stop()


def test_answers_fast(tmp_path):
    sock = tmp_path / "c.sock"
    srv = ControlSocketServer(sock, status_fn=lambda: {"ready": True}, group=None)
    srv.start()
    try:
        t0 = time.monotonic()
        for _ in range(20):
            _get(sock, "/healthz")
        assert (time.monotonic() - t0) / 20 < 0.1        # §13: < 100 ms
    finally:
        srv.stop()
