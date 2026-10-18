"""
Physics API endpoints.
"""

import logging

from fastapi import APIRouter, Query, HTTPException
from datetime import datetime
from typing import Optional, Dict, Any

from station_web.services.physics_service import PhysicsService
from station_web.services.scintillation_service import ScintillationService
from station_web.services.test_signal_service import TestSignalService
from station_web.services.event_service import EventService
from station_web.config import config
from station_web.errors import internal_error
from station_web.timeutil import parse_utc, parse_window

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/physics", tags=["physics"])


def _event_service() -> EventService:
    """EventService at THIS station: [station] latitude/longitude from the
    config (it used to default to Boulder), or no location if unset."""
    st = config.station
    lat, lon = st.get("latitude"), st.get("longitude")
    if lat is None or lon is None:
        return EventService(data_root=config.data_root)
    return EventService(data_root=config.data_root, station_lat=float(lat), station_lon=float(lon))


def parse_time_param(time_str: str) -> datetime:
    """Parse a time parameter into an AWARE UTC datetime (see station_web.timeutil)."""
    return parse_utc(time_str)


@router.get("/latest")
def get_latest_physics():
    """Get latest physics data (TEC, UTC consistency)."""
    try:
        service = PhysicsService(data_root=config.data_root)
        result = service.get_latest()

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise internal_error(logger, "get latest physics", e)


@router.get("/history")
def get_physics_history(
    start: str = Query("-6h", description="Start time"),
    end: str = Query("now", description="End time")
):
    """Get physics data history."""
    try:
        start_dt, end_dt = parse_window(start, end)

        service = PhysicsService(data_root=config.data_root)
        return service.get_history(start=start_dt, end=end_dt)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise internal_error(logger, "get physics history", e)


# ============================================================================
# SCINTILLATION ENDPOINTS
# ============================================================================

@router.get("/scintillation/paths")
def get_scintillation_by_path():
    """
    Get scintillation indices organized by propagation path.

    Returns S4 (amplitude) and σ_φ (phase) scintillation indices
    for each broadcast station path.
    """
    try:
        service = ScintillationService(data_root=config.data_root)
        return service.get_latest_by_path()
    except Exception as e:
        raise internal_error(logger, "get scintillation by path", e)


@router.get("/scintillation/history")
def get_scintillation_history(
    start: str = Query("-6h", description="Start time"),
    end: str = Query("now", description="End time"),
    station: Optional[str] = Query(None, description="Station filter (WWV, WWVH, BPM)")
):
    """Get scintillation history."""
    try:
        start_dt, end_dt = parse_window(start, end)

        service = ScintillationService(data_root=config.data_root)
        return service.get_history(start=start_dt, end=end_dt, station=station)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise internal_error(logger, "get scintillation history", e)


# ============================================================================
# TEST SIGNAL / CHANNEL CHARACTERIZATION ENDPOINTS
# ============================================================================

@router.get("/channels/latest")
def get_channel_characterization():
    """
    Get latest test signal analysis results.

    Returns channel characterization from WWV/WWVH scientific test signals
    including delay spread, coherence time, and frequency selectivity.
    """
    try:
        service = TestSignalService(data_root=config.data_root)
        return service.get_latest()
    except Exception as e:
        raise internal_error(logger, "get channel characterization", e)


@router.get("/channels/summary")
def get_channel_summary():
    """
    Get channel quality summary across all frequencies.
    """
    try:
        service = TestSignalService(data_root=config.data_root)
        return service.get_channel_summary()
    except Exception as e:
        raise internal_error(logger, "get channel summary", e)


@router.get("/channels/history")
def get_channel_history(
    start: str = Query("-24h", description="Start time"),
    end: str = Query("now", description="End time"),
    frequency_mhz: Optional[float] = Query(None, description="Frequency filter"),
    station: Optional[str] = Query(None, description="Station filter (WWV or WWVH)")
):
    """Get test signal history with full metrics."""
    try:
        start_dt, end_dt = parse_window(start, end)

        service = TestSignalService(data_root=config.data_root)
        return service.get_history(start=start_dt, end=end_dt, frequency_mhz=frequency_mhz, station=station)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise internal_error(logger, "get channel history", e)


@router.get("/channels/daily")
def get_channel_daily_comparison(
    date: Optional[str] = Query(None, description="Date in YYYY-MM-DD format (defaults to today)"),
    hours: Optional[int] = Query(None, description="Hours of data to return (overrides date, counts back from now)", ge=1, le=336)
):
    """
    Get test signal data organized for daily comparison.

    Returns structured data for:
    - WWV vs WWVH comparison at same frequencies
    - Same station across different frequencies
    - Evolution over the UTC day

    If 'hours' is specified, returns that many hours of data ending at now,
    ignoring the 'date' parameter.
    """
    try:
        date_dt = None
        if date:
            date_dt = datetime.strptime(date, '%Y-%m-%d')

        service = TestSignalService(data_root=config.data_root)
        return service.get_daily_comparison(date=date_dt, hours=hours)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise internal_error(logger, "get channel daily comparison", e)


# ============================================================================
# EVENT DETECTION ENDPOINTS
# ============================================================================

@router.get("/events/recent")
def get_recent_events(
    hours: int = Query(24, description="Hours to look back", ge=1, le=168)
):
    """
    Get recent ionospheric events.

    Returns detected events including:
    - Sunrise/sunset transitions
    - Signal loss/recovery events
    - Propagation mode changes
    - Anomalous conditions
    """
    try:
        return _event_service().get_recent_events(hours=hours)
    except Exception as e:
        raise internal_error(logger, "get recent events", e)


@router.get("/events/conditions")
def get_current_conditions():
    """
    Get current ionospheric conditions summary.

    Returns day/night status, sun times, and recent event counts.
    """
    try:
        return _event_service().get_current_conditions()
    except Exception as e:
        raise internal_error(logger, "get current conditions", e)
