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
    assert psk["instances"] == {"configured": 1, "enabled": 1, "active": 1}
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
