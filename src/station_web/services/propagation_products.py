"""Two propagation products no page showed: hourly mode statistics, and the detection funnel.

``L3C_propagation_stats``
    hamsci-physics's ionospheric reanalysis writes one HOURLY row per
    (station, frequency) under the fixed channel ``REANALYSIS``: the share
    of the hour's measurements in each propagation mode, and the station's
    estimated MUF (the same value on every frequency row of that station and
    hour).  ``hourly_stats`` folds the frequency rows of each (station, hour)
    into one: the mode shares weighted by each row's ``n_observations``, the
    MUF as published.

``L2_detection_attempts``
    hf-timestd's metrology service writes one row per measurement attempt,
    detected or rejected, with the rejection reason.  At one attempt per
    second per station per channel the table runs to millions of rows a
    day, so ``detection_funnel`` never fetches a row: it counts in SQL,
    one ``GROUP BY`` per channel over that channel's index range.

Both reads lead with ``channel = ?`` and a ``timestamp_utc`` range, so SQLite
seeks the ``(channel, timestamp_utc)`` index, and both hold a heavy.guard
semaphore.  A station without the product answers ``available: false``.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from station_web import heavy, projected
from station_web.config import Settings
from station_web.services.channels import discover_channels
from station_web.timeutil import as_utc, iso_z

STATS_TABLE = "L3C_propagation_stats"
STATS_CHANNEL = "REANALYSIS"
#: One row per station per frequency per hour, ~430 a day.
STATS_MAX_SPAN = timedelta(days=7)
STATS_FAMILY = "propagation stats"
#: Display name -> column, in the order the page stacks them.
MODES = (("1E", "mode_1e_probability"), ("1F", "mode_1f_probability"),
         ("2F", "mode_2f_probability"), ("3F", "mode_3f_probability"),
         ("GW", "mode_gw_probability"), ("unknown", "mode_unknown_probability"))
STATS_COLUMNS = (("timestamp_utc", "period_start", "aggregation_period", "station",
                  "estimated_muf_mhz", "muf_confidence", "n_observations", "quality_flag")
                 + tuple(c for _, c in MODES))

DETECT_TABLE = "L2_detection_attempts"
#: At most a day: the count walks every row of the window inside the index range.
DETECT_MAX_SPAN = timedelta(days=1)
DETECT_FAMILY = "detection attempts"


def _unavailable(reason: str, **extra) -> dict:
    return {"available": False, "reason": reason, **extra}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (table,)).fetchone() is not None


# ── hourly MUF and mode probability ────────────────────────────────────────

def hourly_stats(t0: datetime, t1: datetime, station: Optional[str] = None) -> dict:
    conn = projected.connect()
    if conn is None:
        return _unavailable(f"No product database on this station, so no {STATS_TABLE} "
                            "(hamsci-physics's hourly reanalysis writes it).")
    try:
        present = _table_exists(conn, STATS_TABLE)
    finally:
        conn.close()
    if not present:
        return _unavailable(f"{STATS_TABLE} does not exist on this station "
                            "(hamsci-physics's hourly reanalysis writes it).")

    # (station, hour end) -> accumulators; a few hundred keys a day at most
    acc: Dict[tuple, dict] = {}
    quality: Dict[str, int] = defaultdict(int)
    n_rows = 0
    idx = {c: i for i, c in enumerate(STATS_COLUMNS)}
    with heavy.guard(STATS_FAMILY):
        for part in projected.iter_rows(STATS_TABLE, STATS_CHANNEL, t0, t1, STATS_COLUMNS,
                                        station=station):
            for r in part:
                if (r[idx["aggregation_period"]] or "HOURLY") != "HOURLY":
                    continue
                st = r[idx["station"]]
                if not st:
                    continue
                n_rows += 1
                quality[r[idx["quality_flag"]] or "unflagged"] += 1
                key = (st, r[idx["timestamp_utc"]])
                a = acc.get(key)
                if a is None:
                    a = acc[key] = {"period_start": r[idx["period_start"]], "n": 0,
                                    "w": defaultdict(float), "muf": None, "conf": None}
                n = r[idx["n_observations"]] or 0
                a["n"] += n
                for name, col in MODES:
                    p = r[idx[col]]
                    if p is not None:
                        a["w"][name] += float(p) * n
                muf = r[idx["estimated_muf_mhz"]]
                if muf is not None and (a["muf"] is None or muf > a["muf"]):
                    a["muf"], a["conf"] = float(muf), r[idx["muf_confidence"]]

    stations: Dict[str, dict] = {}
    for (st, t_end) in sorted(acc, key=lambda k: (k[0], k[1])):
        a = acc[(st, t_end)]
        s = stations.setdefault(st, {"time": [], "period_start": [], "muf_mhz": [],
                                     "muf_confidence": [], "n_observations": [],
                                     "mode_probability": {name: [] for name, _ in MODES}})
        s["time"].append(_z(t_end))
        s["period_start"].append(_z(a["period_start"]))
        s["muf_mhz"].append(a["muf"])
        s["muf_confidence"].append(a["conf"])
        s["n_observations"].append(a["n"])
        for name, _ in MODES:
            s["mode_probability"][name].append(
                round(a["w"][name] / a["n"], 4) if a["n"] > 0 else None)

    out = {"available": True, "source": STATS_TABLE, "start": iso_z(t0), "end": iso_z(t1),
           "aggregation_period": "HOURLY", "modes_order": [name for name, _ in MODES],
           "n_rows": n_rows, "stations": stations, "quality_counts": dict(quality),
           "muf_note": "estimated MUF as hamsci-physics publishes it: the highest frequency "
                       "with F-layer propagation probability above 0.5, per station path"}
    if not stations:
        out["reason"] = f"No HOURLY {STATS_TABLE} rows in this window."
    return out


def _z(ts) -> Optional[str]:
    if not ts:
        return None
    try:
        return iso_z(as_utc(datetime.fromisoformat(str(ts).replace("Z", "+00:00"))))
    except ValueError:
        return None


# ── the detection funnel ───────────────────────────────────────────────────

def _bound(dt: datetime) -> str:
    """A timestamp_utc bound to the second; any stored suffix sorts after it."""
    return as_utc(dt).strftime("%Y-%m-%dT%H:%M:%S")


def detection_funnel(settings: Settings, t0: datetime, t1: datetime,
                     station: Optional[str] = None) -> dict:
    conn = projected.connect()
    if conn is None:
        return _unavailable(f"No product database on this station, so no {DETECT_TABLE} "
                            "(hf-timestd's metrology service writes it).")
    try:
        if not _table_exists(conn, DETECT_TABLE):
            return _unavailable(f"{DETECT_TABLE} does not exist on this station "
                                "(hf-timestd's metrology service writes it).")
    finally:
        conn.close()

    lo, hi = _bound(t0), _bound(t1 + timedelta(seconds=1))
    sql = (f"SELECT station, detected, rejection_reason, frequency_mhz, COUNT(*) "
           f"FROM {DETECT_TABLE} WHERE channel = ? AND timestamp_utc >= ? "
           "AND timestamp_utc < ?")
    if station is not None:
        sql += " AND station = ?"
    sql += " GROUP BY station, detected, rejection_reason"

    by_key: Dict[tuple, dict] = {}
    with heavy.guard(DETECT_FAMILY):
        channels = [c["name"] for c in discover_channels(settings, table=DETECT_TABLE)]
        conn = projected.connect()
        try:
            for ch in channels:
                params: list = [ch, lo, hi]
                if station is not None:
                    params.append(station)
                for st, detected, reason, f_mhz, n in conn.execute(sql, params):
                    st = st or "unknown"
                    b = by_key.setdefault((ch, st), {
                        "channel": ch, "station": st,
                        "frequency_mhz": float(f_mhz) if f_mhz is not None else None,
                        "attempts": 0, "detected": 0, "rejected": 0, "reasons": {}})
                    b["attempts"] += n
                    if detected:
                        b["detected"] += n
                    else:
                        b["rejected"] += n
                        r = reason or "unrecorded"
                        b["reasons"][r] = b["reasons"].get(r, 0) + n
        finally:
            conn.close()

    broadcasts: List[dict] = sorted(by_key.values(),
                                    key=lambda b: (b["frequency_mhz"] or 0, b["channel"], b["station"]))
    totals_r: Dict[str, int] = defaultdict(int)
    for b in broadcasts:
        b["detection_rate"] = round(b["detected"] / b["attempts"], 4) if b["attempts"] else None
        for r, n in b["reasons"].items():
            totals_r[r] += n
    out = {"available": True, "source": DETECT_TABLE, "start": iso_z(t0), "end": iso_z(t1),
           "broadcasts": broadcasts,
           "reasons_order": sorted(totals_r, key=lambda r: (-totals_r[r], r)),
           "totals": {"attempts": sum(b["attempts"] for b in broadcasts),
                      "detected": sum(b["detected"] for b in broadcasts),
                      "rejected": sum(b["rejected"] for b in broadcasts),
                      "reasons": dict(totals_r)}}
    if not broadcasts:
        out["reason"] = f"No {DETECT_TABLE} rows in this window."
    return out
