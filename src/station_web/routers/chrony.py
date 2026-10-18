"""
Chrony source comparison API endpoints.

Provides live and historical chrony source statistics for metrology validation.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional

from fastapi import APIRouter, HTTPException, Query

from station_web.errors import internal_error
from station_web.services.chrony_service import ChronyService, LOOKBACK_S
from station_web.config import config
from station_web.timeutil import parse_utc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chrony", tags=["chrony"])

#: the longest history window one request may ask for
MAX_WINDOW = timedelta(days=7)


def _parse_utc(value: str) -> datetime:
    """``now``, a relative time or ISO-8601 (station_web.timeutil); naive = UTC."""
    return parse_utc(value)


@router.get("/snapshot")
def get_live_snapshot() -> Dict[str, Any]:
    """Get a live chrony snapshot (sources + sourcestats + tracking)."""
    try:
        service = ChronyService(data_root=config.data_root, storage_config=config.storage)
        result = service.get_live_snapshot()
        if result is None:
            raise HTTPException(
                status_code=404,
                detail=f"no chrony rows in the last {int(LOOKBACK_S // 60)} minutes",
            )
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise internal_error(logger, "chrony snapshot", e)


@router.get("/comparison")
def get_source_comparison() -> Dict[str, Any]:
    """Get a formatted source comparison table.

    Returns all chrony sources with their offsets, errors, states,
    and the current system tracking info.
    """
    try:
        service = ChronyService(data_root=config.data_root, storage_config=config.storage)
        return service.get_source_comparison()
    except Exception as e:
        raise internal_error(logger, "chrony comparison", e)


@router.get("/history")
def get_source_history(
    hours: int = Query(default=24, ge=1, le=168, description="Hours ending now, when no start is given"),
    start: Optional[str] = Query(default=None, description="Window start, ISO-8601 UTC"),
    end: Optional[str] = Query(default=None, description="Window end, ISO-8601 UTC or 'now'"),
) -> Dict[str, Any]:
    """Historical chrony source statistics from ``DIAG_chrony_stats``.

    Returns per-source time series of offset and std_dev over
    ``[start, end]`` when given (max 7 days), else the last ``hours``.
    """
    try:
        t_end = _parse_utc(end) if end else None
        t_start = _parse_utc(start) if start else None
    except ValueError:
        # fixed text: the parser's message would echo the client's input
        raise HTTPException(status_code=400, detail="bad time: use ISO 8601 UTC or 'now'")
    if t_start is not None:
        t_end = t_end or datetime.now(timezone.utc)
        if t_end <= t_start or t_end - t_start > MAX_WINDOW:
            raise HTTPException(status_code=400,
                                detail="window must have end > start and span at most 7 days")
    try:
        service = ChronyService(data_root=config.data_root, storage_config=config.storage)
        return service.get_history(hours=hours, start=t_start, end=t_end)
    except Exception as e:
        raise internal_error(logger, "chrony history", e)
