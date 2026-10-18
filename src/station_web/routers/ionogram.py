"""
Ionogram API endpoints — all-arrivals Time-of-Flight vs SNR cluster analysis.

Analogous to Gwyn Griffiths' WSPRDaemon grape_acf_doppler_spread.py plots:
  - ToF time series with S+N level overlay
  - ToF vs S+N scatter with KDE density contours (cluster analysis)

Data source: the ``L1_all_arrivals`` SQLite product that hf-timestd's
metrology service writes.  Channels and the stations heard on each are
discovered from that table; a SHARED_* channel carries WWV, WWVH and BPM at
once, so every endpoint takes a ``station`` filter.

Row semantics (hf-timestd ``metrology_service`` writes, ``metrology_engine``
measures):
  - ``minute_boundary_utc`` is the true minute boundary (unix seconds);
    ``utc_second`` is the second the tick was transmitted in.
  - ``tone_correlator`` rows: ``arrival_ms`` counts from the start of the
    minute buffer, so ToF = arrival_ms − 1000·(utc_second − minute_boundary).
    This assumes the buffer starts on the minute (RTP-derived; within a
    sample or so).
  - ``edge_tick`` and ``clean`` rows: ``model_expected_ms`` is the geometric
    propagation delay and ``timing_error_ms`` the residual against it;
    ``clean`` rows write ``arrival_ms = 0``.  ToF = model_expected_ms +
    timing_error_ms.
"""

from fastapi import APIRouter, Query, HTTPException
from datetime import datetime, timedelta, timezone
from typing import Optional
import logging
import math
import sqlite3
import threading
import time

import numpy as np

from station_web import heavy
from station_web.config import config
from station_web.errors import internal_error
from station_web.services.channels import _db_path
from station_web.timeutil import parse_window
from hamsci_dsp.stations import BUILTIN_CATALOG

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ionogram", tags=["ionosphere"])

TABLE = "L1_all_arrivals"

# B4 writes about 240k rows a day on each SHARED channel.  The routes used
# to read a day of every column through hamsci-dsp's ``read_time_range``
# (SELECT *, one dict per row), and the page fired three such reads at once:
# about 300 MB of Python heap each, past the unit's MemoryMax together.  Now
# one projected query per request leads with the (channel, timestamp_utc)
# index, filters in SQL, and fetches in chunks straight into numpy arrays.
# The window stops at a day, the returned scatter at MAX_SCATTER_POINTS, the
# KDE input at KDE_MAX_POINTS, and one heavy read runs at a time.

#: Longest window a request may ask for.
MAX_SPAN = timedelta(hours=24)
#: Upper bound on points in a returned scatter (every station together).
MAX_SCATTER_POINTS = 20000
#: Upper bound on the points a KDE grid is fitted to.
KDE_MAX_POINTS = 20000
#: The ``heavy`` family that serialises ionogram reads.
HEAVY_FAMILY = "ionogram"

_FETCH_ROWS = 20000
# timestamp_utc carries the writer's processing time, which trails the data
# time (minute_boundary_utc) by a minute or so.  The query narrows on the
# index with slack, then cuts the exact window on minute_boundary_utc.
_TS_SLACK_BEFORE_S = 120
_TS_SLACK_AFTER_S = 3600

#: how far back from a channel's newest row discovery looks for its stations
STATION_LOOKBACK = timedelta(hours=1)
#: channel discovery answers from a cache this long
_DISCOVERY_TTL_S = 30.0
_discovery_cache: dict = {}
_discovery_lock = threading.Lock()

# Station colour palette (matches Gwyn Griffiths slide aesthetic)
STATION_COLORS = {
    "WWV": "#2DD4BF",   # bright teal
    "WWVH": "#FB923C",  # bright orange
    "BPM": "#FB7185",   # bright rose
}

#: plausible ToF ceiling per station (ms); farther paths, longer ceilings
TOF_MAX_MS = {"WWV": 200.0, "WWVH": 500.0, "BPM": 500.0}

#: a residual beyond this means the correlator locked onto the wrong tick
MAX_TIMING_ERROR_MS = 15.0

SNR_CAP = 40.0   # removes DC/self-interference artefacts

#: detection methods whose model_expected_ms is the propagation delay
MODEL_RELATIVE = ("edge_tick", "clean")


def clear_cache() -> None:
    with _discovery_lock:
        _discovery_cache.clear()


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _connect():
    db = _db_path(config)
    if not db.exists():
        return None
    try:
        return sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error as e:
        logger.warning(f"ionogram: cannot open {db}: {e}")
        return None


