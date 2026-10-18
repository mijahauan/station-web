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


def read_tec_rows(start: datetime, end: datetime) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """``L3_tec`` rows in ``[start, end]`` from the first channel that has any.

    Returns ``(rows, channel)``; ``([], None)`` when no channel holds a row
    in the window.  A read failure raises — the caller must not report a
    fault as an empty window.
    """
    tec_dir = Path(config.data_root) / 'phase2' / 'science' / 'tec'
    for channel in TEC_CHANNELS:
        reader = make_data_product_reader(
            data_dir=tec_dir,
            product_level='L3',
            product_name='tec',
            channel=channel,
            storage_config=config.storage,
        )
        try:
            rows = reader.read_time_range(start=iso_z(start), end=iso_z(end))
        finally:
            reader.close()
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
