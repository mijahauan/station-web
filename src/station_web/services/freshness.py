"""Health = are the products fresh?  Four verdicts, borrowed from the
heartbeat schema: VALID (fresh), INVALID (present but older than expected),
INDETERMINATE (no rows at all).  No process or disk probes: sigmond owns those."""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

from station_web.config import Settings
from station_web.services.channels import _db_path, discover_channels

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


def _max_timestamp(settings: Settings, level: str, name: str, channel: str) -> str | None:
    """Newest ``timestamp_utc`` for one (level, name, channel).

    A single indexed ``MAX`` query — no reader, no full-window page
    fetch just to keep the last row. A missing database or a missing
    table (product never written) is the expected, common state and
    resolves quietly to ``None``.
    """
    db = _db_path(settings)
    if not db.exists():
        return None
    table = f"{level}_{name}"
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = con.execute(
                f'SELECT MAX(timestamp_utc) FROM "{table}" WHERE channel = ?', (channel,)
            ).fetchone()
        finally:
            con.close()
    except sqlite3.Error as exc:
        logger.warning("freshness MAX query on %s/%s failed: %s", table, channel, exc)
        return None
    return row[0] if row else None


def _newest_across_channels(settings: Settings, level: str, name: str, channels: list[str]):
    """The newest row for a per-receiver-channel product, across every
    discovered channel — not just the alphabetically first one.

    Returns ``(timestamp, channel)``, both ``None`` if no channel has a
    row (or there are no channels at all).
    """
    best_dt = None
    best_ts: str | None = None
    best_channel: str | None = None
    for ch in channels:
        ts = _max_timestamp(settings, level, name, ch)
        if ts is None:
            continue
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if best_dt is None or dt > best_dt:
            best_dt, best_ts, best_channel = dt, ts, ch
    return best_ts, best_channel


def table(settings: Settings) -> dict:
    now = datetime.now(timezone.utc)
    channels = [c["name"] for c in discover_channels(settings)]
    products: dict[str, dict] = {}
    for (level, name), expected in EXPECTED.items():
        fixed = FIXED_CHANNEL.get((level, name))
        if fixed is not None:
            ts, channel = _max_timestamp(settings, level, name, fixed), fixed
        else:
            ts, channel = _newest_across_channels(settings, level, name, channels)
        if ts is None:
            verdict, age = "INDETERMINATE", None
        else:
            age = (now - datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds()
            verdict = "VALID" if age <= expected else "INVALID"
        products[f"{level}_{name}"] = {"verdict": verdict, "age_s": None if age is None else round(age, 1),
                                       "expected_s": expected, "channel": channel}
    verdicts = {p["verdict"] for p in products.values()}
    status = "healthy" if verdicts == {"VALID"} else ("error" if "INVALID" in verdicts else "degraded")
    return {"status": status, "timestamp": now.isoformat().replace("+00:00", "Z"),
            "products": products, "channels": channels}