def _channel_stations(con, channel: str):
    """(newest timestamp_utc, stations heard in the hour before it).

    Anchored to the channel's newest row, as phase_service does, so the
    DISTINCT walks an hour of the index, not days of rows.
    """
    row = con.execute(f"SELECT timestamp_utc FROM {TABLE} WHERE channel = ? "
                      f"ORDER BY timestamp_utc DESC LIMIT 1", (channel,)).fetchone()
    if row is None or not row[0]:
        return None, []
    latest = row[0]
    try:
        t = datetime.fromisoformat(str(latest).replace("Z", "+00:00"))
    except ValueError:
        return latest, []
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    lo = (t - STATION_LOOKBACK).isoformat()
    stations = sorted({r[0] for r in con.execute(
        f"SELECT DISTINCT station FROM {TABLE} WHERE channel = ? AND timestamp_utc >= ?",
        (channel, lo)) if r[0]})
    return latest, stations


def _discover_uncached() -> list:
    con = _connect()
    if con is None:
        return []
    out = []
    try:
        present = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                              (TABLE,)).fetchone()
        if not present:
            return []
        channel = ""
        while True:
            row = con.execute(f"SELECT channel FROM {TABLE} WHERE channel > ? "
                              f"ORDER BY channel LIMIT 1", (channel,)).fetchone()
            if row is None:
                break
            channel = row[0]
            latest, stations = _channel_stations(con, channel)
            out.append({
                "channel": channel,
                "stations": stations,
                "color": STATION_COLORS.get(stations[0], "#888888") if len(stations) == 1 else "#888888",
                "latest": latest,
            })
    except sqlite3.Error as e:
        logger.warning(f"ionogram channel discovery failed: {e}")
    finally:
        con.close()
    return out


def discover_channels() -> list:
    """Channels in ``L1_all_arrivals``, each with the stations heard lately.

    Walks the (channel, timestamp_utc) index: one skip-scan step per
    channel, the newest row, then the hour before it.  No full-table scan,
    and no day-long range: the table grows by about 240k rows a day per
    SHARED channel.  The page and every request without a ``channel`` ask,
    so the answer stays cached for a few seconds.
    """
    key = str(_db_path(config))
    now = time.monotonic()
    with _discovery_lock:
        hit = _discovery_cache.get(key)
        if hit is not None and hit[0] > now:
            return hit[1]
    out = _discover_uncached()
    with _discovery_lock:
        _discovery_cache.clear()          # one database per process; keep one entry
        _discovery_cache[key] = (now + _DISCOVERY_TTL_S, out)
    return out


def _default_channel() -> Optional[str]:
    """The channel with the newest row, or None when the table is empty."""
    chans = discover_channels()
    if not chans:
        return None
    return max(chans, key=lambda c: c["latest"] or "")["channel"]


def _resolve_channel(channel: Optional[str]) -> Optional[str]:
    return channel or _default_channel()


def _no_data(channel, station, t0, t1, reason, **extra):
    return {"status": "no_data", "channel": channel, "station": station,
            "time_range": {"start": _iso(t0), "end": _iso(t1)},
            "reason": reason, **extra}


def _compute_solar_overlay(station: str, t0: datetime, t1: datetime) -> dict:
    """Compute solar elevation at the path midpoint for day/night shading."""
    try:
        from hamsci_physics.solar_zenith import (
            calculate_midpoint, solar_position,
        )

        rx_lat = config.station_metadata.get('latitude', 0.0)
        rx_lon = config.station_metadata.get('longitude', 0.0)
        tx_lat, tx_lon = BUILTIN_CATALOG.get(station).coordinates
        mid_lat, mid_lon = calculate_midpoint(rx_lat, rx_lon, tx_lat, tx_lon)

        # Generate every 5 minutes over the window
        timestamps = []
        elevations = []
        step = timedelta(minutes=5)
        curr = t0
        while curr <= t1:
            _, el = solar_position(curr, mid_lat, mid_lon)
            timestamps.append(int(curr.timestamp()))
            elevations.append(round(el, 2))
            curr += step

        return {
            "timestamps": timestamps,
            "elevation_deg": elevations,
            "midpoint": {"lat": round(mid_lat, 2), "lon": round(mid_lon, 2)},
            "station": station,
        }
    except Exception as e:
        logger.warning(f"Solar overlay failed: {e}")
        return None


