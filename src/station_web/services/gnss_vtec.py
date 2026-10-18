"""GNSS VTEC, the reference the HF TEC chart draws against.

The gnss-vtec service (and hf-timestd's bridge) write ``L3_gnss_vtec`` at
1 Hz under the fixed channel ``GNSS``: vertical TEC from dual-frequency GPS
over the receiver, with a quality flag set by satellite count.  A day holds
86 400 rows, so the read streams three projected columns in chunks and
folds them into at most ``MAX_POINTS`` bucket medians as they pass; memory
holds one bucket at a time.  Rows flagged BAD (fewer than four satellites)
stay out and get counted.

The HF TEC from group delay is slant TEC along each path; this is vertical
TEC over the receiver.  The page labels this line as the reference, not as
the same quantity.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import List, Optional

from station_web import heavy, projected
from station_web.timeutil import iso_z

TABLE = "L3_gnss_vtec"
CHANNEL = "GNSS"
MAX_SPAN = timedelta(days=2)
MAX_POINTS = 1500
HEAVY_FAMILY = "gnss vtec"
COLUMNS = ("unix_timestamp", "vtec_tecu", "n_satellites", "quality_flag")
LABEL = "GNSS VTEC over the receiver (reference)"


def _unavailable(reason: str) -> dict:
    return {"available": False, "source": TABLE, "label": LABEL, "reason": reason}


def series(t0: datetime, t1: datetime) -> dict:
    conn = projected.connect()
    if conn is None:
        return _unavailable(f"No product database on this station, so no {TABLE}.")
    try:
        present = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                               (TABLE,)).fetchone() is not None
    finally:
        conn.close()
    if not present:
        return _unavailable(f"{TABLE} does not exist on this station "
                            "(the gnss-vtec service writes it where a GNSS receiver runs).")

    t0s, t1s = t0.timestamp(), t1.timestamp()
    bucket_s = max(1.0, math.ceil((t1s - t0s) / MAX_POINTS))
    time: List[str] = []
    vtec: List[float] = []
    nsat: List[Optional[float]] = []
    n_rows = n_bad = 0
    cur_k: Optional[int] = None
    cur_v: List[float] = []
    cur_n: List[float] = []

    def close():
        if cur_v:
            centre = t0s + (cur_k + 0.5) * bucket_s
            time.append(iso_z(datetime.fromtimestamp(min(centre, t1s), timezone.utc)))
            vtec.append(round(median(cur_v), 3))
            nsat.append(median(cur_n) if cur_n else None)

    with heavy.guard(HEAVY_FAMILY):
        for part in projected.iter_rows(TABLE, CHANNEL, t0, t1, COLUMNS):
            for ts, v, n, q in part:
                if v is None or ts is None:
                    continue
                if q == "BAD":
                    n_bad += 1
                    continue
                k = int((float(ts) - t0s) // bucket_s)
                if k != cur_k:
                    close()
                    cur_k, cur_v, cur_n = k, [], []
                cur_v.append(float(v))
                if n is not None:
                    cur_n.append(float(n))
                n_rows += 1
        close()

    if not vtec:
        out = _unavailable(f"No usable {TABLE} rows in this window.")
        out["n_excluded_bad"] = n_bad
        return out
    return {"available": True, "source": TABLE, "channel": CHANNEL, "label": LABEL,
            "quantity": "vertical TEC over the receiver, TECU",
            "start": iso_z(t0), "end": iso_z(t1), "n_rows": n_rows, "n_excluded_bad": n_bad,
            "bucket_s": bucket_s if bucket_s > 1 else None, "n_points": len(vtec),
            "time": time, "vtec_tecu": vtec, "n_satellites": nsat}
