"""The HTTP front door: /viewer/<name>/ streams a local viewer through
station-web's own port.

A real upstream (a small Starlette app under uvicorn, on a free port in a
background thread) records every request it sees, so each test can check
both ends: what the browser got and what the viewer saw.
"""
from __future__ import annotations

import gzip
import importlib
import socket
import threading
import time

import pytest
import uvicorn
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Route

from station_web import app as appmod
from station_web.services import viewer_proxy
from station_web.services.viewer_proxy import Target

GZ_BODY = gzip.compress(b"compressed payload " * 200)
BIG_PLAIN = b"".join(b"line %05d of an uncompressed viewer asset\n" % i for i in range(400))


def _upstream_app(seen: list):
    async def any_path(request):
        body = await request.body()
        seen.append({"method": request.method, "path": request.url.path,
                     "query": request.url.query, "headers": dict(request.headers),
                     "body": body})
        p = request.url.path
        if p == "/redirect-local":
            return Response(status_code=302, headers={"location": "/login"})
        if p == "/redirect-abs":
            return Response(status_code=302, headers={"location": "http://elsewhere/"})
        if p == "/gz":
            return Response(GZ_BODY, media_type="application/javascript",
                            headers={"content-encoding": "gzip"})
        if p == "/big":
            return Response(BIG_PLAIN, media_type="text/plain")
        if p == "/non-ascii":
            r = Response(b"ok")
            r.raw_headers.append((b"x-note", "check \u2713 caf\u00e9".encode("utf-8")))
            return r
        if p == "/origin-scoped":
            r = Response(b"ok")
            r.raw_headers += [(b"service-worker-allowed", b"/"),
                              (b"clear-site-data", b'"*"'),
                              (b"access-control-allow-origin", b"*"),
                              (b"access-control-allow-credentials", b"true"),
                              (b"set-cookie", b"d=4; Domain=example.org; Path=/p; Secure"),
                              (b"x-kept", b"1")]
            return r
        if p == "/cookies":
            r = Response(b"ok")
            r.raw_headers.append((b"set-cookie", b"a=1; Path=/"))
            r.raw_headers.append((b"set-cookie", b"b=2; Path=/x/../../.."))
            r.raw_headers.append((b"set-cookie", b"c=3"))
            return r
        return Response(b"hello " + p.encode() + b" " + body, media_type="text/plain")

    return Starlette(routes=[Route("/{p:path}", any_path, methods=["GET", "HEAD", "POST"])])


