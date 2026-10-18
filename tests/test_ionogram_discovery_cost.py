"""Ionogram channel discovery stays cheap (final review S7).

``/ionogram/channels`` runs on every page load, and every request without a
``channel`` discovers the default.  It used to collect the stations heard
in the last two days with ``SELECT DISTINCT station`` over a two-day range;
``station`` sits outside the (channel, timestamp_utc) index, so SQLite
visited every row of those days: 400k-500k rows per SHARED channel on B4.
Discovery now looks at the hour before each channel's newest row, as
phase_service does, and caches its answer for a few seconds.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

from conftest import _iso, _minute_boundary, _write_rows
from station_web.routers import ionogram

CH = "SHARED_5000"


def _row(t, station):
    mb = _minute_boundary(t)
    return {"timestamp_utc": _iso(t), "minute_boundary_utc": mb, "channel": CH,
            "station": station, "frequency_mhz": 5.0, "utc_second": mb + 20,
            "peak_rank": 0, "arrival_ms": 20006.0, "timing_error_ms": 0.1,
            "corr_snr_db": 20.0, "model_expected_ms": 6.0, "carrier_phase_rad": 0.0,
            "detection_method": "edge_tick", "sec_in_minute": 20}


def _seed(seeded):
    now = datetime.now(timezone.utc)
    rows = [_row(now - timedelta(hours=30), "BPM"),          # heard yesterday only
            _row(now - timedelta(minutes=20), "WWV"),
            _row(now - timedelta(minutes=5), "WWVH")]
    _write_rows(seeded.data_root, seeded.storage, "L1", "all_arrivals", CH,
                f"{CH}/all_arrivals", rows)


def test_stations_come_from_the_hour_before_the_newest_row(seeded, seeded_client):
    ionogram.clear_cache()
    _seed(seeded)
    by = {c["channel"]: c for c in seeded_client.get("/api/ionogram/channels").json()["channels"]}
    assert by[CH]["stations"] == ["WWV", "WWVH"]


def test_discovery_answers_from_cache(seeded, seeded_client, monkeypatch):
    ionogram.clear_cache()
    _seed(seeded)
    assert seeded_client.get("/api/ionogram/channels").status_code == 200
    statements = []
    real = sqlite3.connect

    def connect(*a, **kw):
        con = real(*a, **kw)
        con.set_trace_callback(statements.append)
        return con
    monkeypatch.setattr(sqlite3, "connect", connect)
    assert seeded_client.get("/api/ionogram/channels").status_code == 200
    assert [s for s in statements if "L1_all_arrivals" in s] == []
