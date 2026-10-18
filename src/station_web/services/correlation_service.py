"""
Correlation Service - Analyze relationships between space weather and HF propagation.

Provides correlation analysis between:
- SNR vs Solar Zenith Angle
- SNR vs X-ray flux (SID detection)
- TEC vs F10.7 solar flux
- Propagation mode vs Kp index
"""

import logging
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any, Tuple
from pathlib import Path
from scipy import stats

from station_web.services.space_weather_cache import SpaceWeatherCache
from station_web.services.propagation_service import PropagationService
from station_web.timeutil import as_utc, iso_z, utcnow
from hamsci_physics.solar_zenith import calculate_midpoint, solar_position

logger = logging.getLogger(__name__)

SPACE_WEATHER_SERIES_UNAVAILABLE = {
    "available": False,
    "reason": "space weather time series not available from products",
}


class CorrelationService:
    """Service for analyzing space weather / propagation correlations."""

    def __init__(self, data_root: Path, space_weather: Optional[SpaceWeatherCache] = None):
        """Initialize correlation service."""
        self.data_root = data_root
        self.space_weather = space_weather if space_weather is not None else SpaceWeatherCache(data_root)
        self.propagation = PropagationService(data_root)
        logger.info("Correlation Service initialized")

    def analyze_snr_solar_zenith(
        self,
        station: str,
        frequency: float,
        start: datetime,
        end: datetime,
        station_coords: Tuple[float, float],
        rx_coords: Tuple[float, float]
    ) -> Dict[str, Any]:
        """
        Analyze correlation between SNR and solar zenith angle.

        Args:
            station: Station ID (WWV, WWVH, BPM)
            frequency: Frequency in MHz
            start: Start time
            end: End time
            station_coords: (lat, lon) of transmitter
            rx_coords: (lat, lon) of receiver

        Returns:
            Correlation analysis results
        """
        # Get propagation timeline for this station/frequency
        timeline = self.propagation.get_mode_timeline(start, end, station=station)

        if not timeline or not timeline.get('timestamps'):
            return {'error': 'No propagation data available'}

        # Filter for matching frequency
        timestamps = []
        snr_values = []
        for i, (ts, freq, snr) in enumerate(zip(
            timeline.get('timestamps', []),
            timeline.get('frequencies', []),
            timeline.get('snr_db', [])
        )):
            if freq is not None and abs(freq - frequency) < 0.1:
                timestamps.append(ts)
                snr_values.append(snr)

        if not timestamps or not snr_values:
            return {'error': f'No data for {station} {frequency} MHz'}

        # Calculate solar zenith angles at path midpoint
        tx_lat, tx_lon = station_coords
        rx_lat, rx_lon = rx_coords
        mid_lat, mid_lon = calculate_midpoint(rx_lat, rx_lon, tx_lat, tx_lon)

        solar_elevations = []
        solar_zenith_angles = []

        for ts_str in timestamps:
            try:
                ts = as_utc(datetime.fromisoformat(ts_str.replace('Z', '+00:00')))
                az, el = solar_position(ts, mid_lat, mid_lon)
                solar_elevations.append(el)
                solar_zenith_angles.append(90 - el)  # Zenith = 90 - elevation
            except Exception as e:
                logger.warning(f"Error calculating solar position: {e}")
                solar_elevations.append(None)
                solar_zenith_angles.append(None)

        # Filter out None values
        valid_pairs = [
            (snr, sza) for snr, sza in zip(snr_values, solar_zenith_angles)
            if snr is not None and sza is not None
        ]

        if len(valid_pairs) < 10:
            return {'error': 'Insufficient valid data points'}

        snr_clean, sza_clean = zip(*valid_pairs)

        # Calculate correlation
        correlation, p_value = stats.pearsonr(sza_clean, snr_clean)

        # Fit linear model: SNR = a * SZA + b
        slope, intercept, r_value, p_val, std_err = stats.linregress(sza_clean, snr_clean)

        result = {
            'station': station,
            'frequency_mhz': frequency,
            'period': {
                'start': iso_z(start),
                'end': iso_z(end)
            },
            'path_midpoint': {
                'lat': mid_lat,
                'lon': mid_lon
            },
            'statistics': {
                'n_points': len(valid_pairs),
                'correlation': round(correlation, 3),
                'p_value': round(p_value, 6),
                'r_squared': round(r_value**2, 3),
                'slope': round(slope, 3),
                'intercept': round(intercept, 1),
                'std_error': round(std_err, 3)
            },
            'data': {
                'timestamps': timestamps,
                'snr_db': snr_values,
                'solar_elevation_deg': solar_elevations,
                'solar_zenith_angle_deg': solar_zenith_angles
            },
            'interpretation': self._interpret_snr_solar_correlation(correlation, slope)
        }

        return result

    def _interpret_snr_solar_correlation(self, correlation: float, slope: float) -> str:
        """Interpret SNR-solar correlation results."""
        if abs(correlation) < 0.3:
            return "Weak correlation - propagation may be dominated by other factors"
        elif correlation < -0.5:
            return "Strong negative correlation - SNR decreases as sun gets higher (unusual, check for interference)"
        elif correlation > 0.5:
            return "Strong positive correlation - SNR increases with solar illumination (expected for F-layer propagation)"
        else:
            return "Moderate correlation - solar angle is one of several factors affecting SNR"

    def detect_sid_correlation(
        self,
        start: datetime,
        end: datetime
    ) -> Dict[str, Any]:
        """
        Detect correlation between X-ray flares and SNR drops (SID events).

        Args:
            start: Start time
            end: End time

        Returns:
            Detected SID events with X-ray correlation, or an ``available: False``
            marker — station-web has no X-ray time series product to detect
            SID events from.
        """
        return dict(SPACE_WEATHER_SERIES_UNAVAILABLE)

    def analyze_tec_f107_correlation(
        self,
        station: str,
        days: int = 30
    ) -> Dict[str, Any]:
        """
        Analyze correlation between TEC and F10.7 solar flux.

        Args:
            station: Station ID
            days: Number of days to analyze

        Returns:
            TEC-F10.7 correlation analysis
        """
        # Get TEC data
        end = utcnow()
        start = end - timedelta(days=days)

        tec_summary = self.propagation.get_tec_summary(start, end)

        if not tec_summary or 'paths' not in tec_summary:
            return {'error': 'No TEC data available'}

        if station not in tec_summary['paths']:
            return {'error': f'No TEC data for station {station}'}

        path_data = tec_summary['paths'][station]

        # Get F10.7 data (placeholder - not yet implemented in space weather service)
        # For now, return structure without F10.7 correlation

        result = {
            'station': station,
            'period': {
                'start': iso_z(start),
                'end': iso_z(end),
                'days': days
            },
            'tec_statistics': {
                'mean': path_data.get('mean_tec'),
                'min': path_data.get('min_tec'),
                'max': path_data.get('max_tec'),
                'n_points': len(path_data.get('tec_tecu', []))
            },
            'f107_correlation': {
                'status': 'not_implemented',
                'message': 'F10.7 data ingestion not yet implemented'
            }
        }

        return result

    def analyze_propagation_mode_kp(
        self,
        start: datetime,
        end: datetime
    ) -> Dict[str, Any]:
        """
        Analyze relationship between propagation modes and Kp index.

        High Kp (geomagnetic storms) should correlate with:
        - Increased auroral absorption (high-latitude paths)
        - Mode changes (E-layer enhancement, F-layer irregularities)

        Args:
            start: Start time
            end: End time

        Returns:
            Propagation mode vs Kp analysis, or an ``available: False`` marker
            — station-web has no Kp time series product to bin propagation by.
        """
        return dict(SPACE_WEATHER_SERIES_UNAVAILABLE)