#: numeric columns read from the table, in SELECT order
_NUM_COLS = ("minute_boundary_utc", "arrival_ms", "timing_error_ms", "model_expected_ms",
             "corr_snr_db", "peak_rank", "utc_second",
             "COALESCE(carrier_phase_rad, 0.0)", "COALESCE(sec_in_minute, 0)")
_NUM_KEYS = ("minute_boundary", "arrival_ms", "timing_error_ms", "model_expected_ms",
             "corr_snr_db", "peak_rank", "utc_second", "carrier_phase_rad", "sec_in_minute")


def _codes(values, names: list, index: dict) -> np.ndarray:
    """Small-integer codes for repeated strings; ``names[code]`` reads them back."""
    out = np.empty(len(values), dtype=np.int16)
    for i, v in enumerate(values):
        c = index.get(v)
        if c is None:
            c = index[v] = len(names)
            names.append(v)
        out[i] = c
    return out


def _load_all_arrivals(
    channel: str,
    ts0: int,
    ts1: int,
    rank_filter: Optional[int],
    min_snr_db: float,
    station: Optional[str] = None,
    method: Optional[str] = None,
):
    """L1_all_arrivals rows for one channel (and station) in [ts0, ts1], as arrays.

    Returns a dict of numpy arrays ordered by (minute, second), or None when
    no row qualifies.  ``station`` and ``detection_method`` come back as
    small-integer codes, with ``station_names`` / ``method_names`` to read
    them.  Station, SNR, rank and method filters run in SQL.
    """
    con = _connect()
    if con is None:
        return None
    try:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                           (TABLE,)).fetchone():
            return None
        ts_lo = datetime.fromtimestamp(ts0 - _TS_SLACK_BEFORE_S, tz=timezone.utc).isoformat()
        ts_hi = datetime.fromtimestamp(ts1 + _TS_SLACK_AFTER_S, tz=timezone.utc).isoformat()
        sql = (f"SELECT {', '.join(_NUM_COLS)}, station, "
               f"COALESCE(detection_method, 'tone_correlator') FROM {TABLE} "
               "WHERE channel = ? AND timestamp_utc BETWEEN ? AND ? "
               "AND minute_boundary_utc BETWEEN ? AND ? AND corr_snr_db >= ?")
        params: list = [channel, ts_lo, ts_hi, ts0, ts1, min_snr_db]
        if station is not None:
            sql += " AND station = ?"
            params.append(station)
        if rank_filter is not None:
            sql += " AND peak_rank = ?"
            params.append(rank_filter)
        if method is not None:
            sql += " AND COALESCE(detection_method, 'tone_correlator') = ?"
            params.append(method)
        sql += " ORDER BY minute_boundary_utc, utc_second, ROWID"

        st_names: list = []
        st_index: dict = {}
        me_names: list = []
        me_index: dict = {}
        nums, sts, mes = [], [], []
        cur = con.execute(sql, params)
        n_num = len(_NUM_COLS)
        while True:
            rows = cur.fetchmany(_FETCH_ROWS)
            if not rows:
                break
            # None becomes NaN under dtype=float
            nums.append(np.array([r[:n_num] for r in rows], dtype=float))
            sts.append(_codes([r[n_num] or "" for r in rows], st_names, st_index))
            mes.append(_codes([r[n_num + 1] for r in rows], me_names, me_index))
            del rows
    finally:
        con.close()

    if not nums:
        return None
    num = np.concatenate(nums)
    del nums
    d = {k: np.ascontiguousarray(num[:, i]) for i, k in enumerate(_NUM_KEYS)}
    del num
    d["station"] = np.concatenate(sts)
    d["method"] = np.concatenate(mes)
    d["station_names"] = st_names
    d["method_names"] = me_names

    # GPS-referenced ToF (see the module docstring for the row semantics).
    model_codes = [i for i, m in enumerate(me_names) if m in MODEL_RELATIVE]
    model_rel = np.isin(d["method"], model_codes)
    sec_in_min = d["utc_second"] - d["minute_boundary"]
    tof_tone = d["arrival_ms"] - sec_in_min * 1000.0
    with np.errstate(invalid="ignore"):
        tof_model = np.where(d["model_expected_ms"] > 0,
                             d["model_expected_ms"] + d["timing_error_ms"], np.nan)
    d["tof_ms"] = np.where(model_rel, tof_model, tof_tone)
    return d


def _subset(d: dict, mask: np.ndarray) -> dict:
    out = {k: (v[mask] if isinstance(v, np.ndarray) else v) for k, v in d.items()}
    return out


