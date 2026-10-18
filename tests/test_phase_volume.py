"""The phase endpoints against a station-sized L2_tick_phase.

B4 holds about a million tick_phase rows a day.  The old service fetched a
whole day of every column for each channel, built dicts from it, and let
four such requests run at once; station-web hit its 600 MB MemoryMax and
died.  When a request did survive, a row cap filled from the first channel
and every other trace went missing without a word.

These tests build one synthetic day at that density -- one SHARED channel
heard from three stations plus one single-station channel, about 317k rows
-- and hold the endpoints to bounds: response size, every trace across the
whole window, peak Python memory for one request, and a channel listing
that never scans the table.
"""
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
from station_web.app import create_app
from station_web.services import phase_service

DAY0 = datetime(2026, 10, 17, tzinfo=timezone.utc)
DAY1 = DAY0 + timedelta(days=1)
WINDOW = f"start={DAY0:%Y-%m-%dT%H:%M:%SZ}&end={DAY1:%Y-%m-%dT%H:%M:%SZ}"

# Known Doppler per (channel, station): f_D = +(1/2pi) dphi/dt.  Small
# enough that the ~6 s gap between one minute's last window and the next
# minute's first never aliases the unwrap (6 s * 0.05 Hz * 2pi < pi).
# SHARED_10000 carries ONE composite carrier, so every station's rows on it
# share one Doppler; each station's tick windows see it at its own phase
# offset.  The views answer with one composite trace for that channel.
TRACES = {
    ("SHARED_10000", "WWV"): 0.03,
    ("SHARED_10000", "WWVH"): 0.03,
    ("SHARED_10000", "BPM"): 0.03,
    ("WWV_20000", "WWV"): 0.04,
}
PHASE_OFFSET = {"WWV": 0.0, "WWVH": 1.1, "BPM": -2.0}
#: What the views return: one trace per SHARED channel, one per station elsewhere.
VIEW_TRACES = {("SHARED_10000", "composite"): 0.03, ("WWV_20000", "WWV"): 0.04}
WINDOWS_PER_MINUTE = 55
PAD_MINUTES = 10           # rows either side of the day, which the window must cut
# One trace loses an hour, as a receiver outage would.  A phase difference
# across it carries no Doppler, and must not leak into the bucket after it.
GAP_TRACE = ("WWV_20000", "WWV")
GAP = (DAY0 + timedelta(hours=12), DAY0 + timedelta(hours=13))


