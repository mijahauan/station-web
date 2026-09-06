"""CLIENT-CONTRACT §13 control surface: HTTP over a unix socket.

stdlib only, one thread, answers from the ``status_fn`` snapshot the app
refreshes; never touches the data root itself.  Mode 0660, group
``sigmond`` when that group exists (tests pass ``group=None``).
"""
from __future__ import annotations

import grp
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn, UnixStreamServer
from typing import Callable

logger = logging.getLogger(__name__)


class _Server(ThreadingMixIn, UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, path: str, handler, status_fn):
        self.status_fn = status_fn
        super().__init__(path, handler)


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, fmt, *args):            # journal, not stderr spam
        logger.debug("control: " + fmt, *args)

    def _send(self, code: int, body: str, ctype: str = "application/json") -> None:
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):                             # noqa: N802
        route = self.path.split("?", 1)[0]
        if route == "/healthz":
            return self._send(200, json.dumps({"ok": True}))
        status = self.server.status_fn()
        if route == "/readyz":
            ready = bool(status.get("ready", False))
            return self._send(200 if ready else 503, json.dumps({"ready": ready}))
        if route == "/status":
            return self._send(200, json.dumps(status))
        if route == "/metrics":
            lines = ["station_web_up 1"]
            for name, verdict in (status.get("products") or {}).items():
                lines.append(f'station_web_product_valid{{product="{name}"}} '
                             f'{1 if verdict == "VALID" else 0}')
            return self._send(200, "\n".join(lines) + "\n", "text/plain; version=0.0.4")
        self._send(404, json.dumps({"error": "not found"}))


class ControlSocketServer:
    def __init__(self, path: Path, status_fn: Callable[[], dict], group: str | None = "sigmond"):
        self.path = Path(path)
        self.status_fn = status_fn
        self.group = group
        self._srv: _Server | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.path.unlink()
        self._srv = _Server(str(self.path), _Handler, self.status_fn)
        os.chmod(self.path, 0o660)
        if self.group:
            try:
                os.chown(self.path, -1, grp.getgrnam(self.group).gr_gid)
            except (KeyError, PermissionError) as exc:
                logger.warning("control socket group %s not applied: %s", self.group, exc)
        self._thread = threading.Thread(target=self._srv.serve_forever, name="control-socket", daemon=True)
        self._thread.start()
        logger.info("control socket listening on %s", self.path)

    def stop(self) -> None:
        if self._srv:
            self._srv.shutdown()
            self._srv.server_close()
            self._srv = None
        if self.path.exists():
            self.path.unlink()
