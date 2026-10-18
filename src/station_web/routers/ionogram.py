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
from pathlib import Path
import logging
import sqlite3

from station_web.config import config
from station_web.services.channels import _db_path
from hamsci_dsp.io import make_data_product_reader
from hamsci_dsp.stations import BUILTIN_CATALOG

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ionogram", tags=["ionosphere"])

TABLE = "L1_all_arrivals"

#: how far back channel discovery looks for the stations heard on a channel
STATION_LOOKBACK = timedelta(days=2)

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


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _parse_time(s: str, now: datetime) -> datetime:
    """``now``, a relative ``-6h``/``-2d``/``-30m``, or ISO-8601 (naive = UTC)."""
    if s == "now":
        return now
    if s.startswith("-"):
        val = int(s[1:-1])
        u = s[-1]
        if u == "h":
            return now - timedelta(hours=val)
        if u == "d":
            return now - timedelta(days=val)
        if u == "m":
            return now - timedelta(minutes=val)
        raise ValueError(f"Unknown unit '{u}'")
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _window(start: str, end: str):
    now = datetime.now(timezone.utc)
    return _parse_time(start, now), _parse_time(end, now)


def _connect():
    db = _db_path(config)
    if not db.exists():
        return None
    try:
        return sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error as e:
        logger.warning(f"ionogram: cannot open {db}: {e}")
        return None


def discover_channels() -> list:
    """Channels in ``L1_all_arrivals``, each with the stations heard lately.

    Walks the (channel, timestamp_utc) index: one skip-scan step per
    channel, then a bounded range per channel.  No full-table scan — the
    table grows by tens of thousands of rows a day.
    """
    con = _connect()
    if con is None:
        return []
    out = []
    try:
        present = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                              (TABLE,)).fetchone()
        if not present:
            return []
        since = (datetime.now(timezone.utc) - STATION_LOOKBACK).isoformat()
        channel = ""
        while True:
            row = con.execute(f"SELECT channel FROM {TABLE} WHERE channel > ? "
                              f"ORDER BY channel LIMIT 1", (channel,)).fetchone()
            if row is None:
                break
            channel = row[0]
            latest = con.execute(f"SELECT MAX(timestamp_utc) FROM {TABLE} WHERE channel = ?",
                                 (channel,)).fetchone()[0]
            stations = [r[0] for r in con.execute(
                f"SELECT DISTINCT station FROM {TABLE} WHERE channel = ? AND timestamp_utc >= ? "
                f"ORDER BY station", (channel, since)) if r[0]]
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
        logger.debug(f"Solar overlay failed: {e}")
        return None


def _load_all_arrivals(
    channel: str,
    ts0: int,
    ts1: int,
    rank_filter: Optional[int],
    min_snr_db: float,
    data_root: Path,
    station: Optional[str] = None,
):
    """Load L1_all_arrivals records for one channel (and station) in [ts0, ts1]."""
    arr_dir = data_root / "phase2" / channel / "all_arrivals"

    rows = {
        "minute_boundary": [],
        "station": [],
        "arrival_ms": [],
        "timing_error_ms": [],
        "model_expected_ms": [],
        "corr_snr_db": [],
        "peak_rank": [],
        "utc_second": [],
        "carrier_phase_rad": [],
        "detection_method": [],
        "sec_in_minute": [],
    }

    # Read window as ISO; minute_boundary equality filter happens in Python.
    t0_iso = _iso(datetime.fromtimestamp(ts0, tz=timezone.utc))
    t1_iso = _iso(datetime.fromtimestamp(ts1, tz=timezone.utc))

    try:
        reader = make_data_product_reader(
            data_dir=arr_dir,
            product_level='L1',
            product_name='all_arrivals',
            channel=channel,
            storage_config=getattr(config, 'storage', {}) or {},
        )
    except Exception as e:
        logger.warning(f"L1_all_arrivals reader init failed for {channel}: {e}")
        return None

    try:
        try:
            raw_rows = reader.read_time_range(start=t0_iso, end=t1_iso, station=station)
        except Exception as e:
            logger.warning(f"L1_all_arrivals read failed for {channel}: {e}")
            raw_rows = []
    finally:
        close_fn = getattr(reader, 'close', None)
        if close_fn is not None:
            try:
                close_fn()
            except Exception:
                pass

    for row in raw_rows:
        mb = row.get('minute_boundary_utc')
        if mb is None or mb < ts0 or mb > ts1:
            continue
        if station is not None and row.get('station') != station:
            continue
        snr = row.get('corr_snr_db')
        if snr is None or snr < min_snr_db:
            continue
        rank = row.get('peak_rank')
        if rank_filter is not None and rank != rank_filter:
            continue
        rows["minute_boundary"].append(mb)
        rows["station"].append(row.get('station') or '')
        rows["arrival_ms"].append(row.get('arrival_ms'))
        rows["timing_error_ms"].append(row.get('timing_error_ms'))
        rows["model_expected_ms"].append(row.get('model_expected_ms'))
        rows["corr_snr_db"].append(snr)
        rows["peak_rank"].append(rank)
        rows["utc_second"].append(row.get('utc_second'))
        rows["carrier_phase_rad"].append(row.get('carrier_phase_rad') or 0.0)
        rows["detection_method"].append(row.get('detection_method') or 'tone_correlator')
        rows["sec_in_minute"].append(row.get('sec_in_minute') or 0)

    if not rows["minute_boundary"]:
        return None

    # GPS-referenced ToF (see the module docstring for the row semantics).
    import numpy as np
    f = lambda k: np.array([np.nan if v is None else v for v in rows[k]], dtype=float)
    arr_arr = f("arrival_ms")
    te_arr = f("timing_error_ms")
    me_arr = f("model_expected_ms")
    mb_arr = np.array(rows["minute_boundary"], dtype=float)
    sec_arr = f("utc_second")
    model_rel = np.array([m in MODEL_RELATIVE for m in rows["detection_method"]])
    sec_in_min = sec_arr - mb_arr
    tof_tone = arr_arr - sec_in_min * 1000.0
    tof_model = np.where(me_arr > 0, me_arr + te_arr, np.nan)
    rows["tof_ms"] = np.where(model_rel, tof_model, tof_tone).tolist()
    rows["timing_error_ms"] = te_arr.tolist()

    return rows


