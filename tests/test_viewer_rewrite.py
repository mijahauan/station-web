"""The one pinned rewrite per viewer kind: each viewer's WebSocket keeps
the /viewer/<name>/ prefix.

ka9q-web's radio.js and gmag-webui's js/feed.js build their WebSocket URL
from the host alone, so behind the prefix both would reach station-web's
own root.  The proxy rewrites exactly one known line in each, checked here
against verbatim copies of the files at their v3.82 pins.
"""
from __future__ import annotations

import gzip
import json
import logging
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Route

from station_web import app as appmod
from station_web.services import viewer_proxy
from station_web.services.viewer_proxy import REWRITES, apply_rewrite

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "viewers"
FIXTURE_FOR = {"ka9q-web": FIXTURES / "ka9q-web-2c6303f-radio.js",
               "gmag": FIXTURES / "gmag-webui-bc1328b-feed.js"}


def _rw(kind):
    return next(r for r in REWRITES if r.kind == kind)


@pytest.fixture(autouse=True)
def _fresh_warning_memory():
    viewer_proxy.reset_rewrite_warnings()
    yield
    viewer_proxy.reset_rewrite_warnings()


# ------------------------------------------------------------ apply_rewrite


def test_exactly_two_rewrites():
    assert [(r.kind, r.path) for r in REWRITES] == [("ka9q-web", "radio.js"),
                                                   ("gmag", "js/feed.js")]


@pytest.mark.parametrize("kind", ["ka9q-web", "gmag"])
def test_each_rewrite_matches_its_pinned_fixture_exactly_once(kind):
    body = FIXTURE_FOR[kind].read_bytes()
    assert body.count(_rw(kind).old.encode()) == 1


def test_ka9q_rewrite_applied():
    rw = _rw("ka9q-web")
    out, how = apply_rewrite("ka9q-web", "radio.js", FIXTURE_FOR["ka9q-web"].read_bytes())
    assert how == "rewritten"
    assert rw.new.encode() in out and rw.old.encode() not in out


def test_gmag_rewrite_applied():
    rw = _rw("gmag")
    out, how = apply_rewrite("gmag", "js/feed.js", FIXTURE_FOR["gmag"].read_bytes())
    assert how == "rewritten"
    assert rw.new.encode() in out and rw.old.encode() not in out


def test_rewrite_changes_only_that_line():
    src = FIXTURE_FOR["gmag"].read_bytes()
    out, _ = apply_rewrite("gmag", "js/feed.js", src)
    rw = _rw("gmag")
    assert out == src.replace(rw.old.encode(), rw.new.encode())


def test_miss_returns_body_unchanged():
    assert apply_rewrite("gmag", "js/feed.js", b"no such line") == (b"no such line", "missed")


def test_twice_is_a_miss():
    body = (_rw("gmag").old + "\n" + _rw("gmag").old).encode()
    assert apply_rewrite("gmag", "js/feed.js", body) == (body, "missed")


def test_other_paths_untouched():
    body = FIXTURE_FOR["ka9q-web"].read_bytes()
    assert apply_rewrite("ka9q-web", "spectrum.js", body) == (body, "none")
    assert apply_rewrite("other", "radio.js", body) == (body, "none")
    assert apply_rewrite("gmag", "radio.js", body) == (body, "none")


# -------------------------------------------- the new URLs, evaluated in node

# node evaluates only the fixed old/new strings from REWRITES, in a
# throwaway process, with a stub window, loc and WebSocket.

_NODE = r"""
const out = [];
for (const p of PATHS) {
  let ws;
  const window = { location: { protocol: 'http:', host: 'h:8080', pathname: p } };
  function WebSocket(u) { this.url = u; }
  eval(KA9Q);
  const loc = { protocol: 'http:', host: 'h:8080', pathname: p };
  const scheme = 'ws://';
  out.push([ws.url, new Function('loc', 'scheme', GMAG)(loc, scheme)]);
}
console.log(JSON.stringify(out));
"""


def _urls(ka9q_line, gmag_line, paths):
    script = (f"const PATHS = {json.dumps(paths)}; const KA9Q = {json.dumps(ka9q_line)};"
              f" const GMAG = {json.dumps(gmag_line)};\n" + _NODE)
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_new_urls_keep_the_prefix_and_match_old_at_root():
    ka9q, gmag = _rw("ka9q-web"), _rw("gmag")
    paths = ["/viewer/ka9q-web/", "/viewer/ka9q-web/index.html",
             "/viewer/gmag-webui/", "/", "/index.html", ""]
    new = _urls(ka9q.new, gmag.new, paths)
    old = _urls(ka9q.old, gmag.old, paths)
    assert new[0][0] == new[1][0] == "ws://h:8080/viewer/ka9q-web/"
    assert new[2][1] == "ws://h:8080/viewer/gmag-webui/ws"
    # At the root (direct port) the new expressions give the old URLs:
    # ka9q-web's adds only the trailing "/" a browser adds to a bare host.
    for i in (3, 4):
        assert new[i][0] == "ws://h:8080/" and old[i][0] == "ws://h:8080"
        assert new[i][1] == old[i][1] == "ws://h:8080/ws"
    assert new[5][1] == "ws://h:8080/ws"      # gmag tolerates an empty pathname


