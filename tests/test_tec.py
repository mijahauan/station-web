"""HF TEC reaches the pages from whichever channel wrote it (D3).

B4's ``L3_tec`` held 1341 rows, every one under channel ``REANALYZED``
(hamsci-physics' hourly reanalysis), while every reader asked for
``AGGREGATED``.  ``/tec/current`` also looked back only 5 minutes, which an
hourly product never satisfies, and ``/propagation/tec`` fell back silently
to "the last 7 days" when the asked-for window was empty.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from conftest import PROC_VERSION, _iso, _minute_boundary, _write_rows

from station_web.services import freshness


def _reanalyzed(settings, rows):
    """Write L3_tec rows the way ionospheric_reanalysis.py does."""
    _write_rows(settings.data_root, settings.storage, "L3", "tec", "REANALYZED",
                "science/tec_reanalyzed", [
        {"timestamp_utc": _iso(t), "minute_boundary": _minute_boundary(t),
         "station": st, "tec_tecu": tec, "t_vacuum_error_ms": 0.2, "confidence": 0.7,
         "n_frequencies": 4, "residuals_ms": 0.1, "frequencies_mhz": "5,10,15,20",
         "quality_flag": "GOOD", "validation_flag": "UNVALIDATED",
         "propagation_mode": "1F2", "processing_version": "6.0.0"}
        for t, st, tec in rows])


def _drop_aggregated(settings):
    con = sqlite3.connect(settings.storage["sqlite_path"])
    con.execute('DELETE FROM "L3_tec" WHERE channel = ?', ("AGGREGATED",))
    con.commit()
    con.close()


@pytest.fixture
def reanalyzed_only(seeded):
    """B4's state: REANALYZED rows only, the newest ~70 min old (written at
    :05 for the hour before), on the reanalysis' 5-minute grid."""
    _drop_aggregated(seeded)
    now = datetime.now(timezone.utc)
    newest = now - timedelta(minutes=70)
    _reanalyzed(seeded, [(newest - timedelta(minutes=5 * k), st, 20.0 + k)
                         for k in range(6) for st in ("WWV", "WWVH")])
    return seeded


@pytest.fixture
def reanalyzed_client(reanalyzed_only):
    from fastapi.testclient import TestClient
    from station_web.app import create_app
    with TestClient(create_app(reanalyzed_only, control_socket=None)) as c:
        yield c


def test_current_reads_reanalyzed_within_two_hours(reanalyzed_client):
    j = reanalyzed_client.get("/api/tec/current").json()
    assert j["status"] == "ok", j
    assert j["source"] == "REANALYZED"
    assert j["n_stations"] == 2
    assert j["stations"]["WWV"]["tec_tecu"] == 20.0          # the newest window


def test_current_answers_no_data_past_two_hours(seeded):
    _drop_aggregated(seeded)
    _reanalyzed(seeded, [(datetime.now(timezone.utc) - timedelta(hours=3), "WWV", 9.0)])
    from fastapi.testclient import TestClient
    from station_web.app import create_app
    with TestClient(create_app(seeded, control_socket=None)) as c:
        j = c.get("/api/tec/current").json()
    assert j["status"] == "no_data" and j["source"] is None


def test_history_and_summary_label_the_source(reanalyzed_client):
    h = reanalyzed_client.get("/api/tec/history?start=-6h&end=now").json()
    assert h["source"] == "REANALYZED" and len(h["stations"]["WWVH"]["tec_tecu"]) == 6
    assert h["stations"]["WWV"]["mode"][0] == "1F2"          # 'propagation_mode', not 'mode'
    p = reanalyzed_client.get("/api/propagation/tec?start=-6h&end=now").json()
    assert p["source"] == "REANALYZED" and p["paths"]["WWV"]["count"] == 6
    s = reanalyzed_client.get("/api/tec/station/wwvh").json()
    assert s["source"] == "REANALYZED" and s["n_points"] == 6


def test_reanalyzed_wins_over_aggregated(seeded):
    """Both present in the window: the reanalysis is the better product."""
    _reanalyzed(seeded, [(datetime.now(timezone.utc) - timedelta(minutes=10), "WWV", 31.0)])
    from fastapi.testclient import TestClient
    from station_web.app import create_app
    with TestClient(create_app(seeded, control_socket=None)) as c:
        j = c.get("/api/tec/current").json()
    assert j["source"] == "REANALYZED" and j["stations"]["WWV"]["tec_tecu"] == 31.0


def test_aggregated_is_the_fallback(seeded_client):
    j = seeded_client.get("/api/tec/current").json()
    assert j["source"] == "AGGREGATED" and j["stations"]["WWV"]["tec_tecu"] == 14.7


def test_summary_never_substitutes_another_window(seeded_client):
    """A past day with no TEC is 404, not the last 7 days of TEC (the
    seeded fixture holds a fresh row the old fallback would have shown)."""
    day = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
    r = seeded_client.get(f"/api/propagation/tec?start={day}T00:00:00Z&end={day}T23:59:59Z")
    assert r.status_code == 404


def test_station_whitelist_has_no_chu(seeded_client):
    r = seeded_client.get("/api/tec/station/CHU")
    assert r.status_code == 400 and "CHU" not in r.json()["detail"].split("of", 1)[1]
    assert seeded_client.get("/api/tec/station/BPM").status_code == 200


def test_freshness_sees_reanalyzed_tec(reanalyzed_only):
    p = freshness.table(reanalyzed_only)["products"]["L3_tec"]
    assert p["verdict"] == "VALID", p
    assert p["channel"] == "REANALYZED"
    # hourly product, written at :05 for the hour before: ~70 min is normal
    assert p["expected_s"] >= 2 * 3600 - 1


def test_tec_read_failure_is_a_500(seeded_client, monkeypatch):
    from station_web.services import tec_service

    def boom(*a, **k):
        raise RuntimeError("reader exploded")
    monkeypatch.setattr(tec_service, "make_data_product_reader", boom)
    for path in ("/api/tec/current", "/api/tec/history", "/api/propagation/tec"):
        r = seeded_client.get(path)
        assert r.status_code == 500, path
        assert "reader exploded" in r.json()["detail"], path
