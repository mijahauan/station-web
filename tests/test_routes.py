"""Every kept route registers and answers on an EMPTY data root.
A products-only client must degrade to empty payloads, never 500."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

KEPT_PREFIXES = ["/api/station", "/api/stations", "/api/health", "/api/metrology",
                 "/api/stability", "/api/propagation", "/api/space-weather", "/api/correlations",
                 "/api/physics", "/api/tec", "/api/tid", "/api/dashboard", "/api/phase",
                 "/api/grape", "/api/ionogram", "/api/chrony", "/api/docs",
                 "/api/living-docs"]
CUT_PATHS = ["/api/logs/services", "/api/timing-validation/status", "/api/health/storage",
             "/api/health/grape"]


def _routes(client):
    """Every GET path the app serves.

    FastAPI 0.141 keeps an included router lazy — ``app.routes`` holds one
    opaque ``_IncludedRouter`` per ``include_router`` call and only resolves
    the paths when a request arrives.  The OpenAPI schema is the public way
    to enumerate them; the app's own routes add back the few that stay out
    of the schema (the Swagger and ReDoc pages).
    """
    paths = {p for p, ops in client.app.openapi()["paths"].items() if "get" in ops}
    paths |= {r.path for r in client.app.routes
              if hasattr(r, "methods") and "GET" in r.methods}
    return sorted(paths)


def test_kept_routers_registered(client):
    paths = _routes(client)
    for prefix in KEPT_PREFIXES:
        assert any(p.startswith(prefix) for p in paths), prefix


def test_cut_routes_are_gone(client):
    for p in CUT_PATHS:
        assert client.get(p).status_code == 404, p


def test_parameterless_gets_never_500_on_empty_root(client):
    failures = []
    for path in _routes(client):
        # "/api/docs" exactly is FastAPI's own Swagger page; the docs router
        # lives at /api/living-docs and IS swept.
        if "{" in path or path == "/api/docs":
            continue
        r = client.get(path)
        if r.status_code >= 500:
            failures.append((path, r.status_code, r.text[:200]))
    assert not failures, failures


# ---------------------------------------------------------------------------
# The other half of the sweep: given real rows, does each route render them?
#
# ``seeded_client`` (tests/conftest.py) writes one recent row of every product
# station-web reads into a temporary data root, through hamsci-dsp's own
# writer, so the schema validates every row.  Each check below names a value
# the fixture wrote, so a route that answers 200 with an empty payload fails
# here instead of passing as "no data".
# ---------------------------------------------------------------------------

TODAY = datetime.now(timezone.utc).strftime("%Y%m%d")

ROUTES_WITH_DATA = [
    # ── station identity, and the channel list discovered from
    #    L2_timing_measurements ───────────────────────────────────────────
    ("/api/station/metadata",
     lambda j: j["callsign"] == "AC0G"
     and [c["channel_name"] for c in j["channels"]] == ["WWV_20000"]),

    # ── L3_fusion_timing ───────────────────────────────────────────────
    ("/api/metrology/fusion/latest",
     lambda j: j["quality_grade"] == "A" and j["kalman_state"] == "LOCKED"
     and j["stations_used"] == ["WWV", "WWVH"] and j["d_clock_ms"] is not None),
    ("/api/metrology/fusion/history?start=-6h&end=now",
     lambda j: len(j["timestamps"]) == 180 and len(j["d_clock_ms"]) == 180),
    ("/api/stability/adev?start=-24h&end=now",
     lambda j: j["n_points"] == 180 and j["sample_interval"] == 60.0
     and len(j["tau_seconds"]) > 0),

    # ── L3_physics ─────────────────────────────────────────────────────
    ("/api/physics/latest",
     lambda j: j["utc_offset_ms"] == 0.31 and j["stations_used"] == "WWV,WWVH"),
    ("/api/physics/history?start=-6h&end=now",
     lambda j: j["count"] == 1 and j["measurements"][0]["utc_uncertainty_ms"] == 0.22),

    # ── L2_test_signal (S4) joined with L2_tick_phase (sigma_phi) ──────
    ("/api/physics/scintillation/paths",
     lambda j: j["paths"]["WWV"]["s4_mean"] == 0.27
     and j["paths"]["WWV"]["sigma_phi_mean"] > 0.0
     and j["paths"]["WWV"]["n_sigma_phi_measurements"] == 2),
    ("/api/physics/channels/latest",
     lambda j: j["latest_by_frequency"]["20.0"]["channel_quality"] == "good"
     and j["latest_by_frequency"]["20.0"]["delay_spread_ms"] == 0.42),

    # ── L3_tec ─────────────────────────────────────────────────────────
    ("/api/tec/history?start=-24h&end=now",
     lambda j: j["stations"]["WWV"]["tec_tecu"] == [14.7]
     and j["stations"]["WWV"]["confidence"] == [0.81]),
    ("/api/propagation/tec?start=-24h&end=now",
     lambda j: j["paths"]["WWV"]["mean_tec"] == 14.7
     and j["paths"]["WWV"]["quality"] == ["GOOD"]),

    # ── L3_dtec ────────────────────────────────────────────────────────
    ("/api/tec/dtec?start=-24h&end=now",
     lambda j: j["n_series"] == 1
     and j["series"]["WWV_20.0MHz"]["dtec_mean_tecu"] == [0.042]
     and j["series"]["WWV_20.0MHz"]["is_anchored"] == [True]),

    # ── L3_tid ─────────────────────────────────────────────────────────
    ("/api/tid/events?hours=24",
     lambda j: j["n_events"] == 1
     and j["events"][0]["event_id"].endswith("-0001")
     and j["events"][0]["period_minutes"] == 24.0),
    ("/api/tid/statistics",
     lambda j: j["n_events"] == 1 and j["velocity_stats"]["mean_m_s"] == 210.0
     and j["period_stats"]["mean_minutes"] == 24.0),

    # ── L3C_propagation_stats (the reanalysed MUF) over L2_timing modes ─
    ("/api/propagation/conditions",
     lambda j: j["muf_reanalyzed_mhz"] == 24.4
     and j["broadcasts"][0]["dominant_mode"] == "1F2"),

    # ── L2_tick_timing (d_clock) joined with L2_tick_phase (SNR) ───────
    ("/api/propagation/timeline?start=-24h&end=now",
     lambda j: j["count"] == 1 and j["d_clock_ms"] == [0.33]
     and j["stations"] == ["WWV"]),

    # ── L1_all_arrivals ────────────────────────────────────────────────
    ("/api/ionogram/arrivals?channel=WWV_20000&start=-24h&end=now&min_snr_db=0",
     lambda j: j["n_points"] == 2 and j["station"] == "WWV"
     and j["rank_stats"]["0"]["tof_mean_ms"] == 8.42
     and j["rank_stats"]["1"]["tof_mean_ms"] == 12.07),
    ("/api/ionogram/arrivals/timeseries?channel=WWV_20000&start=-24h&end=now&min_snr_db=0",
     lambda j: j["dominant"]["n"] == 1 and j["dominant"]["tof_ms"] == [8.42]
     and j["secondary"]["n"] == 1),

    # ── L2_tick_phase ──────────────────────────────────────────────────
    ("/api/phase/channels",
     lambda j: j["stations"] == ["WWV"]
     and j["channels"][0] == {"channel": "WWV_20000", "stations": ["WWV"],
                              "n_rows": 110, "dc_meaningful": True}),
    ("/api/phase/summary",
     lambda j: j["channels"][0]["n_points"] == 110
     and j["channels"][0]["sigma_phi_rad"] > 0.0),
    ("/api/phase/timeseries?channel=WWV_20000&start=-24h&end=now",
     lambda j: len(j["series"]) == 1 and len(j["series"][0]["epochs"]) == 110),
    ("/api/phase/scintillation?channel=WWV_20000&start=-24h&end=now",
     lambda j: j["metadata"]["n_traces"] == 1
     and all(s > 0.0 for s in j["series"][0]["sigma_phi_rad"])),

    # ── L2_timing_measurements and L2_tick_timing on the dashboard ─────
    ("/api/dashboard/timing-error/24h",
     lambda j: j["broadcasts"]["WWV_20000"]["timing_error_ms"] == [0.31]
     and j["broadcasts"]["WWV_20000"]["propagation_mode"] == ["1F2"]),
    # Two entries: the L2_timing row, then the L2_tick_timing row the
    # second pass supplements it with.
    ("/api/dashboard/broadcasts/24h",
     lambda j: j["broadcasts"]["WWV_20000"]["measurements"]["snr_db"] == [21.5, 20.4]
     and j["broadcasts"]["WWV_20000"]["measurements"]["propagation_mode"]
     == ["1F2", "TICK_FILTER"]
     and j["receiver"]["callsign"] == "AC0G"),

    # ── L1_broadcast_measurements (the dashboard's Doppler pane) ───────
    ("/api/dashboard/doppler/24h",
     lambda j: j["broadcasts"]["WWV_20000"]["doppler_hz"] == [0.08]
     and j["broadcasts"]["WWV_20000"]["requires_discrimination"] is False),

    # ── DIAG_chrony_stats ──────────────────────────────────────────────
    ("/api/chrony/history?hours=1",
     lambda j: j["n_sources"] == 2 and j["sources"]["FUSE"]["offset_ms"] == [0.12]
     and j["sources"]["FUSE"]["states"] == ["*"]),

    # ── GRAPE products on disk ─────────────────────────────────────────
    ("/api/grape/channels", lambda j: j["channels"] == ["WWV_20000"]),
    ("/api/grape/decimation",
     lambda j: j["WWV_20000"]["completeness_pct"] == 99.4
     and j["WWV_20000"]["latest_date"] == TODAY),
    ("/api/grape/spectrograms/WWV_20000", lambda j: j["dates"] == [TODAY]),

    # ── DIAG_chrony_stats: live snapshot + comparison (Task 9) ─────────
    ("/api/chrony/snapshot", lambda j: len(j["sources"]) == 2),
    ("/api/chrony/comparison", lambda j: any(s["name"] == "FUSE" for s in j["sources"])),

    # ── Space weather: read from hamsci-physics's cache (Task 10) ──────
    ("/api/space-weather/current", lambda j: j.get("f107") == 142.0),

    # ── Health as product freshness (Task 11) ──────────────────────────
    ("/api/health/system",
     lambda j: j["products"]["L3_fusion_timing"]["verdict"] == "VALID"
     and j["products"]["DIAG_chrony_stats"]["verdict"] == "VALID"
     and j["products"]["L3_gnss_vtec"]["verdict"] == "INDETERMINATE"
     and j["status"] in {"healthy", "degraded"}),
    ("/api/health/channels",
     lambda j: "WWV_20000" in [c["name"] for c in j["channels"]]),
]


@pytest.mark.parametrize("path,check", ROUTES_WITH_DATA)
def test_route_with_data(seeded_client, path, check):
    r = seeded_client.get(path)
    assert r.status_code == 200, (path, r.status_code, r.text[:300])
    assert check(r.json()), (path, r.text[:400])


def test_spectrogram_png_is_served(seeded_client):
    """The one route that answers with bytes rather than JSON."""
    r = seeded_client.get(f"/api/grape/spectrograms/WWV_20000/{TODAY}")
    assert r.status_code == 200, r.text[:300]
    assert r.headers["content-type"] == "image/png"
    assert r.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_seeding_never_touches_the_real_data_root(seeded):
    """Every path the fixture wrote lives under pytest's tmp_path."""
    assert not str(seeded.data_root).startswith("/var/lib/timestd")
    assert Path(seeded.storage["sqlite_path"]).is_relative_to(seeded.data_root)
