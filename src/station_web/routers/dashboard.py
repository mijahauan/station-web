"""
24-Hour UTC Dashboard API.

Provides aggregated data for all 17 broadcasts over a 24-hour period,
optimized for visualization of ionospheric behavior.
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import math

from fastapi import APIRouter, HTTPException, Query

from station_web.services.propagation_service import PropagationService
from hamsci_physics.solar_zenith import calculate_midpoint, solar_position
from station_web.broadcast import BroadcastRegistry, ReceiverLocation
from hamsci_dsp.io import make_data_product_reader
from station_web.config import config
from station_web.services.channels import discover_channels
from station_web.timeutil import as_utc, iso_z, utcnow
from station_web.errors import internal_error

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
logger = logging.getLogger(__name__)

# Singleton registry
_broadcast_registry = None


def get_broadcast_registry() -> BroadcastRegistry:
    """Get or create the broadcast registry singleton."""
    global _broadcast_registry
    if _broadcast_registry is None:
        receiver = ReceiverLocation(
            callsign=config.station_metadata.get('callsign', 'UNKNOWN'),
            latitude=config.station_metadata.get('latitude', 0.0),
            longitude=config.station_metadata.get('longitude', 0.0),
            grid_square=config.station_metadata.get('grid_square', ''),
        )
        _broadcast_registry = BroadcastRegistry(receiver)
        logger.info(f"Initialized BroadcastRegistry with {_broadcast_registry.n_broadcasts} broadcasts")
    return _broadcast_registry


def sanitize_value(val: Any) -> Any:
    """Sanitize values for JSON serialization."""
    import numpy as np

    if val is None:
        return None

    if isinstance(val, (np.floating, np.integer)):
        val = val.item()
    elif isinstance(val, np.ndarray):
        return [sanitize_value(x) for x in val.tolist()]

    if isinstance(val, float):
        if not math.isfinite(val):
            return None

    return val


def _channels() -> List[str]:
    """Receiver channels named by the products, not directories under
    phase2/ (ionex, WWVB_60, science/* are not channels)."""
    return [c["name"] for c in discover_channels(config)]


def _read(level: str, product: str, channel: str, start: datetime, end: datetime) -> list:
    """One product's rows for one channel; a read failure raises (D11)."""
    reader = make_data_product_reader(
        data_dir=config.data_root / 'phase2' / channel,
        product_level=level,
        product_name=product,
        channel=channel,
        storage_config=config.storage
    )
    try:
        return reader.read_time_range(start=iso_z(start), end=iso_z(end))
    finally:
        reader.close()


def deep_sanitize(obj: Any) -> Any:
    """Recursively sanitize dicts and lists for JSON serialization."""
    if isinstance(obj, dict):
        return {str(k): deep_sanitize(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [deep_sanitize(x) for x in obj]
    else:
        return sanitize_value(obj)


@router.get("/broadcasts/24h")
def get_24h_broadcast_data(
    date: Optional[str] = Query(None, description="Date in YYYYMMDD format (default: today)"),
    interval_minutes: int = Query(15, ge=5, le=60, description="Aggregation interval in minutes")
):
    """
    Get 24-hour data for all 17 broadcasts.

    Returns time series data for each broadcast including:
    - Solar zenith angle at path midpoint
    - SNR (signal strength)
    - Timing error (ToA - expected)
    - Propagation mode
    - Detection count per interval

    This is the primary endpoint for the 24-hour UTC visualization dashboard.
    """
    try:
        registry = get_broadcast_registry()

        # Parse date or use today
        if date:
            year = int(date[0:4])
            month = int(date[4:6])
            day = int(date[6:8])
            start_time = datetime(year, month, day, 0, 0, 0, tzinfo=timezone.utc)
        else:
            # Use last 24 hours ending now
            start_time = utcnow() - timedelta(hours=24)

        end_time = start_time + timedelta(hours=24)

        # Get receiver location
        rx_lat = config.station_metadata.get('latitude', 0.0)
        rx_lon = config.station_metadata.get('longitude', 0.0)

        # Build response for each broadcast
        broadcasts_data = {}

        for broadcast_id, broadcast in registry.broadcasts.items():
            # Get transmitter location
            station = registry.get_station(broadcast.station)
            if not station:
                continue

            tx_lat = station.latitude
            tx_lon = station.longitude

            # Calculate path midpoint for solar zenith
            mid_lat, mid_lon = calculate_midpoint(rx_lat, rx_lon, tx_lat, tx_lon)

            # Generate solar zenith time series
            solar_timestamps = []
            solar_elevations = []

            curr = start_time
            while curr < end_time:
                _, el = solar_position(curr, mid_lat, mid_lon)
                solar_timestamps.append(iso_z(curr))
                solar_elevations.append(round(el, 2))
                curr += timedelta(minutes=interval_minutes)

            broadcasts_data[broadcast_id] = {
                'broadcast_id': broadcast_id,
                'station': broadcast.station,
                'frequency_mhz': broadcast.frequency_mhz,
                'frequency_khz': broadcast.frequency_hz // 1000,
                'distance_km': round(broadcast.distance_km, 1),
                'azimuth_deg': round(broadcast.azimuth_deg, 1),
                'min_propagation_ms': round(broadcast.min_propagation_ms, 2),
                'path_midpoint': {'lat': round(mid_lat, 4), 'lon': round(mid_lon, 4)},
                'solar': {
                    'timestamps': solar_timestamps,
                    'elevation_deg': solar_elevations,
                },
                'measurements': {
                    'timestamps': [],
                    'snr_db': [],
                    'timing_error_ms': [],
                    'propagation_mode': [],
                    'detection_count': [],
                }
            }

        # Now fetch actual measurement data from L2 products
        channels = _channels()
        for channel in channels:
            measurements = _read('L2', 'timing_measurements', channel, start_time, end_time)

            # Aggregate measurements by broadcast and time interval
            for m in measurements:
                station = m.get('station', 'UNKNOWN')
                freq_mhz = m.get('frequency_mhz', 0)
                freq_khz = int(round(freq_mhz * 1000))

                # Construct broadcast_id
                broadcast_id = f"{station}_{freq_khz}"

                if broadcast_id not in broadcasts_data:
                    continue

                # Get timing data
                timestamp = m.get('timestamp_utc', '')
                snr = m.get('snr_db')
                mode = m.get('propagation_mode', 'UNKNOWN')

                # L2 timing error observable is clock_offset_ms (D_clock)
                timing_error = m.get('clock_offset_ms')

                # Append to measurements
                bd = broadcasts_data[broadcast_id]['measurements']
                bd['timestamps'].append(timestamp)
                bd['snr_db'].append(sanitize_value(snr))
                bd['timing_error_ms'].append(sanitize_value(timing_error))
                bd['propagation_mode'].append(mode)

        # Second pass: supplement with tick timing data (55+ points/minute)
        # The tick matched filter provides per-minute SNR estimates even when
        # the single-tick RTP measurement fails.
        for channel in channels:
            tick_measurements = _read('L2', 'tick_timing', channel, start_time, end_time)

            for m in tick_measurements:
                station = m.get('station', 'UNKNOWN')
                freq_mhz = m.get('frequency_mhz', 0)
                freq_khz = int(round(freq_mhz * 1000))
                broadcast_id = f"{station}_{freq_khz}"

                if broadcast_id not in broadcasts_data:
                    continue

                # Only add tick data if it has reasonable quality
                tick_snr = m.get('mean_snr_db')
                valid_windows = m.get('valid_windows', 0)
                if tick_snr is None or valid_windows < 3:
                    continue

                timestamp = m.get('timestamp_utc', '')

                bd = broadcasts_data[broadcast_id]['measurements']
                bd['timestamps'].append(timestamp)
                bd['snr_db'].append(sanitize_value(tick_snr))
                bd['timing_error_ms'].append(None)
                bd['propagation_mode'].append('TICK_FILTER')

        # Calculate detection counts per interval
        for broadcast_id, data in broadcasts_data.items():
            timestamps = data['measurements']['timestamps']
            if not timestamps:
                continue

            # Count detections per solar interval
            solar_ts = data['solar']['timestamps']
            detection_counts = [0] * len(solar_ts)

            for ts in timestamps:
                try:
                    dt = as_utc(datetime.fromisoformat(ts.replace('Z', '+00:00')))
                except (TypeError, ValueError, AttributeError):
                    continue                    # a malformed stored timestamp
                # The interval this belongs to: index by arithmetic, not by
                # re-parsing every solar timestamp for every row.
                i = int((dt - start_time).total_seconds() // (interval_minutes * 60))
                if 0 <= i < len(solar_ts):
                    detection_counts[i] += 1

            data['measurements']['detection_count'] = detection_counts

        result = {
            'time_range': {
                'start': iso_z(start_time),
                'end': iso_z(end_time),
            },
            'interval_minutes': interval_minutes,
            'receiver': {
                'callsign': registry.receiver.callsign,
                'latitude': rx_lat,
                'longitude': rx_lon,
            },
            'n_broadcasts': len(broadcasts_data),
            'broadcasts': deep_sanitize(broadcasts_data),
        }

        return result

    except Exception as e:
        raise internal_error(logger, "get 24h broadcast data", e)


@router.get("/solar-zenith/24h")
def get_24h_solar_zenith(
    date: Optional[str] = Query(None, description="Date in YYYYMMDD format (default: today)"),
    interval_minutes: int = Query(5, ge=1, le=60, description="Interval in minutes")
):
    """
    Get 24-hour solar zenith angles for all 17 broadcast paths.

    Returns solar elevation at the path midpoint for each broadcast,
    which correlates with D-layer absorption and propagation conditions.
    """
    try:
        registry = get_broadcast_registry()

        # Parse date or use today
        if date:
            year = int(date[0:4])
            month = int(date[4:6])
            day = int(date[6:8])
            start_time = datetime(year, month, day, 0, 0, 0, tzinfo=timezone.utc)
        else:
            now = utcnow()
            start_time = datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=timezone.utc)

        end_time = start_time + timedelta(hours=24)

        # Get receiver location
        rx_lat = config.station_metadata.get('latitude', 0.0)
        rx_lon = config.station_metadata.get('longitude', 0.0)

        # Generate timestamps
        timestamps = []
        curr = start_time
        while curr < end_time:
            timestamps.append(iso_z(curr))
            curr += timedelta(minutes=interval_minutes)

        # Calculate solar zenith for each broadcast path
        paths = {}

        for broadcast_id, broadcast in registry.broadcasts.items():
            station = registry.get_station(broadcast.station)
            if not station:
                continue

            tx_lat = station.latitude
            tx_lon = station.longitude
            mid_lat, mid_lon = calculate_midpoint(rx_lat, rx_lon, tx_lat, tx_lon)

            elevations = []
            curr = start_time
            while curr < end_time:
                _, el = solar_position(curr, mid_lat, mid_lon)
                elevations.append(round(el, 2))
                curr += timedelta(minutes=interval_minutes)

            paths[broadcast_id] = {
                'station': broadcast.station,
                'frequency_mhz': broadcast.frequency_mhz,
                'midpoint': {'lat': round(mid_lat, 4), 'lon': round(mid_lon, 4)},
                'elevation_deg': elevations,
            }

        return {
            'date': start_time.strftime('%Y%m%d'),
            'interval_minutes': interval_minutes,
            'timestamps': timestamps,
            'receiver': {'lat': rx_lat, 'lon': rx_lon},
            'paths': paths,
        }

    except Exception as e:
        raise internal_error(logger, "get 24h solar zenith", e)


@router.get("/timing-error/24h")
def get_24h_timing_error(
    broadcast_id: Optional[str] = Query(None, description="Filter by broadcast ID (e.g., WWV_10000)"),
    hours: int = Query(24, ge=1, le=168, description="Hours of history")
):
    """
    Get timing error (ToA - expected) for broadcasts.

    Timing error shows ionospheric delay variations and mode changes.
    Expected to follow diurnal pattern correlated with solar zenith.
    """
    try:
        registry = get_broadcast_registry()

        end_time = utcnow()
        start_time = end_time - timedelta(hours=hours)

        # Collect timing errors by broadcast
        timing_data = defaultdict(lambda: {
            'timestamps': [],
            'timing_error_ms': [],
            'snr_db': [],
            'propagation_mode': [],
        })

        for channel in _channels():
            measurements = _read('L2', 'timing_measurements', channel, start_time, end_time)

            for m in measurements:
                station = m.get('station', 'UNKNOWN')
                freq_mhz = m.get('frequency_mhz') or 0
                freq_khz = int(round(freq_mhz * 1000))
                bid = f"{station}_{freq_khz}"

                # Filter if specified
                if broadcast_id and bid != broadcast_id:
                    continue

                # Get expected delay from registry
                if bid in registry.broadcasts:
                    expected_ms = registry.broadcasts[bid].min_propagation_ms
                else:
                    continue

                # L2 timing error observable is clock_offset_ms (D_clock)
                timing_error = m.get('clock_offset_ms')
                if timing_error is None:
                    continue

                timing_data[bid]['timestamps'].append(m.get('timestamp_utc', ''))
                timing_data[bid]['timing_error_ms'].append(sanitize_value(timing_error))
                timing_data[bid]['snr_db'].append(sanitize_value(m.get('snr_db')))
                timing_data[bid]['propagation_mode'].append(m.get('propagation_mode', 'UNKNOWN'))

        # Add broadcast metadata
        result_broadcasts = {}
        for bid, data in timing_data.items():
            if bid in registry.broadcasts:
                b = registry.broadcasts[bid]
                result_broadcasts[bid] = {
                    'station': b.station,
                    'frequency_mhz': b.frequency_mhz,
                    'expected_delay_ms': round(b.min_propagation_ms, 2),
                    'n_measurements': len(data['timestamps']),
                    **data
                }

        return deep_sanitize({
            'time_range': {
                'start': iso_z(start_time),
                'end': iso_z(end_time),
            },
            'n_broadcasts': len(result_broadcasts),
            'broadcasts': result_broadcasts,
        })

    except Exception as e:
        raise internal_error(logger, "get 24h timing error", e)


@router.get("/doppler/24h")
def get_24h_doppler(
    broadcast_id: Optional[str] = Query(None, description="Filter by broadcast ID"),
    hours: int = Query(24, ge=1, le=168, description="Hours of history")
):
    """
    Get Doppler shift measurements for broadcasts.

    Doppler shift indicates ionospheric motion and TID signatures.
    Typical values: ±0.1-1 Hz at HF.

    Note: Doppler is more reliable on single-station frequencies (WWV 20/25 MHz).
    On shared frequencies, station-specific features serve as proxies.
    """
    try:
        registry = get_broadcast_registry()

        end_time = utcnow()
        start_time = end_time - timedelta(hours=hours)

        doppler_data = defaultdict(lambda: {
            'timestamps': [],
            'doppler_hz': [],
            'snr_db': [],
        })

        for channel in _channels():
            # L1 data carries the doppler_hz field
            measurements = _read('L1', 'broadcast_measurements', channel, start_time, end_time)

            for m in measurements:
                station = m.get('station', 'UNKNOWN')
                freq_khz = m.get('frequency_khz', 0)
                if not freq_khz:
                    freq_mhz = m.get('frequency_mhz') or 0
                    freq_khz = int(round(freq_mhz * 1000))

                bid = f"{station}_{freq_khz}"

                if broadcast_id and bid != broadcast_id:
                    continue

                doppler = m.get('doppler_hz')
                if doppler is None:
                    continue

                doppler_data[bid]['timestamps'].append(m.get('timestamp_utc', ''))
                doppler_data[bid]['doppler_hz'].append(sanitize_value(doppler))
                doppler_data[bid]['snr_db'].append(sanitize_value(m.get('snr_db')))

        # Add broadcast metadata
        result_broadcasts = {}
        for bid, data in doppler_data.items():
            if bid in registry.broadcasts:
                b = registry.broadcasts[bid]
                result_broadcasts[bid] = {
                    'station': b.station,
                    'frequency_mhz': b.frequency_mhz,
                    'requires_discrimination': b.requires_discrimination,
                    'n_measurements': len(data['timestamps']),
                    **data
                }

        return deep_sanitize({
            'time_range': {
                'start': iso_z(start_time),
                'end': iso_z(end_time),
            },
            'note': 'Doppler is most reliable on single-station frequencies (WWV 20/25 MHz)',
            'n_broadcasts': len(result_broadcasts),
            'broadcasts': result_broadcasts,
        })

    except Exception as e:
        raise internal_error(logger, "get 24h doppler", e)