def _names(codes: np.ndarray, names: list) -> list:
    lut = np.array(names, dtype=object) if names else np.array([], dtype=object)
    return lut[codes].tolist() if codes.size else []


def _plausible(d):
    """Mask of physically plausible arrivals: finite ToF within the station's
    ceiling, a residual small enough to rule out a wrong-tick lock, and SNR
    below the self-interference cap."""
    lut = np.array([TOF_MAX_MS.get(s, 500.0) for s in d["station_names"]] or [500.0])
    tof_max = lut[d["station"]]
    tof, te, snr = d["tof_ms"], d["timing_error_ms"], d["corr_snr_db"]
    with np.errstate(invalid="ignore"):
        return (np.isfinite(tof) & (tof >= 0) & (tof <= tof_max)
                & (np.abs(te) < MAX_TIMING_ERROR_MS) & (snr <= SNR_CAP))


def _station_label(channel: str, station: Optional[str], d) -> Optional[str]:
    if station:
        return station
    if d is None:
        return None
    heard = sorted({d["station_names"][c] for c in np.unique(d["station"])})
    return heard[0] if len(heard) == 1 else None


def _stride_index(n: int, requested: int = 1) -> np.ndarray:
    """Every ``requested``-th index, thinned further to MAX_SCATTER_POINTS."""
    step = max(int(requested or 1), 1, math.ceil(n / MAX_SCATTER_POINTS))
    return np.arange(0, n, step)


def _minute_groups(mb: np.ndarray):
    """(minute, slice) for each run of equal minutes in an mb-sorted array."""
    if mb.size == 0:
        return []
    cuts = np.flatnonzero(np.diff(mb)) + 1
    starts = np.concatenate(([0], cuts))
    ends = np.concatenate((cuts, [mb.size]))
    return [(mb[a], slice(a, b)) for a, b in zip(starts, ends)]


def _kde_contours(x, y, n_grid=60, bandwidth=None):
    """
    Compute KDE density on a grid for contour plotting.
    Returns (xi, yi, zi) as flat lists for JSON serialisation.
    """
    try:
        from scipy.stats import gaussian_kde
    except ImportError:
        return None

    if len(x) < 10:
        return None

    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)

    # Remove NaN/Inf
    valid = np.isfinite(x) & np.isfinite(y)
    x, y = x[valid], y[valid]
    if len(x) < 10:
        return None
    if len(x) > KDE_MAX_POINTS:
        # an even stride keeps the clusters' shape at a bounded cost
        keep = np.linspace(0, len(x) - 1, KDE_MAX_POINTS).astype(np.int64)
        x, y = x[keep], y[keep]

    try:
        kde = gaussian_kde(np.vstack([x, y]), bw_method=bandwidth or "scott")
        xi = np.linspace(x.min(), x.max(), n_grid)
        yi = np.linspace(y.min(), y.max(), n_grid)
        Xi, Yi = np.meshgrid(xi, yi)
        Zi = kde(np.vstack([Xi.ravel(), Yi.ravel()])).reshape(Xi.shape)
        # Normalise to [0, 1]
        Zi = (Zi - Zi.min()) / (Zi.max() - Zi.min() + 1e-30)
        return {
            "x": xi.tolist(),
            "y": yi.tolist(),
            "z": Zi.tolist(),   # shape [n_grid, n_grid], row-major (y varies first)
        }
    except Exception as e:
        logger.warning(f"KDE failed: {e}")
        return None


def _freq_mhz(channel: str) -> float:
    try:
        return int(channel.split("_")[-1]) / 1000.0
    except ValueError:
        return 0.0


NO_CHANNELS = "no channel has rows in L1_all_arrivals"


def _window(start: str, end: str):
    """Parse the window; anything unreadable or past a day is a 400."""
    try:
        return parse_window(start, end, max_span=MAX_SPAN)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _load(channel, t0, t1, min_snr_db, station, rank=None, method=None):
    """One heavy read, under the ionogram family's semaphore."""
    with heavy.guard(HEAVY_FAMILY):
        return _load_all_arrivals(channel, int(t0.timestamp()), int(t1.timestamp()),
                                  rank, min_snr_db, station, method)


def _no_rows_reason(channel, station, min_snr_db):
    return (f"no {channel}{' ' + station if station else ''} arrivals "
            f"above {min_snr_db:g} dB in this window")


# ── views: each builds one route's reply from loaded arrays ─────────────────

