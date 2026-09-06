"""Health = are the products fresh?  Four verdicts, borrowed from the
heartbeat schema: VALID (fresh), INVALID (present but older than expected),
INDETERMINATE (no rows at all).  No process or disk probes: sigmond owns those."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from hamsci_dsp.io import make_data_product_reader

from station_web.config import Settings
from station_web.services.channels import discover_channels

logger = logging.getLogger(__name__)

#: (level, name) -> expected max age in seconds
EXPECTED = {
    ("L2", "timing_measurements"): 120, ("L3", "fusion_timing"): 120, ("DIAG", "chrony_stats"): 120,
    ("L3", "tec"): 3600, ("L3", "dtec"): 300, ("L3", "tid"): 3600, ("L3", "physics"): 120,
    ("L3", "gnss_vtec"): 900,
}
#: products written under a fixed channel rather than per receiver channel
#: (matches tests/conftest.py's ``seeded`` fixture — the only ground truth
#: for which channel each writer actually uses).
FIXED_CHANNEL = {("L3", "fusion_timing"): "fusion", ("DIAG", "chrony_stats"): "fusion",
                 ("L3", "physics"): "global", ("L3", "tec"): "AGGREGATED",
                 ("L3", "dtec"): "AGGREGATED", ("L3", "tid"): "AGGREGATED"}


def _newest_age(settings: Settings, level: str, name: str, channel: str, lookback_s: int) -> float | None:
    now = datetime.now(timezone.utc)
    start = (now - timedelta(seconds=lookback_s)).isoformat().replace("+00:00", "Z")
    reader = make_data_product_reader(data_dir=settings.data_root / "phase2", product_level=level,
                                      product_name=name, channel=channel, storage_config=settings.storage)
    try:
        rows = reader.read_time_range(start=start, end=now.isoformat().replace("+00:00", "Z"))
    except Exception as exc:                  # noqa: BLE001
        logger.warning("freshness read %s_%s/%s failed: %s", level, name, channel, exc)
        return None
    finally:
        if hasattr(reader, "close"):
            reader.close()
    if not rows:
        return None
    ts = rows[-1]["timestamp_utc"]
    return (now - datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds()


def table(settings: Settings) -> dict:
    channels = [c["name"] for c in discover_channels(settings)] or ["WWV_10000"]
    products: dict[str, dict] = {}
    for (level, name), expected in EXPECTED.items():
        channel = FIXED_CHANNEL.get((level, name), channels[0])
        age = _newest_age(settings, level, name, channel, lookback_s=max(expected * 10, 86400))
        if age is None:
            verdict = "INDETERMINATE"
        elif age <= expected:
            verdict = "VALID"
        else:
            verdict = "INVALID"
        products[f"{level}_{name}"] = {"verdict": verdict, "age_s": None if age is None else round(age, 1),
                                       "expected_s": expected, "channel": channel}
    verdicts = {p["verdict"] for p in products.values()}
    status = "healthy" if verdicts == {"VALID"} else ("error" if "INVALID" in verdicts else "degraded")
    return {"status": status, "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "products": products, "channels": channels}
