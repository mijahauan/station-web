"""
Fusion timing data service.

Provides access to L3B fusion timing estimates using DataProductReader.
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import logging

from hamsci_dsp.io import make_data_product_reader
from station_web import projected
from station_web.config import config
from station_web.timeutil import iso_z, utcnow

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