def _build_db(db_path):
    """Create the table through hamsci-dsp's writer, then bulk-insert rows."""
    for ch in {c for c, _ in TRACES}:
        w = make_data_product_writer(output_dir=db_path.parent / ch, product_level="L2",
                                     product_name="tick_phase", channel=ch,
                                     storage_config={"sqlite_path": str(db_path)})
        w.close()

    t0 = int(DAY0.timestamp()) - 60 * PAD_MINUTES
    n_min = 24 * 60 + 2 * PAD_MINUTES
    mb = t0 + 60 * np.arange(n_min)
    start_s = np.arange(WINDOWS_PER_MINUTE)
    # processed ~65 s after the minute opened, as the live writer stamps it
    stamps = [datetime.fromtimestamp(int(m) + 65, timezone.utc).isoformat(timespec="microseconds")
              for m in mb]
    con = sqlite3.connect(db_path)
    try:
        for (ch, stn), fd in TRACES.items():
            freq = int(ch.split("_")[1]) / 1000.0
            rows = []
            for i, m in enumerate(mb):
                if (ch, stn) == GAP_TRACE and GAP[0].timestamp() <= m < GAP[1].timestamp():
                    continue
                t = m + start_s + 2.5
                k = (t - t0)
                phi = 2 * math.pi * fd * k + 0.05 * np.sin(0.7 * k) + PHASE_OFFSET[stn]
                wrapped = (phi + math.pi) % (2 * math.pi) - math.pi
                snr = 15.0 + 2.0 * np.sin(k / 300.0)
                for j in range(WINDOWS_PER_MINUTE):
                    rows.append((stamps[i], int(m), ch, stn, freq, int(start_s[j]),
                                 int(start_s[j]) + 5, float(start_s[j]) + 2.5,
                                 float(wrapped[j]), float(wrapped[j]), float(wrapped[j]),
                                 0.3, 0.05, float(snr[j]), 0.6, 0.9, 5))
            con.executemany(
                "INSERT INTO L2_tick_phase (timestamp_utc, minute_boundary_utc, channel, "
                "station, frequency_mhz, window_start_second, window_end_second, "
                "window_center_second, phase_rad, carrier_phase_rad, dc_carrier_phase_rad, "
                "timing_offset_ms, timing_uncertainty_ms, snr_db, correlation_peak, "
                "coherence_quality, valid_ticks) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows)
        con.commit()
    finally:
        con.close()


@pytest.fixture(scope="module")
def big_db(tmp_path_factory):
    d = tmp_path_factory.mktemp("bigphase")
    db = d / "timestd.db"
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
    phase_service.clear_cache()
    yield s
    phase_service.clear_cache()


@pytest.fixture
def big_client(big_settings):
    app = create_app(big_settings, control_socket=None)
    with TestClient(app) as c:
        yield c


def _keys(series):
    return {(s["channel"], s["station"]) for s in series}


# ── every (channel, station) across the whole window ─────────────────────────

@pytest.mark.parametrize("path", ["timeseries", "doppler", "scintillation"])
def test_every_trace_spans_the_whole_day(big_client, path):
    r = big_client.get(f"/api/phase/{path}?{WINDOW}")
    assert r.status_code == 200
    j = r.json()
    assert _keys(j["series"]) == set(VIEW_TRACES)
    lo, hi = DAY0.timestamp(), DAY1.timestamp()
    for s in j["series"]:
        assert 0 < s["n_points"] <= phase_service.MAX_POINTS_PER_TRACE
        assert len(s["epochs"]) == s["n_points"]
        assert lo <= s["epochs"][0] < lo + 600, (s["channel"], s["station"])
        assert hi - 600 < s["epochs"][-1] <= hi, (s["channel"], s["station"])
    meta = j["metadata"]
    assert meta["bucket_seconds"] >= 60          # 86400 s / 1500 points
    assert meta["truncated"] == []


def test_timeseries_reports_its_aggregation(big_client):
    j = big_client.get(f"/api/phase/timeseries?{WINDOW}").json()
    meta = j["metadata"]
    assert meta["max_points_per_trace"] == phase_service.MAX_POINTS_PER_TRACE
    per_trace = 24 * 60 * WINDOWS_PER_MINUTE
    gap_trace = 23 * 60 * WINDOWS_PER_MINUTE
    for s in j["series"]:
        # every sample inside the day landed in some bucket
        if (s["channel"], s["station"]) == GAP_TRACE:
            want = gap_trace
        elif s["composite"]:
            want = 3 * per_trace            # the composite pools all three stations' rows
        else:
            want = per_trace
        assert s["n_samples"] == want


# ── the numbers still mean what the charts say ───────────────────────────────

def test_doppler_recovers_the_injected_shift(big_client):
    j = big_client.get(f"/api/phase/doppler?{WINDOW}").json()
    for s in j["series"]:
        fd = VIEW_TRACES[(s["channel"], s["station"])]
        med = float(np.median(s["doppler_hz"]))
        assert med == pytest.approx(fd, abs=2e-3), (s["channel"], s["station"], med)


def test_every_doppler_bucket_holds_the_shift(big_client):
    # per bucket, not just the median: the bucket after the outage included
    j = big_client.get(f"/api/phase/doppler?{WINDOW}").json()
    for s in j["series"]:
        fd = VIEW_TRACES[(s["channel"], s["station"])]
        worst = max(abs(v - fd) for v in s["doppler_raw_hz"])
        assert worst < 2e-3, (s["channel"], s["station"], worst)


def test_unwrapped_phase_follows_the_ramp(big_client):
    j = big_client.get(f"/api/phase/timeseries?{WINDOW}").json()
    for s in j["series"]:
        fd = VIEW_TRACES[(s["channel"], s["station"])]
        ep, ph = np.array(s["epochs"]), np.array(s["phase_rad"])
        # No unwrap can count the cycles that pass during an outage, so fit
        # each side of the gap on its own.
        cut = GAP[0].timestamp()
        for side in (ep < cut, ep >= cut):
            slope = np.polyfit(ep[side], ph[side], 1)[0]
            assert slope / (2 * math.pi) == pytest.approx(fd, abs=1e-3)


def test_sigma_phi_sees_the_residual(big_client):
    # 0.05 rad sine riding the ramp: std after a linear detrend ~ 0.035 rad
    j = big_client.get(f"/api/phase/scintillation?{WINDOW}").json()
    for s in j["series"]:
        med = float(np.median(s["sigma_phi_rad"]))
        assert 0.02 < med < 0.05, (s["channel"], s["station"], med)
        assert all(v is not None and v > 0 for v in s["s4"])


# ── size, compression, memory ────────────────────────────────────────────────

@pytest.mark.parametrize("path,limit", [
    ("timeseries", 1_000_000), ("doppler", 1_000_000),
    ("scintillation", 1_000_000), ("combined", 2_500_000),
])
def test_response_size_is_bounded(big_client, path, limit):
    r = big_client.get(f"/api/phase/{path}?{WINDOW}", headers={"Accept-Encoding": "identity"})
    assert r.status_code == 200
    assert len(r.content) < limit, len(r.content)


def test_responses_are_gzipped(big_client):
    r = big_client.get(f"/api/phase/timeseries?{WINDOW}", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip"


def test_one_request_peak_memory_is_bounded(big_settings):
    svc = phase_service.PhaseService(big_settings.data_root)
    tracemalloc.start()
    try:
        svc.get_combined(DAY0, DAY1)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 100 * 1024 * 1024, f"peak {peak / 1e6:.1f} MB"


# ── one pass serves every view ───────────────────────────────────────────────

def _count_tick_phase_reads(monkeypatch):
    """Record every statement the service sends that reads row data."""
    statements = []
    real = sqlite3.connect

    def connect(*a, **kw):
        con = real(*a, **kw)
        con.set_trace_callback(statements.append)
        return con
    monkeypatch.setattr(phase_service.sqlite3, "connect", connect)
    return statements


def test_three_views_share_one_read(big_client, monkeypatch):
    statements = _count_tick_phase_reads(monkeypatch)
    for path in ("timeseries", "doppler", "scintillation", "combined"):
        assert big_client.get(f"/api/phase/{path}?{WINDOW}").status_code == 200
    reads = [s for s in statements if "carrier_phase_rad" in s]
    # one projection query per channel, once, for all four endpoints
    assert len(reads) == 2, reads


def test_heavy_reads_never_overlap(big_settings, monkeypatch):
    svc = phase_service.PhaseService(big_settings.data_root)
    live, peak = [0], [0]
    lock = threading.Lock()
    real = phase_service.PhaseService._read_and_aggregate

    def tracked(self, *a, **kw):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        try:
            time.sleep(0.05)
            return real(self, *a, **kw)
        finally:
            with lock:
                live[0] -= 1
    monkeypatch.setattr(phase_service.PhaseService, "_read_and_aggregate", tracked)

    # four different windows, so the cache cannot absorb them
    threads = [threading.Thread(target=svc.get_combined,
                                args=(DAY0 + timedelta(hours=h), DAY0 + timedelta(hours=h + 2)))
               for h in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] == 1


# ── /phase/channels: cheap, and honest about DC ──────────────────────────────

def test_channels_without_a_full_scan(big_client, monkeypatch):
    statements = _count_tick_phase_reads(monkeypatch)
    t0 = time.perf_counter()
    r = big_client.get("/api/phase/channels")
    elapsed = time.perf_counter() - t0
    assert r.status_code == 200
    j = r.json()
    by = {c["channel"]: c for c in j["channels"]}
    assert set(by) == {"SHARED_10000", "WWV_20000"}
    assert by["SHARED_10000"]["stations"] == ["BPM", "WWV", "WWVH"]
    assert by["WWV_20000"]["stations"] == ["WWV"]
    assert j["stations"] == ["BPM", "WWV", "WWVH"]

    sql = [s for s in statements if "L2_tick_phase" in s]
    assert sql and not any("COUNT(" in s.upper() for s in sql)
    con = sqlite3.connect(str(cfgmod.config.storage["sqlite_path"]))
    try:
        for s in sql:
            plan = " | ".join(row[-1] for row in con.execute("EXPLAIN QUERY PLAN " + s))
            assert "USING" in plan and "SCAN L2_tick_phase" not in plan, (s, plan)
    finally:
        con.close()
    assert elapsed < 1.0, elapsed


def test_dc_meaningful_comes_from_the_channel_not_a_chu_list(big_client):
    by = {c["channel"]: c for c in big_client.get("/api/phase/channels").json()["channels"]}
    assert by["WWV_20000"]["dc_meaningful"] is True
    assert by["SHARED_10000"]["dc_meaningful"] is False
    assert not hasattr(phase_service, "UNAMBIGUOUS_CHANNELS")


# ── requests that would size the accumulators without bound ──────────────────

@pytest.mark.parametrize("query", [
    "start=-8d&end=now",
    f"{WINDOW}&channel=NOPE_123",
    f"{WINDOW}&station=XYZ",
    f"{WINDOW}&phase_type=bogus",
    f"{WINDOW}&window=0",
    f"{WINDOW}&window=1",
    f"{WINDOW}&window=15",
    "start=2026-10-18T00:00:00Z&end=2026-10-17T00:00:00Z",
])
def test_unbounded_requests_are_refused(big_client, query):
    assert big_client.get(f"/api/phase/combined?{query}").status_code == 400


def test_known_channel_and_station_are_accepted(big_client):
    r = big_client.get(f"/api/phase/combined?{WINDOW}&channel=SHARED_10000&station=WWVH")
    assert r.status_code == 200
    # the carrier belongs to no single station: the filter keeps the composite whole
    assert _keys(r.json()["phase"]["series"]) == {("SHARED_10000", "composite")}


def test_errors_never_echo_client_input(big_client):
    marker = "zz-script-marker"
    for q in (f"start={marker}&end=now", f"start=-1h&end={marker}", f"start=-{marker}",
              f"{WINDOW}&channel={marker}", f"{WINDOW}&phase_type={marker}"):
        r = big_client.get(f"/api/phase/timeseries?{q}")
        assert r.status_code == 400
        assert "marker" not in r.text, r.text


def test_cache_stays_bounded(big_settings):
    svc = phase_service.PhaseService(big_settings.data_root)
    for m in range(3 * phase_service._CACHE_MAX):
        s = DAY0 + timedelta(minutes=10 * m)
        svc.get_combined(s, s + timedelta(minutes=5))
    assert len(phase_service._cache) <= phase_service._CACHE_MAX


def test_window_choices_are_a_fixed_set():
    # A free window_s would size the per-window accumulators (span / window) and
    # mint cache keys at will; a short list does neither.
    assert phase_service.ALLOWED_WINDOWS_S == (10, 30, 60, 120, 300, 600)


def test_a_busy_lock_answers_quickly(big_client, monkeypatch):
    # A queued request must not park a pool thread for minutes; it answers 503.
    monkeypatch.setattr(phase_service, "_HEAVY_WAIT_S", 0.2)
    client = big_client
    assert phase_service._HEAVY.acquire(timeout=1)
    try:
        t0 = time.monotonic()
        r = client.get(f"/api/phase/combined?{WINDOW}")
        assert r.status_code == 503
        assert time.monotonic() - t0 < 2.0
    finally:
        phase_service._HEAVY.release()


def test_default_wait_is_short():
    assert phase_service._HEAVY_WAIT_S <= 5.0


def test_nearby_windows_share_one_read(big_client, monkeypatch):
    # Start and end snap to bucket boundaries, so requests a few seconds apart
    # land on one cache entry instead of one full read each.
    statements = _count_tick_phase_reads(monkeypatch)
    base = datetime(DAY0.year, DAY0.month, DAY0.day, 0, 0, 7, tzinfo=timezone.utc)
    for dt in (0, 3, 11, 19):
        s = (base + timedelta(seconds=dt)).strftime("%Y-%m-%dT%H:%M:%SZ")
        e = (base + timedelta(days=1, seconds=dt)).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert big_client.get(f"/api/phase/combined?start={s}&end={e}").status_code == 200
    reads = [s for s in statements if "carrier_phase_rad" in s]
    assert len(reads) == 2, reads      # one per channel, once
