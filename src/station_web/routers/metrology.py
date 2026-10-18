"""
Metrology API endpoints.
"""

from fastapi import APIRouter, Query, HTTPException
from datetime import datetime
from typing import Optional

from station_web.models.timing import FusionResponse, FusionHistoryResponse
from station_web.services.fusion_service import FusionService
from station_web.config import config
from station_web.timeutil import parse_utc, parse_window

router = APIRouter(prefix="/metrology", tags=["metrology"])


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
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/fusion/history", response_model=FusionHistoryResponse)
def get_fusion_history(
    start: str = Query("-6h", description="Start time (ISO8601 or relative, e.g., '-6h')"),
    end: str = Query("now", description="End time (ISO8601 or 'now')"),
    min_quality: Optional[str] = Query(None, description="Minimum quality grade (A/B/C/D)")
):
    """
    Get fusion timing history.
    
    Returns time series of fusion estimates for the specified time range.
    Supports quality filtering and relative time specifications.
    
    Examples:
    - Last 6 hours: ?start=-6h&end=now
    - Last day: ?start=-1d&end=now
    - Specific range: ?start=2026-01-03T00:00:00Z&end=2026-01-03T12:00:00Z
    - Quality filtered: ?start=-6h&end=now&min_quality=B
    """
    try:
        start_dt, end_dt = parse_window(start, end)

        service = FusionService(fusion_dir=config.fusion_dir)
        return service.get_history(
            start=start_dt,
            end=end_dt,
            min_quality_grade=min_quality
        )
    
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
