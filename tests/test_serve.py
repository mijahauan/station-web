"""SIGHUP re-reads the WHOLE config, and `serve -c` reaches the routers."""
from __future__ import annotations

import pytest

from station_web import config as cfgmod
from station_web.serve import _make_reload, run_server


@pytest.fixture(autouse=True)
def _restore_global_config():
    """These tests swap the module singleton on purpose; put it back so a
    later test that reads ``config`` without the settings fixture cannot
    inherit one of ours."""
    saved = object.__getattribute__(cfgmod.config, "_current")
    yield
    cfgmod.config.use(saved)


def _write(path, port: int, callsign: str = "AC0G") -> None:
    path.write_text(f'[station]\ncallsign = "{callsign}"\n'
                    f'[web]\nport = {port}\n'
                    f'[logging]\nlevel = "INFO"\n')


def test_sighup_swaps_config_for_a_re_read_file(tmp_path, monkeypatch):
    """``ExecReload=/bin/kill -HUP $MAINPID`` used to re-read only the log
    level, so an operator who edited the config and reloaded got the old
    settings and a new log level — a reload that looked like it worked."""
    cfg = tmp_path / "sw.toml"
    _write(cfg, 8000)
    settings = cfgmod.load_settings(cfg)
    cfgmod.config.use(settings)
    assert cfgmod.config.web_port == 8000

    _write(cfg, 8123, callsign="AC0G-ND")            # the operator edits the file
    _make_reload(settings)(1, None)                  # ... and reloads

    assert cfgmod.config.web_port == 8123
    assert cfgmod.config.station["callsign"] == "AC0G-ND"
    assert cfgmod.config.config_path == cfg


def test_sighup_applies_the_new_log_level(tmp_path):
    import logging

    cfg = tmp_path / "sw.toml"
    cfg.write_text('[station]\ncallsign = "AC0G"\n[logging]\nlevel = "INFO"\n')
    settings = cfgmod.load_settings(cfg)
    cfgmod.config.use(settings)
    cfg.write_text('[station]\ncallsign = "AC0G"\n[logging]\nlevel = "DEBUG"\n')
    try:
        _make_reload(settings)(1, None)
        assert logging.getLogger().getEffectiveLevel() == logging.DEBUG
    finally:
        logging.getLogger().setLevel(logging.WARNING)


def test_run_server_publishes_the_settings_it_was_given(tmp_path, monkeypatch):
    """`station-web serve -c <other>` bound the port from <other> while
    every router still read the compiled default path, because ``config``
    is initialised at import.  run_server must publish first."""
    cfg = tmp_path / "other.toml"
    _write(cfg, 8199)
    settings = cfgmod.load_settings(cfg)
    cfgmod.config.use(cfgmod.load_settings(tmp_path / "absent.toml"))    # the wrong one
    assert cfgmod.config.loaded is False

    seen: dict = {}

    def fake_run(app, **kwargs):
        seen.update(kwargs)
        seen["config_path"] = cfgmod.config.config_path
        seen["port_via_proxy"] = cfgmod.config.web_port

    monkeypatch.setattr("station_web.serve.uvicorn.run", fake_run)
    assert run_server(settings) == 0
    assert seen["port"] == 8199 and seen["port_via_proxy"] == 8199
    assert seen["config_path"] == cfg
