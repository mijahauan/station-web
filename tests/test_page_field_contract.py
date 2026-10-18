"""Page <-> API field contract.

``test_routes.py`` checks each route against the service's own shape, which
let the pages drift: chrony.html read fields its endpoint never sent, and
index.html filtered channel names on a ``.status`` they do not have.  Here
each page lists the fields it reads from each endpoint.  The test checks two
things per field: the page's source mentions it (so the list stays honest),
and the endpoint's JSON on the seeded fixture carries it.

Path syntax: ``a.b`` walks dicts, ``a[]`` steps into every list item,
``a{}`` into every dict value.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from s2d_fixture import s2d_products  # noqa: F401
from timing_fixture import authority_files  # noqa: F401

STATIC = Path(__file__).resolve().parents[1] / "src" / "station_web" / "static"

CONTRACT = [
    ("index.html", "/api/station/metadata",
     ["callsign", "grid_square", "station_id", "instrument_id", "latitude", "longitude",
      "mode", "description", "channels"]),
    ("index.html", "/api/health/system",
     ["status", "timestamp", "channels", "products{}.verdict", "products{}.applicable",
      "products{}.age_s", "products{}.expected_s"]),     # ``reason``: only when not VALID
    ("index.html", "/api/stations/",
     ["stations[].station_id", "stations[].location", "stations[].distance_km",
      "stations[].azimuth_deg"]),
    ("index.html", "/api/timing/alarms?n=5",
     ["available", "watchdog.verdict", "watchdog.severity", "watchdog.ts", "watchdog.channel",
      "watchdog.detail", "incidents[].ts", "incidents[].verdict", "incidents[].severity",
      "incidents[].channel", "incidents[].delta_sec", "incidents[].detail"]),
    # the registration panel lives in common.js and serves every page that shows it
    ("js/common.js", "/api/timing/registration",
     ["available", "stale", "age_s", "utc_published", "tier_active", "tiers_available",
      "witnesses", "ruler.a_level", "ruler.disciplined", "ruler.provenance", "sigma.value",
      "sigma.unit", "sigma.field", "u_epoch", "stability", "registration.state",
      "registration.source", "registration.age_s", "registration.counter_epoch_id",
      "registration.sigma_ms", "registration.contributing", "registration.stations",
      "t6.authority_state", "t6.violations", "t6.suspect_criteria", "host_clock.verdict",
      "host_clock.reason", "host_clock.witnesses[].name", "host_clock.witnesses[].value",
      "host_clock.witnesses[].unit", "host_clock.witnesses[].bound",
      "host_clock.witnesses[].exceeded", "disagreement_flags", "last_transition_utc",
      "governor_radiod", "counter_epoch_changes_24h.count",
      "counter_epoch_changes_24h.reason"]),
    ("health.html", "/api/health/system",
     ["status", "timestamp", "channels", "products{}.verdict", "products{}.age_s",
      "products{}.expected_s", "products{}.channel"]),
    # the Timing page (metrology.html) draws everything from js/timing.js;
    # chrony folded into it, so chrony.html reads nothing now
    ("js/timing.js", "/api/timing/registration/history?start=-24h&end=now",
     ["available", "time", "tier_active", "sigma_ns", "estimators{}.offset_ms",
      "estimators{}.sigma_ms", "transitions[].time", "transitions[].from", "transitions[].to",
      "transitions_truncated", "n_rows", "n_points", "bucket_s"]),
    ("js/timing.js", "/api/metrology/t3/history?start=-24h&end=now",
     ["available", "time", "calibrated_ms", "raw_ms", "sigma_ms", "suspects.time",
      "suspects.value_ms", "suspects.residual_ms", "suspects.kind", "n_suspect",
      "suspects_truncated", "n_rows", "calibration.offset_ms", "calibration.min_ms",
      "calibration.max_ms", "calibration.n"]),
    ("js/timing.js", "/api/metrology/fusion/latest",
     ["d_clock_ms", "d_clock_raw_ms", "uncertainty_ms", "n_broadcasts", "stations_used",
      "timestamp"]),
    ("js/timing.js", "/api/stability/adev?start=-24h&end=now",
     ["tau_seconds", "adev", "dominant_noise", "adev_60s", "adev_1000s", "n_points"]),
    ("js/timing.js", "/api/chrony/comparison",
     ["sources[].name", "sources[].mode", "sources[].state", "sources[].stratum",
      "sources[].offset_us", "sources[].error_us", "sources[].std_dev_us",
      "sources[].frequency_ppm", "sources[].freq_skew_ppm", "sources[].n_samples",
      "sources[].reach", "tracking.sys_offset_us", "tracking.rms_offset_us",
      "tracking.reference_id", "age_s", "stale", "timestamp_utc"]),
    ("js/timing.js", "/api/chrony/envelope?start=-24h&end=now",
     ["available", "time", "ntp.min_ms", "ntp.median_ms", "ntp.max_ms", "ntp.n_sources",
      "ntp.members", "witnesses{}.offset_ms", "witnesses{}.role", "other_refclocks",
      "system_offset_ms"]),
    ("grape.html", "/api/grape/summary",
     ["channels", "channel_list", "decimation{}.dates_available",
      "decimation{}.latest_date", "decimation{}.completeness_pct",
      "decimation{}.valid_minutes", "pipeline", "uploads.packaged_dates"]),
    ("grape.html", "/api/grape/spectrograms/WWV_20000", ["dates"]),
    # Propagation page: time of flight (js/tof-view.js, once ionogram.html)
    ("js/tof-view.js", "/api/ionogram/channels",
     ["channels[].channel", "channels[].stations", "channels[].latest"]),
    # one combined call serves all three views (final review B1)
    ("js/tof-view.js", "/api/ionogram/combined?channel=WWV_20000&start=-1h&end=now&min_snr_db=0",
     ["timeseries.status", "timeseries.color", "timeseries.dominant.timestamps",
      "timeseries.dominant.tof_ms", "timeseries.dominant.snr_db", "timeseries.dominant.n",
      "timeseries.secondary.n", "timeseries.solar", "arrivals.status", "arrivals.n_points",
      "arrivals.rank_stats", "arrivals.scatter.peak_rank", "arrivals.scatter.tof_ms",
      "arrivals.scatter.corr_snr_db", "arrivals.kde", "arrivals.station",
      # Doppler (Hz) against delay (ms), from phase_service's estimator
      "doppler_delay.status", "doppler_delay.doppler_reason", "doppler_delay.doppler_label",
      "doppler_delay.doppler_composite", "doppler_delay.doppler_timeseries.minute_boundary",
      "doppler_delay.doppler_timeseries.doppler_hz", "doppler_delay.scatter.delay_ms",
      "doppler_delay.scatter.doppler_hz", "doppler_delay.scatter.station",
      "doppler_delay.scatter.minute_boundary", "doppler_delay.scatter.corr_snr_db",
      "doppler_delay.kde", "doppler_delay.summary.delay_std_ms",
      "doppler_delay.summary.doppler_std_hz", "doppler_delay.summary.doppler_median_hz",
      "doppler_delay.n_points", "doppler_delay.n_minutes_delay"]),
    # the ruler floor beside every Doppler chart reads the registration
    ("js/physics-views.js", "/api/timing/registration", ["available", "ruler.a_level"]),
    # Propagation page: signal, modes, hourly stats, the funnel, the path map
    ("js/propagation.js", "/api/propagation/timeline?start=-6h&end=now",
     ["timestamps", "modes", "stations", "frequencies", "snr_db", "d_clock_ms", "count"]),
    ("js/propagation.js", "/api/dashboard/solar-zenith/24h?interval_minutes=10",
     ["timestamps", "paths{}.station", "paths{}.elevation_deg"]),
    ("js/propagation.js", "/api/stations/",
     ["stations[].station_id", "stations[].coordinates", "stations[].location",
      "stations[].distance_km", "stations[].azimuth_deg"]),
    ("js/propagation.js", "/api/station/metadata",
     ["latitude", "longitude", "callsign", "grid_square"]),
    ("js/propagation.js", "/api/propagation/stats?start=-24h&end=now",
     ["available", "modes_order", "stations{}.time", "stations{}.muf_mhz",
      "stations{}.muf_confidence", "stations{}.n_observations", "stations{}.mode_probability"]),
    ("js/propagation.js", "/api/propagation/detections?start=-6h&end=now",
     ["available", "reasons_order", "broadcasts[].channel", "broadcasts[].station",
      "broadcasts[].frequency_mhz", "broadcasts[].attempts", "broadcasts[].detected",
      "broadcasts[].detection_rate", "broadcasts[].reasons"]),
    # Ionosphere page: dTEC (js/dtec-view.js, once dtec.html)
    ("js/dtec-view.js", "/api/tec/dtec?start=-1h&end=now&min_snr_db=0",
     ["status", "series", "n_series", "n_points_total", "time_range",
      "series{}.timestamps", "series{}.dtec_rate_tecu_per_s", "series{}.label",
      "series{}.composite", "series{}.stations_heard", "series{}.frequency_mhz",
      "series{}.is_anchored", "noise_floor.value", "noise_floor.unit", "noise_floor.reason",
      "noise_floor.n_series", "reference_comparison.available",
      "reference_comparison.keys", "reference_comparison.reason"]),
    ("js/dtec-view.js", "/api/stations/", ["stations[].station_id", "stations[].azimuth_deg"]),
    ("js/dtec-view.js", "/api/space-weather/solar/elevation?start=-6h&end=now&interval_minutes=5",
     ["timestamps", "elevations"]),
    ("js/dtec-view.js", "/api/space-weather/xray?hours=24", []),   # no series product: nothing to read
    ("js/dtec-view.js", "/api/tid/history?start=-6h&end=now",
     ["events[].timestamp_utc", "events[].velocity_m_s", "events[].direction_deg"]),
    # Ionosphere page: Doppler and phase (js/phase-view.js, once phase.html)
    ("js/phase-view.js", "/api/phase/combined?start=-1h&end=now",
     ["phase.series[].timestamps", "phase.series[].phase_rad", "phase.series[].channel",
      "phase.series[].station", "phase.series[].label", "phase.series[].stations_heard",
      "phase.series[].color", "dc_phase.series",
      "doppler.series[].doppler_hz", "doppler.series[].channel", "doppler.series[].label",
      "scintillation.series[].sigma_phi_rad",
      "scintillation.series[].s4", "metadata.bucket_seconds"]),
    ("js/phase-view.js", "/api/phase/summary",
     ["channels[].channel", "channels[].label", "channels[].color",
      "channels[].dc_meaningful", "channels[].doppler_hz", "channels[].sigma_phi_rad",
      "channels[].mean_snr_db", "channels[].n_points", "channels[].last_update",
      "window_minutes"]),
    ("js/phase-view.js", "/api/phase/timeseries?start=-1h&end=now&unwrap=true",
     ["series[].timestamps", "series[].phase_rad", "series[].channel", "series[].color",
      "metadata.bucket_seconds"]),
    ("js/phase-view.js", "/api/dashboard/solar-zenith/24h?interval_minutes=10",
     ["timestamps", "paths{}.station", "paths{}.elevation_deg"]),
    ("js/phase-view.js", "/api/phase/channels",
     ["channels[].channel", "channels[].stations", "channels[].dc_meaningful"]),
    # Ionosphere page: space weather, events, TEC with GNSS VTEC as its reference
    ("js/ionosphere.js", "/api/space-weather/current",
     ["available", "kp", "kp_time", "kp_source", "f107", "f107_time", "f107_source",
      "ap", "ap_time", "ap_source", "stale", "age_s"]),
    ("js/ionosphere.js", "/api/space-weather/xray?hours=24", ["available"]),
    ("js/ionosphere.js", "/api/space-weather/kp?hours=72", ["available"]),
    ("js/ionosphere.js", "/api/physics/events/recent?hours=24",
     ["events", "events[].timestamp", "events[].type", "events[].title"]),
    ("js/ionosphere.js", "/api/propagation/timeline?start=-6h&end=now",
     ["timestamps", "modes", "stations", "frequencies", "snr_db", "d_clock_ms"]),
    ("js/ionosphere.js", "/api/propagation/tec?start=-24h&end=now",
     ["paths{}.timestamps", "paths{}.tec_tecu", "paths{}.residual_rms_ms"]),
    ("js/ionosphere.js", "/api/tec/gnss-vtec?start=-6h&end=now",
     ["available", "label", "time", "vtec_tecu", "n_points", "n_rows", "bucket_s",
      "n_excluded_bad"]),
    # Timing page: WWVB, which minute it named
    ("js/timing.js", "/api/timing/wwvb",
     ["available", "file", "role", "age_s", "passes.n", "passes.with_frames",
      "passes.last.snr_db", "passes.last.ts", "frames.n", "frames.plausible",
      "frames.last_plausible.minute", "frames.last_plausible.vs_wallclock_s",
      "recent_frames[].ts", "recent_frames[].minute", "recent_frames[].plausible",
      "recent_frames[].vs_wallclock_s", "recent_frames[].parity_errors",
      "recent_frames[].sync_errors"]),
    ("test_signal.html", "/api/physics/channels/daily",
     ["by_station", "frequencies", "solar.timestamps"]),
    ("test_signal.html", "/api/physics/channels/latest",
     ["latest_by_frequency{}.snr_db", "latest_by_frequency{}.delay_spread_ms",
      "latest_by_frequency{}.coherence_time_sec", "latest_by_frequency{}.channel_quality"]),
    ("test_signal.html", "/api/propagation/tec?start=-24h&end=now",
     ["paths{}.timestamps", "paths{}.tec_tecu"]),
    ("docs.html", "/api/living-docs/list",
     ["roots[].name", "roots[].present", "roots[].documents"]),
]


def _resolve(obj, path):
    """Yield every value ``path`` reaches in ``obj``; raise KeyError if any step misses."""
    head, _, rest = path.partition(".")
    m = re.fullmatch(r"(\w+)(\[\]|\{\})?", head)
    key, step = m.group(1), m.group(2)
    if not isinstance(obj, dict) or key not in obj:
        raise KeyError(f"{key!r} missing")
    val = obj[key]
    if step == "[]":
        items = list(val)
    elif step == "{}":
        items = list(val.values())
    else:
        items = [val]
    if not rest:
        return items
    if not items:
        raise KeyError(f"{key} is empty, cannot check {rest}")
    out = []
    for it in items:
        out.extend(_resolve(it, rest))
    return out


#: endpoints that read the timing authority's files, not the product database
TIMING_URLS = ("/api/timing/",)
#: endpoints whose products only the S2D fixture writes
S2D_URLS = ("/api/propagation/detections", "/api/tec/gnss-vtec", "/api/timing/wwvb")


@pytest.mark.parametrize("page,url,fields", CONTRACT,
                         ids=[f"{p}:{u.split('?')[0]}" for p, u, _ in CONTRACT])
def test_page_fields_exist_in_api(seeded_client, request, page, url, fields):
    if url.startswith(TIMING_URLS):
        request.getfixturevalue("authority_files")
    if url.startswith(S2D_URLS):
        request.getfixturevalue("s2d_products")
    text = (STATIC / page).read_text()
    r = seeded_client.get(url)
    assert r.status_code == 200, (url, r.text[:200])
    j = r.json()
    for path in fields:
        leaf = path.split(".")[-1].rstrip("[]{}")
        assert re.search(r"[.'\"\[]" + re.escape(leaf) + r"\b", text), \
            f"{page} no longer reads {leaf!r}; update the contract"
        try:
            _resolve(j, path)
        except KeyError as e:
            pytest.fail(f"{page} reads {path} but {url} lacks it ({e}): {json.dumps(j)[:300]}")


#: The scripts the consolidated pages run (S2D): every endpoint each one
#: reads must carry a contract entry above.
CONSOLIDATED = ("js/propagation.js", "js/tof-view.js", "js/ionosphere.js", "js/phase-view.js",
                "js/dtec-view.js", "js/timing.js")
_CALL = re.compile(r"""(?:api\.get\(\s*['`](/[\w/\-]+)|getJSON\(`/api(/[\w/\-]+))""")


@pytest.mark.parametrize("script", CONSOLIDATED)
def test_every_endpoint_a_page_reads_has_a_contract_entry(script):
    called = {a or b for a, b in _CALL.findall((STATIC / script).read_text())}
    listed = {u.split("?")[0].removeprefix("/api") for p, u, _ in CONTRACT if p == script}
    listed |= {u.split("?")[0].removeprefix("/api") for p, u, _ in CONTRACT if p == "js/common.js"}
    assert called, script
    assert called <= listed, sorted(called - listed)
