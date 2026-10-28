#!/usr/bin/env python3
"""station-web command line.

    version    | inventory | validate     — CLIENT-CONTRACT §3 (all take --json)
    serve                                   — run the web application (the unit's ExecStart)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from station_web.config import load_settings

LOG_LEVEL_ENV = "STATION_WEB_LOG_LEVEL"          # §11
GENERIC_LOG_LEVEL_ENV = "CLIENT_LOG_LEVEL"


def resolve_log_level(flag: str | None, settings) -> str:
    """§11 precedence: --log-level, STATION_WEB_LOG_LEVEL, CLIENT_LOG_LEVEL,
    [logging] level, INFO."""
    for candidate in (flag, os.environ.get(LOG_LEVEL_ENV),
                      os.environ.get(GENERIC_LOG_LEVEL_ENV), settings.log_level, "INFO"):
        if candidate:
            return candidate.upper()
    return "INFO"


def _settings(args):
    return load_settings(Path(args.config) if getattr(args, "config", None) else None)


def _handle_version(args) -> int:
    from station_web.contract import _version
    info = {"name": "station-web", "version": _version(), "python": sys.version.split()[0]}
    print(json.dumps(info, indent=2) if args.json else f"station-web {info['version']}")
    return 0


def _handle_inventory(args) -> int:
    from station_web.contract import build_inventory
    print(json.dumps(build_inventory(_settings(args)), indent=2))
    return 0


def _handle_validate(args) -> int:
    from station_web.contract import build_validate
    payload = build_validate(_settings(args))
    print(json.dumps(payload, indent=2))
    return 0 if payload["ok"] else 1


def _handle_serve(args) -> int:
    from station_web.serve import run_server           # Task 6
    return run_server(_settings(args), instance=args.instance,
                      log_level=resolve_log_level(args.log_level, _settings(args)))


def build_parser() -> argparse.ArgumentParser:
    # -c/--config and --log-level are accepted before OR after the verb (the
    # contract test harness always puts them after), so they live on a shared
    # parent parser inherited by both the top-level parser and every subparser.
    # The top level defaults both to None; each verb's copy defaults to
    # SUPPRESS, or a verb would overwrite a value given before it.  Parent
    # parsers share action objects, hence two parents rather than one.
    def shared(default):
        parent = argparse.ArgumentParser(add_help=False)
        parent.add_argument("-c", "--config", default=default,
                            help="config path (default $STATION_WEB_CONFIG or /etc/station-web/config.toml)")
        parent.add_argument("--log-level", default=default)
        return parent
    common = shared(argparse.SUPPRESS)
    p = argparse.ArgumentParser(prog="station-web", parents=[shared(None)])
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("version", "inventory", "validate"):
        sp = sub.add_parser(name, parents=[common])
        sp.add_argument("--json", action="store_true")
    sv = sub.add_parser("serve", parents=[common])
    sv.add_argument("--instance", default="default")
    return p


def main() -> None:
    args = build_parser().parse_args()
    # §3: only JSON on stdout.  Every logger goes to stderr before any verb runs.
    root = logging.getLogger()
    root.handlers.clear()
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(h)
    root.setLevel(resolve_log_level(args.log_level, _settings(args)))
    handlers = {"version": _handle_version, "inventory": _handle_inventory,
                "validate": _handle_validate, "serve": _handle_serve}
    sys.exit(handlers[args.command](args))


if __name__ == "__main__":
    main()
