"""The front door: each local viewer at /viewer/<name>/ on station-web's port.

The name resolves to a localhost port through the ``viewers`` snapshot
(services/viewer_proxy.resolve); nothing the browser sends picks a host or
a port.  The proxy streams the viewer's own bytes back untouched, its
content-encoding included, and adds no control of its own.

These routes run as coroutines, unlike every other router here: they wait
on a socket, not on the disk, and a thread per open viewer stream would
exhaust the threadpool.  The one disk read (the snapshot) runs in the
threadpool.  tests/test_hygiene.py names this file as the exception.
"""
from __future__ import annotations

import logging
from urllib.parse import quote

import anyio
from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

from station_web.security import same_origin
from station_web.services import viewer_proxy
from station_web.services.viewer_proxy import Refusal

logger = logging.getLogger(__name__)

#: Methods any site may start: a page elsewhere can already GET a viewer by
#: linking to it.  Every other method needs a same-origin request.
SAFE_METHODS = frozenset({"GET", "HEAD"})
CROSS_SITE_REFUSED = "request refused: started by another site"
#: WebSocket close codes for a refusal before accept (the browser sees the
#: handshake fail): 1008 for a policy refusal (another site, or a name that
#: does not resolve), 1011 when the viewer does not answer.
WS_POLICY = 1008
WS_UPSTREAM_DOWN = 1011

router = APIRouter(prefix="/viewer", tags=["viewers"])


class _TooLarge(Exception):
    pass


class _UpstreamStream(StreamingResponse):
    """A streaming response that always closes its upstream response, even
    when the browser goes away mid-stream and the send is cancelled."""

    def __init__(self, upstream, **kw):
        super().__init__(upstream.aiter_raw(), **kw)
        self._upstream = upstream

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                await self._upstream.aclose()


def _refuse(r: Refusal) -> Response:
    return PlainTextResponse(r.message, status_code=r.status)


async def _read_body(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > viewer_proxy.MAX_REQUEST_BODY:
        raise _TooLarge
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > viewer_proxy.MAX_REQUEST_BODY:
            raise _TooLarge
        chunks.append(chunk)
    return b"".join(chunks)


def _rest_of_path(request, path: str) -> bytes:
    """The raw (still percent-encoded) path after /viewer/<name>/."""
    raw = request.scope.get("raw_path") or request.url.path.encode()
    parts = raw.split(b"/", 3)          # b"", b"viewer", <name>, <rest>
    return parts[3] if len(parts) == 4 else path.encode()


@router.get("/{name}", include_in_schema=False)
async def viewer_bare(name: str, request: Request):
    q = request.url.query
    return RedirectResponse(f"/viewer/{quote(name, safe='')}/" + (f"?{q}" if q else ""),
                            status_code=308)


@router.api_route("/{name}/{path:path}", methods=["GET", "HEAD", "POST"],
                  include_in_schema=False)
async def viewer_http(name: str, path: str, request: Request):
    if request.method not in SAFE_METHODS and not same_origin(request.headers):
        return PlainTextResponse(CROSS_SITE_REFUSED, status_code=403)
    try:
        target = await run_in_threadpool(viewer_proxy.resolve, name,
                                         request.app.state.settings)
        try:
            body = await _read_body(request)
        except _TooLarge:
            return PlainTextResponse("request too large", status_code=413)
        client = getattr(request.app.state, "viewer_client", None)
        if client is None:
            raise Refusal(503, "viewer proxy not ready")
        headers = viewer_proxy.request_headers(
            request.headers, target, request.client.host if request.client else "",
            request.url.scheme)
        upstream = await viewer_proxy.open_upstream(
            client, request.method, target, _rest_of_path(request, path),
            request.scope.get("query_string", b""), headers, body)
    except Refusal as r:
        return _refuse(r)
    try:
        # Raw bytes decoded as latin-1 round-trip exactly; httpx's own str
        # values decode UTF-8 and would not re-encode.
        raw = [(k.decode("latin-1"), v.decode("latin-1")) for k, v in upstream.headers.raw]
        headers = [(k.encode("latin-1"), v.encode("latin-1"))
                   for k, v in viewer_proxy.response_headers(
                       raw, viewer_proxy.prefix_of(target))]
    except Exception:
        logger.exception("viewer %s: could not relay the response headers", target.name)
        with anyio.CancelScope(shield=True):
            await upstream.aclose()
        return PlainTextResponse(f"viewer not answering on port {target.port}",
                                 status_code=502)
    resp = _UpstreamStream(upstream, status_code=upstream.status_code)
    resp.raw_headers = headers
    return resp


@router.websocket("/{name}/{path:path}")
async def viewer_ws(websocket: WebSocket, name: str, path: str):
    """Relay a viewer's WebSocket, for a page station-web served only.
    ka9q-web creates radiod channels on request, so a page on another site
    must not reach it: the origin check runs before any upstream connect."""
    if not same_origin(websocket.headers):
        await websocket.close(code=WS_POLICY)
        return
    try:
        target = await run_in_threadpool(viewer_proxy.resolve, name,
                                         websocket.app.state.settings)
    except Refusal:
        await websocket.close(code=WS_POLICY)
        return
    try:
        upstream = await viewer_proxy.open_ws_upstream(
            target, _rest_of_path(websocket, path).decode("latin-1"),
            websocket.scope.get("query_string", b"").decode("latin-1"),
            list(websocket.scope.get("subprotocols") or []))
    except Refusal:
        await websocket.close(code=WS_UPSTREAM_DOWN)
        return
    try:
        await websocket.accept(subprotocol=upstream.subprotocol)
    except Exception:
        with anyio.CancelScope(shield=True):
            await upstream.close()
        raise
    await viewer_proxy.ws_relay(websocket, upstream)