def _plausible(rows, np):
    """Mask of physically plausible arrivals: finite ToF within the station's
    ceiling, a residual small enough to rule out a wrong-tick lock, and SNR
    below the self-interference cap."""
    tof = np.array(rows["tof_ms"], dtype=float)
    te = np.array(rows["timing_error_ms"], dtype=float)
    snr = np.array(rows["corr_snr_db"], dtype=float)
    tof_max = np.array([TOF_MAX_MS.get(s, 500.0) for s in rows["station"]])
    with np.errstate(invalid="ignore"):
        return (np.isfinite(tof) & (tof >= 0) & (tof <= tof_max)
                & (np.abs(te) < MAX_TIMING_ERROR_MS) & (snr <= SNR_CAP))


def _station_label(channel: str, station: Optional[str], rows) -> Optional[str]:
    if station:
        return station
    heard = sorted(set(rows["station"])) if rows else []
    return heard[0] if len(heard) == 1 else None


def _kde_contours(x, y, n_grid=60, bandwidth=None):
    """
    Compute KDE density on a grid for contour plotting.
    Returns (xi, yi, zi) as flat lists for JSON serialisation.
    """
    import numpy as np
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
        logger.debug(f"KDE failed: {e}")
        return None


def _freq_mhz(channel: str) -> float:
    try:
        return int(channel.split("_")[-1]) / 1000.0
    except ValueError:
        return 0.0


NO_CHANNELS = "no channel has rows in L1_all_arrivals"


