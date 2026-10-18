"""
Propagation and ionospheric analysis API endpoints.
"""

from fastapi import APIRouter, Query, HTTPException
from typing import Optional
import logging

from station_web.errors import internal_error
from station_web.services.propagation_service import PropagationService
from station_web.config import config
from station_web.timeutil import parse_window

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/propagation", tags=["propagation"])

def _service() -> PropagationService:
    """Built per request, never at import.

    ``config`` is a proxy: the settings behind it change when the process is
    reconfigured and when a test swaps them.  A data root read at import time
    would freeze the first value the module ever saw.  Construction is cheap —
    the service holds paths, and opens its reader on demand.
    """
    return PropagationService(config.data_root)


def _window(start: str, end: str):
    """Parse ``start``/``end`` (station_web.timeutil); a bad value is a 400."""
    try:
        return parse_window(start, end)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/conditions")
@router.get("/current")  # Alias for frontend compatibility
def get_current_conditions():
    """
    Get current propagation conditions.

    Returns summary of propagation modes, MUF estimates, and signal quality
    from the last hour of measurements.
    """
    try:
        conditions = _service().get_current_conditions()
    except Exception as e:
        raise internal_error(logger, "Propagation conditions read", e)
    if conditions is None:
        raise HTTPException(
            status_code=404,
            detail="No recent propagation data available"
        )
    return conditions


@router.get("/timeline")
def get_mode_timeline(
    start: str = Query("-6h", description="Start time (ISO8601 or relative like '-6h')"),
    end: str = Query("now", description="End time (ISO8601 or 'now')"),
    station: Optional[str] = Query(None, description="Filter by station (WWV, WWVH, BPM)")
):
    """
    Get propagation mode timeline.

    Returns time series of propagation modes observed across all channels.
    """
    start_time, end_time = _window(start, end)
    try:
        timeline = _service().get_mode_timeline(start_time, end_time, station)
    except Exception as e:
        raise internal_error(logger, "Propagation timeline read", e)
    if timeline is None:
        raise HTTPException(
            status_code=404,
            detail="No propagation timeline data available"
        )
    return timeline


@router.get("/tec")
def get_tec_summary(
    start: str = Query("-24h", description="Start time (ISO8601 or relative like '-24h')"),
    end: str = Query("now", description="End time (ISO8601 or 'now')")
):
    """
    Get TEC (Total Electron Content) summary.

    Returns HF-derived TEC measurements for the requested window only, and
    the ``source`` channel (REANALYZED or AGGREGATED) they came from.
    """
    start_time, end_time = _window(start, end)
    try:
        tec_data = _service().get_tec_summary(start_time, end_time)
    except Exception as e:
        raise internal_error(logger, "TEC summary read", e)
    if tec_data is None:
        raise HTTPException(
            status_code=404,
            detail="No TEC data in the requested window"
        )
    return tec_data


@router.get("/test-signals")
def get_test_signals(
    start: str = Query("-24h", description="Start time (ISO8601 or relative like '-24h')"),
    end: str = Query("now", description="End time (ISO8601 or 'now')")
):
    """
    Get WWV/WWVH test signal analysis data.

    Returns test signal detections from minutes 8 (WWV) and 44 (WWVH) with
    ionospheric metrics including field strength, delay spread, and scintillation.
    """
    start_time, end_time = _window(start, end)
    try:
        test_signal_data = _service().get_test_signal_summary(start_time, end_time)
    except Exception as e:
        raise internal_error(logger, "Test signal summary read", e)
    if test_signal_data is None:
        raise HTTPException(
            status_code=404,
            detail="No test signal data available"
        )
    return test_signal_data
