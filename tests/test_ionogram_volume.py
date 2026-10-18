"""The ionogram endpoints against a station-sized L1_all_arrivals.

B4 writes about 240k all_arrivals rows a day on each SHARED channel (measured
read-only 2026-10-18), three stations interleaved.  The old routes read a
day of every column through ``read_time_range``, built a dict per row, and
the page fired all three at once: about 215 MB of Python heap each at 150k
rows, past the unit's 600 MB MemoryMax together (final review B1).

These tests build one day at 240k rows on one SHARED channel and hold the
routes to bounds: peak Python memory per request, response size, one heavy
read at a time, and a 24 h ceiling on the window.
"""
import json
import math
import sqlite3
import threading
import time
import tracemalloc
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient
from hamsci_dsp.io import make_data_product_writer

from station_web import config as cfgmod
from station_web import heavy
from station_web.app import create_app
from station_web.routers import ionogram

CH = "SHARED_10000"
DAY0 = datetime(2026, 10, 17, tzinfo=timezone.utc)
DAY1 = DAY0 + timedelta(days=1)
WINDOW = {"start": f"{DAY0:%Y-%m-%dT%H:%M:%SZ}", "end": f"{DAY1:%Y-%m-%dT%H:%M:%SZ}"}
DELAY = {"WWV": 6.0, "WWVH": 25.0, "BPM": 45.0}
EDGE_PER_MIN, TONE_PER_MIN, CLEAN_PER_MIN = 40, 10, 5      # 55 rows/station/minute
ROWS_PER_DAY = 3 * 1440 * (EDGE_PER_MIN + TONE_PER_MIN + CLEAN_PER_MIN)

COLS = ("timestamp_utc, minute_boundary_utc, channel, station, frequency_mhz, utc_second, "
        "peak_rank, arrival_ms, timing_error_ms, corr_snr_db, model_expected_ms, "
        "carrier_phase_rad, detection_method, sec_in_minute")


def _build_db(db):
    w = make_data_product_writer(output_dir=db.parent / CH, product_level="L1",
                                 product_name="all_arrivals", channel=CH,
                                 storage_config={"sqlite_path": str(db)})
    w.close()
    rng = np.random.default_rng(7)
    con = sqlite3.connect(db)
    try:
        t0 = int(DAY0.timestamp()) - 600
        for i in range(1440 + 20):
            mb = t0 + 60 * i
            ts = datetime.fromtimestamp(mb + 65, timezone.utc).isoformat(timespec="microseconds")
            rows = []
            for stn, d in DELAY.items():
                for s in range(1, EDGE_PER_MIN + 1):
                    rows.append((ts, mb, CH, stn, 10.0, mb + s, 0, s * 1000.0 + d, 0.3 * rng.standard_normal(),
                                 15 + 10 * rng.random(), d, 0.02 * s + 0.1 * rng.standard_normal(),
                                 "edge_tick", s))
                for s in range(45, 45 + TONE_PER_MIN):
                    rows.append((ts, mb, CH, stn, 10.0, mb + s, 0, s * 1000.0 + d + 0.2, 0.2,
                                 20 + 10 * rng.random(), s * 1000.0 + d, 0.0, "tone_correlator", s))
                for s in range(1, CLEAN_PER_MIN + 1):
                    rows.append((ts, mb, CH, stn, 10.0, mb + s, 1, 0.0, 3.5 + rng.random(),
                                 22 + 5 * rng.random(), d, -0.4, "clean", s))
            con.executemany(f"INSERT INTO L1_all_arrivals ({COLS}) VALUES "
                            f"({','.join('?' * 14)})", rows)
        con.commit()
    finally:
        con.close()


@pytest.fixture(scope="module")
def big_db(tmp_path_factory):
    db = tmp_path_factory.mktemp("bigiono") / "timestd.db"
    _build_db(db)
    return db


@pytest.fixture
def big_settings(big_db, tmp_path, monkeypatch):
    root = tmp_path / "timestd"
    (root / "phase2").mkdir(parents=True)
    cfg = tmp_path / "sw.toml"
    cfg.write_text(f'[station]\ncallsign = "AC0G"\ngrid_square = "EM38ww"\n'
                   f'latitude = 38.9\nlongitude = -92.3\n'
                   f'[data]\nroot = "{root}"\n'
                   f'[storage]\nsqlite_path = "{big_db}"\n')
    monkeypatch.setenv("STATION_WEB_CONFIG", str(cfg))
    s = cfgmod.load_settings()
    cfgmod.config.use(s)
    ionogram.clear_cache()
    yield s
    ionogram.clear_cache()


@pytest.fixture
def big_client(big_settings):
    app = create_app(big_settings, control_socket=None)
    with TestClient(app) as c:
        yield c


def test_fixture_holds_a_b4_day(big_db):
    con = sqlite3.connect(big_db)
    n = con.execute("SELECT COUNT(*) FROM L1_all_arrivals WHERE minute_boundary_utc >= ? "
                    "AND minute_boundary_utc < ?", (DAY0.timestamp(), DAY1.timestamp())).fetchone()[0]
    con.close()
    assert n == ROWS_PER_DAY and n >= 230_000


# ── memory: each call the page makes, whole day, all three stations ──────────

PAGE_CALLS = [
    ("arrivals/timeseries", {"min_snr_db": 6}),
    ("arrivals", {"min_snr_db": 6, "downsample": 1, "include_kde": "true"}),
    ("doppler-delay", {"min_snr_db": 3, "method": "edge_tick", "include_kde": "true"}),
    ("combined", {"min_snr_db": 6, "downsample": 1}),
]


