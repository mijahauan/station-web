"""station-web installs on every station: a core that needs nothing scientific,
and science routers that load only where hamsci-dsp, hamsci-physics and
numpy/scipy exist.

The dev venv carries the science stack, so the core-only cases simulate its
absence: a ``None`` entry in ``sys.modules`` makes ``import numpy`` raise
ImportError.  Every science module already imported, and every station_web
module that may hold one, leaves ``sys.modules`` first, so the routers import
afresh against the gap.  monkeypatch puts all of it back afterwards.
"""
from __future__ import annotations

import logging
import sys
import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from station_web import app as appmod

ROOT = Path(__file__).resolve().parents[1]
SCIENCE_LIBS = ("hamsci_dsp", "hamsci_physics", "numpy", "scipy")
#: station_web modules the eviction keeps: the config singleton the fixture
#: configured, and the app module under test (it imports no science).
KEEP = {"station_web", "station_web.config", "station_web.app"}


def _hide_science(monkeypatch):
    for name in list(sys.modules):
        top = name.split(".")[0]
        if top in SCIENCE_LIBS or (top == "station_web" and name not in KEEP):
            monkeypatch.delitem(sys.modules, name)
    for lib in SCIENCE_LIBS:
        monkeypatch.setitem(sys.modules, lib, None)
    # The package keeps attributes naming its submodules; restore those too.
    import station_web
    for attr in ("routers", "services", "broadcast", "projected"):
        if hasattr(station_web, attr):
            monkeypatch.setattr(station_web, attr, getattr(station_web, attr))


@pytest.fixture
def core_client(settings, monkeypatch):
    _hide_science(monkeypatch)
    app = appmod.create_app(settings, control_socket=None)
    with TestClient(app) as c:
        yield c


def _paths(client):
    return {p for p, ops in client.app.openapi()["paths"].items() if "get" in ops}


def test_router_lists():
    assert appmod.CORE_ROUTERS == ("docs", "grape", "timing", "status", "features")
    assert appmod.SCIENCE_ROUTERS == (
        "station", "stations", "health", "metrology", "stability", "propagation",
        "space_weather", "correlations", "physics", "tec", "tid", "dashboard", "phase",
        "ionogram", "chrony")


def test_core_app_starts_without_science(core_client):
    r = core_client.get("/api/living-docs/list")
    assert r.status_code == 200, r.text[:300]


def test_core_app_reports_skipped_science(core_client):
    j = core_client.get("/api/features").json()
    assert j["science"] is False
    assert j["skipped"] == list(appmod.SCIENCE_ROUTERS)


def test_core_app_serves_no_science_routes(core_client):
    paths = _paths(core_client)
    assert not any(p.startswith("/api/stations") for p in paths)
    assert any(p.startswith("/api/grape") for p in paths)
    assert any(p.startswith("/api/timing") for p in paths)


def test_core_app_parameterless_gets_never_500(core_client):
    failures = []
    for path in sorted(_paths(core_client)):
        if "{" in path or path == "/api/docs":
            continue
        r = core_client.get(path)
        if r.status_code >= 500:
            failures.append((path, r.status_code, r.text[:200]))
    assert not failures, failures


def test_skipped_router_logs_one_warning_naming_it(settings, monkeypatch, caplog):
    _hide_science(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="station_web.app"):
        appmod.create_app(settings, control_socket=None)
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    for name in ("stations", "dashboard"):
        assert sum(f"'{name}'" in m for m in warned) == 1, (name, warned)


def test_core_router_import_failure_is_fatal(settings, monkeypatch):
    monkeypatch.setitem(sys.modules, "station_web.routers.docs", None)
    with pytest.raises(ImportError):
        appmod.create_app(settings, control_socket=None)


def test_full_app_reports_science(client):
    assert client.get("/api/features").json() == {"science": True, "skipped": []}
    assert any(p.startswith("/api/stations") for p in _paths(client))


def test_validate_reports_missing_science_as_info(settings, monkeypatch):
    from station_web import contract
    for lib in ("hamsci_dsp", "hamsci_physics"):
        monkeypatch.setitem(sys.modules, lib, None)
    issues = contract.collect_issues(settings)
    science = [i for i in issues if "hamsci_" in i["message"]]
    assert len(science) == 2
    assert all(i["severity"] == "info" for i in science)
    assert all("science pages not installed" in i["message"] for i in science)


def test_build_runs_build_venv_script():
    with open(ROOT / "deploy.toml", "rb") as fh:
        d = tomllib.load(fh)
    assert any("scripts/build-venv.sh" in s for s in d["build"]["steps"])
    script = ROOT / "scripts" / "build-venv.sh"
    assert script.is_file() and script.stat().st_mode & 0o111
    git_deps = {g["name"] for g in d.get("deps", {}).get("git", [])}
    assert not git_deps & {"hamsci-dsp", "hamsci-physics", "hs-uploader"}


def test_unit_runs_without_timestd():
    text = (ROOT / "systemd/station-web@.service").read_text()
    assert "Group=timestd" not in text
    assert "Group=stationweb" in text
    assert "ReadOnlyPaths=-/var/lib/timestd /opt/git/sigmond" in text
    assert "ReadWritePaths=-/var/lib/timestd/phase2 -/run/sigmond/snapshot-request" in text


def test_pyproject_core_has_no_science():
    with open(ROOT / "pyproject.toml", "rb") as fh:
        p = tomllib.load(fh)["project"]
    core = " ".join(p["dependencies"])
    for lib in ("numpy", "scipy", "hamsci-dsp", "hamsci-physics"):
        assert lib not in core, lib
        assert any(d.startswith(lib) for d in p["optional-dependencies"]["science"]), lib
