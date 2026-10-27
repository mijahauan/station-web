"""/api/status over fixture snapshots, over none, and the check request."""
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from station_web.services import snapshot as snap

FIX = Path(__file__).parent / "fixtures" / "snapshot"


def _stamp(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def snapdir(settings, tmp_path):
    d, req = tmp_path / "snap", tmp_path / "req"
    d.mkdir()
    req.mkdir()
    settings.raw["status"] = {"snapshot_dir": str(d), "snapshot_request_dir": str(req)}
    return d


def _install(snapdir, age=timedelta(seconds=30), **over):
    when = _stamp(datetime.now(timezone.utc) - age)
    for f in FIX.glob("*.json"):
        env = json.loads(f.read_text())
        env["ran_at"] = when
        env.update(over.get(env["name"], {}))
        (snapdir / f.name).write_text(json.dumps(env))


def test_no_snapshot_dir_is_not_an_error(client, settings, tmp_path):
    settings.raw["status"] = {"snapshot_dir": str(tmp_path / "nope")}
    for path in ("/api/status/station", "/api/status/check"):
        r = client.get(path)
        assert r.status_code == 200, path
    body = client.get("/api/status/station").json()
    assert set(body["snapshots"]) == set(snap.NAMES)
    assert all(not s["available"] for s in body["snapshots"].values())
    assert body["heartbeat"] is None and body["viewers"] is None
    assert body["rows"] == []
    chk = client.get("/api/status/check").json()
    assert chk["available"] is False and chk["verdict"] == "NONE"
    for n, cmd in snap.COMMANDS.items():
        assert cmd.startswith("smd ")


def test_stale_check_never_passes(client, snapdir):
    _install(snapdir, age=timedelta(minutes=20),
             check={"data": {"lines": [], "verdict": "PASS", "exit": 0}, "exit": 0})
    chk = client.get("/api/status/check").json()
    assert chk["stale"] is True
    assert chk["verdict"] == "STALE"


def test_fresh_check_groups_by_stage(client, snapdir):
    _install(snapdir)
    chk = client.get("/api/status/check").json()
    assert chk["verdict"] == "FAIL" and not chk["stale"]
    assert [s["stage"] for s in chk["stages"]] == [
        "platform", "sensor", "decode", "upload"]          # empty "sink" omitted
    assert [v["severity"] for v in chk["validate"]] == ["fail", "warn"]
    sensor = chk["stages"][1]["lines"][0]
    assert sensor["mark"] == "--"


def test_failed_snapshot_shows_error(client, snapdir):
    _install(snapdir, check={"data": None, "exit": 2, "error": "smd check crashed"})
    chk = client.get("/api/status/check").json()
    assert chk["verdict"] == "NONE"
    assert chk["error"] == "smd check crashed"
    assert client.get("/api/status/station").json()["snapshots"]["check"]["error"] \
        == "smd check crashed"


def test_station_from_fixtures(client, snapdir):
    _install(snapdir)
    body = client.get("/api/status/station").json()
    assert body["identity"]["callsign"] == "AC0G"
    assert body["identity"]["sigmond"]["local"] == "a1b2c3d"
    assert body["heartbeat"]["verdict"] == "degraded"
    assert body["viewers"]["viewers"][0]["name"] == "ka9q-web"
    psk = next(r for r in body["rows"] if r["name"] == "psk-recorder")
    assert psk["manifest_status"] == "match"
    assert psk["check"] == {"id": "decode.psk", "mark": "ok"}


def test_check_now_writes_one_request(client, snapdir, tmp_path):
    r = client.post("/api/status/check-now")
    assert r.status_code == 202 and r.json() == {"requested": True}
    files = list((tmp_path / "req").iterdir())
    assert len(files) == 1
    assert files[0].name.startswith("check-") and files[0].name[6:].isdigit()
    assert files[0].stat().st_size == 0


def test_check_now_without_dir_is_503(client, settings, tmp_path):
    settings.raw["status"] = {"snapshot_request_dir": str(tmp_path / "missing")}
    r = client.post("/api/status/check-now")
    assert r.status_code == 503
    assert "missing" not in r.text
