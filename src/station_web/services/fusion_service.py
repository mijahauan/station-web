"""
Fusion timing data service.

Provides access to L3B fusion timing estimates using DataProductReader.
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional
import logging

import numpy as np

from hamsci_dsp.io import make_data_product_reader
from station_web import heavy, projected
from station_web.config import config
from station_web.timeutil import as_utc, iso_z, utcnow

logger = logging.getLogger(__name__)

#: L3_fusion_timing carries 48 columns; the history plots these.
_HISTORY_COLUMNS = ('timestamp_utc', 'd_clock_fused_ms', 'uncertainty_ms',
                    'quality_grade', 'n_broadcasts')
_GRADE_ORDER = {'A': 0, 'B': 1, 'C': 2, 'D': 3}


class FusionService:
    """Service for accessing fusion timing data."""
    
    def __init__(self, fusion_dir: Path):
        """
        Initialize fusion service.
        
        Args:
            fusion_dir: Path to fusion data directory
        """
        self.fusion_dir = Path(fusion_dir)
        self.reader = make_data_product_reader(
            data_dir=self.fusion_dir,
            product_level='L3',
            product_name='fusion_timing',
            channel='fusion',
            storage_config=config.storage
        )
    
    def get_latest(self) -> Optional[Dict[str, Any]]:
        """
        Get latest fusion estimate.
        
        Returns:
            Dictionary with latest fusion data, or None when the last hour
            holds no row.  A read failure raises: it is a fault, not an
            empty window, and the router reports it as one.
        """
        now = utcnow()
        end_time = iso_z(now)
        start_time = iso_z(now - timedelta(hours=1))

        measurements = self.reader.read_time_range(
            start=start_time,
            end=end_time
        )
        
        if not measurements:
            logger.debug("No fusion measurements found in last hour")
            return None
        
        # Get most recent measurement
        latest = measurements[-1]
        
        # Parse stations_used
        stations_used = latest.get('stations_used', '')
        if isinstance(stations_used, str) and stations_used:
            stations_list = [s.strip() for s in stations_used.split(',') if s.strip()]
        else:
            stations_list = []
        
        # Helper to convert NaN/Inf to None for JSON serialization
        import math
        def clean_float(val):
            if val is None:
                return None
            if isinstance(val, (int, float)) and (math.isnan(val) or math.isinf(val)):
                return None
            return val
        
        # Return full detailed data for expert metrics
        return {
            'timestamp': latest.get('timestamp_utc'),
            'd_clock_ms': clean_float(latest.get('d_clock_fused_ms')),
            'd_clock_raw_ms': clean_float(latest.get('d_clock_raw_ms')),
            'uncertainty_ms': clean_float(latest.get('uncertainty_ms')),
            'statistical_uncertainty_ms': clean_float(latest.get('statistical_uncertainty_ms')),
            'systematic_uncertainty_ms': clean_float(latest.get('systematic_uncertainty_ms')),
            'propagation_uncertainty_ms': clean_float(latest.get('propagation_uncertainty_ms')),
            'quality_grade': latest.get('quality_grade'),
            'quality_flag': latest.get('quality_flag'),
            'n_broadcasts': latest.get('n_broadcasts'),
            'n_stations': latest.get('n_stations'),
            'stations_used': stations_list,
            'inter_station_spread_ms': clean_float(latest.get('inter_station_spread_ms')),
            'consistency_flag': latest.get('consistency_flag'),
            'outliers_rejected': latest.get('outliers_rejected'),
            'kalman_state': latest.get('kalman_state'),
            'calibration_applied': latest.get('calibration_applied'),
            'processing_version': latest.get('processing_version'),
            'global_solve_verified': latest.get('global_solve_verified'),
            'global_solve_consistency_ms': clean_float(latest.get('global_solve_consistency_ms')),
            'global_solve_n_obs': latest.get('global_solve_n_obs'),
            # Per-station means
            'wwv_mean_ms': clean_float(latest.get('wwv_mean_ms')),
            'wwvh_mean_ms': clean_float(latest.get('wwvh_mean_ms')),
            'bpm_mean_ms': clean_float(latest.get('bpm_mean_ms')),
            # Per-station counts
            'wwv_count': latest.get('wwv_count'),
            'wwvh_count': latest.get('wwvh_count'),
            'bpm_count': latest.get('bpm_count'),
            # Per-station intra-station std
            'wwv_intra_std_ms': clean_float(latest.get('wwv_intra_std_ms')),
            'wwvh_intra_std_ms': clean_float(latest.get('wwvh_intra_std_ms')),
            # L1 vs L2 comparison (v6.2 metrological tracking)
            'd_clock_l1_ms': clean_float(latest.get('d_clock_l1_ms')),
            'd_clock_l2_ms': clean_float(latest.get('d_clock_l2_ms')),
            'l1_l2_difference_ms': clean_float(latest.get('l1_l2_difference_ms')),
        }
    
    def get_history(
        self,
        start: datetime,
        end: datetime,
        min_quality_grade: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get fusion timing history.
        
        Args:
            start: Start datetime
            end: End datetime
            min_quality_grade: Minimum quality grade filter
            
        Returns:
            Dictionary with time series data
        """
        # Only the plotted columns, in chunks (L3_fusion_timing has 48).
        measurements = projected.read_rows('L3_fusion_timing', 'fusion', start, end,
                                           _HISTORY_COLUMNS)
        if min_quality_grade:
            # as read_time_range filters: a missing grade counts as D
            limit = _GRADE_ORDER.get(min_quality_grade, 3)
            measurements = [m for m in measurements
                            if _GRADE_ORDER.get(m.get('quality_grade') or 'D', 3) <= limit]

        # Extract arrays
        timestamps = []
        d_clock_ms = []
        uncertainty_ms = []
        quality_grades = []
        n_broadcasts = []
        
        for m in measurements:
            # A row without a fused value or its uncertainty is not a
            # measurement; skip it rather than fail the whole series (the
            # float(None) TypeError used to be swallowed into an empty one).
            if m.get('d_clock_fused_ms') is None or m.get('uncertainty_ms') is None:
                continue
            timestamps.append(m.get('timestamp_utc', ''))
            d_clock_ms.append(float(m['d_clock_fused_ms']))
            uncertainty_ms.append(float(m['uncertainty_ms']))
            quality_grades.append(m.get('quality_grade') or 'D')
            n_broadcasts.append(int(m.get('n_broadcasts') or 0))

        return {
            'timestamps': timestamps,
            'd_clock_ms': d_clock_ms,
            'uncertainty_ms': uncertainty_ms,
            'quality_grade': quality_grades,
            'n_broadcasts': n_broadcasts,
            'count': len(timestamps)
        }