def _arrivals_view(d, channel, station, t0, t1, min_snr_db, rank, include_kde, downsample):
    if d is not None:
        keep = d["corr_snr_db"] >= min_snr_db
        if rank is not None:
            keep &= d["peak_rank"] == rank
        d = _subset(d, keep) if not keep.all() else d
    if d is None or d["minute_boundary"].size == 0:
        return _no_data(channel, station, t0, t1, _no_rows_reason(channel, station, min_snr_db),
                        n_points=0, scatter={}, kde=None)

    label = _station_label(channel, station, d)
    v = _subset(d, _plausible(d))
    tof, snr = v["tof_ms"], v["corr_snr_db"]
    rank_arr = v["peak_rank"].astype(np.int64)
    idx = _stride_index(tof.size, downsample)
    scatter = {
        "minute_boundary": v["minute_boundary"][idx].astype(np.int64).tolist(),
        "tof_ms": tof[idx].tolist(),
        "corr_snr_db": snr[idx].tolist(),
        "peak_rank": rank_arr[idx].tolist(),
        "station": _names(v["station"][idx], v["station_names"]),
    }

    # KDE on the plausible set (thinned to KDE_MAX_POINTS) -- x=snr, y=tof
    # (Griffiths convention)
    kde_result = None
    if include_kde and tof.size >= 10:
        kde_result = _kde_contours(snr, tof, n_grid=60)

    rank_stats = {}
    for r in np.unique(rank_arr):
        m = rank_arr == r
        rank_stats[int(r)] = {
            "n": int(np.sum(m)),
            "tof_mean_ms": float(np.nanmean(tof[m])),
            "tof_std_ms": float(np.nanstd(tof[m])),
            "snr_mean_db": float(np.nanmean(snr[m])),
        }

    return {
        "status": "ok",
        "channel": channel,
        "station": label,
        "frequency_mhz": _freq_mhz(channel),
        "color": STATION_COLORS.get(label, "#888888"),
        "time_range": {"start": _iso(t0), "end": _iso(t1)},
        "n_points": int(tof.size),
        "n_scatter": int(idx.size),
        "max_scatter_points": MAX_SCATTER_POINTS,
        "rank_stats": rank_stats,
        "scatter": scatter,
        "kde": kde_result,
    }


def _timeseries_view(d, channel, station, t0, t1, min_snr_db):
    empty = {"timestamps": [], "tof_ms": [], "snr_db": []}
    if d is not None:
        keep = d["corr_snr_db"] >= min_snr_db
        d = _subset(d, keep) if not keep.all() else d
    if d is None or d["minute_boundary"].size == 0:
        return _no_data(channel, station, t0, t1, _no_rows_reason(channel, station, min_snr_db),
                        dominant=dict(empty), secondary=dict(empty))

    label = _station_label(channel, station, d)
    v = _subset(d, _plausible(d))
    tof, snr, mb = v["tof_ms"], v["corr_snr_db"], v["minute_boundary"]
    rank_arr = v["peak_rank"]

    # Per-minute median (cleaner than raw scatter); rows arrive minute-sorted
    def _bin_by_minute(sel):
        ts_out, val_out, snr_out = [], [], []
        m_tof, m_snr = tof[sel], snr[sel]
        for t, sl in _minute_groups(mb[sel]):
            ts_out.append(int(t))
            val_out.append(float(np.nanmedian(m_tof[sl])))
            snr_out.append(float(np.nanmedian(m_snr[sl])))
        return ts_out, val_out, snr_out

    dom_ts, dom_tof, dom_snr = _bin_by_minute(rank_arr == 0)
    sec_ts, sec_tof, sec_snr = _bin_by_minute(rank_arr > 0)

    # Implied F2 height from rank-0 vs rank-1 delay difference.
    # Match dominant and secondary by minute, compute Δτ.
    # Require Δτ ≥ 2ms to reject correlation sidelobes masquerading
    # as multipath (real 1F2→2F2 separation is typically 3-10ms).
    dom_dict = dict(zip(dom_ts, dom_tof))
    dom_snr_dict = dict(zip(dom_ts, dom_snr))
    sec_dict = dict(zip(sec_ts, sec_tof))
    sec_snr_dict = dict(zip(sec_ts, sec_snr))
    common = sorted(set(dom_dict) & set(sec_dict))

    C_KM_S = 299792.458
    MIN_DELTA_TAU_MS = 2.0   # reject sidelobes (< 2ms not physical)
    MIN_PAIR_SNR_DB = 20.0   # both arrivals must be strong

    h_ts, h_vals = [], []
    for t in common:
        delta_tau_ms = sec_dict[t] - dom_dict[t]
        if delta_tau_ms < MIN_DELTA_TAU_MS:
            continue
        # Require both rank-0 and rank-1 to have decent SNR
        if dom_snr_dict.get(t, 0) < MIN_PAIR_SNR_DB:
            continue
        if sec_snr_dict.get(t, 0) < MIN_PAIR_SNR_DB:
            continue
        # Simple vertical-path approximation: h ≈ c·Δτ/2
        h_approx_km = C_KM_S * (delta_tau_ms / 1000.0) / 2.0
        if 100 < h_approx_km < 600:
            h_ts.append(int(t))
            h_vals.append(float(h_approx_km))

    # Solar elevation at path midpoint — for day/night shading overlay
    solar = _compute_solar_overlay(label, t0, t1) if label else None

    return {
        "status": "ok",
        "channel": channel,
        "station": label,
        "color": STATION_COLORS.get(label, "#888888"),
        "time_range": {"start": _iso(t0), "end": _iso(t1)},
        "dominant": {"timestamps": dom_ts, "tof_ms": dom_tof, "snr_db": dom_snr, "n": len(dom_ts)},
        "secondary": {"timestamps": sec_ts, "tof_ms": sec_tof, "snr_db": sec_snr, "n": len(sec_ts)},
        "f2_height": {
            "timestamps": h_ts,
            "height_km": h_vals,
            "n": len(h_ts),
            "note": "Approximate F2 virtual height from rank-0 vs rank-1 delay difference",
        },
        "solar": solar,
    }


