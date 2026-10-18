"""
Ionospheric Event Detection Service.

Detects and tracks ionospheric events including:
- Day/Night transitions (sunrise/sunset effects)
- Sporadic-E propagation
- Sudden Ionospheric Disturbances (SIDs)
- Anomalous propagation conditions
- Signal quality events
"""

from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import logging
import math

from hamsci_dsp.io import make_data_product_reader
from hamsci_physics.solar_zenith import solar_position
from station_web.config import config
from station_web.services.channels import discover_channels
from station_web.timeutil import as_utc, iso_z, utcnow

logger = logging.getLogger(__name__)

#: A propagation mode must hold this many minutes on one (channel, station)
#: before it counts as a change.  Mode labels flicker minute to minute; a
#: one-minute blip is noise, not an event.
MODE_PERSIST_MINUTES = 10
#: An SNR step of this size between consecutive rows of ONE station is a
#: signal loss (down) or recovery (up).
SNR_STEP_DB = 10.0
#: Solar elevation at sunrise/sunset: the upper limb on the horizon with
#: standard refraction (the NOAA convention).
SUN_HORIZON_DEG = -0.833


# Event type definitions
EVENT_TYPES = {
    'sunrise': {'icon': '🌅', 'color': '#f59e0b', 'description': 'Sunrise transition'},
    'sunset': {'icon': '🌆', 'color': '#8b5cf6', 'description': 'Sunset transition'},
    'sporadic_e': {'icon': '⚡', 'color': '#ef4444', 'description': 'Sporadic-E detected'},
    'sid': {'icon': '☀️', 'color': '#f97316', 'description': 'Sudden Ionospheric Disturbance'},
    'mode_change': {'icon': '📡', 'color': '#3b82f6', 'description': 'Propagation mode change'},
    'signal_loss': {'icon': '📉', 'color': '#ef4444', 'description': 'Signal loss'},
    'signal_recovery': {'icon': '📈', 'color': '#10b981', 'description': 'Signal recovery'},
    'anomaly': {'icon': '⚠️', 'color': '#f59e0b', 'description': 'Anomalous condition'},
    'multipath': {'icon': '🔀', 'color': '#6366f1', 'description': 'Multipath detected'},
    'scintillation': {'icon': '✨', 'color': '#ec4899', 'description': 'Scintillation event'},
}