@pytest.mark.parametrize("path,params", PAGE_CALLS, ids=[p for p, _ in PAGE_CALLS])
def test_one_request_peak_memory_is_bounded(big_client, path, params):
    tracemalloc.start()
    try:
        r = big_client.get(f"/api/ionogram/{path}", params={"channel": CH, **WINDOW, **params},
                           headers={"Accept-Encoding": "identity"})
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert r.status_code == 200, r.text[:300]
    assert peak < 100 * 1024 * 1024, f"{path}: peak {peak / 1e6:.1f} MB"


@pytest.mark.parametrize("path,params", PAGE_CALLS, ids=[p for p, _ in PAGE_CALLS])
def test_response_size_is_bounded(big_client, path, params):
    r = big_client.get(f"/api/ionogram/{path}", params={"channel": CH, **WINDOW, **params},
                       headers={"Accept-Encoding": "identity"})
    assert r.status_code == 200
    assert len(r.content) < 6_000_000, len(r.content)


def test_scatter_points_are_capped(big_client):
    j = big_client.get("/api/ionogram/combined",
                       params={"channel": CH, **WINDOW, "min_snr_db": 6}).json()
    assert j["status"] == "ok"
    arr, dd = j["arrivals"], j["doppler_delay"]
    assert arr["n_points"] > ionogram.MAX_SCATTER_POINTS          # the day was read whole
    assert len(arr["scatter"]["tof_ms"]) <= ionogram.MAX_SCATTER_POINTS
    assert arr["n_scatter"] == len(arr["scatter"]["tof_ms"])
    assert dd["n_points"] > ionogram.MAX_SCATTER_POINTS
    assert len(dd["scatter"]["timing_error_ms"]) <= ionogram.MAX_SCATTER_POINTS
    for series in (j["timeseries"]["dominant"], j["timeseries"]["secondary"]):
        assert 0 < series["n"] <= 1441          # both ends inclusive


def test_combined_matches_the_three_routes(big_client):
    common = {"channel": CH, "station": "WWVH", **WINDOW}
    j = big_client.get("/api/ionogram/combined", params={**common, "min_snr_db": 6}).json()
    ts = big_client.get("/api/ionogram/arrivals/timeseries", params={**common, "min_snr_db": 6}).json()
    ar = big_client.get("/api/ionogram/arrivals", params={**common, "min_snr_db": 6}).json()
    dd = big_client.get("/api/ionogram/doppler-delay",
                        params={**common, "min_snr_db": 3, "method": "edge_tick"}).json()
    assert j["timeseries"] == ts
    assert j["arrivals"] == ar
    assert j["doppler_delay"] == dd
    assert ts["dominant"]["tof_ms"][0] == pytest.approx(25.2, abs=0.5)


def test_one_read_serves_the_combined_call(big_client, monkeypatch):
    calls = []
    real = ionogram._load_all_arrivals
    monkeypatch.setattr(ionogram, "_load_all_arrivals",
                        lambda *a, **kw: calls.append(1) or real(*a, **kw))
    assert big_client.get("/api/ionogram/combined",
                          params={"channel": CH, **WINDOW}).status_code == 200
    assert len(calls) == 1


# ── the query seeks the index and projects ───────────────────────────────────

def test_read_projects_and_seeks_the_index(big_client, monkeypatch):
    statements = []
    real = sqlite3.connect

    def connect(*a, **kw):
        con = real(*a, **kw)
        con.set_trace_callback(statements.append)
        return con
    monkeypatch.setattr(sqlite3, "connect", connect)
    assert big_client.get("/api/ionogram/combined",
                          params={"channel": CH, **WINDOW}).status_code == 200
    reads = [s for s in statements if "L1_all_arrivals" in s and "corr_snr_db" in s]
    assert reads
    con = real(str(cfgmod.config.storage["sqlite_path"]))
    try:
        for s in reads:
            assert "SELECT *" not in s.upper()
            plan = " | ".join(row[-1] for row in con.execute("EXPLAIN QUERY PLAN " + s))
            assert "channel=?" in plan and "timestamp_utc>" in plan, (s, plan)
    finally:
        con.close()


# ── concurrency ──────────────────────────────────────────────────────────────

def test_heavy_reads_never_overlap(big_client, monkeypatch):
    live, peak = [0], [0]
    lock = threading.Lock()
    real = ionogram._load_all_arrivals

    def tracked(*a, **kw):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        try:
            time.sleep(0.05)
            return real(*a, **kw)
        finally:
            with lock:
                live[0] -= 1
    monkeypatch.setattr(ionogram, "_load_all_arrivals", tracked)

    codes = []
    paths = ["arrivals/timeseries", "arrivals", "doppler-delay"]
    threads = [threading.Thread(target=lambda p=p: codes.append(
        big_client.get(f"/api/ionogram/{p}", params={"channel": CH, **WINDOW}).status_code))
        for p in paths]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] == 1
    assert set(codes) <= {200, 503} and 200 in codes


def test_a_busy_family_answers_503_quickly(big_client, monkeypatch):
    monkeypatch.setattr(heavy, "WAIT_S", 0.2)
    sem = heavy.semaphore(ionogram.HEAVY_FAMILY)
    assert sem.acquire(timeout=1)
    try:
        t0 = time.monotonic()
        r = big_client.get("/api/ionogram/combined", params={"channel": CH, **WINDOW})
        assert r.status_code == 503
        assert time.monotonic() - t0 < 2.0
    finally:
        sem.release()


def test_default_wait_is_short():
    assert heavy.WAIT_S <= 5.0


def test_window_past_a_day_is_refused(big_client):
    for path in ("combined", "arrivals", "arrivals/timeseries", "doppler-delay"):
        r = big_client.get(f"/api/ionogram/{path}",
                           params={"channel": CH, "start": "-25h", "end": "now"})
        assert r.status_code == 400, path
