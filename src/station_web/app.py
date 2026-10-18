"""FastAPI application factory.  No CORS (same origin), no import-time
side effects, no writes.  Routers register here (Task 7 onward)."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from station_web.config import Settings
from station_web.control_socket import ControlSocketServer

try:
    from systemd import daemon as systemd_daemon
except ImportError:                                 # pragma: no cover
    systemd_daemon = None

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent / "static"

#: Snapshot served by the control socket.  Task 11 refreshes ``products``.
STATUS: dict = {"ready": False, "products": {}, "updated_at": None, "control_socket": None}


async def _watchdog():
    while True:
        if systemd_daemon:
            systemd_daemon.notify("WATCHDOG=1")
        await asyncio.sleep(10)


def create_app(settings: Settings, control_socket: Path | None = Path("/run/station-web/control.sock")) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        sock = None
        if control_socket:
            sock = ControlSocketServer(control_socket, status_fn=lambda: STATUS)
            try:
                sock.start()
                STATUS["control_socket"] = str(control_socket)
            except Exception as exc:          # noqa: BLE001 — auxiliary channel; the pages are the product
                logger.error("control socket %s failed to start: %s — serving without it", control_socket, exc)
                STATUS["control_socket"] = f"failed: {exc}"
                sock = None
        STATUS["ready"] = True
        STATUS["updated_at"] = datetime.now(timezone.utc).isoformat()
        if systemd_daemon:
            systemd_daemon.notify("READY=1")
        task = asyncio.create_task(_watchdog())
        logger.info("station-web serving %s for %s", settings.data_root, settings.station.get("callsign", "?"))
        try:
            yield
        finally:
            task.cancel()
            if sock:
                sock.stop()

    app = FastAPI(title="station-web", version="0.1.0", docs_url="/api/docs",
                  redoc_url="/api/redoc", lifespan=lifespan)
    app.state.settings = settings
    # The JSON series run to megabytes; gzip shrinks them several-fold.
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    def page(name: str):
        p = STATIC_DIR / name
        if p.exists():
            return FileResponse(p)
        return HTMLResponse(f"<h1>station-web</h1><p>{name} not shipped</p>", status_code=404)

    @app.get("/", response_class=HTMLResponse)
    async def root():
        return page("index.html")

    @app.get("/phase", response_class=HTMLResponse)
    async def phase_page():
        return page("phase.html")

    @app.get("/grape", response_class=HTMLResponse)
    async def grape_page():
        return page("grape.html")

    @app.get("/health")
    async def health():
        return JSONResponse({"status": "ok", "data_root": str(settings.data_root),
                             "callsign": settings.station.get("callsign", "")})

    _register_routers(app)
    return app


def _register_routers(app: FastAPI) -> None:
    from station_web.routers import (chrony, correlations, dashboard, docs, grape, health,
                                     ionogram, metrology, phase, physics, propagation,
                                     space_weather, stability, station, stations, tec, tid,
                                     timing)
    for mod in (station, stations, health, metrology, stability, propagation, space_weather,
                correlations, physics, tec, tid, dashboard, phase, grape, ionogram, chrony,
                timing):
        app.include_router(mod.router, prefix="/api")
    app.include_router(docs.router)          # carries its own /api/living-docs prefix