# ════════════════════════════════════════════════════════════════════
# The T3 estimate, drawn honestly (Timing page, "T3 estimate of t0").
#
# L3_fusion_timing carries hf-timestd's tick-fusion estimate, one row a
# minute or faster.  The page draws a median line with a sigma band, not
# one error bar per row, so the server decimates to at most MAX_POINTS
# buckets.  Before it takes the medians it pulls out the rows that look
# like a wrong tick lock and returns them separately, as suspects.
#
# Suspect rule, decided for this page (station-web Stage 2, 2026-10-18):
#   1. A centred rolling median over ROLLING_WINDOW rows follows the
#      estimate; each row's residual r = value - rolling median.
#   2. One robust scale s for the window: 1.4826 x the median |r|, floored
#      at SCALE_FLOOR_MS so a flat series does not flag its own noise.
#   3. "lattice": |r| > LATTICE_K x s AND |r| lies within LATTICE_TOL_MS
#      of 5, 10, 15 or 20 ms.  A fusion that locks the wrong tick lands a
#      whole number of tick-lattice steps away, so a step that size reads
#      as a wrong lock, not as noise.
#   4. "outlier": any other row with |r| > OUTLIER_K x s.
# The rule labels points for the eye.  It changes no value and feeds no
# estimate; hf-timestd's own outlier rejection runs upstream, untouched.
# ════════════════════════════════════════════════════════════════════

#: points per trace the page receives, at most
MAX_POINTS = 1500
#: rows in the centred rolling median (about half an hour at one a minute)
ROLLING_WINDOW = 31
OUTLIER_K = 5.0
LATTICE_K = 3.0
LATTICE_STEP_MS = 5.0
LATTICE_STEPS = (1, 2, 3, 4)
LATTICE_TOL_MS = 1.0
SCALE_FLOOR_MS = 0.1
#: longest window the T3 history serves
T3_MAX_WINDOW = timedelta(days=7)
HEAVY_FAMILY = "t3 history"

_T3_COLUMNS = ('timestamp_utc', 'd_clock_fused_ms', 'd_clock_raw_ms', 'uncertainty_ms')


def _rolling_median(v: np.ndarray, window: int) -> np.ndarray:
    """Centred rolling median; the window shrinks at either end."""
    n = len(v)
    h = window // 2
    padded = np.concatenate([np.full(h, np.nan), v, np.full(h, np.nan)])
    view = np.lib.stride_tricks.sliding_window_view(padded, 2 * h + 1)
    return np.nanmedian(view, axis=1)


def classify_suspects(values, window: int = ROLLING_WINDOW):
    """Label each value 'ok', 'lattice' or 'outlier' (rule above).

    Returns ``(kinds, residuals_ms, scale_ms)``.
    """
    if len(values) == 0:
        return [], [], SCALE_FLOOR_MS
    v = np.asarray(values, dtype=float)
    resid = v - _rolling_median(v, window)
    scale = max(1.4826 * float(np.median(np.abs(resid))), SCALE_FLOOR_MS)
    kinds = []
    for r in np.abs(resid):
        n_steps = int(round(r / LATTICE_STEP_MS))
        on_lattice = (n_steps in LATTICE_STEPS
                      and abs(r - n_steps * LATTICE_STEP_MS) <= LATTICE_TOL_MS)
        if on_lattice and r > LATTICE_K * scale:
            kinds.append("lattice")
        elif r > OUTLIER_K * scale:
            kinds.append("outlier")
        else:
            kinds.append("ok")
    return kinds, [float(x) for x in resid], scale


