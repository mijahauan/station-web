"""
TEC (Total Electron Content) service for v6.5.0 data access.

Provides access to HF-derived TEC estimates from the
TimingConsistencyValidator and archived TEC data products via the
L3_tec SQLite table (Phase 4 cutover).
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
import logging

from station_web import projected
from station_web.config import config
from station_web.timeutil import iso_z, utcnow

logger = logging.getLogger(__name__)

from hamsci_dsp.io import make_data_product_reader, SqliteDataProductReader

# Retained for callers that still annotate the return type as
# DataProductReader; SqliteDataProductReader exposes the same
# read_time_range API.
DataProductReader = SqliteDataProductReader
DATA_READER_AVAILABLE = True

#: The ``L3_tec`` channels, in order of preference.  hamsci-physics writes
#: absolute TEC under two channels: the hourly ionospheric reanalysis
#: (``ionospheric_reanalysis.py``, timer at :05) writes ``REANALYZED``, one
#: row per station per 5-minute window; the real-time physics-fusion service
#: writes ``AGGREGATED``.  Every reader here used to hard-code
#: ``AGGREGATED``, so a station whose TEC came only from the reanalysis (B4:
#: all 1341 rows REANALYZED) showed no TEC anywhere.  Read the reanalysis
#: first; fall back to the real-time product; say which one answered.
TEC_CHANNELS: Tuple[str, ...] = ("REANALYZED", "AGGREGATED")

#: How old the newest row may be and still count as "current".  The
#: reanalysis writes the previous hour at :05, so its newest row is up to
#: ~70 minutes old just before the next run.
CURRENT_MAX_AGE = timedelta(hours=2)


#: The L3_tec columns any reader here uses (TECService and the
#: propagation TEC summary).
TEC_COLUMNS: Tuple[str, ...] = ('timestamp_utc', 'minute_boundary', 'station', 'tec_tecu',
                                'confidence', 'propagation_mode', 'residuals_ms',
                                'quality_flag', 'n_frequencies')


def read_tec_rows(start: datetime, end: datetime) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """``L3_tec`` rows in ``[start, end]`` from the first channel that has any.

    Returns ``(rows, channel)``; ``([], None)`` when no channel holds a row
    in the window.  A read failure raises — the caller must not report a
    fault as an empty window.
    """
    for channel in TEC_CHANNELS:
        rows = projected.read_rows('L3_tec', channel, start, end, TEC_COLUMNS)
        if rows:
            return rows, channel
    return [], None


class TECService:
    """
    Service for accessing TEC (Total Electron Content) data products.
    
    TEC is derived from multi-frequency HF observations using the
    ionospheric dispersion relation: Δτ = K·TEC·(1/f₁² - 1/f₂²)
    
    Data: the ``L3_tec`` SQLite table, channel ``REANALYZED`` (preferred)
    or ``AGGREGATED`` — see ``TEC_CHANNELS``.
    """
    
    def __init__(self, data_root: Path):
        """
        Initialize TEC service.
        
        Args:
            data_root: Root directory for data products
        """
        self.data_root = Path(data_root)
        self.tec_dir = self.data_root / 'phase2' / 'science' / 'tec'
        
    def get_current_tec(self) -> Optional[Dict[str, Any]]:
        """
        Get the most recent TEC estimate per station.

        "Current" means the newest row within ``CURRENT_MAX_AGE``.  The
        window used to be 5 minutes, which an hourly product can never
        satisfy.

        Returns:
            Dictionary with the latest TEC per station and the ``source``
            channel, or None if no row is that recent.
        """
        now = utcnow()
        measurements, source = read_tec_rows(now - CURRENT_MAX_AGE, now)
        if not measurements:
            logger.debug("No TEC measurements within %s", CURRENT_MAX_AGE)
            return None

        # Most recent measurement per station
        latest_by_station: Dict[str, Dict[str, Any]] = {}
        for m in measurements:
            station = m.get('station', 'UNKNOWN')
            ts = m.get('timestamp_utc') or ''
            if station not in latest_by_station or ts >= latest_by_station[station].get('timestamp_utc', ''):
                latest_by_station[station] = m

        return {
            'timestamp': iso_z(now),
            'source': source,
            'stations': {
                station: {
                    'tec_tecu': m.get('tec_tecu'),
                    'confidence': m.get('confidence'),
                    'mode': m.get('propagation_mode') or 'UNKNOWN',
                    'timestamp_utc': m.get('timestamp_utc'),
                    'unix_timestamp': m.get('minute_boundary'),
                }
                for station, m in latest_by_station.items()
            },
            'n_stations': len(latest_by_station)
        }
    
    def get_tec_history(
        self,
        start: datetime,
        end: datetime,
        station: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get TEC history for a time range.
        
        Args:
            start: Start time (naive is taken as UTC)
            end: End time (naive is taken as UTC)
            station: Optional station filter (e.g., 'WWV', 'BPM')
        
        Returns:
            Dictionary with timestamps, TEC values per station, and the
            ``source`` channel (None when the window is empty).
        """
        result = {
            'timestamps': [],
            'stations': {},  # station -> {'tec_tecu': [], 'confidence': [], 'mode': [], 'timestamps': []}
            'n_points': 0,
            'source': None,
            'time_range': {'start': iso_z(start), 'end': iso_z(end)},
        }

        measurements, source = read_tec_rows(start, end)
        result['source'] = source
        if not measurements:
            return result

        timestamps = set()
        for m in measurements:
            station_name = m.get('station', 'UNKNOWN')
            if station and station_name != station:
                continue

            # L3_tec stores 'timestamp_utc' (hamsci-dsp schema);
            # 'timestamp_iso' never existed, so every timestamp
            # list came back empty.
            ts_iso = m.get('timestamp_utc', '')
            if ts_iso:
                timestamps.add(ts_iso)

            entry = result['stations'].setdefault(station_name, {
                'tec_tecu': [], 'confidence': [], 'mode': [], 'timestamps': []})
            entry['tec_tecu'].append(m.get('tec_tecu'))
            entry['confidence'].append(m.get('confidence'))
            # The schema field is 'propagation_mode'; 'mode' never existed.
            entry['mode'].append(m.get('propagation_mode') or 'UNKNOWN')
            entry['timestamps'].append(ts_iso)

        result['timestamps'] = sorted(timestamps)
        result['n_points'] = sum(len(s['tec_tecu']) for s in result['stations'].values())
        return result

    def get_tec_by_station(self, station: str, hours: int = 24) -> Dict[str, Any]:
        """
        Get TEC data for a specific station.
        
        Args:
            station: Station name (WWV, WWVH, BPM)
            hours: Number of hours of history
        
        Returns:
            Dictionary with TEC data for the specified station
        """
        end = utcnow()
        start = end - timedelta(hours=hours)
        return self.get_tec_history(start, end, station=station)