class EventService:
    """Service for detecting and tracking ionospheric events."""

    def __init__(self, data_root: Path, station_lat: Optional[float] = None,
                 station_lon: Optional[float] = None, channels: Optional[List[str]] = None):
        """
        Initialize event service.

        Args:
            data_root: Root directory for data products
            station_lat: Station latitude for sunrise/sunset (None: unknown)
            station_lon: Station longitude, positive East (None: unknown)
            channels: Receiver channels to scan; default: the channels that
                produced L2_timing_measurements (``discover_channels``).

        The latitude and longitude used to default to 40 N, 105 W (Boulder),
        so a router that forgot them reported Boulder's day and night.  With
        no location there is no day/night answer, and the service says so.
        """
        self.data_root = Path(data_root)
        self.phase2_dir = self.data_root / 'phase2'
        self.station_lat = station_lat
        self.station_lon = station_lon
        self.channels = (list(channels) if channels is not None
                         else [c["name"] for c in discover_channels(config)])

    @property
    def has_location(self) -> bool:
        return self.station_lat is not None and self.station_lon is not None

    def _clean_value(self, val: Any) -> Any:
        """Clean value for JSON serialization."""
        import numpy as np

        if val is None:
            return None
        if isinstance(val, (np.floating, np.integer)):
            val = float(val) if isinstance(val, np.floating) else int(val)
        if isinstance(val, float):
            if math.isnan(val) or math.isinf(val):
                return None
        return val

    def _elevation(self, t: datetime) -> float:
        return solar_position(t, self.station_lat, self.station_lon)[1]

    def _crossing(self, a: datetime, b: datetime) -> datetime:
        """Bisect [a, b], across which elevation crosses the horizon, to 1 s."""
        above_a = self._elevation(a) > SUN_HORIZON_DEG
        while (b - a) > timedelta(seconds=1):
            mid = a + (b - a) / 2
            if (self._elevation(mid) > SUN_HORIZON_DEG) == above_a:
                a = mid
            else:
                b = mid
        return (a + (b - a) / 2).replace(microsecond=0)

    def _solar_day(self, noon: datetime) -> Dict[str, Optional[datetime]]:
        """Sunrise and sunset around one local solar noon (UTC instants).

        Scans noon-12 h .. noon+12 h in 10-minute steps with hamsci-physics'
        ``solar_position`` and bisects each horizon crossing.  The old
        arithmetic built ``date.replace(hour=int(sunset_utc))``, which is
        hour 24 or more for a western station; the ValueError became None
        and ``is_daytime`` read false in daylight.
        """
        step = timedelta(minutes=10)
        t = noon - timedelta(hours=12)
        prev_up = self._elevation(t) > SUN_HORIZON_DEG
        out: Dict[str, Optional[datetime]] = {'sunrise': None, 'sunset': None}
        while t < noon + timedelta(hours=12):
            nxt = t + step
            up = self._elevation(nxt) > SUN_HORIZON_DEG
            if up != prev_up:
                when = self._crossing(t, nxt)
                if up and out['sunrise'] is None:
                    out['sunrise'] = when
                elif not up and out['sunset'] is None and when > noon:
                    out['sunset'] = when
            t, prev_up = nxt, up
        return out

    def _noon_near(self, t: datetime) -> datetime:
        """The local solar noon (approximate, mean sun) nearest ``t``."""
        base = t.replace(hour=12, minute=0, second=0, microsecond=0) \
            - timedelta(hours=self.station_lon / 15.0)
        cands = [base + timedelta(days=d) for d in (-1, 0, 1)]
        return min(cands, key=lambda n: abs((n - t).total_seconds()))

    def _calculate_sun_times(self, when: datetime) -> Dict[str, Any]:
        """Sunrise and sunset of the local solar day nearest ``when``."""
        if not self.has_location:
            return {'sunrise': None, 'sunset': None}
        return self._solar_day(self._noon_near(as_utc(when)))

    def get_recent_events(self, hours: int = 24) -> Dict[str, Any]:
        """
        Get recent ionospheric events.

        Args:
            hours: Number of hours to look back

        Returns:
            Dictionary with detected events
        """
        events = []
        end_time = utcnow()
        start_time = end_time - timedelta(hours=hours)

        # Add sunrise/sunset events
        sun_events = self._detect_sun_events(start_time, end_time)
        events.extend(sun_events)

        # Detect signal events from timing data
        signal_events = self._detect_signal_events(start_time, end_time)
        events.extend(signal_events)

        # Sort by timestamp descending
        events.sort(key=lambda x: x.get('timestamp') or '', reverse=True)

        return {
            'events': events,
            'count': len(events),
            'start_time': iso_z(start_time),
            'end_time': iso_z(end_time)
        }

    def _detect_sun_events(self, start: datetime, end: datetime) -> List[Dict]:
        """Detect sunrise/sunset events in the time range."""
        events = []
        if not self.has_location:
            return events

        # Each local solar day that can hold a sunrise or sunset in range
        noon = self._noon_near(start) - timedelta(days=1)
        while noon <= end + timedelta(days=1):
            sun_times = self._solar_day(noon)

            if sun_times.get('sunrise') and start <= sun_times['sunrise'] <= end:
                events.append({
                    'type': 'sunrise',
                    'timestamp': iso_z(sun_times['sunrise']),
                    'title': 'Local Sunrise',
                    'description': 'D-layer ionization increasing, HF absorption rising',
                    'icon': EVENT_TYPES['sunrise']['icon'],
                    'color': EVENT_TYPES['sunrise']['color'],
                    'severity': 'info',
                    'details': {
                        'effect': 'Increased D-layer absorption on lower HF frequencies',
                        'expected_duration': '1-2 hours transition'
                    }
                })

            if sun_times.get('sunset') and start <= sun_times['sunset'] <= end:
                events.append({
                    'type': 'sunset',
                    'timestamp': iso_z(sun_times['sunset']),
                    'title': 'Local Sunset',
                    'description': 'D-layer recombining, HF absorption decreasing',
                    'icon': EVENT_TYPES['sunset']['icon'],
                    'color': EVENT_TYPES['sunset']['color'],
                    'severity': 'info',
                    'details': {
                        'effect': 'Reduced D-layer absorption, improved low-band propagation',
                        'expected_duration': '1-2 hours transition'
                    }
                })

            noon += timedelta(days=1)

        return events

    def _event(self, kind, timestamp, title, description, severity, details):
        return {
            'type': kind, 'timestamp': timestamp, 'title': title,
            'description': description, 'icon': EVENT_TYPES[kind]['icon'],
            'color': EVENT_TYPES[kind]['color'], 'severity': severity,
            'details': details,
        }

    def _detect_signal_events(self, start: datetime, end: datetime) -> List[Dict]:
        """Detect signal-based events from timing measurements.

        State is kept per (channel, station).  A SHARED channel interleaves
        WWV, WWVH and BPM rows; comparing consecutive rows regardless of
        station made every row a "change" (1013 mode changes in 6 h on B4).
        SNR comes from ``snr_db``, the column the schema has (the old
        ``carrier_snr_db`` never existed, so loss/recovery never fired).
        A mode counts as changed only after it holds ``MODE_PERSIST_MINUTES``.
        A read failure raises; the router reports it as a fault.
        """
        events = []
        persist = timedelta(minutes=MODE_PERSIST_MINUTES)

        for channel in self.channels:
            reader = make_data_product_reader(
                data_dir=self.phase2_dir / channel,
                product_level='L2',
                product_name='timing_measurements',
                channel=channel,
                storage_config=config.storage
            )
            try:
                measurements = reader.read_time_range(start=iso_z(start), end=iso_z(end))
            finally:
                reader.close()

            # (station) -> state for this channel
            state: Dict[str, Dict[str, Any]] = {}
            for m in measurements:
                timestamp = m.get('timestamp_utc')
                station = m.get('station') or 'UNKNOWN'
                freq = m.get('frequency_mhz')
                snr = self._clean_value(m.get('snr_db'))
                mode = m.get('propagation_mode')
                if mode == 'UNKNOWN':
                    mode = None
                st = state.setdefault(station, {'snr': None, 'mode': None,
                                                'cand': None, 'cand_since': None,
                                                'cand_ts': None})
                details = {'channel': channel, 'station': station, 'frequency_mhz': freq}

                # Detect significant SNR steps on this station
                if st['snr'] is not None and snr is not None:
                    change = snr - st['snr']
                    if change <= -SNR_STEP_DB:
                        events.append(self._event(
                            'signal_loss', timestamp, f'Signal Drop on {station} {freq} MHz',
                            f'SNR dropped {abs(change):.1f} dB', 'warning',
                            {**details, 'snr_before': st['snr'], 'snr_after': snr}))
                    elif change >= SNR_STEP_DB:
                        events.append(self._event(
                            'signal_recovery', timestamp, f'Signal Recovery on {station} {freq} MHz',
                            f'SNR increased {change:.1f} dB', 'info',
                            {**details, 'snr_before': st['snr'], 'snr_after': snr}))
                if snr is not None:
                    st['snr'] = snr

                # Detect persistent mode changes on this station
                if mode is None or not timestamp:
                    continue
                t = as_utc(datetime.fromisoformat(timestamp.replace('Z', '+00:00')))
                if st['mode'] is None:
                    st['mode'] = mode
                elif mode == st['mode']:
                    st['cand'] = None                    # a blip that did not hold
                elif mode != st['cand']:
                    st['cand'], st['cand_since'], st['cand_ts'] = mode, t, timestamp
                elif t - st['cand_since'] >= persist:
                    events.append(self._event(
                        'mode_change', st['cand_ts'], f'Mode Change on {station} {freq} MHz',
                        f"Changed from {st['mode']} to {mode}", 'info',
                        {**details, 'mode_before': st['mode'], 'mode_after': mode}))
                    st['mode'], st['cand'] = mode, None

        return events

    def get_current_conditions(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Get current ionospheric conditions summary.

        Returns:
            Dictionary with current conditions
        """
        now = as_utc(now) if now is not None else utcnow()
        sun_times = self._calculate_sun_times(now)

        # Day or night: the sun's elevation at the station, now.  None
        # when the station has no configured location.
        is_daytime = (self._elevation(now) > SUN_HORIZON_DEG) if self.has_location else None

        # Get recent events
        recent = self.get_recent_events(hours=6)

        # Count events by type
        event_counts = {}
        for event in recent.get('events', []):
            etype = event.get('type', 'unknown')
            event_counts[etype] = event_counts.get(etype, 0) + 1

        if is_daytime is None:
            summary = 'Station location not configured'
        else:
            summary = 'Daytime propagation' if is_daytime else 'Nighttime propagation'
        return {
            'timestamp': iso_z(now),
            'is_daytime': is_daytime,
            'sun_times': {
                'sunrise': iso_z(sun_times['sunrise']) if sun_times.get('sunrise') else None,
                'sunset': iso_z(sun_times['sunset']) if sun_times.get('sunset') else None
            },
            'recent_event_count': recent.get('count', 0),
            'event_counts_by_type': event_counts,
            'conditions_summary': summary
        }
