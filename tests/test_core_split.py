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

import importlib
import logging
import os
import subprocess
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
    assert appmod.CORE_ROUTERS == ("docs", "grape", "timing", "status", "features", "viewer", "commands")
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


def test_science_router_with_a_real_import_bug_is_fatal(settings, monkeypatch, caplog):
    """Only an ABSENT science package skips a router.  An ImportError from
    station_web itself is a defect and must stop startup, naming the router."""
    real = importlib.import_module

    def fake(name, *a, **kw):
        if name == "station_web.routers.metrology":
            raise ImportError("cannot import name 'gone' from 'station_web.services.channels'")
        return real(name, *a, **kw)

    monkeypatch.setattr(importlib, "import_module", fake)
    with caplog.at_level(logging.ERROR, logger="station_web.app"), pytest.raises(ImportError):
        appmod.create_app(settings, control_socket=None)
    assert any("'metrology'" in r.getMessage() for r in caplog.records)


def test_science_router_without_numpy_is_skipped(settings, monkeypatch):
    real = importlib.import_module

    def fake(name, *a, **kw):
        if name == "station_web.routers.metrology":
            raise ModuleNotFoundError("No module named 'numpy.linalg'", name="numpy.linalg")
        return real(name, *a, **kw)

    monkeypatch.setattr(importlib, "import_module", fake)
    app = appmod.create_app(settings, control_socket=None)
    assert app.state.skipped_routers == ["metrology"]


def test_full_app_reports_science(client):
    assert client.get("/api/features").json() == {"science": True, "skipped": []}
    assert any(p.startswith("/api/stations") for p in _paths(client))


def test_validate_reports_missing_science_as_info(settings, monkeypatch, tmp_path):
    from station_web import contract
    monkeypatch.setattr(contract, "SCIENCE_SIBLINGS", tmp_path / "no-siblings")
    for lib in ("hamsci_dsp", "hamsci_physics"):
        monkeypatch.setitem(sys.modules, lib, None)
    issues = contract.collect_issues(settings)
    science = [i for i in issues if "hamsci_" in i["message"]]
    assert len(science) == 2
    assert all(i["severity"] == "info" for i in science)
    assert all("science pages not installed" in i["message"] for i in science)


def test_validate_warns_when_checkouts_exist_but_science_fails(settings, monkeypatch, tmp_path):
    """The siblings are checked out, so build-venv.sh should have installed
    the extra: an absent package there means the science build failed."""
    from station_web import contract
    for name in ("hamsci-dsp", "hamsci-physics"):
        (tmp_path / name).mkdir()
    monkeypatch.setattr(contract, "SCIENCE_SIBLINGS", tmp_path)
    for lib in ("hamsci_dsp", "hamsci_physics"):
        monkeypatch.setitem(sys.modules, lib, None)
    science = [i for i in contract.collect_issues(settings) if "hamsci_" in i["message"]]
    assert [i["severity"] for i in science] == ["warn", "warn"]


def test_validate_fails_on_a_science_import_bug(settings, monkeypatch):
    from station_web import contract
    real = __import__

    def fake(name, *a, **kw):
        if name == "hamsci_physics":
            raise ImportError("cannot import name 'solar_zenith' from 'hamsci_physics.sun'")
        return real(name, *a, **kw)

    monkeypatch.setattr("builtins.__import__", fake)
    science = [i for i in contract.collect_issues(settings) if "hamsci_physics" in i["message"]]
    assert [i["severity"] for i in science] == ["fail"]


def test_build_runs_build_venv_script():
    with open(ROOT / "deploy.toml", "rb") as fh:
        d = tomllib.load(fh)
    assert any("scripts/build-venv.sh" in s for s in d["build"]["steps"])
    script = ROOT / "scripts" / "build-venv.sh"
    assert script.is_file() and script.stat().st_mode & 0o111
    git_deps = {g["name"] for g in d.get("deps", {}).get("git", [])}
    assert not git_deps & {"hamsci-dsp", "hamsci-physics", "hs-uploader"}


