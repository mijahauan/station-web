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

#: The repo checkout this package was installed from (editable install, which
#: is how every client on a station is installed).  ``src/station_web/`` sits
#: two directories below it.
REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATED_UNIT = "station-web@.service"
TEMPLATED_UNIT_PATH = Path("/etc/systemd/system") / TEMPLATED_UNIT
CONSOLE_SCRIPT = "station-web"


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
                # the timing authority hf-timestd publishes outside the
                # product database, read-only (Stage 2 registration views)
                "timing_authority_files": [str(settings.authority_path),
                                           str(settings.authority_history_path),
                                           str(settings.timing_status_dir)],
                # hf-timestd's WWVB decode ledger, read-only (Timing page)
                "wwvb_ledger": str(settings.wwvb_dir),
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


def _deploy_manifest_issues(deploy_toml: Path | None) -> list[dict]:
    """§12.6: the manifest's declared contract version must match the code's.

    A manifest that says 0.7 while the code answers 0.8 makes sigmond
    reason about the wrong surface, and nothing else would notice: both
    numbers are self-reported.
    """
    import tomllib

    path = deploy_toml
    if path is None:
        installed = Path(DEPLOY_TOML_PATH)
        path = installed if installed.exists() else REPO_ROOT / "deploy.toml"
    if not path.exists():
        return [_issue("warn", f"deploy manifest {path} not found — cannot check "
                               f"[package].contract_version", "all")]
    try:
        with open(path, "rb") as fh:
            declared = (tomllib.load(fh).get("package", {}) or {}).get("contract_version")
    except (OSError, ValueError) as exc:
        return [_issue("fail", f"deploy manifest {path} unreadable: {exc}", "all")]
    if declared != CONTRACT_VERSION:
        return [_issue("fail", f"{path} declares [package].contract_version = {declared!r} "
                               f"but this build implements {CONTRACT_VERSION!r}", "all")]
    return []


def _entry_point_issues() -> list[dict]:
    """§12.1: the unit's ExecStart must actually reach ``main()``.

    Two halves, both of which have broken a client before: the templated
    unit has to invoke the console script (not ``python -m`` against a path
    that the venv rebuild moves), and ``cli.py`` needs its ``__main__``
    guard so ``python -m station_web.cli`` runs rather than exiting silently.
    """
    issues: list[dict] = []
    unit = TEMPLATED_UNIT_PATH if TEMPLATED_UNIT_PATH.exists() else REPO_ROOT / "systemd" / TEMPLATED_UNIT
    if not unit.exists():
        issues.append(_issue("warn", f"templated unit {unit} not found — cannot check ExecStart", "all"))
    else:
        exec_lines = [ln for ln in unit.read_text().splitlines() if ln.startswith("ExecStart=")]
        if not any(f"/{CONSOLE_SCRIPT} " in ln or ln.rstrip().endswith(f"/{CONSOLE_SCRIPT}")
                   for ln in exec_lines):
            issues.append(_issue("fail", f"{unit}: no ExecStart names the {CONSOLE_SCRIPT} "
                                         f"console script (found {exec_lines or ['nothing']})", "all"))
    cli = REPO_ROOT / "src" / "station_web" / "cli.py"
    if cli.exists():
        text = cli.read_text()
        if 'if __name__ == "__main__":' not in text and "if __name__ == '__main__':" not in text:
            issues.append(_issue("fail", f"{cli} has no __main__ guard — "
                                         f"`python -m station_web.cli` would do nothing", "all"))
    else:
        issues.append(_issue("warn", f"{cli} not found — cannot check the __main__ guard", "all"))
    return issues


def build_validate(settings: Settings) -> dict:
    issues = collect_issues(settings)
    return {
        "ok": not any(i["severity"] == "fail" for i in issues),
        "config_path": str(settings.config_path),        # §12.3
        "issues": issues,
    }


def _issue(severity: str, message: str, instance: str = "default") -> dict:
    return {"severity": severity, "instance": instance, "message": message}


def collect_issues(settings: Settings, deploy_toml: Path | None = None) -> list[dict]:
    issues: list[dict] = []
    if not settings.loaded:
        issues.append(_issue("fail", f"no configuration loaded from {settings.config_path} "
                                     f"— run `smd config init station-web`", "all"))
        return issues

    # Deployment integrity first: these hold whatever the data plane looks
    # like, and the data-root check below returns early on a bare station.
    issues.extend(_deploy_manifest_issues(deploy_toml))
    issues.extend(_entry_point_issues())

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
