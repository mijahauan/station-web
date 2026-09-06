import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from station_web import config as cfgmod
from station_web.app import create_app


@pytest.fixture
def settings(tmp_path, monkeypatch):
    root = tmp_path / "timestd"
    (root / "phase2").mkdir(parents=True)
    cfg = tmp_path / "sw.toml"
    cfg.write_text(f'[station]\ncallsign = "AC0G"\ngrid_square = "EM38ww"\n'
                   f'latitude = 38.9\nlongitude = -92.3\n'
                   f'[data]\nroot = "{root}"\n'
                   f'[storage]\nsqlite_path = "{root / "phase2" / "timestd.db"}"\n')
    monkeypatch.setenv("STATION_WEB_CONFIG", str(cfg))
    s = cfgmod.load_settings()
    cfgmod.config.use(s)          # the moved routers import this singleton
    return s


@pytest.fixture
def client(settings):
    app = create_app(settings, control_socket=None)
    with TestClient(app) as c:
        yield c
