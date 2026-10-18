"""Scintillation reads the channels that exist and reports faults (D5, D11, D15).

The journal on B4 showed "L2_tick_phase read failed ...: can't compare
offset-naive and offset-aware datetimes" for every channel: the service
caught the TypeError per channel, went on with no rows, and answered
count 0 with HTTP 200.  It also opened readers for three CHU channels
that no longer exist.
"""
from datetime import datetime, timedelta, timezone

import pytest

from error_assert import assert_opaque_500
from shared_fixture import SHARED, STATIONS, shared_seeded  # noqa: F401

from station_web import projected
from station_web.services import scintillation_service
from station_web.services.scintillation_service import ScintillationService


def test_naive_bounds_still_return_rows(shared_seeded):
    """A caller that hands in naive datetimes (meaning UTC) gets rows, not
    a swallowed TypeError."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    out = ScintillationService(shared_seeded.data_root).get_history(now - timedelta(hours=2), now)
    stations = {r["station"] for r in out["measurements"] if r["source"] == "tick_phase"}
    assert stations == set(STATIONS)


def test_channels_come_from_the_products(shared_seeded):
    assert scintillation_service.tick_phase_channels() == [SHARED]
    assert scintillation_service.test_signal_channels() == [SHARED]


def test_test_signal_channels_skip_frequencies_without_wwv_or_wwvh(settings, monkeypatch):
    """Only WWV and WWVH send the minute-8/44 multitone test signal."""
    monkeypatch.setattr(scintillation_service, "discover_channels", lambda s: [
        {"name": "SHARED_10000", "frequency_hz": 10_000_000},
        {"name": "WWV_25000", "frequency_hz": 25_000_000},
        {"name": "CHU_7850", "frequency_hz": 7_850_000},
        {"name": "WWVB_60", "frequency_hz": 60_000}])
    assert scintillation_service.test_signal_channels() == ["SHARED_10000", "WWV_25000"]


@pytest.fixture
def client(shared_seeded):
    from fastapi.testclient import TestClient
    from station_web.app import create_app
    with TestClient(create_app(shared_seeded, control_socket=None)) as c:
        yield c


@pytest.mark.parametrize("path", ["/api/physics/scintillation/history?start=-2h&end=now",
                                  "/api/physics/scintillation/paths"])
def test_read_failure_is_a_500_not_count_zero(client, monkeypatch, path):
    def boom(*a, **k):
        raise TypeError("can't compare offset-naive and offset-aware datetimes")
    monkeypatch.setattr(projected, "iter_rows", boom)
    assert_opaque_500(client.get(path), "offset-naive")