# ── dTEC: SHARED carriers, the noise floor, the reference comparison ─────

#: A frequency more than one catalogue station broadcasts on reaches the
#: receiver as ONE composite carrier (WWV + WWVH + BPM on 2.5-15 MHz).  Its
#: per-station dTEC rows measure that one carrier, so they merge into one
#: series under this label.
COMPOSITE_STATION = 'composite'
COMPOSITE_LABEL = 'composite: WWV + WWVH + BPM'
#: Fewer points than this per series give no noise estimate.
NOISE_MIN_POINTS = 10
NOISE_UNIT = 'TECU/s'
NOISE_METHOD = ("1.4826 x MAD of minute-to-minute differences of dtec_rate_tecu_per_s, "
                "divided by sqrt(2); median over series")
#: The reference comparison holds the composite at this frequency against the
#: single-station paths, when both exist.
REFERENCE_MHZ = 10.0


def shared_frequencies() -> set:
    """Frequencies (MHz) that more than one catalogue station broadcasts on."""
    from station_web.broadcast import DEFAULT_STATIONS
    owners: Dict[float, int] = {}
    for st in DEFAULT_STATIONS:
        for f in st.frequencies_mhz:
            k = round(float(f), 3)
            owners[k] = owners.get(k, 0) + 1
    return {f for f, n in owners.items() if n > 1}