@router.get("/arrivals")
def get_all_arrivals(
    channel: Optional[str] = Query(None, description="Channel name, e.g. SHARED_10000, WWV_20000 (default: newest)"),
    station: Optional[str] = Query(None, description="Station on the channel: WWV, WWVH or BPM (default: all)"),
    start: str = Query("-24h", description="Start time (ISO8601 or relative like '-6h')"),
    end: str = Query("now", description="End time"),
    rank: Optional[int] = Query(None, description="Filter by peak_rank (0=dominant, 1=secondary, None=all)"),
    min_snr_db: float = Query(6.0, description="Minimum correlation SNR"),
    include_kde: bool = Query(True, description="Include KDE density grid for contour plot"),
    downsample: int = Query(1, description="Keep every Nth point for scatter (1=all)"),
):
    """
    All-arrivals Time-of-Flight data for one channel (and station).

    Returns scatter data (ToF vs corr_snr_db) and optionally a KDE density
    grid for contour overlay — analogous to Gwyn Griffiths' cluster analysis
    of ToF and S+N level.

    Rank 0 = dominant (shortest) arrival; rank ≥ 1 = secondary (multi-hop).
    """
    try:
        import numpy as np

        t0, t1 = _window(start, end)
        channel = _resolve_channel(channel)
        if channel is None:
            return _no_data(None, station, t0, t1, NO_CHANNELS, n_points=0, scatter={}, kde=None)

        data_root = Path(config.data_root)
        rows = _load_all_arrivals(channel, int(t0.timestamp()), int(t1.timestamp()),
                                  rank, min_snr_db, data_root, station)

        if rows is None or not rows["minute_boundary"]:
            return _no_data(channel, station, t0, t1,
                            f"no {channel}{' ' + station if station else ''} arrivals "
                            f"above {min_snr_db:g} dB in this window",
                            n_points=0, scatter={}, kde=None)

        label = _station_label(channel, station, rows)
        valid = _plausible(rows, np)
        tof = np.array(rows["tof_ms"])[valid]
        snr = np.array(rows["corr_snr_db"])[valid]
        rank_arr = np.array(rows["peak_rank"])[valid]
        mb = np.array(rows["minute_boundary"])[valid]
        st_arr = np.array(rows["station"])[valid]

        # Downsample for scatter (KDE always uses full set)
        if downsample > 1:
            idx = np.arange(0, len(tof), downsample)
        else:
            idx = np.arange(len(tof))

        scatter = {
            "minute_boundary": mb[idx].tolist(),
            "tof_ms": tof[idx].tolist(),
            "corr_snr_db": snr[idx].tolist(),
            "peak_rank": rank_arr[idx].tolist(),
            "station": st_arr[idx].tolist(),
        }

        # KDE on full (undownsampled, capped) data — x=snr, y=tof (Griffiths convention)
        kde_result = None
        if include_kde and len(tof) >= 10:
            kde_result = _kde_contours(snr, tof, n_grid=60)

        # Summary statistics per rank
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
            "n_points": len(tof),
            "n_scatter": len(idx),
            "rank_stats": rank_stats,
            "scatter": scatter,
            "kde": kde_result,
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error in /ionogram/arrivals: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/arrivals/timeseries")
def get_arrivals_timeseries(
    channel: Optional[str] = Query(None, description="Channel name (default: newest)"),
    station: Optional[str] = Query(None, description="Station on the channel (default: all)"),
    start: str = Query("-24h", description="Start time"),
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
    empty = {"timestamps": [], "tof_ms": [], "snr_db": []}
    try:
        import numpy as np

        t0, t1 = _window(start, end)
        channel = _resolve_channel(channel)
        if channel is None:
            return _no_data(None, station, t0, t1, NO_CHANNELS,
                            dominant=dict(empty), secondary=dict(empty))

        data_root = Path(config.data_root)
        rows = _load_all_arrivals(channel, int(t0.timestamp()), int(t1.timestamp()),
                                  None, min_snr_db, data_root, station)

        if rows is None or not rows["minute_boundary"]:
            return _no_data(channel, station, t0, t1,
                            f"no {channel}{' ' + station if station else ''} arrivals "
                            f"above {min_snr_db:g} dB in this window",
                            dominant=dict(empty), secondary=dict(empty))

        label = _station_label(channel, station, rows)
        valid = _plausible(rows, np)
        tof = np.array(rows["tof_ms"])[valid]
        snr = np.array(rows["corr_snr_db"])[valid]
        rank_arr = np.array(rows["peak_rank"])[valid]
        mb = np.array(rows["minute_boundary"])[valid]

        dom = rank_arr == 0
        sec = rank_arr > 0

        # Per-minute median for dominant arrivals (cleaner than raw scatter)
        def _bin_by_minute(mb_arr, val_arr, snr_arr):
            unique_mb = np.unique(mb_arr)
            ts_out, val_out, snr_out = [], [], []
            for t in unique_mb:
                m = mb_arr == t
                ts_out.append(int(t))
                val_out.append(float(np.nanmedian(val_arr[m])))
                snr_out.append(float(np.nanmedian(snr_arr[m])))
            return ts_out, val_out, snr_out

        dom_ts, dom_tof, dom_snr = _bin_by_minute(mb[dom], tof[dom], snr[dom])
        sec_ts, sec_tof, sec_snr = _bin_by_minute(mb[sec], tof[sec], snr[sec])

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
            "dominant": {
                "timestamps": dom_ts,
                "tof_ms": dom_tof,
                "snr_db": dom_snr,
                "n": len(dom_ts),
            },
            "secondary": {
                "timestamps": sec_ts,
                "tof_ms": sec_tof,
                "snr_db": sec_snr,
                "n": len(sec_ts),
            },
            "f2_height": {
                "timestamps": h_ts,
                "height_km": h_vals,
                "n": len(h_ts),
                "note": "Approximate F2 virtual height from rank-0 vs rank-1 delay difference",
            },
            "solar": solar,
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error in /ionogram/arrivals/timeseries: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/doppler-delay")
def get_doppler_delay(
    channel: Optional[str] = Query(None, description="Channel name (default: newest)"),
    station: Optional[str] = Query(None, description="Station on the channel (default: all)"),
    start: str = Query("-6h", description="Start time"),
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
    function from channel sounding.
    """
    try:
        import numpy as np

        t0, t1 = _window(start, end)
        channel = _resolve_channel(channel)
        if channel is None:
            return _no_data(None, station, t0, t1, NO_CHANNELS, n_points=0, scatter={}, kde=None)

        data_root = Path(config.data_root)
        rows = _load_all_arrivals(channel, int(t0.timestamp()), int(t1.timestamp()),
                                  None, min_snr_db, data_root, station)

        if rows is None or not rows["minute_boundary"]:
            return _no_data(channel, station, t0, t1,
                            f"no {channel}{' ' + station if station else ''} arrivals "
                            f"above {min_snr_db:g} dB in this window",
                            n_points=0, scatter={}, kde=None)

        label = _station_label(channel, station, rows)
        te_arr = np.array(rows["timing_error_ms"], dtype=float)
        phase_arr = np.array(rows["carrier_phase_rad"], dtype=float)
        snr_arr = np.array(rows["corr_snr_db"])
        mb_arr = np.array(rows["minute_boundary"])
        sec_arr = np.array(rows["utc_second"])
        sim_arr = np.array(rows["sec_in_minute"])
        method_arr = np.array(rows["detection_method"])

        # Filter by detection method if specified
        if method:
            method_mask = np.array([m == method for m in method_arr])
        else:
            method_mask = np.ones(len(te_arr), dtype=bool)

        # Require finite values and an implausibly large residual rules out
        # a wrong-tick lock.
        valid = (
            method_mask
            & np.isfinite(te_arr)
            & np.isfinite(phase_arr)
            & (snr_arr >= min_snr_db)
            & (np.abs(te_arr) < MAX_TIMING_ERROR_MS)
        )

        te = te_arr[valid]
        phase = phase_arr[valid]
        snr = snr_arr[valid]
        mb = mb_arr[valid]
        sec = sec_arr[valid]
        sim = sim_arr[valid]
        meth = method_arr[valid]

        # Unwrap phase per minute to show Doppler trend
        # Group by minute_boundary, unwrap within each minute
        phase_unwrapped = np.copy(phase)
        for m in np.unique(mb):
            minute_mask = mb == m
            if np.sum(minute_mask) >= 3:
                phase_unwrapped[minute_mask] = np.unwrap(phase[minute_mask])

        # Compute per-minute Doppler from phase slope
        minute_doppler = {}
        for m in np.unique(mb):
            minute_mask = mb == m
            if np.sum(minute_mask) >= 5:
                t_sec = sim[minute_mask].astype(float)
                p_rad = phase_unwrapped[minute_mask]
                if t_sec[-1] - t_sec[0] > 5.0:
                    try:
                        coeffs = np.polyfit(t_sec, p_rad, 1)
                        doppler_hz = coeffs[0] / (2.0 * np.pi)
                        minute_doppler[int(m)] = float(doppler_hz)
                    except Exception:
                        pass

        scatter = {
            "minute_boundary": mb.tolist(),
            "utc_second": sec.tolist(),
            "sec_in_minute": sim.tolist(),
            "timing_error_ms": te.tolist(),
            "carrier_phase_rad": phase.tolist(),
            "phase_unwrapped_rad": phase_unwrapped.tolist(),
            "corr_snr_db": snr.tolist(),
            "detection_method": [str(m) for m in meth],
        }

        # 2D KDE: timing_error (x) vs carrier_phase (y)
        kde_result = None
        if include_kde and len(te) >= 20:
            kde_result = _kde_contours(te, phase, n_grid=50, bandwidth=None)

        # Summary statistics
        n_edge = int(np.sum(np.array([m == 'edge_tick' for m in meth])))
        n_corr = int(np.sum(np.array([m == 'tone_correlator' for m in meth])))

        return {
            "status": "ok",
            "channel": channel,
            "station": label,
            "color": STATION_COLORS.get(label, "#888888"),
            "time_range": {"start": _iso(t0), "end": _iso(t1)},
            "n_points": len(te),
            "n_edge_ticks": n_edge,
            "n_tone_correlator": n_corr,
            "scatter": scatter,
            "kde": kde_result,
            "minute_doppler": minute_doppler,
            "summary": {
                "timing_error_mean_ms": float(np.nanmean(te)) if len(te) > 0 else 0.0,
                "timing_error_std_ms": float(np.nanstd(te)) if len(te) > 0 else 0.0,
                "phase_std_rad": float(np.nanstd(phase)) if len(phase) > 0 else 0.0,
                "mean_snr_db": float(np.nanmean(snr)) if len(snr) > 0 else 0.0,
                "n_minutes_with_doppler": len(minute_doppler),
            },
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error in /ionogram/doppler-delay: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/channels")
def list_ionogram_channels():
    """Channels with rows in L1_all_arrivals, and the stations heard on each."""
    available = discover_channels()
    return {"status": "ok", "channels": available, "n_channels": len(available)}
