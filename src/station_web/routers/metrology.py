"""
Metrology API endpoints.
"""

import logging

from fastapi import APIRouter, Query, HTTPException
from datetime import datetime, timedelta
from typing import Optional

from station_web.models.timing import FusionResponse, FusionHistoryResponse
from station_web.services import fusion_service
from station_web.services.fusion_service import FusionService
from station_web.config import config
from station_web.errors import internal_error
from station_web.timeutil import parse_utc, parse_window

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/metrology", tags=["metrology"])

#: Longest window /fusion/history serves; the pages ask for a day at most.
FUSION_HISTORY_MAX_SPAN = timedelta(days=7)


def parse_time_param(time_str: str) -> datetime:
    """Parse a time parameter into an AWARE UTC datetime.

    ``now`` used to come back naive and ISO input aware, so a mixed query
    (``start=<ISO>&end=now``) raised TypeError comparing the two and
    answered 500.  See station_web.timeutil for the accepted forms.
    """
    return parse_utc(time_str)


@router.get("/fusion/latest", response_model=FusionResponse)
def get_latest_fusion():
    """
    Get latest fusion timing estimate.
    
    Returns the most recent multi-station fusion estimate including:
    - D_clock offset with uncertainty
    - Quality grade
    - Contributing stations
    - Number of broadcasts
    """
    try:
        service = FusionService(fusion_dir=config.fusion_dir)
        result = service.get_latest()
        
        if result is None:
            raise HTTPException(
                status_code=404,
                detail="No recent fusion data available"
            )
        
        return result
    
    except HTTPException:
        raise
    except Exception as e:
        raise internal_error(logger, "get latest fusion", e)


@router.get("/t3/history")
def get_t3_history(
    start: str = Query("-24h", description="Start time (ISO8601 or relative, e.g. '-6h')"),
    end: str = Query("now", description="End time (ISO8601 or 'now')"),
):
    """The T3 estimate (tick fusion) for the Timing page.

    At most ``MAX_POINTS`` bucket medians of the calibrated and raw values
    with their sigma, wrong-lock suspects apart, and the calibration offset
    between raw and calibrated.  Window at most 7 days.
    """
    try:
        start_dt, end_dt = parse_window(start, end, max_span=fusion_service.T3_MAX_WINDOW)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))      # fixed text (timeutil)
    try:
        return fusion_service.get_t3_history(start_dt, end_dt)
    except HTTPException:
        raise                      # 503 busy
    except Exception as e:
        raise internal_error(logger, "get T3 history", e)


@router.get("/fusion/history", response_model=FusionHistoryResponse)
def get_fusion_history(
    start: str = Query("-6h", description="Start time (ISO8601 or relative, e.g., '-6h')"),
    end: str = Query("now", description="End time (ISO8601 or 'now')"),
    min_quality: Optional[str] = Query(None, description="Minimum quality grade (A/B/C/D)")
):
    """
    Get fusion timing history.
    
    Returns time series of fusion estimates for the specified time range
    (at most 7 days).
    Supports quality filtering and relative time specifications.
    
    Examples:
    - Last 6 hours: ?start=-6h&end=now
    - Last day: ?start=-1d&end=now
    - Specific range: ?start=2026-01-03T00:00:00Z&end=2026-01-03T12:00:00Z
    - Quality filtered: ?start=-6h&end=now&min_quality=B
    """
    # Only the parse may answer 400: a ValueError from the service is a fault.
    try:
        start_dt, end_dt = parse_window(start, end, max_span=FUSION_HISTORY_MAX_SPAN)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        service = FusionService(fusion_dir=config.fusion_dir)
        return service.get_history(
            start=start_dt,
            end=end_dt,
            min_quality_grade=min_quality
        )
    except HTTPException:
        raise
    except Exception as e:
        raise internal_error(logger, "get fusion history", e)
