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
