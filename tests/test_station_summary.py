"""build_rows and the snapshot reader, over the fixture snapshots."""
import json
import shutil
from pathlib import Path

from station_web.services import snapshot as snap
from station_web.services import station_summary as ss

FIX = Path(__file__).parent / "fixtures" / "snapshot"


def _load(name):
    return json.loads((FIX / f"{name}.json").read_text())["data"]


def _rows():
    return {r["name"]: r for r in ss.build_rows(
        _load("catalog"), _load("components"), _load("instances"),
        _load("manifest"), _load("check"))}


def test_rows_from_fixture_snapshots():
    rows = _rows()
    psk = rows["psk-recorder"]
    assert psk["installed"] is True
    assert psk["manifest_status"] == "match"
    assert psk["instances"] == {"rows": 1, "configured": 1, "enabled": 1, "active": 1}
    assert psk["check"] == {"id": "decode.psk", "mark": "ok"}
    assert psk["behind"] == 2
    assert rows["wspr-recorder"]["manifest_status"] == "moved"
    assert rows["wspr-recorder"]["instances"]["active"] == 0
    assert rows["hs-uploader"]["check"] == {"id": "upload.uploads", "mark": "FAIL"}
    meteor = rows["meteor-scatter"]
    assert meteor["installed"] is False
    assert meteor["manifest_status"] is None
    # sigmond is installed but not in the catalog: appended after it.
    assert rows["sigmond"]["installed"] is True


def test_never_fetched_shows_no_count():
    comps = _load("components")
    comps[1]["fetched_at"] = None          # psk-recorder, behind 2
    rows = {r["name"]: r for r in ss.build_rows(
        _load("catalog"), comps, _load("instances"), _load("manifest"),
        _load("check"))}
    assert rows["psk-recorder"]["behind"] is None
    assert rows["psk-recorder"]["fetched_at"] is None


def test_no_manifest_reads_no_manifest():
    man = {"present": False, "release": None, "source": None, "components": {}}
    rows = {r["name"]: r for r in ss.build_rows(
        _load("catalog"), _load("components"), _load("instances"), man,
        _load("check"))}
    assert rows["psk-recorder"]["manifest_status"] == "no_manifest"
    assert rows["meteor-scatter"]["manifest_status"] is None


def test_worst_mark_wins_for_a_client():
    check = {"lines": [
        {"mark": "ok", "id": "a", "client": "x"},
        {"mark": "FAIL", "id": "b", "client": "x"},
        {"mark": "wait", "id": "c", "client": "x"}]}
    rows = ss.build_rows([{"name": "x", "installed": True}], [], [], None, check)
    assert rows[0]["check"] == {"id": "b", "mark": "FAIL"}


def test_no_instance_rows_is_said_not_counted():
    """A single-unit service or a library has no per-reporter instances;
    the row says so with rows 0, which the page shows in words."""
    rows = {r["name"]: r for r in ss.build_rows(
        _load("catalog"), _load("components"), {"instances": []},
        _load("manifest"), _load("check"))}
    assert rows["psk-recorder"]["instances"]["rows"] == 0


def test_failed_components_snapshot_is_unknown_not_never_fetched():
    rows = {r["name"]: r for r in ss.build_rows(
        _load("catalog"), None, _load("instances"), _load("manifest"),
        _load("check"))}
    psk = rows["psk-recorder"]
    assert psk["repo_known"] is False
    assert psk["behind"] is None and psk["fetched_at"] is None
    assert _rows()["psk-recorder"]["repo_known"] is True


def test_failed_check_snapshot_is_unknown_not_blank():
    rows = {r["name"]: r for r in ss.build_rows(
        _load("catalog"), _load("components"), _load("instances"),
        _load("manifest"), None)}
    assert rows["psk-recorder"]["check"] is None          # unknown
    blank = ss.build_rows([{"name": "x", "installed": True}], [], [], None,
                          {"lines": []})
    assert blank[0]["check"] == {"id": None, "mark": None}  # no line for it


def _hb(available=True, data=None, error=None, stale=False):
    return {"available": available, "data": data, "error": error,
            "stale": stale}


def test_heartbeat_unavailable():
    hb = ss._heartbeat(_hb(available=False, error="snapshot not written yet"))
    assert hb["state"] == "unavailable"


def test_heartbeat_no_tick_is_not_not_running():
    err = "admin heartbeat show: no heartbeat ticks in /var/spool/x"
    hb = ss._heartbeat(_hb(error=err))
    assert hb == {"state": "no_tick", "stale": False}


def test_heartbeat_tick_carries_verdict_reason_time():
    hb = ss._heartbeat(_hb(data=_load("heartbeat")))
    assert hb["state"] == "tick" and hb["verdict"] == "INVALID"
    assert hb["reason"].startswith("uploads:")
    assert hb["emitted_at"] == "2026-10-27T11:58:00Z"


def test_heartbeat_other_failure_is_failed():
    hb = ss._heartbeat(_hb(error="timed out"))
    assert hb == {"state": "failed", "error": "timed out"}


def test_read_snapshot_ages_and_goes_stale(settings, tmp_path):
    d = tmp_path / "snap"
    d.mkdir()
    shutil.copy(FIX / "check.json", d / "check.json")
    settings.raw["status"] = {"snapshot_dir": str(d)}
    ran = snap._parse_ts("2026-10-27T12:00:00Z")
    fresh = snap.read_snapshot("check", settings, now=ran + 60)
    assert fresh["available"] and not fresh["stale"] and fresh["age_s"] == 60
    old = snap.read_snapshot("check", settings, now=ran + 901)
    assert old["stale"] is True


def test_garbage_snapshot_is_unavailable(settings, tmp_path):
    d = tmp_path / "snap"
    d.mkdir()
    (d / "check.json").write_text("{not json")
    settings.raw["status"] = {"snapshot_dir": str(d)}
    r = snap.read_snapshot("check", settings)
    assert r["available"] is False and r["error"]
    assert r["command"] == "smd check --json"