# ---------------------------------------------------------- through the route

RADIO = FIXTURE_FOR["ka9q-web"].read_bytes()
RADIO_GZ = gzip.compress(RADIO)
STALE_FEED = b"export function sameOriginFeed(loc) { return 'ws://elsewhere/ws'; }\n"


def _upstream_app(seen: list):
    async def any_path(request):
        seen.append({"path": request.url.path, "headers": dict(request.headers)})
        p = request.url.path
        if p == "/radio.js" and request.url.query == "missing=1":
            return Response(b"", status_code=404)
        if p == "/radio.js":
            # gzip regardless of what was asked: the proxy must still decode.
            return Response(RADIO_GZ, media_type="application/javascript",
                            headers={"content-encoding": "gzip"})
        if p == "/js/feed.js":
            return Response(STALE_FEED, media_type="application/javascript")
        if p == "/spectrum.js":
            return Response(RADIO_GZ, media_type="application/javascript",
                            headers={"content-encoding": "gzip"})
        return Response(b"x")

    return Starlette(routes=[Route("/{p:path}", any_path, methods=["GET", "HEAD"])])


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
    yield server.servers[0].sockets[0].getsockname()[1], seen
    server.should_exit = True
    t.join(timeout=5)


@pytest.fixture
def proxy(tmp_settings, write_viewers, upstream):
    port, seen = upstream
    seen.clear()
    write_viewers([{"name": "ka9q-web", "port": port, "active": "active"},
                   {"name": "gmag-webui", "port": port, "active": "active"},
                   {"name": "up", "port": port, "active": "active"}])
    with TestClient(appmod.create_app(tmp_settings, control_socket=None)) as c:
        yield c, seen


def _raw(c, url, **kw):
    with c.stream("GET", url, **kw) as r:
        return r.status_code, r.headers, b"".join(r.iter_raw())


def test_rewritten_asset_decoded_and_relength(proxy):
    c, seen = proxy
    status, headers, body = _raw(c, "/viewer/ka9q-web/radio.js",
                                 headers={"Accept-Encoding": "gzip"})
    assert status == 200
    assert _rw("ka9q-web").new.encode() in body
    assert _rw("ka9q-web").old.encode() not in body
    assert "content-encoding" not in headers
    assert headers["content-length"] == str(len(body))
    assert headers["content-type"].startswith("application/javascript")
    assert seen[-1]["headers"]["accept-encoding"] == "identity"


def test_rewrite_ignores_the_query(proxy):
    c, _ = proxy
    status, _, body = _raw(c, "/viewer/ka9q-web/radio.js?v=2")
    assert status == 200 and _rw("ka9q-web").new.encode() in body


def test_other_paths_still_stream_raw(proxy):
    c, seen = proxy
    status, headers, body = _raw(c, "/viewer/ka9q-web/spectrum.js",
                                 headers={"Accept-Encoding": "gzip"})
    assert status == 200 and headers["content-encoding"] == "gzip" and body == RADIO_GZ
    assert seen[-1]["headers"]["accept-encoding"] == "gzip"


def test_other_kind_is_not_rewritten(proxy):
    c, _ = proxy
    status, headers, body = _raw(c, "/viewer/up/radio.js", headers={"Accept-Encoding": "gzip"})
    assert status == 200 and headers["content-encoding"] == "gzip" and body == RADIO_GZ


def test_non_200_is_not_rewritten(proxy, caplog):
    c, _ = proxy
    with caplog.at_level(logging.WARNING):
        r = c.get("/viewer/ka9q-web/radio.js?missing=1")
    assert r.status_code == 404 and r.content == b""
    assert "lacks the line" not in caplog.text


def test_miss_through_route_is_200_and_warns_once(proxy, caplog):
    c, _ = proxy
    with caplog.at_level(logging.WARNING):
        r1 = c.get("/viewer/gmag-webui/js/feed.js")
        r2 = c.get("/viewer/gmag-webui/js/feed.js")
    assert r1.status_code == r2.status_code == 200
    assert r1.content == r2.content == STALE_FEED
    msgs = [r.getMessage() for r in caplog.records
            if r.levelno == logging.WARNING and "lacks the line" in r.getMessage()]
    assert msgs == ["viewer gmag-webui: js/feed.js lacks the line the front door rewrites;"
                    " its live data will not work through /viewer/gmag-webui/"
                    " — use the direct port"]


def test_head_is_not_rewritten(proxy, caplog):
    """HEAD carries no body to rewrite; it streams as before and never warns."""
    c, _ = proxy
    with caplog.at_level(logging.WARNING):
        r = c.head("/viewer/ka9q-web/radio.js")
    assert r.status_code == 200
    assert "lacks the line" not in caplog.text
