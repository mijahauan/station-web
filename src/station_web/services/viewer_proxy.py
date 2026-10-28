"""Front door for sigmond's viewers: find the localhost port behind a name.

The port comes only from the ``viewers`` snapshot.  Nothing here probes a
port, and nothing a browser sends chooses a host or a port.  A stale snapshot
still resolves, because ports rarely move and the proxy's own connect error
reports a dead viewer.  ``active`` does not gate resolution for the same
reason.
"""
from __future__ import annotations

import logging
import posixpath
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from urllib.parse import unquote

import anyio
import httpx
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from station_web.config import Settings
from station_web.services.snapshot import read_snapshot

logger = logging.getLogger(__name__)

UPSTREAM_HOST = "127.0.0.1"


@dataclass(frozen=True)
class Target:
    name: str
    port: int
    kind: str


class Refusal(Exception):
    """A refusal with a fixed, user-facing message (never exception text)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _kind(name: str) -> str:
    if name == "ka9q-web" or name.startswith("ka9q-web-"):
        return "ka9q-web"
    if name == "gmag-webui":
        return "gmag"
    return "other"


def _valid_port(port) -> bool:
    return (isinstance(port, int) and not isinstance(port, bool)
            and 1 <= port <= 65535)


def resolve(name: str, settings: Settings) -> Target:
    snap = read_snapshot("viewers", settings)
    data = snap.get("data")
    if not snap.get("available") or not isinstance(data, dict) \
            or not isinstance(data.get("viewers"), list):
        raise Refusal(503, "viewer list unavailable")
    row = next((r for r in data["viewers"]
                if isinstance(r, dict) and r.get("name") == name), None)
    if row is None:
        raise Refusal(404, "no such viewer on this station")
    conflicts = data.get("conflicts")
    for c in conflicts if isinstance(conflicts, list) else []:
        names = c.get("names") if isinstance(c, dict) else None
        if isinstance(names, list) and name in names:
            raise Refusal(409, "port %s is claimed by %s" % (
                c.get("port"), " and ".join(str(n) for n in names)))
    port = row.get("port")
    if not _valid_port(port):
        raise Refusal(502, "viewer has no usable port")
    return Target(name=name, port=port, kind=_kind(name))


# ---------------------------------------------------------------------------
# The HTTP proxy: header rules and the one upstream client.
#
# This module and routers/viewer.py hold the only outbound HTTP in
# station-web, and it reaches only UPSTREAM_HOST, at a port read from the
# snapshot (tests/test_hygiene.py names the exception).
# ---------------------------------------------------------------------------

HOP_BY_HOP = frozenset({"connection", "keep-alive", "proxy-authenticate",
                        "proxy-authorization", "te", "trailer", "transfer-encoding",
                        "upgrade"})
MAX_REQUEST_BODY = 1 << 20

#: Request headers the viewer never sees.  Cookies stay out so a future
#: front-door login never reaches a viewer; origin and referer name
#: station-web's own address, which means nothing to the viewer.  httpx
#: computes content-length from the body it sends.
_DROP_REQUEST = frozenset({"host", "cookie", "origin", "referer", "content-length",
                           "expect", "forwarded", "x-forwarded-for",
                           "x-forwarded-proto", "x-forwarded-host",
                           "x-forwarded-prefix"})

#: Response headers that act on station-web's whole origin, not on one
#: viewer: a viewer must not widen a service worker's scope, wipe the
#: origin's storage, or open the origin to other sites (access-control-*,
#: dropped by prefix in response_headers).
_DROP_RESPONSE = frozenset({"service-worker-allowed", "clear-site-data"})

TIMEOUT = httpx.Timeout(connect=3.0, read=60.0, write=10.0, pool=3.0)


def _pairs(headers) -> Iterable[tuple[str, str]]:
    """Every (name, value) pair, duplicates kept (httpx and Starlette both
    expose multi_items or a list-valued items)."""
    if hasattr(headers, "multi_items"):
        return headers.multi_items()
    if isinstance(headers, Mapping):
        return headers.items()
    return headers


def _connection_tokens(pairs: list[tuple[str, str]]) -> set[str]:
    return {t.strip().lower() for k, v in pairs if k.lower() == "connection"
            for t in v.split(",") if t.strip()}


def prefix_of(target: Target) -> str:
    return f"/viewer/{target.name}"


def request_headers(incoming: Mapping[str, str], target: Target, client_host: str,
                    scheme: str) -> dict:
    pairs = [(k, v) for k, v in _pairs(incoming)]
    drop = HOP_BY_HOP | _DROP_REQUEST | _connection_tokens(pairs)
    out: dict[str, str] = {}
    for k, v in pairs:
        k = k.lower()
        if k in drop:
            continue
        out[k] = f"{out[k]}, {v}" if k in out else v
    # httpx adds its own Accept-Encoding when none is set; a browser that
    # named none must not get a gzip body it never asked for.
    if not out.get("accept-encoding"):
        out["accept-encoding"] = "identity"
    out["host"] = f"{UPSTREAM_HOST}:{target.port}"
    out["x-forwarded-prefix"] = prefix_of(target)
    out["x-forwarded-for"] = client_host
    out["x-forwarded-proto"] = scheme
    return out


#: Set-Cookie attributes relayed, by trimmed lower-case name: those that
#: take a value, and the flags.  Domain and anything unrecognised go.
_COOKIE_VALUED = {"expires": "Expires", "max-age": "Max-Age", "samesite": "SameSite",
                  "path": "Path"}
_COOKIE_FLAGS = {"secure": "Secure", "httponly": "HttpOnly", "partitioned": "Partitioned"}


def _prefixed_cookie_path(val: str, prefix: str) -> str | None:
    """``val`` moved under ``prefix``, or None when it would leave it."""
    decoded = unquote(val)
    if not decoded.startswith("/") or decoded.startswith("//"):
        return None
    if ".." in decoded.split("/") or "\\" in decoded:
        return None
    norm = posixpath.normpath(decoded)
    tail = "/" if norm == "/" else norm + ("/" if decoded.endswith("/") else "")
    return prefix + tail


def _cookie_path(value: str, prefix: str) -> str | None:
    """Rebuild a Set-Cookie value from its trimmed, known attributes, with
    Path moved under ``prefix``; None drops the cookie.  Domain goes, so the
    cookie stays host-only on station-web; so does any attribute this cannot
    parse or does not know."""
    pair, *attrs = value.split(";")
    kept: list[tuple[str, str | None]] = []
    for attr in attrs:
        name, sep, val = attr.partition("=")
        key = name.strip().lower()
        val = val.strip()
        if key in _COOKIE_FLAGS:
            kept.append((_COOKIE_FLAGS[key], None))
        elif key in _COOKIE_VALUED and sep and val:
            if key == "path":
                val = _prefixed_cookie_path(val, prefix)
                if val is None:
                    return None
            kept.append((_COOKIE_VALUED[key], val))
    # A browser honours the last Path; relay that one alone.
    paths = [i for i, (k, _) in enumerate(kept) if k == "Path"]
    kept = [a for i, a in enumerate(kept) if a[0] != "Path" or i == paths[-1]]
    return "; ".join([pair.strip()] + [k if v is None else f"{k}={v}" for k, v in kept])


def response_headers(upstream: Mapping[str, str], prefix: str) -> list[tuple[str, str]]:
    """The viewer's response headers as station-web relays them.  The router
    passes values decoded as latin-1, so every byte round-trips unchanged."""
    pairs = [(k, v) for k, v in _pairs(upstream)]
    drop = HOP_BY_HOP | _DROP_RESPONSE | _connection_tokens(pairs)
    out: list[tuple[str, str]] = []
    for k, v in pairs:
        k = k.lower()
        if k in drop or k.startswith("access-control-"):
            continue
        if k == "location" and v.startswith("/") and not v.startswith("//"):
            v = prefix + v
        elif k == "set-cookie":
            v = _cookie_path(v, prefix)
            if v is None:
                logger.info("viewer %s: dropped a cookie whose path leaves the prefix", prefix)
                continue
        out.append((k, v))
    return out


def make_client() -> httpx.AsyncClient:
    """The app's one upstream client.  trust_env=False: an HTTP(S)_PROXY in
    the service environment must never carry loopback traffic elsewhere."""
    return httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False, trust_env=False)


async def open_upstream(client: httpx.AsyncClient, method: str, target: Target,
                        raw_path: bytes, query: bytes, headers: dict,
                        body: bytes) -> httpx.Response:
    """Send one request to the viewer and return the streaming response.
    Every transport failure becomes a fixed 502; the cause stays in the log."""
    raw = b"/" + raw_path + (b"?" + query if query else b"")
    url = httpx.URL(scheme="http", host=UPSTREAM_HOST, port=target.port, raw_path=raw)
    req = client.build_request(method, url, headers=headers,
                               content=body if body else None)
    try:
        return await client.send(req, stream=True)
    except httpx.TransportError as exc:
        logger.warning("viewer %s on port %d: %s: %s", target.name, target.port,
                       type(exc).__name__, exc)
        raise Refusal(502, f"viewer not answering on port {target.port}") from None


# ---------------------------------------------------------------------------
# The WebSocket relay.  Like the HTTP proxy it reaches only UPSTREAM_HOST at
# the snapshot's port; the router checks the origin before it gets here.
# ---------------------------------------------------------------------------

#: The largest single message either side may send through the relay.
WS_MAX_MESSAGE = 16 << 20
WS_OPEN_TIMEOUT = 3


def ws_upstream_url(target: Target, path: str, query: str) -> str:
    """``path`` and ``query`` arrive still percent-encoded and pick only the
    resource on the viewer, never the host or the port."""
    return f"ws://{UPSTREAM_HOST}:{target.port}/{path}" + (f"?{query}" if query else "")


def ws_close_code(code: int | None) -> int:
    """A close code the other side can be sent.  1005 (no code given) and
    no code at all mean a clean close; 1006, 1015 and any other reserved or
    out-of-range code mean an abnormal one."""
    if code is None or code == 1005:
        return 1000
    if code in (1000, 1001, 1002, 1003) or 1007 <= code <= 1014 or 3000 <= code <= 4999:
        return code
    return 1011


async def open_ws_upstream(target: Target, path: str, query: str,
                           subprotocols: list[str]):
    """Open the viewer's WebSocket.  ``websockets`` sends Host as
    127.0.0.1:<port> and, with origin=None, no Origin, so the viewer sees a
    local client.  proxy=None: a proxy in the service environment must never
    carry loopback traffic elsewhere.  Every failure becomes a fixed Refusal;
    the cause stays in the log."""
    try:
        return await ws_connect(
            ws_upstream_url(target, path, query), origin=None,
            subprotocols=subprotocols or None, proxy=None,
            open_timeout=WS_OPEN_TIMEOUT, ping_interval=None,
            max_size=WS_MAX_MESSAGE)
    except (OSError, TimeoutError, WebSocketException) as exc:
        logger.warning("viewer %s on port %d: websocket: %s: %s", target.name,
                       target.port, type(exc).__name__, exc)
        raise Refusal(502, f"viewer not answering on port {target.port}") from None


async def ws_relay(browser, upstream) -> None:
    """Pump messages both ways until either side closes, then close the
    other with the same code.  ``browser`` is an accepted Starlette
    WebSocket; ``upstream`` a websockets client connection.  Text stays
    text and binary stays binary.

    The pumps only pump.  The first to stop cancels the other, and all
    closing happens once, afterwards, under a shield: a close sent from
    inside a pump could be cancelled half-done by the other pump's exit."""
    #: Set when the browser side ended: the code to pass to the viewer.
    browser_ended: dict = {}

    async def browser_to_upstream():
        try:
            while True:
                msg = await browser.receive()
                if msg["type"] == "websocket.disconnect":
                    browser_ended["code"] = msg.get("code")
                    return
                if msg.get("text") is not None:
                    await upstream.send(msg["text"])
                elif msg.get("bytes") is not None:
                    await upstream.send(msg["bytes"])
        except ConnectionClosed:
            return          # the viewer closed; its code goes to the browser
        except Exception:
            browser_ended["code"] = 1011

    async def upstream_to_browser():
        try:
            async for msg in upstream:
                if isinstance(msg, str):
                    await browser.send_text(msg)
                else:
                    await browser.send_bytes(msg)
        except ConnectionClosed:
            return
        except Exception:
            browser_ended["code"] = 1001    # the browser went away mid-send

    try:
        async with anyio.create_task_group() as tg:
            async def run(pump):
                await pump()
                tg.cancel_scope.cancel()
            tg.start_soon(run, browser_to_upstream)
            tg.start_soon(run, upstream_to_browser)
    finally:
        with anyio.CancelScope(shield=True):
            if "code" in browser_ended:
                await upstream.close(ws_close_code(browser_ended["code"]))
            else:
                # close() does nothing to a connection the viewer already
                # closed, and returns once the TCP connection ends: only then
                # does close_code hold the viewer's code (None before; 1005
                # if it sent none; 1006 if it dropped without a close frame).
                await upstream.close()
                try:
                    await browser.close(ws_close_code(upstream.close_code))
                except Exception:
                    pass
