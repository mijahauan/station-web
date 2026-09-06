"""CLIENT-CONTRACT v0.8 surface for station-web, a §16.3.1 meta-client.

station-web produces nothing.  Its data plane is the products hf-timestd,
hamsci-physics and (when present) gnss-vtec write under /var/lib/timestd.
The contract's ``upstream_client`` is singular, so hf-timestd, which owns
the data root, takes the slot; ``also_reads`` is an extension key that
names the others.  ``serves`` is another extension key: the contract has
no field for a TCP listener, and station-web is the first conformant
client that binds one (spec §7 proposes it for the next contract bump).
Radiod-side fields are omitted (§16.5).
"""
from __future__ import annotations

import logging
import os
import stat
from importlib.metadata import PackageNotFoundError, version as pkg_version
from pathlib import Path
from typing import Any

from station_web.config import Settings
from station_web.version import GIT_INFO

CONTRACT_VERSION = "0.8"
UPSTREAM_CLIENT = "hf-timestd"
ALSO_READS = ["hamsci-physics", "gnss-vtec"]
DEPLOY_TOML_PATH = "/opt/git/sigmond/station-web/deploy.toml"
VENDOR_DIR = Path(__file__).resolve().parent / "static" / "vendor"
CONTROL_SOCKET = "/run/station-web/control.sock"


def _version() -> str:
    try:
        return pkg_version("station-web")
    except PackageNotFoundError:
        from station_web import __version__
        return __version__


def build_inventory(settings: Settings) -> dict:
    """``inventory --json``.  MUST exit 0 on degraded paths: every lookup
    tolerates a missing config and reports through ``issues``."""
    root = settings.data_root
    instance: dict[str, Any] = {
        "instance": "default",
        "host": "localhost",
        "data_path": {
            "kind": "file",
            "path": str(root),
            "details": {
                "upstream_client": UPSTREAM_CLIENT,
                "upstream_unit": "timestd-fusion.service",
                "products": str(root / "phase2"),
                "also_reads": list(ALSO_READS),
                "description": ("L1-L3 data products, DIAG_chrony_stats and the "
                                "space-weather cache, read in place"),
            },
        },
        "serves": {"http": {"bind": settings.web_bind, "port": settings.web_port}},
        "control_socket": CONTROL_SOCKET,
        "data_sinks": [],
        "uses_timing_calibration": False,
        "provides_timing_calibration": False,
        "timing_authority_applied": None,
        "deploy_toml_path": DEPLOY_TOML_PATH,
        "station": {
            "callsign": settings.station.get("callsign", ""),
            "grid_square": settings.station.get("grid_square", ""),
        },
    }
    payload: dict[str, Any] = {
        "client": "station-web",
        "version": _version(),
        "contract_version": CONTRACT_VERSION,
        "config_path": str(settings.config_path),
    }
    if GIT_INFO:
        payload["git"] = GIT_INFO
    payload["log_level"] = logging.getLevelName(logging.getLogger().getEffectiveLevel())
    payload["instances"] = [instance]
    payload["deps"] = {
        "git": [
            {"name": "hamsci-dsp", "note": "readers, registry, station catalog, stability"},
            {"name": "hamsci-physics", "note": "solar geometry"},
            {"name": UPSTREAM_CLIENT, "note": "writes the products this client renders"},
        ],
        "pypi": [
            {"name": "fastapi", "version": ">=0.104.0"},
            {"name": "hamsci-dsp", "version": ">=0.7.0"},
        ],
    }
    payload["issues"] = collect_issues(settings)
    return payload


def build_validate(settings: Settings) -> dict:
    issues = collect_issues(settings)
    return {
        "ok": not any(i["severity"] == "fail" for i in issues),
        "config_path": str(settings.config_path),        # §12.3
        "issues": issues,
    }


def _issue(severity: str, message: str, instance: str = "default") -> dict:
    return {"severity": severity, "instance": instance, "message": message}


def collect_issues(settings: Settings) -> list[dict]:
    issues: list[dict] = []
    if not settings.loaded:
        issues.append(_issue("fail", f"no configuration loaded from {settings.config_path} "
                                     f"— run `smd config init station-web`", "all"))
        return issues

    for key in ("callsign", "grid_square"):
        if not settings.station.get(key):
            issues.append(_issue("warn", f"[station] {key} is empty"))

    root = settings.data_root
    if not root.is_dir():
        issues.append(_issue("fail", f"data root {root} does not exist"))
        return issues

    phase2 = root / "phase2"
    if not phase2.is_dir():
        issues.append(_issue("warn", f"{phase2} missing — hf-timestd has not written yet"))
    else:
        db = Path(settings.storage.get("sqlite_path", phase2 / "timestd.db"))
        if db.exists():
            mode = db.parent.stat().st_mode
            if not mode & stat.S_IWGRP:
                issues.append(_issue("fail", f"{db.parent} is not group-writable; a WAL "
                                             f"reader in another uid cannot open {db.name}"))
            if not os.access(db, os.R_OK):
                issues.append(_issue("fail", f"{db} is not readable by this user"))
        else:
            issues.append(_issue("warn", f"{db} not present yet"))

    for r in settings.docs_roots:
        if not Path(r["path"]).is_dir():
            issues.append(_issue("warn", f"docs root {r['name']} at {r['path']} does not exist"))

    for mod in ("hamsci_dsp", "hamsci_physics"):
        try:
            __import__(mod)
        except Exception as exc:        # noqa: BLE001
            issues.append(_issue("fail", f"cannot import {mod}: {exc}"))

    if not (VENDOR_DIR / "plotly-2.27.0.min.js").exists():
        issues.append(_issue("fail", f"vendored plotly missing from {VENDOR_DIR}"))
    return issues
