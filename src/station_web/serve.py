"""The unit's ExecStart: uvicorn over create_app, SIGHUP re-reads the log level."""
from __future__ import annotations

import logging
import os
import signal

import uvicorn

from station_web.app import create_app
from station_web.config import Settings

logger = logging.getLogger(__name__)


def _install_sighup(settings: Settings) -> None:
    def _reload(signum, frame):
        from station_web.cli import resolve_log_level
        level = resolve_log_level(None, settings)
        logging.getLogger().setLevel(level)
        logger.info("SIGHUP: log level now %s", level)
    signal.signal(signal.SIGHUP, _reload)


def run_server(settings: Settings, instance: str = "default", log_level: str = "INFO") -> int:
    _install_sighup(settings)
    app = create_app(settings)
    uvicorn.run(app, host=settings.web_bind, port=settings.web_port,
                log_level=log_level.lower(), access_log=False)
    return 0
