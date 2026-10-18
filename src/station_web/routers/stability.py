"""
Stability analysis API endpoints.
"""

from datetime import timedelta

from fastapi import APIRouter, Query, HTTPException
import logging

from station_web import heavy
from station_web.errors import internal_error
from station_web.services.stability_service import StabilityService
from station_web.config import config
from station_web.timeutil import parse_window

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stability", tags=["stability"])

#: Longest window /adev serves: metrology.html's "All Data" asks for -365d.
#: The read streams two columns into flat arrays, so a year stays small.
ADEV_MAX_SPAN = timedelta(days=366)

def _service() -> StabilityService:
    """Built per request, never at import.

    ``config`` is a proxy: the settings behind it change when the process is
    reconfigured and when a test swaps them.  A data root read at import time
    would freeze the first value the module ever saw.  Construction is cheap —
    the service holds paths, and opens its reader on demand.
    """
    return StabilityService(config.data_root)


@router.get("/adev")
def get_allan_deviation(
    start: str = Query("-24h", description="Start time (ISO8601 or relative like '-24h')"),
    end: str = Query("now", description="End time (ISO8601 or 'now')"),
):
    """
    Get Allan deviation analysis.
    
    Returns overlapping Allan deviation computed from fusion timing data.
    Includes tau values, ADEV values, and noise type identification.
    """
    # Only the parse may answer 400: a ValueError from the service (numpy's
    # LinAlgError is one) is a fault.
    try:
        start_time, end_time = parse_window(start, end, max_span=ADEV_MAX_SPAN)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        with heavy.guard("stability"):
            metrics = _service().compute_stability_metrics(start_time, end_time)
    except HTTPException:
        raise                      # 503 busy
    except Exception as e:
        raise internal_error(logger, "Allan deviation", e)
    if metrics is None:
        raise HTTPException(
            status_code=404,
            detail="Insufficient data for stability analysis"
        )
    return metrics
