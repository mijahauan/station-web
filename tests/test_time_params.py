"""Every time-taking route reads every accepted time form the same way.

The routers each parsed ``start``/``end`` themselves: ``now`` came back
naive, ISO input came back aware, and some forms (``-100000s``) were
refused by one router and accepted by the next.  A naive bound reaching
the hamsci-dsp reader raises TypeError inside ``read_time_range``; the
scintillation service caught it and answered ``count 0`` with HTTP 200
(D5 in the 2026-10-18 review).  These tests push relative, ISO-with-Z,
ISO-without-zone and mixed windows through each route and require real
rows back, so a swallowed TypeError fails here.
"""
from datetime import datetime, timedelta, timezone

import pytest

from station_web.timeutil import as_utc, iso_z, parse_utc, parse_window

NOW = datetime(2026, 10, 18, 14, 20, 0, tzinfo=timezone.utc)


# ── the helper itself ────────────────────────────────────────────────

@pytest.mark.parametrize("text,want", [
    ("now", NOW),
    ("-100000s", NOW - timedelta(seconds=100000)),
    ("-90m", NOW - timedelta(minutes=90)),
    ("-6h", NOW - timedelta(hours=6)),
    ("-1d", NOW - timedelta(days=1)),
    ("-2w", NOW - timedelta(weeks=2)),
    ("2026-10-18T12:00:00Z", datetime(2026, 10, 18, 12, tzinfo=timezone.utc)),
    ("2026-10-18T12:00:00", datetime(2026, 10, 18, 12, tzinfo=timezone.utc)),
    ("2026-10-18T07:00:00-05:00", datetime(2026, 10, 18, 12, tzinfo=timezone.utc)),
    ("2026-10-18", datetime(2026, 10, 18, tzinfo=timezone.utc)),
])
def test_parse_utc_forms(text, want):
    got = parse_utc(text, NOW)
    assert got == want
    assert got.tzinfo is not None and got.utcoffset() == timedelta(0)


@pytest.mark.parametrize("bad", ["-6x", "-h", "yesterday", "+6h", ""])
def test_parse_utc_rejects(bad):
    with pytest.raises(ValueError):
        parse_utc(bad, NOW)


def test_window_refuses_inversion():
    with pytest.raises(ValueError):
        parse_window("now", "-1h", NOW)


def test_iso_z_never_doubles_the_zone():
    assert iso_z(NOW) == "2026-10-18T14:20:00Z"
    assert iso_z(NOW.replace(tzinfo=None)) == "2026-10-18T14:20:00Z"
    assert as_utc(NOW.replace(tzinfo=None)) == NOW


# ── every time-taking route this worker owns ─────────────────────────

def _forms():
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(days=1)
    return {
        "relative": ("-100000s", "now"),
        "iso_z": (iso_z(day_ago), iso_z(now + timedelta(minutes=1))),
        "iso_naive": (day_ago.strftime("%Y-%m-%dT%H:%M:%S"),
                      (now + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S")),
        "mixed": (iso_z(day_ago), "now"),
    }


ROUTES = [
    ("/api/propagation/timeline", lambda j: j["count"] == 1),
    ("/api/propagation/tec", lambda j: j["paths"]["WWV"]["tec_tecu"] == [14.7]),
    ("/api/tec/history", lambda j: j["stations"]["WWV"]["tec_tecu"] == [14.7]),
    ("/api/tec/dtec", lambda j: j["n_series"] == 1),
    ("/api/physics/history", lambda j: j["count"] == 1),
    # The defect itself: σφ rows came back as count 0.
    ("/api/physics/scintillation/history", lambda j: j["n_sigma_phi"] == 2 and j["n_s4"] == 1),
    ("/api/physics/channels/history", lambda j: j is not None),
    ("/api/metrology/fusion/history", lambda j: j["count"] == 180),
    ("/api/tid/history", lambda j: j["n_events"] == 1),
    ("/api/space-weather/solar/elevation", lambda j: len(j["timestamps"]) > 0),
    ("/api/stability/adev", lambda j: len(j["tau_seconds"]) > 0),
]


@pytest.mark.parametrize("form", ["relative", "iso_z", "iso_naive", "mixed"])
@pytest.mark.parametrize("path,check", ROUTES, ids=[r[0] for r in ROUTES])
def test_route_reads_every_time_form(seeded_client, path, check, form):
    start, end = _forms()[form]
    r = seeded_client.get(path, params={"start": start, "end": end})
    assert r.status_code == 200, (path, form, r.status_code, r.text[:300])
    assert check(r.json()), (path, form, r.text[:400])


@pytest.mark.parametrize("path", [p for p, _ in ROUTES])
def test_route_rejects_garbage_time_with_400(seeded_client, path):
    r = seeded_client.get(path, params={"start": "-6x", "end": "now"})
    assert r.status_code == 400, (path, r.status_code, r.text[:200])


def test_stability_reads_the_shared_relative_forms(seeded_client):
    # stability kept its own parser, which knew s/m/h/d but not w.
    r = seeded_client.get("/api/stability/adev", params={"start": "-1w", "end": "now"})
    assert r.status_code == 200, r.text[:200]
