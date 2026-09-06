"""deploy.toml invariants (CLIENT-CONTRACT §5) and unit hygiene (§4)."""
from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def deploy():
    with open(ROOT / "deploy.toml", "rb") as fh:
        return tomllib.load(fh)


def test_package_identity(deploy):
    assert deploy["package"]["name"] == "station-web"
    assert deploy["package"]["contract_version"] == "0.8"


def test_only_link_steps():
    """sigmond executes only kind = "link" today (installer.py:532)."""
    with open(ROOT / "deploy.toml", "rb") as fh:
        d = tomllib.load(fh)
    for step in d["install"]["steps"]:
        assert step.get("kind") == "link", step


def test_build_produces_the_console_script(deploy):
    assert deploy["build"]["produces"] == ["/opt/git/sigmond/station-web/venv/bin/station-web"]
    steps = deploy["build"]["steps"]
    assert "cpython-3.11-linux-x86_64-gnu" in steps[0]           # minor-series symlink
    assert any("hamsci-dsp" in s for s in steps) and any("hamsci-physics" in s for s in steps)
    assert steps[-1].endswith("pip install -e .")


def test_units_declared_and_shipped(deploy):
    shipped = {p.name for p in (ROOT / "systemd").glob("*.service")}
    assert set(deploy["systemd"]["templated_units"]) == {"station-web@.service"}
    assert set(deploy["systemd"]["units"]) == {"station-web.service"}
    for u in deploy["systemd"]["units"] + deploy["systemd"]["templated_units"]:
        assert u in shipped


def test_templated_unit_sources_both_env_files():
    text = (ROOT / "systemd/station-web@.service").read_text()
    assert "EnvironmentFile=-/etc/sigmond/coordination.env" in text
    assert "EnvironmentFile=-/etc/station-web/env/%i.env" in text
    assert "ExecReload=/bin/kill -HUP $MAINPID" in text
    assert "ProtectSystem=strict" in text
    assert "ReadWritePaths=/var/lib/timestd/phase2" in text
    assert "User=stationweb" in text and "Group=timestd" in text


def test_config_scripts_exist_and_are_executable(deploy):
    for key in ("init", "edit"):
        p = ROOT / deploy["contract"]["config"][key]
        assert p.exists() and p.stat().st_mode & 0o111, p


def test_setup_creates_default_instance_env_file(tmp_path):
    """CLIENT-CONTRACT §5.1: sigmond enumerates a client's instances from
    /etc/<client>/env/<instance>.env.  setup-station.sh must seed the
    "default" instance's file, and it must not clobber it on a re-run
    against an existing config (this repo's unprivileged test run never
    reaches the useradd or chmod-g+ws branches — those require EUID 0 and
    an existing db_dir respectively)."""
    config_dir = tmp_path / "etc" / "station-web"
    env = dict(os.environ, CONFIG_DIR=str(config_dir), CONFIG=str(config_dir / "config.toml"))
    script = ROOT / "scripts" / "setup-station.sh"
    env_file = config_dir / "env" / "default.env"

    proc = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=30, env=env)
    assert proc.returncode == 0, proc.stderr
    assert env_file.exists()
    first_content = env_file.read_text()

    proc2 = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=30, env=env)
    assert proc2.returncode == 0, proc2.stderr
    assert env_file.read_text() == first_content


def test_setup_migrates_legacy_station_web_toml(tmp_path):
    """sigmond invents an instance for any /etc/<client>/*.toml that isn't
    config.toml, <client>-config.toml or *-config.toml (CLIENT-CONTRACT §14).
    A pre-existing station-web.toml (the old default path) must be renamed to
    config.toml in place, with its content preserved and no second copy
    written alongside it."""
    config_dir = tmp_path / "etc" / "station-web"
    config_dir.mkdir(parents=True)
    legacy = config_dir / "station-web.toml"
    legacy.write_text('[station]\ncallsign = "AC0G"\n')

    env = dict(os.environ, CONFIG_DIR=str(config_dir), CONFIG=str(config_dir / "config.toml"))
    script = ROOT / "scripts" / "setup-station.sh"

    proc = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=30, env=env)
    assert proc.returncode == 0, proc.stderr
    assert "migrated station-web.toml -> config.toml" in proc.stdout

    new_config = config_dir / "config.toml"
    assert new_config.exists()
    assert new_config.read_text() == '[station]\ncallsign = "AC0G"\n'
    assert not legacy.exists()
