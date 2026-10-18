"""
Stability analysis service for Allan deviation and related metrics.

Note: The core ADEV algorithms are defined in hamsci_dsp.stability.
This service provides the API wrapper and data retrieval logic.
"""

import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import logging

from station_web import projected
from station_web.timeutil import as_utc

# Import core stability functions - try from installed package first,
# fall back to local implementation if not available
try:
    from hamsci_dsp.stability import (
        compute_phase_adev,
        compute_stability_metrics as core_compute_stability,
        identify_noise_type,
        compute_stability_at_tau
    )
except ImportError:
    # Core library not installed - use local implementation
    from .stability_core import (
        compute_phase_adev,
        compute_stability_metrics as core_compute_stability,
        identify_noise_type,
        compute_stability_at_tau
    )

logger = logging.getLogger(__name__)


class StabilityService:
    """
    API service for computing oscillator stability metrics.

    This is a thin wrapper that handles data retrieval and delegates
    the actual ADEV calculations to hamsci_dsp.stability.
    """

    def __init__(self, data_root: Path):
        """
        Initialize stability service.

        Args:
            data_root: Root directory for data products
        """
        self.data_root = Path(data_root)
        self.fusion_dir = self.data_root / 'phase2' / 'fusion'

    def compute_stability_metrics(
        self,
        start: datetime,
        end: datetime,
        min_points: int = 60
    ) -> Optional[Dict]:
        """
        Compute stability metrics from fusion timing data.

        Args:
            start: Start time
            end: End time
            min_points: Minimum number of data points required

        Returns:
            Dictionary with stability metrics, or None if the window holds
            too few points.  A read or computation failure raises.
        """
        start, end = as_utc(start), as_utc(end)

        # Two columns, streamed into flat arrays: the page's "All data" asks
        # for a year, ~525k rows at one a minute, and a dict per row of all
        # 48 columns would not fit the unit's memory.
        ts_list: List[float] = []
        d_list: List[float] = []
        for part in projected.iter_rows('L3_fusion_timing', 'fusion', start, end,
                                        ('timestamp_utc', 'd_clock_fused_ms')):
            for ts, d in part:
                # A row without a fused value is not a phase sample.
                if d is None:
                    continue
                ts_list.append(as_utc(datetime.fromisoformat(
                    str(ts).replace('Z', '+00:00'))).timestamp())
                d_list.append(float(d))
        if len(d_list) < min_points:
            logger.warning(f"Insufficient data for stability analysis: {len(d_list)} < {min_points}")
            return None

        d_clock_ms = np.array(d_list)
        timestamps = np.array(ts_list)
        n_points = len(d_list)
        del ts_list, d_list

        # Calculate sample interval dynamically
        intervals = np.diff(timestamps)
        if len(intervals) > 0:
            sample_interval = float(np.median(intervals))
        else:
            sample_interval = 8.0  # Fallback to nominal 8s

        if sample_interval <= 0:
            logger.warning(f"Invalid sample interval calculated: {sample_interval}")
            sample_interval = 8.0

        # D_clock is phase data (time offset in ms)
        # Convert to seconds for ADEV calculation
        phase_seconds = d_clock_ms * 1e-3

        # Delegate to core library for ADEV calculation
        result = core_compute_stability(phase_seconds, sample_interval)

        if not result or len(result.get('tau_seconds', [])) == 0:
            logger.warning("No valid ADEV values computed")
            return None

        # Add API-specific fields
        result['n_points'] = n_points
        result['sample_rate_hz'] = 1.0 / sample_interval
        result['time_span_hours'] = (end - start).total_seconds() / 3600

        return result
