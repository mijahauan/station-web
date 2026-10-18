"""The phase router reads time the way every other router does (final review S8, S4).

It kept its own parser: a zone-less ISO time reached the service naive, and
``.timestamp()`` read it as HOST-local time; ``-2w`` was refused; and
``-999999999d`` raised OverflowError into a 500.  Its ``except ValueError``
also wrapped the service call, so a fault inside the service (numpy's
LinAlgError is a ValueError) answered 400.
"""
import time

import pytest

from error_assert import assert_opaque_500
from station_web.services import phase_service


@pytest.fixture(autouse=True)
def _fresh():
    phase_service.clear_cache()
    yield
    phase_service.clear_cache()


def test_zone_less_time_means_utc_not_host_local(seeded_client, monkeypatch):
    monkeypatch.setenv("TZ", "America/Chicago")
    time.tzset()
    try:
        naive = seeded_client.get("/api/phase/combined?start=2026-10-17T00:00:00"
                                  "&end=2026-10-17T06:00:00").json()
        zoned = seeded_client.get("/api/phase/combined?start=2026-10-17T00:00:00Z"
                                  "&end=2026-10-17T06:00:00Z").json()
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()
    assert naive["metadata"]["start"] == zoned["metadata"]["start"]
    assert naive["metadata"]["start"].endswith("+00:00")


def test_weeks_are_accepted(seeded_client):
    assert seeded_client.get("/api/phase/combined?start=-1w&end=now").status_code == 200


@pytest.mark.parametrize("start", ["-999999999d", "-6x", "yesterday"])
def test_unreadable_times_are_400(seeded_client, start):
    r = seeded_client.get(f"/api/phase/combined?start={start}&end=now")
    assert r.status_code == 400
    assert start not in r.text


def test_a_service_fault_is_an_opaque_500_not_a_400(seeded_client, monkeypatch):
    import numpy as np

    def boom(*a, **k):
        raise np.linalg.LinAlgError("SVD did not converge at /var/lib/secret")
    monkeypatch.setattr(phase_service.PhaseService, "_read_and_aggregate", boom)
    for path in ("timeseries", "doppler", "scintillation", "combined"):
        r = seeded_client.get(f"/api/phase/{path}?start=-1h&end=now")
        assert_opaque_500(r, "/var/lib/secret")
