"""Ionogram: channels and stations come from L1_all_arrivals; ToF matches the writer.

On B4 the channel list globbed ``phase2/<ch>/all_arrivals/*.h5`` over a
hard-coded list that still led with CHU, so it returned ``[]`` and the page
defaulted to CHU_7850.  The live rows sit in SQLite under SHARED_* channels,
which carry WWV, WWVH and BPM at once.

ToF semantics, from hf-timestd ``metrology_service`` (the writer) and
``metrology_engine``:
- ``tone_correlator`` rows: ``minute_boundary_utc`` is the true minute,
  ``utc_second`` the tick's own second, and ``arrival_ms`` counts from the
  start of the minute buffer.  ToF = arrival_ms - 1000*(utc_second - minute).
- ``edge_tick`` / ``clean`` rows: ``model_expected_ms`` is the geometric
  propagation delay and ``timing_error_ms`` the residual against it, while
  ``clean`` rows write ``arrival_ms = 0``.  ToF = model_expected_ms + timing_error_ms.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import _iso, _minute_boundary, _write_rows

ROUTER = Path(__file__).resolve().parents[1] / "src" / "station_web" / "routers" / "ionogram.py"
SHARED = "SHARED_10000"
# per-station geometric delay (ms) the fixture pretends the model predicted
DELAY = {"WWV": 6.0, "WWVH": 25.0, "BPM": 45.0}


def _rows(t):
    mb = _minute_boundary(t)
    ts = _iso(t)
    out = []
    for station, delay in DELAY.items():
        base = {"timestamp_utc": ts, "minute_boundary_utc": mb, "channel": SHARED,
                "station": station, "frequency_mhz": 10.0, "corr_snr_db": 20.0}
        # tone correlator at second 15: arrival counts from the buffer start
        out.append({**base, "utc_second": mb + 15, "peak_rank": 0,
                    "arrival_ms": 15000.0 + delay + 0.2, "timing_error_ms": 0.2,
                    "model_expected_ms": 15000.0 + delay, "carrier_phase_rad": 0.0,
                    "detection_method": "tone_correlator", "sec_in_minute": 15})
        # edge tick at second 20: model_expected_ms is the propagation delay
        out.append({**base, "utc_second": mb + 20, "peak_rank": 0,
                    "arrival_ms": 20000.0 + delay + 0.4, "timing_error_ms": 0.4,
                    "model_expected_ms": delay, "carrier_phase_rad": 0.7,
                    "detection_method": "edge_tick", "sec_in_minute": 20})
        # CLEAN secondary path: arrival_ms is written as 0.0
        out.append({**base, "utc_second": mb + 20, "peak_rank": 1,
                    "arrival_ms": 0.0, "timing_error_ms": 3.5,
                    "model_expected_ms": delay, "carrier_phase_rad": -0.4,
                    "detection_method": "clean", "sec_in_minute": 20})
    return out


@pytest.fixture
def shared_client(seeded, seeded_client):
    t = datetime.now(timezone.utc) - timedelta(minutes=5)
    _write_rows(seeded.data_root, seeded.storage, "L1", "all_arrivals", SHARED,
                f"{SHARED}/all_arrivals", _rows(t))
    return seeded_client


def test_channels_discovered_from_the_table(shared_client):
    j = shared_client.get("/api/ionogram/channels").json()
    by = {c["channel"]: c for c in j["channels"]}
    assert set(by) == {SHARED, "WWV_20000"}
    assert by[SHARED]["stations"] == ["BPM", "WWV", "WWVH"]
    assert by["WWV_20000"]["stations"] == ["WWV"]
    assert by[SHARED]["latest"] is not None


def test_channels_empty_without_table(client):
    j = client.get("/api/ionogram/channels").json()
    assert j["channels"] == [] and j["n_channels"] == 0


@pytest.mark.parametrize("station,delay", sorted(DELAY.items()))
def test_station_filter_and_tof(shared_client, station, delay):
    j = shared_client.get(f"/api/ionogram/arrivals/timeseries?channel={SHARED}"
                          f"&station={station}&start=-1h&end=now").json()
    assert j["status"] == "ok", j
    assert j["station"] == station
    # dominant: tone (delay+0.2) and edge tick (delay+0.4), median per minute
    assert j["dominant"]["tof_ms"] == [pytest.approx(delay + 0.3)]
    # the CLEAN secondary arrives at delay + 3.5, despite arrival_ms = 0
    assert j["secondary"]["tof_ms"] == [pytest.approx(delay + 3.5)]


def test_cluster_endpoint_filters_station(shared_client):
    j = shared_client.get(f"/api/ionogram/arrivals?channel={SHARED}&station=WWVH"
                          "&start=-1h&end=now&include_kde=false").json()
    assert j["status"] == "ok"
    assert sorted(j["scatter"]["tof_ms"]) == [pytest.approx(25.2), pytest.approx(25.4),
                                              pytest.approx(28.5)]


def test_doppler_delay_filters_station(shared_client):
    j = shared_client.get(f"/api/ionogram/doppler-delay?channel={SHARED}&station=BPM"
                          "&start=-1h&end=now&include_kde=false").json()
    assert j["status"] == "ok" and j["station"] == "BPM"
    assert j["n_points"] == 3


def test_default_channel_exists(shared_client):
    j = shared_client.get("/api/ionogram/arrivals/timeseries?start=-1h&end=now").json()
    assert j["channel"] in (SHARED, "WWV_20000")
    assert j["status"] == "ok"


def test_no_data_carries_a_reason(client):
    j = client.get("/api/ionogram/arrivals/timeseries?start=-1h&end=now").json()
    assert j["status"] == "no_data" and j["reason"]


def test_no_chu_and_no_hdf5_in_router():
    text = ROUTER.read_text()
    assert "CHU" not in text
    assert ".h5" not in text
