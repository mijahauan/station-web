"""Every reader-backed route caps its window, and refuses a longer one with 400.

The 2026-10-18 final review (S1, S2) found routes that read a whole window of
full rows with no ceiling on its length: one ``start=-1000d`` read a product
table end to end.  ``parse_window`` now takes ``max_span``, and each route
names a ceiling that fits what its pages ask for.
"""
from datetime import datetime, timedelta, timezone

import pytest

from station_web.timeutil import parse_utc, parse_window

NOW = datetime(2026, 10, 18, 14, 20, 0, tzinfo=timezone.utc)


def test_parse_window_refuses_a_span_past_the_cap():
    with pytest.raises(ValueError) as e:
        parse_window("-25h", "now", NOW, max_span=timedelta(hours=24))
    assert "-25h" not in str(e.value)       # fixed text, never the client's input


def test_parse_window_accepts_exactly_the_cap():
    t0, t1 = parse_window("-24h", "now", NOW, max_span=timedelta(hours=24))
    assert t1 - t0 == timedelta(hours=24)


@pytest.mark.parametrize("bad", ["-999999999d", "-99999999999999999999w"])
def test_huge_relative_values_are_a_400_not_a_500(bad):
    with pytest.raises(ValueError):
        parse_utc(bad, NOW)


# (route, longest window it serves)
CAPS = [
    ("/api/ionogram/arrivals", timedelta(hours=24)),
    ("/api/ionogram/arrivals/timeseries", timedelta(hours=24)),
    ("/api/ionogram/doppler-delay", timedelta(hours=24)),
    ("/api/ionogram/combined", timedelta(hours=24)),
    ("/api/physics/scintillation/history", timedelta(hours=6)),
    ("/api/metrology/fusion/history", timedelta(days=7)),
    ("/api/stability/adev", timedelta(days=366)),
    ("/api/physics/history", timedelta(days=7)),
    ("/api/physics/channels/history", timedelta(days=7)),
    ("/api/tec/dtec", timedelta(days=2)),
    ("/api/tec/history", timedelta(days=31)),
    ("/api/tid/history", timedelta(days=31)),
    ("/api/propagation/tec", timedelta(days=7)),
    ("/api/propagation/test-signals", timedelta(days=7)),
]


@pytest.mark.parametrize("path,cap", CAPS, ids=[c[0] for c in CAPS])
def test_route_refuses_a_window_past_its_cap(seeded_client, path, cap):
    over = int(cap.total_seconds()) + 3600
    r = seeded_client.get(path, params={"start": f"-{over}s", "end": "now"})
    assert r.status_code == 400, (path, r.status_code, r.text[:200])


@pytest.mark.parametrize("path,cap", CAPS, ids=[c[0] for c in CAPS])
def test_route_serves_a_window_at_its_cap(seeded_client, path, cap):
    r = seeded_client.get(path, params={"start": f"-{int(cap.total_seconds())}s", "end": "now"})
    assert r.status_code in (200, 404), (path, r.status_code, r.text[:200])


# N4: a 400 never echoes the client's input, on any router.
@pytest.mark.parametrize("path", [p for p, _ in CAPS] + ["/api/propagation/timeline"])
def test_bad_time_400_does_not_echo_input(seeded_client, path):
    r = seeded_client.get(path, params={"start": "zz-marker", "end": "now"})
    assert r.status_code == 400, (path, r.status_code)
    assert "zz-marker" not in r.text, r.text


# N6: a malformed date on the dashboard is the client's error, not a fault.
@pytest.mark.parametrize("date", ["2026xx01", "garbage", "20261399"])
def test_dashboard_bad_date_is_400(seeded_client, date):
    r = seeded_client.get("/api/dashboard/broadcasts/24h", params={"date": date})
    assert r.status_code == 400, (r.status_code, r.text[:200])
    assert date not in r.text
