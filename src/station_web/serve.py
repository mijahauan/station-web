"""The unit's ExecStart: uvicorn over create_app, SIGHUP re-reads the config."""
from __future__ import annotations

import logging
import signal

import uvicorn

from station_web.app import create_app
from station_web.config import Settings, config, load_settings

logger = logging.getLogger(__name__)


def _make_reload(settings: Settings):
    """The SIGHUP handler, factored out so a test can call it directly.

    ``ExecReload=/bin/kill -HUP $MAINPID`` used to re-read the log level and
    nothing else, so an operator who edited [station] or [data] and reloaded
    got the old values with a new log level — the reload looked like it
    worked.  Re-read the whole file and publish it through ``config``, the
    proxy every router reads, so one signal reconfigures the process.
    """
    def _reload(signum, frame):
        from station_web.cli import resolve_log_level
        fresh = load_settings(settings.config_path)
        config.use(fresh)
        level = resolve_log_level(None, fresh)
        logging.getLogger().setLevel(level)
        logger.info("SIGHUP: settings reloaded from %s, log level %s",
                    settings.config_path, level)
    return _reload


def run_server(settings: Settings, instance: str = "default", log_level: str = "INFO") -> int:
    # First, before any router can read it: ``config`` is initialised at import
    # from the DEFAULT path, so a `serve -c <other>` would otherwise serve the
    # requested port from these settings while every router read /etc's file.
    config.use(settings)
    signal.signal(signal.SIGHUP, _make_reload(settings))
    app = create_app(settings)
    uvicorn.run(app, host=settings.web_bind, port=settings.web_port,
                log_level=log_level.lower(), access_log=False)
    return 0
