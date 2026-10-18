"""/api/propagation/timeline: right rows, bounded cost, honest errors (D4).

On B4 the 24 h station history took 14.8 s and much of station-web's
600 MB budget: for every directory under ``phase2/`` the service read
``L2_tick_phase`` from start-day 00:00 to end-day+1 00:00, as full row
dicts, to take a per-minute median SNR.  Its four ``except`` handlers
named an undefined ``channel``, so any read error became NameError, then
``None``, then 404 "No propagation timeline data".
"""
import shutil
import sqlite3
import time
import tracemalloc
from datetime import datetime, timedelta, timezone

import pytest

from conftest import PROC_VERSION, _iso, _write_rows
from shared_fixture import (BASE_SNR, N_MINUTES, SHARED, STATIONS, shared_seeded,  # noqa: F401
                            snr_at, minute_times)

from station_web.services import propagation_service


@pytest.fixture(autouse=True)
def _no_cache_between_tests():
    propagation_service.clear_timeline_cache()
    yield
    propagation_service.clear_timeline_cache()


@pytest.fixture
def shared_client(shared_seeded):
    from fastapi.testclient import TestClient
    from station_web.app import create_app
    with TestClient(create_app(shared_seeded, control_socket=None)) as c:
        yield c


def _by_station(j):
    out = {}
    for st, snr, dc in zip(j["stations"], j["snr_db"], j["d_clock_ms"]):
        out.setdefault(st, []).append((snr, dc))
    return out


def test_timeline_keeps_every_station_of_a_shared_channel(shared_client):
    j = shared_client.get("/api/propagation/timeline?start=-2h&end=now").json()
    assert j["count"] == N_MINUTES * len(STATIONS)
    rows = _by_station(j)
    assert set(rows) == set(STATIONS)
    # SNR is the MEDIAN of the minute's tick_phase windows for THAT station,
    # not the coarse tick_timing estimate (snr - 10) and not a mean (which
    # the one wild window would drag up by ~6 dB).
    for st in STATIONS:
        snrs = sorted({round(s, 3) for s, _ in rows[st]})
        assert BASE_SNR[st] in snrs, (st, snrs)
        assert all(s in (BASE_SNR[st], BASE_SNR[st] - 15.0) for s in snrs), (st, snrs)


def test_timeline_station_filter(shared_client):
    j = shared_client.get("/api/propagation/timeline?start=-2h&end=now&station=WWVH").json()
    assert set(j["stations"]) == {"WWVH"} and j["count"] == N_MINUTES


def test_timeline_finds_channels_from_the_products_not_directories(shared_seeded):
    """SQLite holds every product; a channel needs no directory, and a
    directory (ionex, WWVB_60, science/*) is not a channel."""
    root = shared_seeded.data_root / "phase2"
    shutil.rmtree(root / SHARED)
    for junk in ("ionex", "WWVB_60", "science/tec"):
        (root / junk).mkdir(parents=True, exist_ok=True)
    from fastapi.testclient import TestClient
    from station_web.app import create_app
    with TestClient(create_app(shared_seeded, control_socket=None)) as c:
        j = c.get("/api/propagation/timeline?start=-2h&end=now").json()
    assert j["count"] == N_MINUTES * len(STATIONS)