def _median(vals):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    n = len(vals)
    return vals[n // 2] if n % 2 else 0.5 * (vals[n // 2 - 1] + vals[n // 2])


def merge_shared_dtec(series: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """One series per SHARED frequency; single-station series pass through.

    Each series holds per-minute lists (timestamps, dtec_rate_tecu_per_s,
    dtec_mean_tecu, mean_snr_db, is_anchored).  On a shared frequency the
    stations' rows for one minute merge into one point: the median of the
    rates and of the means, the best SNR, anchored if any row was.
    """
    shared = shared_frequencies()
    out: Dict[str, Dict[str, Any]] = {}
    pools: Dict[float, Dict[int, list]] = {}
    heard: Dict[float, set] = {}
    for key, s in series.items():
        f = round(float(s['frequency_mhz']), 3)
        if f not in shared:
            out[key] = {**s, 'composite': False, 'label': f"{s['station']} {f:.1f} MHz",
                        'stations_heard': [s['station']]}
            continue
        heard.setdefault(f, set()).add(s['station'])
        by_min = pools.setdefault(f, {})
        for i, t in enumerate(s['timestamps']):
            by_min.setdefault(t, []).append(
                (s['dtec_rate_tecu_per_s'][i], s['dtec_mean_tecu'][i],
                 s['mean_snr_db'][i], s['is_anchored'][i]))
    for f, by_min in pools.items():
        ts = sorted(by_min)
        out[f"SHARED_{f:.1f}MHz"] = {
            'station': COMPOSITE_STATION,
            'frequency_mhz': float(f),
            'composite': True,
            'label': f"{COMPOSITE_LABEL} {f:.1f} MHz",
            'stations_heard': sorted(heard[f]),
            'timestamps': ts,
            'dtec_rate_tecu_per_s': [_median([p[0] for p in by_min[t]]) for t in ts],
            'dtec_mean_tecu': [_median([p[1] for p in by_min[t]]) for t in ts],
            'mean_snr_db': [max(p[2] for p in by_min[t]) for t in ts],
            'is_anchored': [any(p[3] for p in by_min[t]) for t in ts],
        }
    return out


def dtec_noise_floor(series: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """The white-noise floor of the dTEC rate, in the chart's unit (TECU/s).

    Per series, minute-sorted: the difference of consecutive minutes cancels
    a slow disturbance (a TID moves the rate over tens of minutes) and keeps
    the noise, sqrt(2) times larger.  The MAD keeps the odd cycle slip from
    inflating it.  The floor reported is the median over series.
    """
    floors = []
    for s in series.values():
        pts = sorted((t, r) for t, r in zip(s['timestamps'], s['dtec_rate_tecu_per_s'])
                     if r is not None)
        diffs = [b[1] - a[1] for a, b in zip(pts, pts[1:]) if 0 < b[0] - a[0] <= 90]
        if len(diffs) < NOISE_MIN_POINTS - 1:
            continue
        med = _median(diffs)
        mad = _median([abs(d - med) for d in diffs])
        floors.append(1.4826 * mad / 2 ** 0.5)
    base = {'unit': NOISE_UNIT, 'method': NOISE_METHOD, 'n_series': len(floors)}
    if not floors:
        return {**base, 'value': None,
                'reason': f"no series holds {NOISE_MIN_POINTS} consecutive minutes"}
    return {**base, 'value': _median(floors), 'reason': None}


def reference_comparison(series: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """The keys the reference comparison draws: distinct-frequency paths only.

    WWV, WWVH and BPM at 10 MHz share one carrier, so comparing them proves
    nothing.  The comparison holds the 10 MHz composite (or, failing that,
    the nearest composite) against the single-station paths (WWV 20 and
    25 MHz).  It needs one of each; otherwise the panel drops, with a reason.
    """
    singles = sorted((k for k, s in series.items() if not s.get('composite')),
                     key=lambda k: series[k]['frequency_mhz'])
    comps = sorted((k for k, s in series.items() if s.get('composite')),
                   key=lambda k: abs(series[k]['frequency_mhz'] - REFERENCE_MHZ))
    if not singles or not comps:
        missing = "no single-station path" if not singles else "no composite carrier"
        return {'available': False, 'keys': [], 'reference_mhz': None,
                'reason': ("the comparison needs a composite carrier and a single-station "
                           f"path on distinct frequencies; this window holds {missing}")}
    return {'available': True, 'keys': [comps[0]] + singles, 'reason': None,
            'reference_mhz': series[comps[0]]['frequency_mhz']}