def _doppler_view(d, channel, station, t0, t1, min_snr_db, method, include_kde):
    if d is not None:
        keep = d["corr_snr_db"] >= min_snr_db
        if method:
            code = d["method_names"].index(method) if method in d["method_names"] else -1
            keep &= d["method"] == code
        d = _subset(d, keep) if not keep.all() else d
    if d is None or d["minute_boundary"].size == 0:
        return _no_data(channel, station, t0, t1, _no_rows_reason(channel, station, min_snr_db),
                        n_points=0, scatter={}, kde=None)

    label = _station_label(channel, station, d)
    te_arr, phase_arr = d["timing_error_ms"], d["carrier_phase_rad"]
    # Require finite values; an implausibly large residual means a
    # wrong-tick lock.
    with np.errstate(invalid="ignore"):
        valid = (np.isfinite(te_arr) & np.isfinite(phase_arr)
                 & (np.abs(te_arr) < MAX_TIMING_ERROR_MS))
    v = _subset(d, valid)
    te, phase, snr = v["timing_error_ms"], v["carrier_phase_rad"], v["corr_snr_db"]
    mb, sec, sim = v["minute_boundary"], v["utc_second"], v["sec_in_minute"]

    # Unwrap phase within each minute to show the Doppler trend, and take
    # each minute's Doppler from its phase slope.  Rows arrive minute-sorted.
    phase_unwrapped = np.copy(phase)
    minute_doppler = {}
    for m, sl in _minute_groups(mb):
        n = sl.stop - sl.start
        if n >= 3:
            phase_unwrapped[sl] = np.unwrap(phase[sl])
        if n >= 5:
            t_sec = sim[sl]
            if t_sec[-1] - t_sec[0] > 5.0:
                try:
                    coeffs = np.polyfit(t_sec, phase_unwrapped[sl], 1)
                    minute_doppler[int(m)] = float(coeffs[0] / (2.0 * np.pi))
                except (np.linalg.LinAlgError, ValueError):
                    pass

    idx = _stride_index(te.size)
    meth_names = _names(v["method"][idx], v["method_names"])
    scatter = {
        "minute_boundary": mb[idx].astype(np.int64).tolist(),
        "utc_second": sec[idx].astype(np.int64).tolist(),
        "sec_in_minute": sim[idx].astype(np.int64).tolist(),
        "timing_error_ms": te[idx].tolist(),
        "carrier_phase_rad": phase[idx].tolist(),
        "phase_unwrapped_rad": phase_unwrapped[idx].tolist(),
        "corr_snr_db": snr[idx].tolist(),
        "detection_method": meth_names,
    }

    # 2D KDE: timing_error (x) vs carrier_phase (y)
    kde_result = None
    if include_kde and te.size >= 20:
        kde_result = _kde_contours(te, phase, n_grid=50, bandwidth=None)

    names = v["method_names"]
    n_of = lambda m: int(np.sum(v["method"] == names.index(m))) if m in names else 0

    return {
        "status": "ok",
        "channel": channel,
        "station": label,
        "color": STATION_COLORS.get(label, "#888888"),
        "time_range": {"start": _iso(t0), "end": _iso(t1)},
        "n_points": int(te.size),
        "n_scatter": int(idx.size),
        "max_scatter_points": MAX_SCATTER_POINTS,
        "n_edge_ticks": n_of("edge_tick"),
        "n_tone_correlator": n_of("tone_correlator"),
        "scatter": scatter,
        "kde": kde_result,
        "minute_doppler": minute_doppler,
        "summary": {
            "timing_error_mean_ms": float(np.nanmean(te)) if te.size else 0.0,
            "timing_error_std_ms": float(np.nanstd(te)) if te.size else 0.0,
            "phase_std_rad": float(np.nanstd(phase)) if phase.size else 0.0,
            "mean_snr_db": float(np.nanmean(snr)) if snr.size else 0.0,
            "n_minutes_with_doppler": len(minute_doppler),
        },
    }