def test_unit_runs_without_timestd():
    """No Group= at all: a unit pulled by `smd update` onto a station whose
    setup predates the stationweb group must still start (216/GROUP).
    ensure-groups.sh grants the supplementary groups as root before start."""
    text = (ROOT / "systemd/station-web@.service").read_text()
    assert not any(line.startswith("Group=") for line in text.splitlines())
    assert "ExecStartPre=+/opt/git/sigmond/station-web/scripts/ensure-groups.sh" in text
    assert "ReadOnlyPaths=-/var/lib/timestd /opt/git/sigmond" in text
    assert "ReadWritePaths=-/var/lib/timestd/phase2 -/run/sigmond/snapshot-request" in text


def test_pyproject_core_has_no_science():
    with open(ROOT / "pyproject.toml", "rb") as fh:
        p = tomllib.load(fh)["project"]
    core = " ".join(p["dependencies"])
    for lib in ("numpy", "scipy", "hamsci-dsp", "hamsci-physics"):
        assert lib not in core, lib
        assert any(d.startswith(lib) for d in p["optional-dependencies"]["science"]), lib


# ---------------------------------------------------------------------------
# ensure-groups.sh runs as root before every start.  These tests put fake
# getent/usermod first on PATH; the fake usermod records its calls.
# ---------------------------------------------------------------------------

ENSURE = ROOT / "scripts" / "ensure-groups.sh"


def _run_ensure(tmp_path, group_lines, usermod_rc=0, user_exists=True):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (tmp_path / "group").write_text("".join(f"{g}\n" for g in group_lines))
    calls = tmp_path / "usermod.calls"
    (bindir / "getent").write_text(f"""#!/usr/bin/env bash
if [[ $1 == passwd ]]; then {'exit 0' if user_exists else 'exit 2'}; fi
grep "^$2:" {tmp_path / 'group'} || exit 2
""")
    (bindir / "usermod").write_text(f'#!/usr/bin/env bash\necho "$*" >> {calls}\nexit {usermod_rc}\n')
    for f in bindir.iterdir():
        f.chmod(0o755)
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}")
    proc = subprocess.run(["bash", str(ENSURE)], capture_output=True, text=True, timeout=30, env=env)
    return proc, (calls.read_text().splitlines() if calls.exists() else [])


def test_ensure_groups_is_executable_and_parses():
    assert ENSURE.stat().st_mode & 0o111
    assert subprocess.run(["bash", "-n", str(ENSURE)]).returncode == 0


def test_ensure_groups_adds_only_missing_memberships(tmp_path):
    proc, calls = _run_ensure(tmp_path, ["timestd:x:900:", "sigmond-status:x:901:rob"])
    assert proc.returncode == 0, proc.stderr
    assert calls == ["-aG timestd stationweb", "-aG sigmond-status stationweb"]


def test_ensure_groups_is_idempotent(tmp_path):
    proc, calls = _run_ensure(tmp_path, ["timestd:x:900:hftimestd,stationweb",
                                         "sigmond-status:x:901:stationweb"])
    assert proc.returncode == 0 and calls == []


def test_ensure_groups_skips_absent_groups(tmp_path):
    proc, calls = _run_ensure(tmp_path, [])
    assert proc.returncode == 0 and calls == []


def test_ensure_groups_never_fails_the_start(tmp_path):
    proc, calls = _run_ensure(tmp_path, ["timestd:x:900:"], usermod_rc=1)
    assert proc.returncode == 0
    assert calls == ["-aG timestd stationweb"]
    assert "could not add stationweb to group timestd" in proc.stderr


def test_ensure_groups_without_the_user_exits_0(tmp_path):
    proc, calls = _run_ensure(tmp_path, ["timestd:x:900:"], user_exists=False)
    assert proc.returncode == 0 and calls == []
    assert "user stationweb absent" in proc.stderr
