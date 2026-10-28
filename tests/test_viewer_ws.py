"""WebSockets through the front door: /viewer/<name>/... relays a local
viewer's WebSocket, and only for a page station-web itself served.

ka9q-web streams its waterfall over a WebSocket and creates radiod channels
on request, so a page on another site must never open one.  A real upstream
(websockets' own server on a free port, in a background thread with its own
event loop) records every connection attempt, so each refusal test can show
that no upstream connection happened.
"""
from __future__ import annotations

import asyncio
import socket
import threading

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.server import serve

from station_web import app as appmod
from station_web.services import viewer_proxy
from station_web.services.viewer_proxy import Target


async def _handler(ws):
    if ws.request.path == "/close4001":
        await ws.close(4001, "bye")
        return
    async for msg in ws:
        await ws.send(msg)


def _pick_b(connection, offered):
    return "b" if "b" in offered else None


@pytest.fixture(scope="module")
def ws_upstream():
    seen: list = []

    def record(connection, request):
        seen.append({"path": request.path, "headers": request.headers})
        return None

    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()

    async def start():
        return await serve(_handler, "127.0.0.1", 0, process_request=record,
                           select_subprotocol=_pick_b)

    server = asyncio.run_coroutine_threadsafe(start(), loop).result(timeout=10)
    port = server.sockets[0].getsockname()[1]
    yield port, seen

    async def stop():
        server.close()
        await server.wait_closed()

    asyncio.run_coroutine_threadsafe(stop(), loop).result(timeout=10)
    loop.call_soon_threadsafe(loop.stop)
    t.join(timeout=5)


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def proxy(tmp_settings, write_viewers, ws_upstream):
    port, seen = ws_upstream
    seen.clear()
    write_viewers([{"name": "up", "port": port, "active": "active"},
                   {"name": "down", "port": _closed_port(), "active": "active"}])
    with TestClient(appmod.create_app(tmp_settings, control_socket=None)) as c:
        yield c, port, seen


# --------------------------------------------------------------- the module


def test_ws_upstream_url_is_loopback_at_the_snapshot_port():
    t = Target(name="up", port=8081, kind="ka9q-web")
    assert viewer_proxy.ws_upstream_url(t, "ws", "a=1") == "ws://127.0.0.1:8081/ws?a=1"
    assert viewer_proxy.ws_upstream_url(t, "", "") == "ws://127.0.0.1:8081/"
    assert viewer_proxy.WS_MAX_MESSAGE == 16 << 20


# ------------------------------------------------------------------ relay


def test_ws_text_and_binary_echo(proxy):
    c, _, _ = proxy
    with c.websocket_connect("/viewer/up/ws") as ws:
        ws.send_text("hi")
        assert ws.receive() == {"type": "websocket.send", "text": "hi"}
        ws.send_bytes(b"\x00\x01")
        assert ws.receive() == {"type": "websocket.send", "bytes": b"\x00\x01"}


def test_ws_path_reaches_upstream(proxy):
    c, port, seen = proxy
    with c.websocket_connect("/viewer/up/ws?a=1") as ws:
        ws.send_text("x")
        ws.receive_text()
    assert seen[-1]["path"] == "/ws?a=1"
    with c.websocket_connect("/viewer/up/") as ws:
        ws.send_text("x")
        ws.receive_text()
    assert seen[-1]["path"] == "/"
    # The viewer sees a local client: its own host, no browser origin.
    assert seen[-1]["headers"]["host"] == f"127.0.0.1:{port}"
    assert "origin" not in seen[-1]["headers"]


def test_ws_cross_origin_refused_before_upstream(proxy):
    c, _, seen = proxy
    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect("/viewer/up/ws",
                                 headers={"origin": "http://evil.example"}):
            pass
    assert e.value.code == 1008
    assert seen == []


def test_ws_sec_fetch_cross_site_refused(proxy):
    c, _, seen = proxy
    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect("/viewer/up/ws", headers={"sec-fetch-site": "cross-site"}):
            pass
    assert e.value.code == 1008
    assert seen == []


def test_ws_same_origin_allowed(proxy):
    c, _, _ = proxy
    with c.websocket_connect("/viewer/up/ws", headers={"origin": "http://testserver"}) as ws:
        ws.send_text("same")
        assert ws.receive_text() == "same"


def test_ws_unknown_viewer_refused_1008(proxy):
    c, _, seen = proxy
    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect("/viewer/nope/ws"):
            pass
    assert e.value.code == 1008
    assert seen == []


def test_ws_subprotocol_negotiated(proxy):
    c, _, _ = proxy
    with c.websocket_connect("/viewer/up/ws", subprotocols=["a", "b"]) as ws:
        assert ws.accepted_subprotocol == "b"
        ws.send_text("p")
        assert ws.receive_text() == "p"


def test_ws_no_subprotocol_when_none_offered(proxy):
    c, _, _ = proxy
    with c.websocket_connect("/viewer/up/ws") as ws:
        assert ws.accepted_subprotocol is None


def test_ws_upstream_close_code_propagates(proxy):
    c, _, _ = proxy
    with c.websocket_connect("/viewer/up/close4001") as ws:
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_text()
    assert e.value.code == 4001


def test_ws_upstream_down_closes_1011(proxy):
    c, _, _ = proxy
    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect("/viewer/down/ws"):
            pass
    assert e.value.code == 1011


@pytest.mark.parametrize("code,sent", [(1000, 1000), (4001, 4001), (1005, 1000),
                                       (1006, 1011), (1015, 1011), (None, 1000),
                                       (1004, 1011), (2999, 1011), (5000, 1011)])
def test_ws_close_code_mapping(code, sent):
    assert viewer_proxy.ws_close_code(code) == sent