# ── routes ───────────────────────────────────────────────────────────────────

@router.get("/arrivals")
def get_all_arrivals(
    channel: Optional[str] = Query(None, description="Channel name, e.g. SHARED_10000, WWV_20000 (default: newest)"),
    station: Optional[str] = Query(None, description="Station on the channel: WWV, WWVH or BPM (default: all)"),
    start: str = Query("-24h", description="Start time (ISO8601 or relative like '-6h'); at most 24 h before end"),
    end: str = Query("now", description="End time"),
    rank: Optional[int] = Query(None, description="Filter by peak_rank (0=dominant, 1=secondary, None=all)"),
    min_snr_db: float = Query(6.0, description="Minimum correlation SNR"),
    include_kde: bool = Query(True, description="Include KDE density grid for contour plot"),
    downsample: int = Query(1, description="Keep every Nth point for scatter (1=all, up to the scatter cap)"),
):
    """
    All-arrivals Time-of-Flight data for one channel (and station).

    Returns scatter data (ToF vs corr_snr_db) and optionally a KDE density
    grid for contour overlay — analogous to Gwyn Griffiths' cluster analysis
    of ToF and S+N level.  The scatter holds at most MAX_SCATTER_POINTS.

    Rank 0 = dominant (shortest) arrival; rank ≥ 1 = secondary (multi-hop).
    """
    t0, t1 = _window(start, end)
    try:
        channel = _resolve_channel(channel)
        if channel is None:
            return _no_data(None, station, t0, t1, NO_CHANNELS, n_points=0, scatter={}, kde=None)
        d = _load(channel, t0, t1, min_snr_db, station, rank=rank)
        return _arrivals_view(d, channel, station, t0, t1, min_snr_db, rank, include_kde, downsample)
    except HTTPException:
        raise
    except Exception as e:
        raise internal_error(logger, "ionogram arrivals", e)


@router.get("/arrivals/timeseries")
def get_arrivals_timeseries(
    channel: Optional[str] = Query(None, description="Channel name (default: newest)"),
    station: Optional[str] = Query(None, description="Station on the channel (default: all)"),
    start: str = Query("-24h", description="Start time; at most 24 h before end"),
    end: str = Query("now", description="End time"),
    min_snr_db: float = Query(6.0, description="Minimum correlation SNR"),
):
    """
    All-arrivals ToF time series (top panel of Griffiths-style plot).

    Returns per-minute ToF for rank-0 (dominant) and rank-1+ (secondary)
    arrivals separately, plus the corr_snr_db time series (bottom panel).

    The implied F2 virtual height can be derived from the rank-0 vs rank-1
    delay difference:
        Δτ = τ(2F2) − τ(1F2)
        h_F2 = sqrt( (c·Δτ/2)² − (d/4)² )   [flat-Earth approx]
    """
    t0, t1 = _window(start, end)
    try:
        channel = _resolve_channel(channel)
        if channel is None:
            empty = {"timestamps": [], "tof_ms": [], "snr_db": []}
            return _no_data(None, station, t0, t1, NO_CHANNELS,
                            dominant=dict(empty), secondary=dict(empty))
        d = _load(channel, t0, t1, min_snr_db, station)
        return _timeseries_view(d, channel, station, t0, t1, min_snr_db)
    except HTTPException:
        raise
    except Exception as e:
        raise internal_error(logger, "ionogram time series", e)