def test_timeline_read_failure_is_a_500_not_no_data(shared_client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(propagation_service, "make_data_product_reader", boom)
    r = shared_client.get("/api/propagation/timeline?start=-2h&end=now")
    assert r.status_code == 500
    assert "disk on fire" in r.json()["detail"]


def test_timeline_empty_window_is_404(shared_client):
    r = shared_client.get("/api/propagation/timeline?start=2020-01-01T00:00:00Z"
                          "&end=2020-01-01T06:00:00Z")
    assert r.status_code == 404


def test_timeline_is_cached_briefly(shared_seeded, monkeypatch):
    svc = propagation_service.PropagationService(shared_seeded.data_root)
    t1 = datetime.now(timezone.utc)
    t0 = t1 - timedelta(hours=2)
    first = svc.get_mode_timeline(t0, t1)
    calls = []
    monkeypatch.setattr(propagation_service, "make_data_product_reader",
                        lambda *a, **k: calls.append(1))
    again = svc.get_mode_timeline(t0, t1 + timedelta(seconds=5))
    assert again == first and not calls


# ── volume: a day of tick_phase on one SHARED channel ────────────────

WINDOWS = 55


def _bulk_tick_phase(settings, start, minutes):
    """Insert ``minutes`` x 3 stations x 55 windows straight into SQLite.

    The writer creates the table and its index (one row); executemany does
    the rest — ~240k rows a day, as on a SHARED channel at B4.
    """
    now = datetime.now(timezone.utc)
    t = start
    _write_rows(settings.data_root, settings.storage, "L2", "tick_phase", SHARED,
                f"{SHARED}/tick_phase", [{
                    "timestamp_utc": _iso(t), "minute_boundary_utc": int(t.timestamp()),
                    "channel": SHARED, "station": "WWV", "frequency_mhz": 10.0,
                    "window_start_second": 0, "window_end_second": 1,
                    "window_center_second": 0.5, "phase_rad": 0.0,
                    "carrier_phase_rad": 0.0, "dc_carrier_phase_rad": 0.0,
                    "timing_offset_ms": 0.3, "timing_uncertainty_ms": 0.05,
                    "snr_db": 20.0, "correlation_peak": 0.6, "coherence_quality": 0.9,
                    "valid_ticks": 1, "processed_at": _iso(now),
                    "processing_version": PROC_VERSION}])
    con = sqlite3.connect(settings.storage["sqlite_path"])
    cols = ("timestamp_utc", "minute_boundary_utc", "channel", "station", "frequency_mhz",
            "window_start_second", "window_end_second", "window_center_second",
            "phase_rad", "carrier_phase_rad", "dc_carrier_phase_rad", "timing_offset_ms",
            "timing_uncertainty_ms", "snr_db", "correlation_peak", "coherence_quality",
            "valid_ticks", "processed_at", "processing_version")

    def rows():
        mb0 = int(start.timestamp())
        for m in range(minutes):
            mb = mb0 + 60 * m
            for st_i, st in enumerate(STATIONS):
                for w in range(WINDOWS):
                    ts = datetime.fromtimestamp(mb + w + 1 + 0.01 * st_i, timezone.utc)
                    yield (_iso(ts), mb, SHARED, st, 10.0, w + 1, w + 2, w + 1.5,
                           0.01 * w, 0.01 * w, 0.01 * w, 0.3, 0.05,
                           10.0 + st_i + (w % 7), 0.6, 0.9, 1, _iso(now), PROC_VERSION)
    con.executemany(f'INSERT INTO "L2_tick_phase" ({",".join(cols)}) '
                    f'VALUES ({",".join("?" * len(cols))})', rows())
    con.commit()
    con.close()


def _bulk_tick_timing(settings, start, minutes):
    now = datetime.now(timezone.utc)
    rows = []
    for m in range(minutes):
        t = start + timedelta(minutes=m, seconds=30)
        for st in STATIONS:
            rows.append({
                "timestamp_utc": _iso(t), "minute_boundary_utc": int((start + timedelta(minutes=m)).timestamp()),
                "channel": SHARED, "station": st, "frequency_mhz": 10.0,
                "mean_snr_db": 5.0, "valid_windows": 50, "total_windows": 59,
                "overall_confidence": 0.9, "expected_delay_ms": 5.0, "d_clock_ms": 0.2,
                "d_clock_uncertainty_ms": 0.08, "d_clock_source": "tick_ensemble",
                "doppler_hz": 0.05, "doppler_uncertainty_hz": 0.02,
                "ensemble_n_edges": 50, "n_clean": 48,
                "processed_at": _iso(now), "processing_version": PROC_VERSION})
    _write_rows(settings.data_root, settings.storage, "L2", "tick_timing", SHARED,
                f"{SHARED}/tick_timing", rows)
    # discover_channels reads L2_timing_measurements: one row names the channel.
    from shared_fixture import _timing_row
    _write_rows(settings.data_root, settings.storage, "L2", "timing_measurements", SHARED,
                f"{SHARED}/clock_offset", [_timing_row(start, "WWV", 0, now)])


def test_24h_timeline_stays_fast_and_bounded(settings):
    """Two days of tick_phase on disk, a 24 h request: the answer reads
    only the window (+-120 s), aggregates in a stream, and fits in a few
    tens of MB.  The old path materialised both days as full dicts."""
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    day = 24 * 60
    begin = end - timedelta(minutes=2 * day)
    _bulk_tick_phase(settings, begin, 2 * day)
    _bulk_tick_timing(settings, end - timedelta(minutes=day), day)

    svc = propagation_service.PropagationService(settings.data_root)
    tracemalloc.start()
    t0 = time.monotonic()
    out = svc.get_mode_timeline(end - timedelta(hours=24), end)
    elapsed = time.monotonic() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert out["count"] == day * len(STATIONS)
    # median of 10+st_i+(w%7) over w=0..54 is 10+st_i+3
    assert sorted(set(out["snr_db"])) == [13.0, 14.0, 15.0]
    assert peak < 48 * 1024 * 1024, f"peak {peak / 1e6:.1f} MB"
    assert elapsed < 20, f"{elapsed:.1f} s"
