"""FastAPI application factory.  No CORS (same origin), no import-time
side effects, no writes.  Routers register here (Task 7 onward)."""
from __future__ import annotations

import asyncio
import importlib
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from station_web.config import Settings
from station_web.control_socket import ControlSocketServer
from station_web.services import viewer_proxy

try:
    from systemd import daemon as systemd_daemon
except ImportError:                                 # pragma: no cover
    systemd_daemon = None

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent / "static"

#: Snapshot served by the control socket.  Task 11 refreshes ``products``.
STATUS: dict = {"ready": False, "products": {}, "updated_at": None, "control_socket": None}


class _GZipExceptViewers:
    """GZip for station-web's own responses; /viewer/ passes through as the
    viewer sent it.  Starlette's GZipMiddleware compresses any large
    uncompressed body, and a viewer's encoding belongs to the viewer."""

    def __init__(self, app, **kw):
        self.app = app
        self.gzip = GZipMiddleware(app, **kw)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path", "").startswith("/viewer/"):
            await self.app(scope, receive, send)
        else:
            await self.gzip(scope, receive, send)


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
        app.state.viewer_client = viewer_proxy.make_client()
        logger.info("station-web serving %s for %s", settings.data_root, settings.station.get("callsign", "?"))
        try:
            yield
        finally:
            task.cancel()
            await app.state.viewer_client.aclose()
            app.state.viewer_client = None
            if sock:
                sock.stop()

    app = FastAPI(title="station-web", version="0.1.0", docs_url="/api/docs",
                  redoc_url="/api/redoc", lifespan=lifespan)
    app.state.settings = settings
    # The JSON series run to megabytes; gzip shrinks them several-fold.
    # /viewer/ responses keep the viewer's own encoding (_GZipExceptViewers).
    app.add_middleware(_GZipExceptViewers, minimum_size=1024)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    def page(name: str):
        p = STATIC_DIR / name
        if p.exists():
            return FileResponse(p)
        return HTMLResponse(f"<h1>station-web</h1><p>{name} not shipped</p>", status_code=404)

    @app.get("/", response_class=HTMLResponse)
    async def root():
        return page("home.html")

    @app.get("/check", response_class=HTMLResponse)
    async def check_page():
        return page("check.html")

    @app.get("/phase")
    async def phase_page():
        # phase.html folded into the Ionosphere page (stage 2 §6)
        return RedirectResponse("/static/ionosphere.html#phase", status_code=307)

    @app.get("/grape", response_class=HTMLResponse)
    async def grape_page():
        return page("grape.html")

    @app.get("/health")
    async def health():
        return JSONResponse({"status": "ok", "data_root": str(settings.data_root),
                             "callsign": settings.station.get("callsign", "")})

    _register_routers(app)
    return app


#: Routers every station serves.  They need nothing scientific, so an import
#: failure among them is a defect and stays fatal.
CORE_ROUTERS = ("docs", "grape", "timing", "status", "features", "viewer")
#: Routers that need hamsci-dsp, hamsci-physics and numpy/scipy (the
#: ``science`` extra).  A station without them skips these routers.
SCIENCE_ROUTERS = ("station", "stations", "health", "metrology", "stability", "propagation",
                   "space_weather", "correlations", "physics", "tec", "tid", "dashboard", "phase",
                   "ionogram", "chrony")
#: The top-level packages the ``science`` extra installs.
SCIENCE_PACKAGES = frozenset({"hamsci_dsp", "hamsci_physics", "numpy", "scipy"})


def science_missing(exc: BaseException) -> bool:
    """True when ``exc`` says a science package is absent.  Any other import
    failure (a renamed function, a typo in station_web) is a defect."""
    return (isinstance(exc, ModuleNotFoundError) and bool(exc.name)
            and exc.name.split(".")[0] in SCIENCE_PACKAGES)


def _register_routers(app: FastAPI) -> list[str]:
    """Include each science router whose science packages are installed,
    then the core routers.  Returns the science routers it skipped;
    ``/api/features`` reports the same list.  Any import failure other than
    an absent science package is fatal, science router or core."""
    skipped: list[str] = []
    for name in SCIENCE_ROUTERS:
        try:
            mod = importlib.import_module(f"station_web.routers.{name}")
        except Exception as exc:
            if not science_missing(exc):
                logger.error("science router %r failed to import: %s", name, exc)
                raise
            logger.warning("science router %r not loaded (%s) — its pages stay off "
                           "on this station", name, exc)
            skipped.append(name)
            continue
        app.include_router(mod.router, prefix="/api")
    app.state.skipped_routers = skipped
    for name in CORE_ROUTERS:
        mod = importlib.import_module(f"station_web.routers.{name}")
        if name in ("docs", "viewer"):
            app.include_router(mod.router)       # own prefix: /api/living-docs, /viewer
        else:
            app.include_router(mod.router, prefix="/api")
    return skipped
