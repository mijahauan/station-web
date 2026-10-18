"""Dashboard, station and correlation routes: channels from products, no CHU,
aware UTC, faults as faults (D4, D5, D11, D15)."""
import shutil
from datetime import datetime

import pytest

from error_assert import assert_opaque_500
from shared_fixture import N_MINUTES, SHARED, STATIONS, shared_seeded  # noqa: F401


@pytest.fixture
def client(shared_seeded):
    from fastapi.testclient import TestClient
    from station_web.app import create_app
    from station_web.services.propagation_service import clear_timeline_cache
    clear_timeline_cache()
    with TestClient(create_app(shared_seeded, control_socket=None)) as c:
        yield c


def test_dashboard_reads_a_channel_with_no_directory(shared_seeded, client):
    """SQLite holds the products; the dashboard used to iterdir phase2/."""
    shutil.rmtree(shared_seeded.data_root / "phase2" / SHARED)
    j = client.get("/api/dashboard/timing-error/24h").json()
    assert {f"{st}_10000" for st in STATIONS} <= set(j["broadcasts"])
    assert j["broadcasts"]["BPM_10000"]["n_measurements"] == N_MINUTES
    assert j["time_range"]["start"].endswith("Z") and "+00:00" not in j["time_range"]["start"]


def test_dashboard_detection_counts_land_in_intervals(client):
    j = client.get("/api/dashboard/broadcasts/24h").json()
    b = j["broadcasts"]["WWVH_10000"]["measurements"]
    # one L2_timing row and one tick_timing row per minute
    assert sum(b["detection_count"]) == 2 * N_MINUTES


def test_doppler_note_names_no_chu(client):
    assert "CHU" not in client.get("/api/dashboard/doppler/24h").json()["note"]


def test_dashboard_read_failure_is_a_500(client, monkeypatch):
    from station_web.routers import dashboard

    def boom(*a, **k):
        raise RuntimeError("db path /secret/timestd.db")
    monkeypatch.setattr(dashboard, "make_data_product_reader", boom)
    assert_opaque_500(client.get("/api/dashboard/timing-error/24h"), "/secret")


def test_station_history_serialises_range_as_utc_strings(client):
    j = client.get("/api/stations/BPM/history?hours=2").json()
    assert j["range"]["start"].endswith("Z") and j["range"]["end"].endswith("Z")
    assert set(j["timeline"]["stations"]) == {"BPM"}


def test_station_chu_is_unknown(client):
    assert client.get("/api/stations/CHU").status_code == 404


def test_correlation_stations_come_from_the_catalogue():
    from hamsci_dsp.stations import BUILTIN_CATALOG
    from station_web.routers.correlations import STATION_COORDS
    assert set(STATION_COORDS) == {"WWV", "WWVH", "BPM"}
    for name, coords in STATION_COORDS.items():
        assert coords == BUILTIN_CATALOG.get(name).coordinates


def test_correlation_chu_is_refused(client):
    assert client.get("/api/correlations/snr-solar?station=CHU&frequency=7.85").status_code == 400


def test_correlation_summary_periods_are_utc(client):
    j = client.get("/api/correlations/summary").json()
    for k in ("start", "end"):
        assert j["period"][k].endswith("Z") and "+00:00" not in j["period"][k]
    assert "CHU" not in str(j)
