"""The timing authority's two files in the freshness table: informational only.

A station without hf-timestd has neither file, and a stale authority must
not hold the station at DEGRADED either; the Status page shows it, named.
"""
from datetime import datetime, timedelta, timezone

from station_web.services import freshness
from timing_fixture import T6_SNAPSHOT, history_row, write_authority, write_history


def test_absent_authority_files_are_listed_not_judged(settings):
    t = freshness.table(settings)
    for key in ("authority.json", "authority_history.db"):
        p = t["products"][key]
        assert p["verdict"] == "INFORMATIONAL"
        assert p["applicable"] is False
        assert "not available on this station" in p["reason"]


def test_fresh_authority_files(settings):
    now = datetime.now(timezone.utc)
    write_authority(settings.authority_path, T6_SNAPSHOT, published=now - timedelta(seconds=4))
    write_history(settings.authority_history_path, [history_row(now - timedelta(seconds=20), 0)])
    t = freshness.table(settings)
    a, h = t["products"]["authority.json"], t["products"]["authority_history.db"]
    assert a["verdict"] == h["verdict"] == "INFORMATIONAL"
    assert a["applicable"] is True and a["stale"] is False and 3 <= a["age_s"] <= 10
    assert h["stale"] is False and 19 <= h["age_s"] <= 30
    assert a["expected_s"] == 75 and h["expected_s"] == 120


def test_stale_authority_never_forces_degraded(settings):
    old = datetime.now(timezone.utc) - timedelta(hours=3)
    write_authority(settings.authority_path, T6_SNAPSHOT, published=old)
    write_history(settings.authority_history_path, [history_row(old, 0)])
    t = freshness.table(settings)
    assert t["products"]["authority.json"]["stale"] is True
    assert t["products"]["authority_history.db"]["stale"] is True
    # with nothing else to judge the station reads "degraded" anyway; compare
    # against the same table without the authority files
    settings.authority_path.unlink()
    settings.authority_history_path.unlink()
    assert freshness.table(settings)["status"] == t["status"]


def test_control_socket_omits_authority_entries(client, settings):
    write_authority(settings.authority_path, T6_SNAPSHOT)
    client.get("/api/health/system")
    from station_web.app import STATUS
    assert "authority.json" not in STATUS["products"]


def test_authority_one_slow_cycle_old_is_not_stale(settings):
    """The writer's period runs 30 s plus its tick; 31 s old is a healthy snapshot."""
    now = datetime.now(timezone.utc)
    write_authority(settings.authority_path, T6_SNAPSHOT, published=now - timedelta(seconds=31))
    a = freshness.table(settings)["products"]["authority.json"]
    assert a["stale"] is False
