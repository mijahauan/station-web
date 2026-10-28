"""The remit, enforced: products in, HTML out, nothing else."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src" / "station_web"
PY = [p for p in SRC.rglob("*.py")]

FORBIDDEN_IMPORTS = {
    "hf_timestd": "station-web never imports the timing core",
    "subprocess": "no shell, no journalctl, no chronyc",
    "requests": "no outbound HTTP",
    "urllib.request": "no outbound HTTP",
    "httpx": "no outbound HTTP",
    "aiohttp": "no outbound HTTP",
}

#: The one sanctioned outbound HTTP: the viewer front door (Plan 2) proxies
#: a local viewer's own traffic to 127.0.0.1 at a port from the viewers
#: snapshot.  Only this module may import httpx.
OUTBOUND_HTTP_ALLOWED = {("services/viewer_proxy.py", "httpx")}
#: The one coroutine router: the viewer proxy waits on sockets, not on the
#: disk, and a threadpool slot per open viewer stream would starve /health.
#: Its one disk read (the snapshot) goes through run_in_threadpool.
ASYNC_ROUTERS = {"routers/viewer.py"}


def _imports(path: Path):
    for line in path.read_text().splitlines():
        m = re.match(r"\s*(?:from|import)\s+([\w.]+)", line)
        if m:
            yield m.group(1)


def test_no_forbidden_imports():
    bad = []
    for p in PY:
        for mod in _imports(p):
            for root, why in FORBIDDEN_IMPORTS.items():
                if (str(p.relative_to(SRC)), root) in OUTBOUND_HTTP_ALLOWED:
                    continue
                if mod == root or mod.startswith(root + "."):
                    bad.append(f"{p.relative_to(SRC)}: {mod} ({why})")
    assert not bad, "\n".join(bad)


def test_no_cors_middleware():
    text = (SRC / "app.py").read_text()
    assert "CORSMiddleware" not in text


def test_static_dir_is_not_created_at_import():
    text = (SRC / "app.py").read_text()
    assert "mkdir" not in text


def test_root_and_health(client):
    assert client.get("/").status_code == 200
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["callsign"] == "AC0G"


def test_app_serves_when_control_socket_cannot_start(settings, tmp_path):
    from station_web.app import create_app, STATUS
    blocker = tmp_path / "notadir"
    blocker.write_text("x")                      # parent path is a regular file → mkdir fails
    app = create_app(settings, control_socket=blocker / "control.sock")
    with TestClient(app) as c:
        assert c.get("/").status_code == 200
        assert str(STATUS["control_socket"]).startswith("failed:")


def test_no_import_time_config_reads():
    import re
    bad = []
    for p in list((SRC / "routers").glob("*.py")) + list((SRC / "services").glob("*.py")):
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*=\s*.*\bconfig\.", line):
                bad.append(f"{p.relative_to(SRC)}:{i}: {line.strip()}")
    assert not bad, "module-level config reads defeat per-request settings:\n" + "\n".join(bad)


def test_suite_never_touches_the_real_data_root(client):
    from pathlib import Path
    real = Path("/var/lib/timestd/space_weather_cache")
    before = real.exists()
    client.get("/api/space-weather/current")
    client.get("/api/correlations/summary")
    assert real.exists() == before


def test_routers_hold_no_async_def():
    """Every route in ``routers/`` reads the filesystem, SQLite or HDF5.

    An ``async def`` endpoint runs ON the event loop, so one slow SQLite
    scan stalls every other request in the process — including /health,
    which the systemd watchdog and sigmond both poll.  A plain ``def``
    endpoint runs in FastAPI's threadpool instead.  Keep them all sync;
    there is no non-blocking work in this package to justify a coroutine.
    """
    offenders = []
    for p in sorted((SRC / "routers").glob("*.py")):
        if str(p.relative_to(SRC)) in ASYNC_ROUTERS:
            continue
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if re.match(r"\s*async def\s", line):
                offenders.append(f"{p.relative_to(SRC)}:{i}: {line.strip()}")
    assert not offenders, "blocking IO on the event loop:\n" + "\n".join(offenders)


def test_no_await_left_in_routers():
    """The other half of the conversion: a converted body that still
    awaits would be a SyntaxError at import, but an ``await`` inside a
    string or a stray helper would not.  None should remain."""
    offenders = []
    for p in sorted((SRC / "routers").glob("*.py")):
        if str(p.relative_to(SRC)) in ASYNC_ROUTERS:
            continue
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if re.search(r"(^|[^\w.])await\s", line):
                offenders.append(f"{p.relative_to(SRC)}:{i}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


def test_viewer_router_keeps_disk_reads_off_the_loop():
    """The async exception holds only while the viewer router reads the
    snapshot through the threadpool, never on the event loop."""
    text = (SRC / "routers" / "viewer.py").read_text()
    assert "run_in_threadpool(viewer_proxy.resolve" in text
    assert "open(" not in text and "read_snapshot" not in text
