"""
Propagation analysis service for ionospheric and propagation mode data.
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from collections import Counter, OrderedDict
import logging
import re
import sqlite3
import statistics
import threading
import time

from hamsci_dsp.io import make_data_product_reader
from station_web.broadcast import DEFAULT_STATIONS
from station_web.config import config
from station_web import heavy, projected
from station_web.services.channels import _db_path, discover_channels
from station_web.services.tec_service import read_tec_rows
from station_web.timeutil import as_utc, iso_z, utcnow

logger = logging.getLogger(__name__)

#: How long a computed timeline is reused.  station.html and the Observatory
#: tab ask for the same day several times a minute; a minute-old timeline
#: of per-minute data is still current.
TIMELINE_TTL_S = 60.0
#: Slack on the tick_phase ``timestamp_utc`` window.  Rows are selected by
#: ``minute_boundary_utc``, but only ``(channel, timestamp_utc)`` is indexed,
#: so the time window narrows the scan and the minute boundary decides.
#: A row is written within seconds of its minute; 120 s covers that.
TICK_PHASE_SLACK_S = 120

#: At most this many timelines are kept.  Any client can mint new keys
#: (arbitrary windows), so the cache is a bounded LRU, not a growing dict.
TIMELINE_CACHE_MAX = 16
#: The longest window the timeline route serves.  station.html asks for at
#: most 168 h; a longer request is refused rather than read.
TIMELINE_MAX_WINDOW = timedelta(days=7)

#: The columns the timeline reads from each L2 product.
_TIMELINE_COLUMNS = {
    'timing_measurements': ('timestamp_utc', 'minute_boundary_utc', 'station', 'frequency_mhz',
                            'doppler_hz', 'propagation_mode', 'snr_db', 'raw_arrival_time_ms'),
    'tick_timing': ('timestamp_utc', 'minute_boundary_utc', 'station', 'frequency_mhz',
                    'mean_snr_db', 'doppler_hz', 'd_clock_ms'),
}

_timeline_cache: "OrderedDict[tuple, Tuple[float, Optional[Dict[str, Any]]]]" = OrderedDict()
_timeline_lock = threading.Lock()


def known_stations() -> List[str]:
    """Broadcast stations a timeline may be filtered by (the catalogue)."""
    return [st.name for st in DEFAULT_STATIONS]


def timeline_cache_size() -> int:
    with _timeline_lock:
        return len(_timeline_cache)


def clear_timeline_cache() -> None:
    """Forget every cached timeline (tests; a reconfigured process)."""
    with _timeline_lock:
        _timeline_cache.clear()


def _station_frequencies() -> Dict[str, List[float]]:
    """station -> broadcast frequencies (MHz), from the broadcast catalogue."""
    return {st.name: list(st.frequencies_mhz) for st in DEFAULT_STATIONS}


def _single_station_frequencies() -> Dict[float, str]:
    """frequency (MHz) -> the one station that broadcasts there."""
    owners: Dict[float, List[str]] = {}
    for st in DEFAULT_STATIONS:
        for f in st.frequencies_mhz:
            owners.setdefault(f, []).append(st.name)
    return {f: names[0] for f, names in owners.items() if len(names) == 1}


def _median_snr_by_minute(db: Path, channel: str, start: datetime, end: datetime,
                          station: Optional[str]) -> Dict[tuple, float]:
    """``(minute_boundary_utc, station) -> median tick_phase snr_db``.

    One indexed query per channel over ``[start-120 s, end+120 s]``,
    projecting three columns and streaming them in (station, minute) order,
    so memory holds one minute's ~55 windows at a time.
    """
    if not db.exists():
        return {}
    lo = iso_z(start - timedelta(seconds=TICK_PHASE_SLACK_S)).replace("Z", "+00:00")
    hi = iso_z(end + timedelta(seconds=TICK_PHASE_SLACK_S)).replace("Z", "+00:00")
    sql = ('SELECT minute_boundary_utc, station, snr_db FROM "L2_tick_phase" '
           'WHERE channel = ? AND timestamp_utc BETWEEN ? AND ? '
           'AND minute_boundary_utc BETWEEN ? AND ? AND snr_db IS NOT NULL')
    args: list = [channel, lo, hi, int(start.timestamp()), int(end.timestamp())]
    if station:
        sql += ' AND station = ?'
        args.append(station)
    sql += ' ORDER BY station, minute_boundary_utc'
    out: Dict[tuple, float] = {}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5.0)
    try:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                           "AND name='L2_tick_phase'").fetchone():
            return {}
        key, vals = None, []
        for mb, st, snr in con.execute(sql, args):
            k = (int(mb), st)
            if k != key:
                if vals:
                    out[key] = statistics.median(vals)
                key, vals = k, []
            vals.append(float(snr))
        if vals:
            out[key] = statistics.median(vals)
    finally:
        con.close()
    return out


class PropagationService:
    """Service for accessing propagation and ionospheric data."""

    # Valid station/frequency combinations (MHz), from the broadcast
    # catalogue (station_web.broadcast.DEFAULT_STATIONS) rather than a
    # hand-kept copy — the copy still listed CHU after CHU left the fleet.
    VALID_BROADCASTS = _station_frequencies()

    # Station-specific frequencies (no discrimination needed)
    STATION_SPECIFIC = _single_station_frequencies()

    # Shared frequencies where discrimination is required
    SHARED_FREQUENCIES = sorted({f for fs in VALID_BROADCASTS.values() for f in fs}
                                - set(STATION_SPECIFIC))

    def __init__(self, data_root: Path):
        """
        Initialize propagation service.

        Args:
            data_root: Root directory for data products
        """
        self.data_root = Path(data_root)
        self.phase2_dir = self.data_root / 'phase2'
        self.science_dir = self.phase2_dir / 'science'
        self.tec_dir = self.science_dir / 'tec'
        self.prop_stats_dir = self.science_dir / 'propagation_stats'

    def _channels(self) -> List[str]:
        """Receiver channels that produced data (L2_timing_measurements),
        not the directories under phase2/ (ionex, WWVB_60, science, ...)."""
        return [c["name"] for c in discover_channels(config)]

    def _sanitize_value(self, val: Any) -> Any:
        """Sanitize values for JSON serialization (convert NaN/Inf to None, NumPy to Python types)."""
        import math
        import numpy as np

        if val is None:
            return None

        # Handle NumPy types
        if isinstance(val, (np.floating, np.integer)):
            val = val.item()
        elif isinstance(val, np.ndarray):
            return [self._sanitize_value(x) for x in val.tolist()]

        # Handle NaN/Inf
        if isinstance(val, (float, int)):
            if not math.isfinite(val):
                return None
        else:
            # Check if it's still some weird type that might convert to non-finite
            try:
                if hasattr(val, '__float__') and not math.isfinite(float(val)):
                    return None
            except:
                pass

        return val

    def _deep_sanitize(self, obj: Any) -> Any:
        """Recursively sanitize dicts and lists for JSON serialization."""
        if isinstance(obj, dict):
            return {str(k): self._deep_sanitize(v) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [self._deep_sanitize(x) for x in obj]
        else:
            return self._sanitize_value(obj)

    def _is_valid_broadcast(self, station: str, frequency_mhz: float) -> bool:
        """
        Check if station/frequency combination is valid.

        Args:
            station: Station callsign
            frequency_mhz: Frequency in MHz

        Returns:
            True if valid broadcast, False if impossible combination
        """
        if station not in self.VALID_BROADCASTS:
            return True  # Unknown station, allow it

        valid_freqs = self.VALID_BROADCASTS[station]
        # Check if frequency is close to any valid frequency (within 0.1 MHz)
        return any(abs(frequency_mhz - valid_freq) < 0.1 for valid_freq in valid_freqs)

    def get_current_conditions(self) -> Optional[Dict[str, Any]]:
        """
        Get current propagation conditions with per-broadcast analysis.

        Returns propagation modes organized by station/frequency pairs,
        showing path-specific characteristics.
        """
        # Get L2 timing measurements from last hour
        end_time = utcnow()
        start_time = end_time - timedelta(hours=1)

        # Collect measurements from all channels.  A read failure
        # propagates: the router reports it as a fault, not "no data".
        all_measurements = []
        channels = self._channels()
        for channel in channels:
            reader = make_data_product_reader(
                data_dir=self.phase2_dir / channel,
                product_level='L2',
                product_name='timing_measurements',
                channel=channel,
                storage_config=config.storage
            )
            try:
                all_measurements.extend(reader.read_time_range(
                    start=iso_z(start_time), end=iso_z(end_time)))
            finally:
                reader.close()

        logger.debug(f"Channels scanned: {len(channels)}, measurements: {len(all_measurements)}")

        if not all_measurements:
            return None

        # Analyze per-broadcast (station + frequency)
        broadcast_stats = {}

        for m in all_measurements:
            station = m.get('station', 'UNKNOWN')
            freq = m.get('frequency_mhz') or 0
            mode = m.get('propagation_mode', 'UNKNOWN')
            snr = m.get('snr_db')
            timestamp = m.get('timestamp_utc')

            # Validate station/frequency combination
            if not self._is_valid_broadcast(station, freq):
                logger.debug(f"Rejecting invalid broadcast: {station} at {freq:.2f} MHz")
                continue

            # Create broadcast key
            broadcast_key = f"{station}_{freq:.1f}"

            if broadcast_key not in broadcast_stats:
                broadcast_stats[broadcast_key] = {
                    'station': station,
                    'frequency_mhz': freq,
                    'mode_counts': Counter(),
                    'snr_values': [],
                    'n_measurements': 0
                }

            broadcast_stats[broadcast_key]['mode_counts'][mode] += 1
            broadcast_stats[broadcast_key]['n_measurements'] += 1

            if snr is not None:
                broadcast_stats[broadcast_key]['snr_values'].append(snr)


        # Calculate per-broadcast statistics
        broadcasts = []
        for key, stats in broadcast_stats.items():
            total = stats['n_measurements']
            mode_probs = {mode: count/total for mode, count in stats['mode_counts'].items()}
            dominant_mode = stats['mode_counts'].most_common(1)[0][0] if stats['mode_counts'] else 'UNKNOWN'

            avg_snr = self._sanitize_value(sum(stats['snr_values']) / len(stats['snr_values']) if stats['snr_values'] else None)

            broadcasts.append({
                'station': stats['station'],
                'frequency_mhz': self._sanitize_value(stats['frequency_mhz']),
                'dominant_mode': dominant_mode,
                'mode_distribution': dict(stats['mode_counts']),
                'mode_probabilities': {m: self._sanitize_value(p) for m, p in mode_probs.items()},
                'n_measurements': total,
                'avg_snr_db': avg_snr
            })

        # Sort by frequency
        broadcasts.sort(key=lambda x: x['frequency_mhz'])

        # Estimate current MUF from per-broadcast aggregated stats.
        # Require: (1) dominant mode is F-layer (regex: digit + F),
        #          (2) avg SNR >= 15 dB (real signal, not noise),
        #          (3) at least 3 measurements (not a fluke).
        f_layer_pattern = re.compile(r'^\d+F')
        f_layer_freqs = []
        for b in broadcasts:
            dom = b.get('dominant_mode', '')
            if (f_layer_pattern.match(dom)
                    and (b.get('avg_snr_db') or 0) >= 15.0
                    and b.get('n_measurements', 0) >= 3):
                f_layer_freqs.append(b['frequency_mhz'])

        muf_estimate = None
        if f_layer_freqs:
            # MUF ≈ 1.15 × highest credible F-layer frequency
            muf_estimate = self._sanitize_value(max(f_layer_freqs) * 1.15)

        # Check for reanalyzed MUF from ionospheric reanalysis service.
        # The reanalysis applies physics-based mode validation (foF2,
        # oblique MUF, SNR gating) and produces a more reliable estimate.
        reanalyzed_muf = self._get_reanalyzed_muf(start_time, end_time)

        # Prefer reanalyzed MUF when available
        best_muf = reanalyzed_muf if reanalyzed_muf is not None else muf_estimate

        result = self._deep_sanitize({
            'timestamp': iso_z(end_time),
            'time_span_hours': 1.0,
            'n_measurements': len(all_measurements),
            'broadcasts': broadcasts,
            'muf_estimate_mhz': best_muf,
            'muf_realtime_mhz': muf_estimate,
            'muf_reanalyzed_mhz': reanalyzed_muf,
            'n_broadcasts': len(broadcasts)
        })

        return result

    def _get_reanalyzed_muf(
        self,
        start_time: datetime,
        end_time: datetime
    ) -> Optional[float]:
        """
        Read the most recent reanalyzed MUF from L3C propagation stats.

        The ionospheric reanalysis service writes hourly L3C records with
        physics-validated MUF estimates. Returns the highest non-null MUF
        from the most recent reanalysis hour, or None if unavailable.
        """
        reanalysis_dir = self.phase2_dir / 'science' / 'propagation_stats'
        reader = make_data_product_reader(
            data_dir=reanalysis_dir,
            product_level='L3C',
            product_name='propagation_stats',
            channel='REANALYSIS',
            use_registry=False,
            storage_config=config.storage
        )

        try:
            records = reader.read_time_range(
                start=iso_z(start_time), end=iso_z(end_time))
        finally:
            reader.close()

        if not records:
            return None

        # Collect all non-null MUF estimates from the reanalysis period
        muf_values = []
        for r in records:
            muf = r.get('estimated_muf_mhz')
            if muf is not None and muf > 0:
                muf_values.append(float(muf))

        if not muf_values:
            return None

        # Return the maximum reanalyzed MUF
        return self._sanitize_value(max(muf_values))

    def get_mode_timeline(
        self,
        start: datetime,
        end: datetime,
        station: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Get propagation mode timeline with best-available observables per source:

          - d_clock_ms:  tick_timing (TickEdgeDetector ensemble)
          - snr_db:      median of tick_phase windows in the minute (TMF window
                         correlator, ~55 windows/min, 20-50 dB dynamic range
                         vs 7-11 dB from tick_timing/mean_snr_db)
          - doppler_hz:  timing_measurements (minute-marker carrier phase
                         slope, 60 s baseline)

        All three are joined on (minute_boundary_utc, station). tick_timing
        drives record enumeration; tick_phase SNR and timing_measurements
        Doppler/mode are looked up by key and substituted where available.
        Falls back to timing_measurements for channels without tick_timing.

        Channels come from the products (``discover_channels``).  The SNR
        median is computed in one projected, indexed SQL pass per channel
        (``_median_snr_by_minute``), and the result is cached for
        ``TIMELINE_TTL_S``.  Returns None for a window with no rows; a read
        failure raises.
        """
        start, end = as_utc(start), as_utc(end)
        db = _db_path(config)
        key = (str(db), int(start.timestamp()) // 60, int(end.timestamp()) // 60, station)
        now_mono = time.monotonic()
        with _timeline_lock:
            hit = _timeline_cache.get(key)
            if hit and now_mono - hit[0] < TIMELINE_TTL_S:
                _timeline_cache.move_to_end(key)
                return hit[1]

        # One timeline computation at a time: each reads up to a week of
        # rows per channel.  A twin request may have filled the cache while
        # this one waited.
        with heavy.guard("timeline"):
            with _timeline_lock:
                hit = _timeline_cache.get(key)
                if hit and time.monotonic() - hit[0] < TIMELINE_TTL_S:
                    _timeline_cache.move_to_end(key)
                    return hit[1]
            result = self._compute_mode_timeline(db, start, end, station)

        with _timeline_lock:
            for k in [k for k, (t, _) in _timeline_cache.items()
                      if now_mono - t >= TIMELINE_TTL_S]:
                del _timeline_cache[k]
            _timeline_cache[key] = (now_mono, result)
            _timeline_cache.move_to_end(key)
            while len(_timeline_cache) > TIMELINE_CACHE_MAX:
                _timeline_cache.popitem(last=False)
        return result

    def _compute_mode_timeline(self, db: Path, start: datetime, end: datetime,
                               station: Optional[str]) -> Optional[Dict[str, Any]]:
        import math

        def _safe_float(val):
            """Convert to float, returning None for NaN/Inf/invalid."""
            if val is None:
                return None
            try:
                v = float(val)
                if math.isnan(v) or math.isinf(v):
                    return None
                return v
            except (TypeError, ValueError):
                return None

        def _read(product: str, channel: str) -> List[Dict[str, Any]]:
            # only the columns the timeline uses, in chunks
            return projected.read_rows(f'L2_{product}', channel, start, end,
                                       _TIMELINE_COLUMNS[product], station=station)

        all_records = []
        for channel in self._channels():
            # (minute_boundary_utc, station) -> median snr_db of the minute
            snr_lookup = _median_snr_by_minute(db, channel, start, end, station)

            # Doppler + mode from timing_measurements, keyed the same way.
            # 60-second baseline gives ~0.017 Hz resolution vs ~1 Hz from
            # the 57-tick linear fit in tick_timing.
            doppler_lookup: Dict[tuple, float] = {}
            mode_lookup: Dict[tuple, str] = {}
            co_records = _read('timing_measurements', channel)
            for co in co_records:
                mb = co.get('minute_boundary_utc')
                st = co.get('station', '')
                if mb is not None and st:
                    k = (int(mb), st)
                    dop = _safe_float(co.get('doppler_hz'))
                    if dop is not None:
                        doppler_lookup[k] = dop
                    mode = co.get('propagation_mode')
                    if mode and mode != 'UNKNOWN':
                        mode_lookup[k] = mode

            tick_records = _read('tick_timing', channel)
            if tick_records:
                for t in tick_records:
                    mb = t.get('minute_boundary_utc')
                    st = t.get('station', 'UNKNOWN')
                    k = (int(mb), st) if mb is not None else None
                    # SNR: prefer tick_phase median; fall back to tick_timing
                    snr = snr_lookup.get(k) if k else None
                    if snr is None:
                        snr = _safe_float(t.get('mean_snr_db'))
                    # Doppler: prefer the 60 s baseline; fall back to tick_timing
                    dop = doppler_lookup.get(k) if k else None
                    if dop is None:
                        dop = _safe_float(t.get('doppler_hz'))
                    all_records.append({
                        'timestamp_utc': t.get('timestamp_utc'),
                        'station': st,
                        'frequency_mhz': t.get('frequency_mhz', 0),
                        'snr_db': snr,
                        'd_clock_ms': _safe_float(t.get('d_clock_ms')),
                        'doppler_hz': dop,
                        'propagation_mode': mode_lookup.get(k, 'UNKNOWN') if k else 'UNKNOWN',
                    })
            else:
                # Fallback: timing_measurements for channels without tick_timing
                for m in co_records:
                    mb = m.get('minute_boundary_utc')
                    st = m.get('station', 'UNKNOWN')
                    k = (int(mb), st) if mb is not None else None
                    snr = snr_lookup.get(k) if k else None
                    if snr is None:
                        snr = _safe_float(m.get('snr_db'))
                    all_records.append({
                        'timestamp_utc': m.get('timestamp_utc'),
                        'station': st,
                        'frequency_mhz': m.get('frequency_mhz', 0),
                        'snr_db': snr,
                        'd_clock_ms': _safe_float(m.get('raw_arrival_time_ms')),
                        'doppler_hz': _safe_float(m.get('doppler_hz')),
                        'propagation_mode': m.get('propagation_mode', 'UNKNOWN'),
                    })

        if not all_records:
            return None

        all_records.sort(key=lambda r: r.get('timestamp_utc') or '')

        return self._deep_sanitize({
            'timestamps': [r['timestamp_utc'] for r in all_records],
            'modes': [r['propagation_mode'] for r in all_records],
            'stations': [r['station'] for r in all_records],
            'frequencies': [r['frequency_mhz'] for r in all_records],
            'snr_db': [r['snr_db'] for r in all_records],
            'd_clock_ms': [r['d_clock_ms'] for r in all_records],
            'doppler_hz': [r['doppler_hz'] for r in all_records],
            'count': len(all_records),
        })

    def get_tec_summary(
        self,
        start: datetime,
        end: datetime
    ) -> Optional[Dict[str, Any]]:
        """
        Get TEC (Total Electron Content) by path.

        Returns per-station TEC time series for each broadcast path, read
        from ``L3_tec`` channel REANALYZED, else AGGREGATED; ``source``
        names the one that answered.  Only the requested window: the old
        silent fallback to "the last 7 days" let a past date show
        unrelated TEC.

        Args:
            start: Start time
            end: End time

        Returns:
            TEC measurements organized by station/path, or None for an
            empty window.  A read failure raises.
        """
        measurements, source = read_tec_rows(start, end)
        if not measurements:
            return None

        # Organize by station/path with uncertainty
        paths = {}
        for m in measurements:
            station = m.get('station', 'UNKNOWN')
            tec = m.get('tec_tecu')
            timestamp = m.get('timestamp_utc')
            # residuals_ms: the fit's RMS residual, in milliseconds.  It used to
            # ship as "uncertainty_tecu", a unit it never had.  L3_tec carries
            # no TEC uncertainty in TECU.
            residual_ms = m.get('residuals_ms')
            confidence = m.get('confidence')
            quality = m.get('quality_flag', 'UNKNOWN')
            n_freqs = m.get('n_frequencies', 0)

            if tec is None or station == 'UNKNOWN':
                continue

            if station not in paths:
                paths[station] = {
                    'timestamps': [],
                    'tec_tecu': [],
                    'residual_rms_ms': [],
                    'confidence': [],
                    'quality': [],
                    'n_frequencies': []
                }

            paths[station]['timestamps'].append(timestamp)
            paths[station]['tec_tecu'].append(self._sanitize_value(tec))
            paths[station]['residual_rms_ms'].append(self._sanitize_value(residual_ms))
            paths[station]['confidence'].append(self._sanitize_value(confidence))
            paths[station]['quality'].append(quality)
            paths[station]['n_frequencies'].append(int(n_freqs) if n_freqs else 0)

        if not paths:
            return None

        # Calculate statistics per path
        for station, data in paths.items():
            if data['tec_tecu']:
                data['mean_tec'] = sum(data['tec_tecu']) / len(data['tec_tecu'])
                data['min_tec'] = min(data['tec_tecu'])
                data['max_tec'] = max(data['tec_tecu'])
                data['count'] = len(data['tec_tecu'])

                rms = [r for r in data['residual_rms_ms'] if r is not None]
                if rms:
                    data['mean_residual_rms_ms'] = sum(rms) / len(rms)

                # Count quality measurements
                good_count = sum(1 for q in data['quality'] if q == 'GOOD')
                data['quality_ratio'] = good_count / len(data['quality']) if data['quality'] else 0

        result = self._deep_sanitize({
            'paths': paths,
            'stations': list(paths.keys()),
            'source': source,
            'tec_uncertainty': 'not published',
            'total_measurements': sum(len(p['tec_tecu']) for p in paths.values())
        })

        return result

    def get_test_signal_summary(
        self,
        start: datetime,
        end: datetime
    ) -> Optional[Dict[str, Any]]:
        """
        Get WWV/WWVH test signal analysis data.

        Returns test signal detections and ionospheric metrics from minutes 8 (WWV)
        and 44 (WWVH) with per-frequency field strength, scintillation, and anomalies.

        Args:
            start: Start time
            end: End time

        Returns:
            Test signal measurements organized by channel/frequency
        """
        import csv
        from collections import defaultdict

        start, end = as_utc(start), as_utc(end)
        all_measurements = []

        # Read test signal CSV files from each channel directory
        for channel_name in self._channels():
            channel_dir = self.phase2_dir / channel_name / 'test_signal'

            if not channel_dir.exists():
                continue

            # Find CSV files in date range
            csv_files = sorted(channel_dir.glob('*_test_signal_*.csv'))

            for csv_file in csv_files:
                try:
                    with open(csv_file, 'r') as f:
                        reader = csv.DictReader(f)
                        for row in reader:
                            # Parse timestamp
                            timestamp_str = row.get('timestamp_utc', '')
                            if not timestamp_str:
                                continue

                            try:
                                ts = as_utc(datetime.fromisoformat(timestamp_str.replace('Z', '+00:00')))
                            except ValueError:
                                continue

                            # Filter by time range
                            if ts < start or ts > end:
                                continue

                            # Parse fields
                            detected = int(row.get('detected', 0)) == 1
                            if not detected:
                                continue

                            minute_num = int(row.get('minute_number', 0))
                            station = row.get('station', '')

                            # Extract frequency from channel name
                            freq_mhz = None
                            if 'SHARED' in channel_name:
                                freq_str = channel_name.replace('SHARED_', '')
                                freq_mhz = float(freq_str) / 1000.0
                            elif 'WWV' in channel_name:
                                freq_str = channel_name.replace('WWV_', '')
                                freq_mhz = float(freq_str) / 1000.0

                            measurement = {
                                'timestamp_utc': timestamp_str,
                                'minute_boundary': int(row.get('minute_boundary', 0)),
                                'minute_number': minute_num,
                                'station': station,
                                'frequency_mhz': freq_mhz,
                                'channel': channel_name,
                                'detected': detected,
                                'confidence': float(row.get('confidence', 0)) if row.get('confidence') else None,
                                'multitone_score': float(row.get('multitone_score', 0)) if row.get('multitone_score') else None,
                                'chirp_score': float(row.get('chirp_score', 0)) if row.get('chirp_score') else None,
                                'snr_db': float(row.get('snr_db', 0)) if row.get('snr_db') else None,
                                'frequency_selectivity_db': float(row.get('frequency_selectivity_db', 0)) if row.get('frequency_selectivity_db') else None,
                                'delay_spread_ms': float(row.get('delay_spread_ms', 0)) if row.get('delay_spread_ms') else None,
                                'toa_offset_ms': float(row.get('toa_offset_ms', 0)) if row.get('toa_offset_ms') else None,
                                'coherence_time_sec': float(row.get('coherence_time_sec', 0)) if row.get('coherence_time_sec') else None
                            }

                            all_measurements.append(measurement)

                except Exception as e:
                    logger.warning(f"Could not read {csv_file}: {e}")
                    continue

        if not all_measurements:
            return None

        # Sort by timestamp
        all_measurements.sort(key=lambda m: m['timestamp_utc'])

        # Organize by station and frequency
        by_station = defaultdict(lambda: {
            'timestamps': [],
            'frequencies': [],
            'snr_db': [],
            'confidence': [],
            'delay_spread_ms': [],
            'coherence_time_sec': [],
            'frequency_selectivity_db': [],
            'count': 0
        })

        for m in all_measurements:
            station = m['station']
            by_station[station]['timestamps'].append(m['timestamp_utc'])
            by_station[station]['frequencies'].append(m['frequency_mhz'])
            by_station[station]['snr_db'].append(m['snr_db'])
            by_station[station]['confidence'].append(m['confidence'])
            by_station[station]['delay_spread_ms'].append(m['delay_spread_ms'])
            by_station[station]['coherence_time_sec'].append(m['coherence_time_sec'])
            by_station[station]['frequency_selectivity_db'].append(m['frequency_selectivity_db'])
            by_station[station]['count'] += 1

        # Calculate statistics per station
        for station, data in by_station.items():
            # Average SNR
            valid_snr = [s for s in data['snr_db'] if s is not None]
            data['mean_snr_db'] = sum(valid_snr) / len(valid_snr) if valid_snr else None

            # Average confidence
            valid_conf = [c for c in data['confidence'] if c is not None]
            data['mean_confidence'] = sum(valid_conf) / len(valid_conf) if valid_conf else None

            # Average delay spread
            valid_delay = [d for d in data['delay_spread_ms'] if d is not None]
            data['mean_delay_spread_ms'] = sum(valid_delay) / len(valid_delay) if valid_delay else None

        result = self._deep_sanitize({
            'measurements': all_measurements,
            'by_station': dict(by_station),
            'total_detections': len(all_measurements),
            'stations': list(by_station.keys()),
            'time_range': {
                'start': iso_z(start),
                'end': iso_z(end)
            }
        })

        return result
