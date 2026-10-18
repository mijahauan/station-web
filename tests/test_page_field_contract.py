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

STATIC = Path(__file__).resolve().parents[1] / "src" / "station_web" / "static"

CONTRACT = [
    ("index.html", "/api/station/metadata",
     ["callsign", "grid_square", "station_id", "instrument_id", "latitude", "longitude",
      "mode", "description", "channels"]),
    ("index.html", "/api/health/system",
     ["status", "channels", "products{}.verdict", "products{}.applicable"]),
    ("index.html", "/api/stations/",
     ["stations[].station_id", "stations[].location", "stations[].distance_km",
      "stations[].azimuth_deg"]),
    ("index.html", "/api/metrology/fusion/latest",
     ["d_clock_ms", "quality_grade", "uncertainty_ms"]),
    ("index.html", "/api/metrology/fusion/history?start=-1h&end=now",
     ["count", "timestamps", "d_clock_ms", "quality_grade", "n_broadcasts"]),
    ("health.html", "/api/health/system",
     ["status", "timestamp", "channels", "products{}.verdict", "products{}.age_s",
      "products{}.expected_s", "products{}.channel"]),
    ("chrony.html", "/api/chrony/comparison",
     ["sources[].name", "sources[].mode", "sources[].state", "sources[].stratum",
      "sources[].offset_us", "sources[].error_us", "sources[].std_dev_us",
      "sources[].frequency_ppm", "sources[].freq_skew_ppm", "sources[].n_samples",
      "sources[].reach", "tracking.sys_offset_us", "tracking.rms_offset_us",
      "tracking.reference_id", "age_s", "stale", "timestamp_utc"]),
    ("chrony.html", "/api/chrony/history",
     ["sources{}.timestamps", "sources{}.offset_ms", "sources{}.std_dev_ms",
      "system[].unix_time", "system[].sys_offset_us"]),
    ("grape.html", "/api/grape/summary",
     ["channels", "channel_list", "decimation{}.dates_available",
      "decimation{}.latest_date", "decimation{}.completeness_pct",
      "decimation{}.valid_minutes", "pipeline", "uploads.packaged_dates"]),
    ("grape.html", "/api/grape/spectrograms/WWV_20000", ["dates"]),
    ("ionogram.html", "/api/ionogram/channels",
     ["channels[].channel", "channels[].stations", "channels[].latest"]),
    # one combined call serves all three views (final review B1)
    ("ionogram.html", "/api/ionogram/combined?channel=WWV_20000&start=-1h&end=now&min_snr_db=0",
     ["timeseries.status", "timeseries.color", "timeseries.dominant.timestamps",
      "timeseries.dominant.tof_ms", "timeseries.dominant.snr_db", "timeseries.dominant.n",
      "timeseries.secondary.n", "timeseries.f2_height.n", "timeseries.f2_height.height_km",
      "timeseries.solar", "arrivals.status", "arrivals.n_points", "arrivals.rank_stats",
      "arrivals.scatter.peak_rank", "arrivals.scatter.tof_ms", "arrivals.scatter.corr_snr_db",
      "arrivals.kde", "arrivals.station", "doppler_delay.status"]),
    ("station.html", "/api/stations/WWV",
     ["info.name", "info.location", "info.coordinates", "info.frequencies", "status",
      "fusion", "ionosphere"]),
    ("dtec.html", "/api/tec/dtec?start=-1h&end=now&min_snr_db=0",
     ["status", "series", "n_series", "n_points_total", "time_range"]),
    # pages the first pass skipped (final review N7)
    ("phase.html", "/api/phase/combined?start=-1h&end=now",
     ["phase.series[].timestamps", "phase.series[].phase_rad", "phase.series[].channel",
      "phase.series[].station", "phase.series[].color", "dc_phase.series",
      "doppler.series[].doppler_hz", "scintillation.series[].sigma_phi_rad",
      "scintillation.series[].s4", "metadata.bucket_seconds"]),
    ("phase.html", "/api/phase/summary",
     ["channels[].channel", "channels[].station", "channels[].color",
      "channels[].dc_meaningful", "channels[].doppler_hz", "channels[].sigma_phi_rad",
      "channels[].mean_snr_db", "channels[].n_points", "channels[].last_update"]),
    ("phase.html", "/api/phase/channels",
     ["channels[].channel", "channels[].stations", "channels[].dc_meaningful"]),
    ("ionosphere.html", "/api/propagation/timeline?start=-6h&end=now",
     ["timestamps", "modes", "stations", "frequencies", "snr_db", "d_clock_ms", "doppler_hz"]),
    ("ionosphere.html", "/api/propagation/tec?start=-24h&end=now",
     ["paths{}.timestamps", "paths{}.tec_tecu"]),
    ("ionosphere.html", "/api/dashboard/solar-zenith/24h?interval_minutes=10",
     ["timestamps", "paths{}.station", "paths{}.elevation_deg"]),
    ("ionosphere.html", "/api/stations/",
     ["stations[].station_id", "stations[].coordinates"]),
    ("test_signal.html", "/api/physics/channels/daily",
     ["by_station", "frequencies", "solar.timestamps"]),
    ("test_signal.html", "/api/physics/channels/latest",
     ["latest_by_frequency{}.snr_db", "latest_by_frequency{}.delay_spread_ms",
      "latest_by_frequency{}.coherence_time_sec", "latest_by_frequency{}.channel_quality"]),
    ("test_signal.html", "/api/propagation/tec?start=-24h&end=now",
     ["paths{}.timestamps", "paths{}.tec_tecu"]),
    ("metrology.html", "/api/metrology/fusion/latest",
     ["d_clock_ms", "uncertainty_ms", "quality_grade", "n_broadcasts"]),
    ("metrology.html", "/api/metrology/fusion/history?start=-24h&end=now",
     ["timestamps", "d_clock_ms", "uncertainty_ms", "quality_grade"]),
    ("metrology.html", "/api/stability/adev?start=-24h&end=now",
     ["tau_seconds", "adev", "dominant_noise", "adev_60s", "adev_1000s", "n_points"]),
    ("station.html", "/api/stations/WWV/history?hours=24",
     ["timeline.timestamps", "timeline.snr_db", "tec", "solar.timestamps",
      "solar.elevation_deg"]),
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


@pytest.mark.parametrize("page,url,fields", CONTRACT,
                         ids=[f"{p}:{u.split('?')[0]}" for p, u, _ in CONTRACT])
def test_page_fields_exist_in_api(seeded_client, page, url, fields):
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