def _median(xs):
    xs = [x for x in xs if x is not None]
    return float(np.median(xs)) if xs else None


def get_t3_history(start: datetime, end: datetime,
                   max_points: int = MAX_POINTS) -> Dict[str, Any]:
    """The T3 estimate over ``[start, end]``: bucket medians, suspects, calibration.

    Reads four projected columns of L3_fusion_timing under the heavy guard.
    A row without a calibrated value carries no estimate and drops out.
    """
    times: List[str] = []
    tsec: List[float] = []
    cal: List[float] = []
    raw: List[Optional[float]] = []
    sig: List[Optional[float]] = []
    with heavy.guard(HEAVY_FAMILY):
        for part in projected.iter_rows('L3_fusion_timing', 'fusion', start, end, _T3_COLUMNS):
            for ts, fused, r, u in part:
                if fused is None:
                    continue
                try:
                    t = as_utc(datetime.fromisoformat(str(ts).replace('Z', '+00:00')))
                except ValueError:
                    continue
                times.append(iso_z(t))
                tsec.append(t.timestamp())
                cal.append(float(fused))
                raw.append(None if r is None else float(r))
                sig.append(None if u is None else float(u))

    n_rows = len(cal)
    out: Dict[str, Any] = {
        'available': n_rows > 0, 'start': iso_z(start), 'end': iso_z(end),
        'n_rows': n_rows, 'n_points': 0, 'bucket_s': None,
        'time': [], 'calibrated_ms': [], 'raw_ms': [], 'sigma_ms': [],
        'suspects': {'time': [], 'value_ms': [], 'raw_ms': [], 'residual_ms': [], 'kind': []},
        'n_suspect': 0, 'suspects_truncated': False,
        'fields': {'calibrated': 'd_clock_fused_ms', 'raw': 'd_clock_raw_ms',
                   'sigma': 'uncertainty_ms'},
        'calibration': {'offset_ms': None, 'min_ms': None, 'max_ms': None, 'n': 0,
                        'definition': 'd_clock_raw_ms - d_clock_fused_ms, median over the window'},
        'classification': {'method': 'centred rolling median', 'window_points': ROLLING_WINDOW,
                           'outlier_k': OUTLIER_K, 'lattice_k': LATTICE_K,
                           'lattice_step_ms': LATTICE_STEP_MS,
                           'lattice_steps': list(LATTICE_STEPS),
                           'lattice_tol_ms': LATTICE_TOL_MS, 'scale_ms': None},
    }
    if n_rows == 0:
        out['reason'] = 'no L3_fusion_timing rows in this window'
        return out

    diffs = [r - c for r, c in zip(raw, cal) if r is not None]
    if diffs:
        out['calibration'].update(offset_ms=float(np.median(diffs)), min_ms=float(min(diffs)),
                                  max_ms=float(max(diffs)), n=len(diffs))

    kinds, resid, scale = classify_suspects(cal)
    out['classification']['scale_ms'] = scale
    sus_idx = [i for i, k in enumerate(kinds) if k != 'ok']
    out['n_suspect'] = len(sus_idx)
    if len(sus_idx) > max_points:
        stride = len(sus_idx) / max_points
        sus_idx = [sus_idx[int(j * stride)] for j in range(max_points)]
        out['suspects_truncated'] = True
    s = out['suspects']
    for i in sus_idx:
        s['time'].append(times[i])
        s['value_ms'].append(cal[i])
        s['raw_ms'].append(raw[i])
        s['residual_ms'].append(resid[i])
        s['kind'].append(kinds[i])

    keep = [i for i, k in enumerate(kinds) if k == 'ok']
    if len(keep) <= max_points:
        out['time'] = [times[i] for i in keep]
        out['calibrated_ms'] = [cal[i] for i in keep]
        out['raw_ms'] = [raw[i] for i in keep]
        out['sigma_ms'] = [sig[i] for i in keep]
    else:
        t0s = as_utc(start).timestamp()
        bucket_s = (as_utc(end).timestamp() - t0s) / max_points
        out['bucket_s'] = bucket_s
        buckets: Dict[int, List[int]] = {}
        for i in keep:                      # rows arrive in time order
            b = min(max(int((tsec[i] - t0s) // bucket_s), 0), max_points - 1)
            buckets.setdefault(b, []).append(i)
        for b, idx in buckets.items():
            out['time'].append(iso_z(datetime.fromtimestamp(t0s + (b + 0.5) * bucket_s,
                                                            tz=timezone.utc)))
            out['calibrated_ms'].append(_median([cal[i] for i in idx]))
            out['raw_ms'].append(_median([raw[i] for i in idx]))
            out['sigma_ms'].append(_median([sig[i] for i in idx]))
    out['n_points'] = len(out['time'])
    return out
