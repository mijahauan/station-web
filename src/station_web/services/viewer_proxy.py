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

import httpx

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


def _cookie_path(value: str, prefix: str) -> str | None:
    """Rewrite a Set-Cookie value's Path under ``prefix``; None drops it.
    A Domain attribute goes: the cookie stays host-only on station-web."""
    parts = value.split(";")
    out = [parts[0]]
    for attr in parts[1:]:
        name, sep, val = attr.strip().partition("=")
        if name.lower() == "domain":
            continue
        if name.lower() != "path":
            out.append(attr)
            continue
        val = val.strip()
        decoded = unquote(val)
        if not decoded.startswith("/") or decoded.startswith("//"):
            return None
        if ".." in decoded.split("/") or "\\" in decoded:
            return None
        norm = posixpath.normpath(decoded)
        tail = "/" if norm == "/" else norm + ("/" if decoded.endswith("/") else "")
        out.append(f" {name}={prefix}{tail}")
    return ";".join(out)


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
