"""CLIENT-CONTRACT v0.8 conformance for station-web (meta-client, §16.3.1)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = str(ROOT / "src")


def run(*argv, env_extra=None):
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": SRC}
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-m", "station_web.cli", *argv],
                          capture_output=True, text=True, timeout=120, env=env)


@pytest.fixture(scope="module")
def inventory(tmp_path_factory):
    d = tmp_path_factory.mktemp("cfg")
    (d / "phase2").mkdir()
    cfg = d / "sw.toml"
    cfg.write_text(f'[station]\ncallsign = "AC0G"\ngrid_square = "EM38ww"\n'
                   f'[data]\nroot = "{d}"\n')
    proc = run("inventory", "--json", "-c", str(cfg))
    assert proc.returncode == 0, proc.stderr[-500:]
    return json.loads(proc.stdout)


@pytest.mark.parametrize("sub", ["inventory", "validate", "version"])
def test_stdout_is_only_json(sub):
    proc = run(sub, "--json")
    json.loads(proc.stdout)
    assert proc.stdout.lstrip()[0] in "{["


def test_inventory_exits_zero_even_with_no_config(tmp_path):
    proc = run("inventory", "--json", "-c", str(tmp_path / "absent.toml"))
    assert proc.returncode == 0, proc.stderr[-400:]
    doc = json.loads(proc.stdout)
    assert any("no configuration" in i["message"] for i in doc["issues"])


def test_top_level_keys(inventory):
    assert inventory["client"] == "station-web"
    assert inventory["version"]
    assert inventory["contract_version"] == "0.8"
    assert Path(inventory["config_path"]).is_absolute()
    assert inventory["instances"] and inventory["instances"][0]["instance"] == "default"
    assert "log_level" in inventory
    assert "log_paths" not in inventory          # §10: no file logs


def test_contract_version_matches_the_deploy_manifest(inventory):
    import tomllib
    with open(ROOT / "deploy.toml", "rb") as fh:
        deploy = tomllib.load(fh)
    assert inventory["contract_version"] == deploy["package"]["contract_version"]


def test_data_path_declares_a_meta_client(inventory):
    dp = inventory["instances"][0]["data_path"]
    assert dp["kind"] == "file"
    assert dp["details"]["upstream_client"] == "hf-timestd"
    assert dp["details"]["upstream_unit"] == "timestd-fusion.service"
    assert dp["details"]["also_reads"] == ["hamsci-physics", "gnss-vtec"]


def test_radiod_fields_are_absent(inventory):
    inst = inventory["instances"][0]
    for forbidden in ("radiod_id", "data_destination", "chain_delay_ns_applied"):
        assert forbidden not in inst
    assert inst["timing_authority_applied"] is None


def test_serves_block_names_the_http_listener(inventory):
    assert inventory["instances"][0]["serves"] == {"http": {"bind": "0.0.0.0", "port": 8000}}


def test_produces_nothing(inventory):
    inst = inventory["instances"][0]
    assert inst["data_sinks"] == []
    assert inst["provides_timing_calibration"] is False
    assert inst["uses_timing_calibration"] is False


def test_deploy_toml_path(inventory):
    assert inventory["instances"][0]["deploy_toml_path"] == "/opt/git/sigmond/station-web/deploy.toml"


def test_validate_fails_on_missing_data_root(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text(f'[station]\ncallsign = "AC0G"\ngrid_square = "EM38ww"\n'
                   f'[data]\nroot = "{tmp_path / "nowhere"}"\n')
    proc = run("validate", "--json", "-c", str(cfg))
    doc = json.loads(proc.stdout)
    assert doc["ok"] is False and proc.returncode == 1
    assert Path(doc["config_path"]).is_absolute()
    assert any("data root" in i["message"] for i in doc["issues"] if i["severity"] == "fail")
    for issue in doc["issues"]:
        assert set(issue) >= {"severity", "instance", "message"}


def test_validate_warns_on_missing_docs_root(tmp_path):
    (tmp_path / "phase2").mkdir()
    cfg = tmp_path / "c.toml"
    cfg.write_text(f'[station]\ncallsign = "AC0G"\ngrid_square = "EM38ww"\n'
                   f'[data]\nroot = "{tmp_path}"\n'
                   f'[docs]\nroots = [{{ name = "gone", path = "{tmp_path / "gone"}" }}]\n')
    doc = json.loads(run("validate", "--json", "-c", str(cfg)).stdout)
    warns = [i for i in doc["issues"] if i["severity"] == "warn"]
    assert any("docs root" in i["message"] and "gone" in i["message"] for i in warns)


def test_config_path_is_absolute_for_relative_flag(tmp_path):
    (tmp_path / "rel.toml").write_text('[station]\ncallsign = "AC0G"\n')
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": SRC}
    proc = subprocess.run([sys.executable, "-m", "station_web.cli", "inventory", "--json", "-c", "rel.toml"],
                          capture_output=True, text=True, timeout=120, env=env, cwd=tmp_path)
    doc = json.loads(proc.stdout)
    assert doc["config_path"] == str(tmp_path / "rel.toml")


def test_main_guard_present():
    """§12.1 entry-point reachability."""
    text = (ROOT / "src/station_web/cli.py").read_text()
    assert 'if __name__ == "__main__":' in text or "if __name__ == '__main__':" in text


# ---------------------------------------------------------------------------
# §12.6 / §12.1: validate must catch a deployment whose manifest disagrees
# with the code, and an entry point the unit could not actually reach.  Both
# numbers are self-reported, so nothing else in the system would notice.
# ---------------------------------------------------------------------------


def _loaded_settings(tmp_path):
    from station_web.config import load_settings
    (tmp_path / "phase2").mkdir(exist_ok=True)
    cfg = tmp_path / "c.toml"
    cfg.write_text(f'[station]\ncallsign = "AC0G"\ngrid_square = "EM38ww"\n'
                   f'[data]\nroot = "{tmp_path}"\n')
    return load_settings(cfg)


def test_validate_fails_on_a_contract_version_mismatch(tmp_path):
    import sys
    sys.path.insert(0, SRC)
    from station_web.contract import CONTRACT_VERSION, collect_issues

    bad = tmp_path / "deploy.toml"
    bad.write_text('[package]\nname = "station-web"\ncontract_version = "0.7"\n')
    issues = collect_issues(_loaded_settings(tmp_path), deploy_toml=bad)
    fails = [i for i in issues if i["severity"] == "fail"]
    assert any("contract_version" in i["message"] and CONTRACT_VERSION in i["message"]
               for i in fails), issues


def test_validate_passes_the_real_manifest(tmp_path):
    import sys
    sys.path.insert(0, SRC)
    from station_web.contract import collect_issues

    issues = collect_issues(_loaded_settings(tmp_path), deploy_toml=ROOT / "deploy.toml")
    assert not [i for i in issues if "contract_version" in i["message"]], issues


def test_validate_checks_the_unit_reaches_the_console_script(tmp_path):
    """§12.1: ExecStart must name the station-web console script, and
    cli.py must keep its __main__ guard."""
    import sys
    sys.path.insert(0, SRC)
    from station_web.contract import CONSOLE_SCRIPT, _entry_point_issues

    unit = (ROOT / "systemd" / "station-web@.service").read_text()
    assert any(ln.startswith("ExecStart=") and f"/{CONSOLE_SCRIPT} " in ln
               for ln in unit.splitlines())
    assert _entry_point_issues() == []
