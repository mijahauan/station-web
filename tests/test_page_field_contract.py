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
    ("ionogram.html", "/api/ionogram/arrivals/timeseries?channel=WWV_20000&start=-1h&end=now&min_snr_db=0",
     ["status", "color", "dominant.timestamps", "dominant.tof_ms", "dominant.snr_db",
      "dominant.n", "secondary.n", "f2_height.n", "f2_height.height_km", "solar"]),
    ("ionogram.html", "/api/ionogram/arrivals?channel=WWV_20000&start=-1h&end=now&min_snr_db=0",
     ["status", "n_points", "rank_stats", "scatter.peak_rank", "scatter.tof_ms",
      "scatter.corr_snr_db", "kde", "station"]),
    ("station.html", "/api/stations/WWV",
     ["info.name", "info.location", "info.coordinates", "info.frequencies", "status",
      "fusion", "ionosphere"]),
    ("dtec.html", "/api/tec/dtec?start=-1h&end=now&min_snr_db=0",
     ["status", "series", "n_series", "n_points_total", "time_range"]),
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