@pytest.fixture(scope="module")
def upstream():
    seen: list = []
    server = uvicorn.Server(uvicorn.Config(_upstream_app(seen), host="127.0.0.1", port=0,
                                           log_level="warning", lifespan="off"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, "upstream did not start"
    port = server.servers[0].sockets[0].getsockname()[1]
    yield port, seen
    server.should_exit = True
    t.join(timeout=5)


@pytest.fixture
def proxy(tmp_settings, write_viewers, upstream):
    port, seen = upstream
    seen.clear()
    write_viewers([{"name": "up", "port": port, "active": "active"}])
    with TestClient(appmod.create_app(tmp_settings, control_socket=None)) as c:
        yield c, port, seen


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# --------------------------------------------------------------------- route


def test_redirects_bare_name_to_slash(proxy):
    c, _, _ = proxy
    r = c.get("/viewer/up?x=1", follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["location"] == "/viewer/up/?x=1"


def test_proxies_path_and_query(proxy):
    c, _, seen = proxy
    r = c.get("/viewer/up/a/b.js?q=2")
    assert r.status_code == 200
    assert r.content == b"hello /a/b.js "
    assert (seen[-1]["path"], seen[-1]["query"]) == ("/a/b.js", "q=2")


def test_root_of_viewer_is_upstream_root(proxy):
    c, _, seen = proxy
    assert c.get("/viewer/up/").status_code == 200
    assert seen[-1]["path"] == "/"


def test_post_body_reaches_upstream(proxy):
    c, _, seen = proxy
    r = c.post("/viewer/up/form", content=b"k=v")
    assert r.status_code == 200 and seen[-1]["body"] == b"k=v"
    assert seen[-1]["method"] == "POST"


def test_head_is_proxied(proxy):
    c, _, seen = proxy
    r = c.head("/viewer/up/a")
    assert r.status_code == 200 and seen[-1]["method"] == "HEAD"


def test_upstream_sees_localhost_host_and_no_cookie(proxy):
    c, port, seen = proxy
    c.get("/viewer/up/x", headers={"Cookie": "s=1", "Origin": "http://evil",
                                   "Referer": "http://evil/"})
    h = seen[-1]["headers"]
    assert h["host"] == f"127.0.0.1:{port}"
    assert "cookie" not in h and "origin" not in h and "referer" not in h
    assert h["x-forwarded-prefix"] == "/viewer/up"
    assert "x-forwarded-for" in h and h["x-forwarded-proto"] == "http"


def test_location_is_rewritten_under_prefix(proxy):
    c, _, _ = proxy
    r = c.get("/viewer/up/redirect-local", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/viewer/up/login"
    r = c.get("/viewer/up/redirect-abs", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "http://elsewhere/"


def test_set_cookie_paths_stay_under_prefix(proxy):
    c, _, _ = proxy
    r = c.get("/viewer/up/cookies")
    cookies = r.headers.get_list("set-cookie")
    assert "a=1; Path=/viewer/up/" in cookies
    assert not any(v.startswith("b=") for v in cookies)
    assert "c=3" in cookies


def _raw(c, url, **kw):
    with c.stream("GET", url, **kw) as r:
        return r.status_code, r.headers, b"".join(r.iter_raw())


def test_gzip_passes_through_untouched(proxy):
    c, _, _ = proxy
    status, headers, body = _raw(c, "/viewer/up/gz", headers={"Accept-Encoding": "gzip"})
    assert status == 200
    assert headers["content-encoding"] == "gzip"
    assert body == GZ_BODY


def test_uncompressed_upstream_is_not_gzipped_by_station_web(proxy):
    """GZipMiddleware would compress this (over minimum_size, text/plain,
    browser accepts gzip).  /viewer/ must pass it through byte-identical."""
    c, _, _ = proxy
    assert len(BIG_PLAIN) > 4096
    status, headers, body = _raw(c, "/viewer/up/big", headers={"Accept-Encoding": "gzip"})
    assert status == 200
    assert "content-encoding" not in headers
    assert body == BIG_PLAIN


def test_api_routes_still_gzip(client):
    """The exclusion stays narrow: station-web's own pages still compress."""
    with client.stream("GET", "/openapi.json", headers={"Accept-Encoding": "gzip"}) as r:
        assert r.headers.get("content-encoding") == "gzip"


def test_upstream_asked_for_identity_when_browser_named_no_encoding(proxy):
    c, _, seen = proxy
    c.get("/viewer/up/x", headers={"Accept-Encoding": ""})
    assert seen[-1]["headers"].get("accept-encoding") in ("identity", None)


# -------------------------------------------------------------------- errors


def test_upstream_down_is_502_fixed_text(tmp_settings, write_viewers):
    p = _closed_port()
    write_viewers([{"name": "up", "port": p}])
    with TestClient(appmod.create_app(tmp_settings, control_socket=None)) as c:
        r = c.get("/viewer/up/")
    assert r.status_code == 502
    assert r.text == f"viewer not answering on port {p}"
    assert r.headers["content-type"].startswith("text/plain")


def test_snapshot_missing_is_503(tmp_settings):
    with TestClient(appmod.create_app(tmp_settings, control_socket=None)) as c:
        r = c.get("/viewer/up/")
    assert r.status_code == 503 and r.text == "viewer list unavailable"


def test_unknown_viewer_is_404(proxy):
    c, _, _ = proxy
    r = c.get("/viewer/nope/")
    assert r.status_code == 404 and r.text == "no such viewer on this station"


def test_shared_port_is_409(tmp_settings, write_viewers):
    write_viewers([{"name": "ka9q-web-2", "port": 8082}, {"name": "gmag-webui", "port": 8082}],
                  conflicts=[{"port": 8082, "names": ["ka9q-web-2", "gmag-webui"]}])
    with TestClient(appmod.create_app(tmp_settings, control_socket=None)) as c:
        r = c.get("/viewer/gmag-webui/")
    assert r.status_code == 409
    assert r.text == "port 8082 is claimed by ka9q-web-2 and gmag-webui"


def test_body_over_limit_is_413(proxy):
    c, _, seen = proxy
    before = len(seen)
    r = c.post("/viewer/up/form", content=b"x" * (viewer_proxy.MAX_REQUEST_BODY + 1))
    assert r.status_code == 413 and r.text == "request too large"
    assert len(seen) == before                    # never reached the viewer


def test_body_at_limit_passes(proxy):
    c, _, seen = proxy
    r = c.post("/viewer/up/form", content=b"x" * viewer_proxy.MAX_REQUEST_BODY)
    assert r.status_code == 200 and len(seen[-1]["body"]) == viewer_proxy.MAX_REQUEST_BODY


# ------------------------------------------------------------- header units

T = Target(name="up", port=8081, kind="other")


def test_request_headers_unit():
    h = viewer_proxy.request_headers(
        {"host": "station:8000", "cookie": "s=1", "origin": "o", "referer": "r",
         "connection": "keep-alive, x-private", "x-private": "1", "keep-alive": "5",
         "te": "trailers", "upgrade": "h2c", "accept": "text/html",
         "x-forwarded-for": "6.6.6.6"},
        T, "10.0.0.5", "https")
    assert h["host"] == "127.0.0.1:8081"
    assert h["x-forwarded-prefix"] == "/viewer/up"
    assert h["x-forwarded-for"] == "10.0.0.5"
    assert h["x-forwarded-proto"] == "https"
    assert h["accept"] == "text/html"
    for gone in ("cookie", "origin", "referer", "connection", "x-private", "keep-alive",
                 "te", "upgrade"):
        assert gone not in h, gone


def test_response_headers_unit():
    out = viewer_proxy.response_headers(
        [("location", "/a?b=1"), ("transfer-encoding", "chunked"), ("connection", "close"),
         ("set-cookie", "k=v; Path=/sub; HttpOnly"), ("set-cookie", "e=1; path=../x"),
         ("content-type", "text/html")],
        "/viewer/up")
    assert ("location", "/viewer/up/a?b=1") in out
    assert ("set-cookie", "k=v; Path=/viewer/up/sub; HttpOnly") in out
    assert not any(k == "set-cookie" and v.startswith("e=") for k, v in out)
    assert not any(k in ("transfer-encoding", "connection") for k, _ in out)
    assert ("content-type", "text/html") in out


def test_protocol_relative_location_is_unchanged():
    out = viewer_proxy.response_headers([("location", "//elsewhere/x")], "/viewer/up")
    assert out == [("location", "//elsewhere/x")]


def test_constants():
    assert viewer_proxy.MAX_REQUEST_BODY == 1 << 20
    assert viewer_proxy.HOP_BY_HOP == frozenset({
        "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
        "trailer", "transfer-encoding", "upgrade"})


# ---------------------------------------------------------------------- core


def test_viewer_is_a_core_router(settings, monkeypatch):
    from test_core_split import _hide_science
    assert "viewer" in appmod.CORE_ROUTERS
    _hide_science(monkeypatch)
    mod = importlib.import_module("station_web.routers.viewer")
    assert mod.router.prefix == "/viewer"


def test_upstream_closes_when_browser_goes_away():
    """A browser that leaves mid-stream must not leave the upstream
    connection open: the response closes it even when the send fails."""
    import anyio
    from station_web.routers.viewer import _UpstreamStream

    class FakeUpstream:
        closed = False

        async def aiter_raw(self):
            for _ in range(100):
                yield b"x" * 10

        async def aclose(self):
            FakeUpstream.closed = True

    sent = []

    async def send(msg):
        sent.append(msg)
        if msg["type"] == "http.response.body":
            raise OSError("client went away")

    async def receive():
        await anyio.sleep_forever()

    async def main():
        resp = _UpstreamStream(FakeUpstream(), status_code=200)
        scope = {"type": "http", "asgi": {"spec_version": "2.4"}, "method": "GET"}
        with pytest.raises(Exception):
            await resp(scope, receive, send)

    anyio.run(main)
    assert FakeUpstream.closed


# ------------------------------------------------------------ fix round 1


def test_non_ascii_header_value_passes_byte_identical(tmp_settings, write_viewers, upstream):
    """httpx decodes header values as UTF-8; re-encoding them as latin-1
    raised and leaked the upstream connection.  The proxy relays the raw
    bytes instead.  Driven over raw ASGI: TestClient itself cannot carry a
    non-ASCII header back to the test."""
    import anyio
    port, _ = upstream
    write_viewers([{"name": "up", "port": port}])
    app = appmod.create_app(tmp_settings, control_socket=None)
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        messages.append(msg)

    async def main():
        app.state.viewer_client = viewer_proxy.make_client()
        try:
            await app({"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                       "http_version": "1.1", "method": "GET", "scheme": "http",
                       "path": "/viewer/up/non-ascii", "raw_path": b"/viewer/up/non-ascii",
                       "query_string": b"", "root_path": "",
                       "headers": [(b"host", b"testserver")],
                       "client": ("127.0.0.1", 5), "server": ("testserver", 80)},
                      receive, send)
        finally:
            await app.state.viewer_client.aclose()

    anyio.run(main)
    start = next(m for m in messages if m["type"] == "http.response.start")
    assert start["status"] == 200
    assert dict(start["headers"])[b"x-note"] == "check \u2713 caf\u00e9".encode("utf-8")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    assert body == b"ok"


def test_header_failure_closes_upstream_and_is_fixed_502(proxy, monkeypatch):
    c, port, _ = proxy
    opened = []
    real = viewer_proxy.open_upstream

    async def spy(*a, **kw):
        resp = await real(*a, **kw)
        opened.append(resp)
        return resp

    def boom(*a, **kw):
        raise UnicodeEncodeError("latin-1", "\u2713", 0, 1, "secret detail")

    monkeypatch.setattr(viewer_proxy, "open_upstream", spy)
    monkeypatch.setattr(viewer_proxy, "response_headers", boom)
    r = c.get("/viewer/up/x")
    assert r.status_code == 502
    assert r.text == f"viewer not answering on port {port}"
    assert "secret" not in r.text
    assert opened and opened[0].is_closed


def test_cross_origin_post_is_refused_before_upstream(proxy):
    c, _, seen = proxy
    before = len(seen)
    r = c.post("/viewer/up/form", content=b"k=v", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert r.text == "request refused: started by another site"
    assert r.headers["content-type"].startswith("text/plain")
    assert len(seen) == before


def test_cross_site_fetch_post_is_refused(proxy):
    c, _, seen = proxy
    before = len(seen)
    r = c.post("/viewer/up/form", content=b"k=v", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403 and len(seen) == before


def test_cross_origin_refusal_comes_before_resolve(tmp_settings):
    """No snapshot at all: the 403 still wins over the 503 resolve would give."""
    with TestClient(appmod.create_app(tmp_settings, control_socket=None)) as c:
        r = c.post("/viewer/up/form", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


def test_same_origin_post_is_proxied(proxy):
    c, _, seen = proxy
    r = c.post("/viewer/up/form", content=b"k=v",
               headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200 and seen[-1]["body"] == b"k=v"


def test_cross_origin_get_is_proxied(proxy):
    c, _, seen = proxy
    r = c.get("/viewer/up/page", headers={"Origin": "http://evil.example",
                                          "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 200 and seen[-1]["path"] == "/page"


def test_origin_scoped_response_headers_are_dropped(proxy):
    c, _, _ = proxy
    r = c.get("/viewer/up/origin-scoped")
    names = {k.lower() for k in r.headers.keys()}
    assert "service-worker-allowed" not in names
    assert "clear-site-data" not in names
    assert not any(n.startswith("access-control-") for n in names)
    assert r.headers["x-kept"] == "1"
    assert r.headers.get_list("set-cookie") == ["d=4; Path=/viewer/up/p; Secure"]


def test_response_headers_unit_drops_origin_scoped():
    out = viewer_proxy.response_headers(
        [("Service-Worker-Allowed", "/"), ("Clear-Site-Data", '"cookies"'),
         ("Access-Control-Allow-Origin", "*"), ("Access-Control-Expose-Headers", "x"),
         ("Set-Cookie", "k=v; domain=.station.lan; Path=/"), ("x-ok", "1")],
        "/viewer/up")
    assert out == [("set-cookie", "k=v; Path=/viewer/up/"), ("x-ok", "1")]


# ------------------------------------------------------------ fix round 2


def _cookie(value):
    out = viewer_proxy.response_headers([("set-cookie", value)], "/viewer/up")
    return [v for k, v in out if k == "set-cookie"]


def test_cookie_domain_with_space_before_equals_is_stripped():
    assert _cookie("k=v; Domain =evil.example; Path=/") == ["k=v; Path=/viewer/up/"]


def test_cookie_domain_with_leading_space_and_spaced_value_is_stripped():
    assert _cookie("k=v;  domain= x; Path=/") == ["k=v; Path=/viewer/up/"]


def test_cookie_path_with_space_before_equals_is_rewritten():
    assert _cookie("k=v; Path =/") == ["k=v; Path=/viewer/up/"]


def test_cookie_path_in_capitals_is_rewritten():
    assert _cookie("k=v; PATH=/a") == ["k=v; Path=/viewer/up/a"]


def test_cookie_unknown_attribute_is_dropped():
    assert _cookie("k=v; Path=/; Priority=High; weird; HttpOnly") == [
        "k=v; Path=/viewer/up/; HttpOnly"]


def test_cookie_known_attributes_survive_intact():
    value = ("k=v; Expires=Wed, 21 Oct 2026 07:28:00 GMT; Max-Age=60; Secure; "
             "HttpOnly; SameSite=Lax; Partitioned; Path=/s")
    assert _cookie(value) == [
        "k=v; Expires=Wed, 21 Oct 2026 07:28:00 GMT; Max-Age=60; Secure; HttpOnly; "
        "SameSite=Lax; Partitioned; Path=/viewer/up/s"]


def test_cookie_spaced_escaping_path_still_drops_the_cookie():
    assert _cookie("k=v; Path = /a/../..") == []
    assert _cookie("k=v; path=//evil") == []
    assert _cookie("k=v; Path=/a\\b") == []
