"""
TID (Traveling Ionospheric Disturbance) API endpoints for v6.5.0.
"""

from datetime import timedelta

from fastapi import APIRouter, Query, HTTPException
from typing import Optional
import logging

from station_web.errors import internal_error
from station_web.services.tid_service import TIDService
from station_web.config import config
from station_web.timeutil import iso_z, parse_window

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tid", tags=["ionosphere"])

#: Longest window /history serves.  TID events are sparse, but the table
#: only grows; a ceiling keeps one request from reading all of it.
HISTORY_MAX_SPAN = timedelta(days=31)

def _service() -> TIDService:
    """Built per request, never at import.

    ``config`` is a proxy: the settings behind it change when the process is
    reconfigured and when a test swaps them.  A data root read at import time
    would freeze the first value the module ever saw.  Construction is cheap —
    the service holds paths, and opens its reader on demand.
    """
    return TIDService(config.data_root)


@router.get("/events")
def get_tid_events(
    hours: int = Query(24, description="Number of hours to look back")
):
    """
    Get recent TID events.

    Returns list of detected Traveling Ionospheric Disturbances.
    """
    try:
        events = _service().get_recent_events(hours)
    except Exception as e:
        raise internal_error(logger, "TID events read", e)
    return {
        "status": "ok",
        "n_events": len(events),
        "hours": hours,
        "events": events
    }


@router.get("/events/{event_id}")
def get_tid_event_details(event_id: str):
    """
    Get detailed information about a specific TID event.

    Returns full event data including correlation and residual time series.
    """
    try:
        event = _service().get_event_details(event_id)

        if event is None:
            raise HTTPException(
                status_code=404,
                detail=f"TID event not found: {event_id}"
            )

        return {
            "status": "ok",
            "event": event
        }

    except HTTPException:
        raise
    except Exception as e:
        raise internal_error(logger, "TID event read", e)


@router.get("/statistics")
def get_tid_statistics(
    days: int = Query(7, description="Number of days to analyze")
):
    """
    Get TID detection statistics.

    Returns summary statistics including event rate, velocity distribution,
    and propagation direction histogram.
    """
    try:
        stats = _service().get_statistics(days)
    except Exception as e:
        raise internal_error(logger, "TID statistics read", e)
    return {
        "status": "ok",
        **stats
    }


@router.get("/history")
def get_tid_history(
    start: str = Query("-7d", description="Start time (ISO8601 or relative like '-7d')"),
    end: str = Query("now", description="End time (ISO8601 or 'now')")
):
    """
    Get TID events within a time range.

    Returns all TID events detected between start and end times.
    """
    try:
        start_time, end_time = parse_window(start, end, max_span=HISTORY_MAX_SPAN)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        events = _service().get_events_in_range(start_time, end_time)
    except Exception as e:
        raise internal_error(logger, "TID history read", e)
    return {
        "status": "ok",
        "n_events": len(events),
        "time_range": {
            "start": iso_z(start_time),
            "end": iso_z(end_time)
        },
        "events": events
    }
