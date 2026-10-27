"""Config resolution: STATION_WEB_CONFIG > /etc/station-web > absent."""
from pathlib import Path

import pytest

from station_web import config as cfgmod


def test_env_override_wins(tmp_path, monkeypatch):
    p = tmp_path / "sw.toml"
    p.write_text('[station]\ncallsign = "AC0G"\n[web]\nport = 8123\n')
    monkeypatch.setenv("STATION_WEB_CONFIG", str(p))
    s = cfgmod.load_settings()
    assert s.loaded is True
    assert s.config_path == p
    assert s.station["callsign"] == "AC0G"
    assert s.web_port == 8123
    assert s.web_bind == "0.0.0.0"          # default survives a partial file


def test_missing_config_is_not_fatal(tmp_path, monkeypatch):
    monkeypatch.setenv("STATION_WEB_CONFIG", str(tmp_path / "absent.toml"))
    s = cfgmod.load_settings()
    assert s.loaded is False
    assert s.data_root == Path("/var/lib/timestd")
    assert s.storage == {}
    assert s.fusion_dir == Path("/var/lib/timestd/phase2/fusion")


def test_storage_and_docs_roots(tmp_path, monkeypatch):
    p = tmp_path / "sw.toml"
    p.write_text(
        '[data]\nroot = "%s"\n[storage]\nsqlite_path = "%s"\n'
        '[docs]\nroots = [{ name = "a", path = "%s" }]\n'
        % (tmp_path, tmp_path / "db.sqlite", tmp_path)
    )
    monkeypatch.setenv("STATION_WEB_CONFIG", str(p))
    s = cfgmod.load_settings()
    assert s.data_root == tmp_path
    assert s.storage == {"sqlite_path": str(tmp_path / "db.sqlite")}
    # The configured root, then the checks root the Check page links into.
    assert s.docs_roots == [{"name": "a", "path": str(tmp_path)},
                            {"name": "checks",
                             "path": "/opt/git/sigmond/sigmond/docs/operator/checks"}]


def test_empty_sqlite_path_yields_empty_storage(tmp_path, monkeypatch):
    p = tmp_path / "sw.toml"
    p.write_text('[storage]\nsqlite_path = ""\n')
    monkeypatch.setenv("STATION_WEB_CONFIG", str(p))
    assert cfgmod.load_settings().storage == {}


def test_station_metadata_keys(tmp_path, monkeypatch):
    p = tmp_path / "sw.toml"
    p.write_text('[station]\ncallsign = "AC0G"\ngrid_square = "EM38ww"\n'
                 'latitude = 38.9\nlongitude = -92.3\n')
    monkeypatch.setenv("STATION_WEB_CONFIG", str(p))
    md = cfgmod.load_settings().station_metadata
    assert md["callsign"] == "AC0G"
    assert md["grid_square"] == "EM38ww"
    assert md["latitude"] == 38.9
    assert set(md) >= {"callsign", "grid_square", "latitude", "longitude", "description"}


def test_proxy_swaps(tmp_path, monkeypatch):
    p = tmp_path / "sw.toml"
    p.write_text('[web]\nport = 9999\n')
    s = cfgmod.load_settings(p)
    cfgmod.config.use(s)
    assert cfgmod.config.web_port == 9999
    assert cfgmod.config.config_path == p
