"""No route sends exception text to a client (final review S5, S4).

chrony.py and station.py answered 500 with ``detail=str(e)``; a sqlite
error carries the database path and sometimes SQL.  Several routers wrapped
their service calls in ``except ValueError -> 400 str(e)``, so a fault in
the service answered "bad request" with its own message.
"""
import re
from pathlib import Path

import numpy as np
import pytest

from error_assert import assert_opaque_500

ROUTERS = Path(__file__).resolve().parents[1] / "src" / "station_web" / "routers"
SECRET = "/var/lib/timestd/secret.db"


def test_no_router_puts_exception_text_in_a_500():
    for f in ROUTERS.glob("*.py"):
        text = f.read_text()
        for m in re.finditer(r"HTTPException\(\s*status_code=5\d\d,[^)]*\)", text, re.S):
            assert "str(e)" not in m.group(0) and "{e}" not in m.group(0), (f.name, m.group(0))


def _boom(*a, **k):
    raise RuntimeError(f"unable to open database file {SECRET}")


def _value_boom(*a, **k):
    raise np.linalg.LinAlgError(f"SVD did not converge {SECRET}")


CHRONY = [
    ("get_live_snapshot", "/api/chrony/snapshot"),
    ("get_source_comparison", "/api/chrony/comparison"),
    ("get_history", "/api/chrony/history"),
]


@pytest.mark.parametrize("method,url", CHRONY, ids=[u for _, u in CHRONY])
def test_chrony_faults_are_opaque(seeded_client, monkeypatch, method, url):
    from station_web.services import chrony_service
    monkeypatch.setattr(chrony_service.ChronyService, method, _boom)
    assert_opaque_500(seeded_client.get(url), SECRET)


def test_chrony_bad_time_does_not_echo(seeded_client):
    r = seeded_client.get("/api/chrony/history?start=zz-marker")
    assert r.status_code == 400 and "zz-marker" not in r.text


def test_station_metadata_fault_is_opaque(seeded_client, monkeypatch):
    from station_web.routers import station
    monkeypatch.setattr(station, "discover_channel_infos", _boom)
    assert_opaque_500(seeded_client.get("/api/station/metadata"), SECRET)


# (module, attribute, url): a ValueError inside the service is a fault, a 500.
SERVICE_VALUE_ERRORS = [
    ("station_web.services.fusion_service", "FusionService.get_history",
     "/api/metrology/fusion/history?start=-1h&end=now"),
    ("station_web.services.stability_service", "StabilityService.compute_stability_metrics",
     "/api/stability/adev?start=-24h&end=now"),
    ("station_web.services.physics_service", "PhysicsService.get_history",
     "/api/physics/history?start=-1h&end=now"),
    ("station_web.services.scintillation_service", "ScintillationService.get_history",
     "/api/physics/scintillation/history?start=-1h&end=now"),
    ("station_web.services.test_signal_service", "TestSignalService.get_history",
     "/api/physics/channels/history?start=-1h&end=now"),
    ("station_web.services.test_signal_service", "TestSignalService.get_daily_comparison",
     "/api/physics/channels/daily"),
    ("station_web.routers.ionogram", "_load_all_arrivals",
     "/api/ionogram/combined?channel=WWV_20000&start=-1h&end=now"),
    ("station_web.routers.ionogram", "_load_all_arrivals",
     "/api/ionogram/arrivals?channel=WWV_20000&start=-1h&end=now"),
    ("station_web.routers.ionogram", "_load_all_arrivals",
     "/api/ionogram/arrivals/timeseries?channel=WWV_20000&start=-1h&end=now"),
    ("station_web.routers.ionogram", "_load_all_arrivals",
     "/api/ionogram/doppler-delay?channel=WWV_20000&start=-1h&end=now"),
]


@pytest.mark.parametrize("module,attr,url", SERVICE_VALUE_ERRORS,
                         ids=[u.split("?")[0] for _, _, u in SERVICE_VALUE_ERRORS])
def test_a_value_error_in_a_service_is_an_opaque_500(seeded_client, monkeypatch, module, attr, url):
    import importlib
    mod = importlib.import_module(module)
    owner_name, _, name = attr.rpartition(".")
    owner = getattr(mod, owner_name) if owner_name else mod
    monkeypatch.setattr(owner, name, _value_boom)
    assert_opaque_500(seeded_client.get(url), SECRET)