@router.get("/doppler-delay")
def get_doppler_delay(
    channel: Optional[str] = Query(None, description="Channel name (default: newest)"),
    station: Optional[str] = Query(None, description="Station on the channel (default: all)"),
    start: str = Query("-6h", description="Start time; at most 24 h before end"),
    end: str = Query("now", description="End time"),
    min_snr_db: float = Query(3.0, description="Minimum tick SNR (lower for edge ticks)"),
    method: Optional[str] = Query(None, description="Filter by detection_method: 'edge_tick', 'tone_correlator', or None for all"),
    include_kde: bool = Query(True, description="Include 2D KDE density grid"),
):
    """
    Doppler-Delay scatter: per-tick timing_error vs carrier_phase.

    Each point is one per-second tick detection from the TickEdgeDetector.
    The carrier_phase_rad progression across seconds within a minute encodes
    Doppler shift; the timing_error_ms is the propagation delay residual.

    Multipath modes (1F2, 2F2, ...) arriving via different ionospheric layers
    have different Doppler shifts (because the layers move at different
    velocities).  Even when delays are too close to resolve temporally,
    different Doppler signatures separate them in the phase domain.

    The 2D KDE highlights clusters — each cluster is a candidate propagation
    mode.  This is the HF time-signal analogue of a Doppler-Delay spread
    function from channel sounding.  The scatter holds at most
    MAX_SCATTER_POINTS, evenly thinned.
    """
    t0, t1 = _window(start, end)
    try:
        channel = _resolve_channel(channel)
        if channel is None:
            return _no_data(None, station, t0, t1, NO_CHANNELS, n_points=0, scatter={}, kde=None)
        d = _load(channel, t0, t1, min_snr_db, station, method=method)
        return _doppler_view(d, channel, station, t0, t1, min_snr_db, method, include_kde)
    except HTTPException:
        raise
    except Exception as e:
        raise internal_error(logger, "ionogram Doppler-delay", e)


@router.get("/combined")
def get_combined(
    channel: Optional[str] = Query(None, description="Channel name (default: newest)"),
    station: Optional[str] = Query(None, description="Station on the channel (default: all)"),
    start: str = Query("-24h", description="Start time; at most 24 h before end"),
    end: str = Query("now", description="End time"),
    min_snr_db: float = Query(6.0, description="Minimum correlation SNR (time series, cluster)"),
    downsample: int = Query(1, description="Keep every Nth cluster point (up to the scatter cap)"),
    doppler_min_snr_db: float = Query(3.0, description="Minimum tick SNR for the Doppler-delay view"),
    doppler_method: Optional[str] = Query("edge_tick", description="detection_method for the Doppler-delay view"),
    include_kde: bool = Query(True, description="Include the KDE grids"),
):
    """What ionogram.html shows, from one read: the time series, the cluster
    scatter and the Doppler-delay scatter, each shaped exactly as its own
    route answers it."""
    t0, t1 = _window(start, end)
    try:
        channel = _resolve_channel(channel)
        if channel is None:
            empty = {"timestamps": [], "tof_ms": [], "snr_db": []}
            none = _no_data(None, station, t0, t1, NO_CHANNELS, n_points=0, scatter={}, kde=None)
            return {"status": "no_data", "channel": None, "reason": NO_CHANNELS,
                    "timeseries": _no_data(None, station, t0, t1, NO_CHANNELS,
                                           dominant=dict(empty), secondary=dict(empty)),
                    "arrivals": none, "doppler_delay": dict(none)}
        d = _load(channel, t0, t1, min(min_snr_db, doppler_min_snr_db), station)
        ts = _timeseries_view(d, channel, station, t0, t1, min_snr_db)
        ar = _arrivals_view(d, channel, station, t0, t1, min_snr_db, None, include_kde, downsample)
        dd = _doppler_view(d, channel, station, t0, t1, doppler_min_snr_db, doppler_method,
                           include_kde)
        del d
        ok = "ok" if "ok" in (ts["status"], ar["status"], dd["status"]) else "no_data"
        return {"status": ok, "channel": channel, "timeseries": ts, "arrivals": ar,
                "doppler_delay": dd}
    except HTTPException:
        raise
    except Exception as e:
        raise internal_error(logger, "ionogram combined", e)


@router.get("/channels")
def list_ionogram_channels():
    """Channels with rows in L1_all_arrivals, and the stations heard on each."""
    available = discover_channels()
    return {"status": "ok", "channels": available, "n_channels": len(available)}
