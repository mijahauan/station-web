"""Event detection: real changes on real stations, at the real location (D7, D8).

D7: ``_detect_signal_events`` walked ``L2_timing_measurements`` per channel
in time order, but a SHARED channel interleaves WWV, WWVH and BPM, so
``prev_mode`` compared different stations: 1013 "mode changes" in 6 h on
B4.  It read ``carrier_snr_db``, a column the schema lacks, so signal
loss and recovery never fired.

D8: the routers built ``EventService`` without a location (it defaulted to
Boulder), and ``date.replace(hour=int(sunset_utc))`` asked for hour 24+
at western longitudes, so sunset became None and ``is_daytime`` read
false in daylight.
"""
from datetime import datetime, timedelta, timezone

import pytest
from error_assert import assert_opaque_500
from hamsci_physics.solar_zenith import solar_position

from shared_fixture import (BPM_SWITCH_MINUTE, SHARED, WWV_FADE, minute_times,  # noqa: F401
                            shared_seeded)

from station_web.services.event_service import EventService, MODE_PERSIST_MINUTES

LAT, LON = 38.9, -92.3          # the test station (conftest settings)


@pytest.fixture
def client(shared_seeded):
    from fastapi.testclient import TestClient
    from station_web.app import create_app
    with TestClient(create_app(shared_seeded, control_socket=None)) as c:
        yield c


def _events(client, kind):
    j = client.get("/api/physics/events/recent?hours=2").json()
    return [e for e in j["events"] if e["type"] == kind]


def test_one_real_mode_change_on_a_shared_channel(client):
    """Three stations, three different modes, interleaved: only BPM's
    persistent switch is a change.  Its one-minute blip is not."""
    assert MODE_PERSIST_MINUTES <= 40 - BPM_SWITCH_MINUTE
    changes = _events(client, "mode_change")
    assert len(changes) == 1, [c["title"] for c in changes]
    c = changes[0]
    assert c["details"]["station"] == "BPM" and c["details"]["channel"] == SHARED
    assert (c["details"]["mode_before"], c["details"]["mode_after"]) == ("1F2", "2F2")


def test_signal_loss_and_recovery_fire_per_station(client):
    """WWV fades 15 dB for five minutes.  The 16 dB gap BETWEEN stations
    is not a fade, and reading the real column (snr_db) makes it visible."""
    times = minute_times()
    loss = _events(client, "signal_loss")
    rec = _events(client, "signal_recovery")
    assert [e["details"]["station"] for e in loss] == ["WWV"]
    assert [e["details"]["station"] for e in rec] == ["WWV"]
    assert loss[0]["timestamp"].startswith(times[WWV_FADE[0]].strftime("%Y-%m-%dT%H:%M"))
    assert rec[0]["timestamp"].startswith(times[WWV_FADE[-1] + 1].strftime("%Y-%m-%dT%H:%M"))


def test_events_use_discovered_channels_not_a_chu_list(shared_seeded):
    svc = EventService(shared_seeded.data_root, station_lat=LAT, station_lon=LON)
    assert svc.channels == [SHARED]
    assert not any(c.startswith("CHU") for c in svc.channels)


def test_daytime_at_a_western_station_after_00z_sunset_hour():
    """18:30 CDT on the solstice: the sun is up in Missouri, and local
    sunset (~01:40 UTC) falls on the next UTC date."""
    now = datetime(2026, 6, 21, 23, 30, tzinfo=timezone.utc)
    svc = EventService("/nonexistent", station_lat=LAT, station_lon=LON, channels=[])
    c = svc.get_current_conditions(now=now)
    assert c["is_daytime"] is True
    sunrise = datetime.fromisoformat(c["sun_times"]["sunrise"].replace("Z", "+00:00"))
    sunset = datetime.fromisoformat(c["sun_times"]["sunset"].replace("Z", "+00:00"))
    assert sunrise < now < sunset
    assert sunset.date() == (now + timedelta(days=1)).date()
    for t in (sunrise, sunset):                     # the sun is on the horizon then
        assert abs(solar_position(t, LAT, LON)[1] + 0.833) < 0.05


def test_night_is_night():
    now = datetime(2026, 6, 21, 8, 0, tzinfo=timezone.utc)      # 03:00 CDT
    svc = EventService("/nonexistent", station_lat=LAT, station_lon=LON, channels=[])
    assert svc.get_current_conditions(now=now)["is_daytime"] is False


def test_conditions_route_uses_the_configured_station(client):
    """The router passes [station] latitude/longitude, not Boulder."""
    c = client.get("/api/physics/events/conditions").json()
    now = datetime.now(timezone.utc)
    el = solar_position(now, LAT, LON)[1]
    assert c["is_daytime"] is (el > -0.833)
    sunrise = datetime.fromisoformat(c["sun_times"]["sunrise"].replace("Z", "+00:00"))
    assert abs(solar_position(sunrise, LAT, LON)[1] + 0.833) < 0.05


def test_no_location_means_unknown_daytime(settings):
    svc = EventService(settings.data_root, channels=[])
    c = svc.get_current_conditions()
    assert c["is_daytime"] is None and c["sun_times"] == {"sunrise": None, "sunset": None}


def test_event_read_failure_is_a_500(client, monkeypatch):
    from station_web.services import event_service

    def boom(*a, **k):
        raise RuntimeError("timing table unreadable")
    monkeypatch.setattr(event_service, "make_data_product_reader", boom)
    r = client.get("/api/physics/events/recent?hours=2")
    assert_opaque_500(r, "timing table unreadable")
