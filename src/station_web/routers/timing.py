"""Timing authority: the registration in force, its history, the radiod alarms.

See ``services/timing_authority.py`` for the sources and their shapes.
"""
import logging

from fastapi import APIRouter, HTTPException, Query

from station_web.config import config
from station_web.errors import internal_error
from station_web.services import timing_authority as ta
from station_web.timeutil import parse_window

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/timing", tags=["timing"])


@router.get("/registration")
def get_registration():
    try:
        return ta.registration_snapshot(config)
    except Exception as e:
        raise internal_error(logger, "timing registration", e)


@router.get("/registration/history")
def get_registration_history(start: str = Query(default="-24h"),
                             end: str = Query(default="now")):
    try:
        t0, t1 = parse_window(start, end, max_span=ta.MAX_WINDOW)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))      # fixed text (timeutil)
    try:
        return ta.history(config, t0, t1)
    except HTTPException:
        raise
    except Exception as e:
        raise internal_error(logger, "timing registration history", e)


@router.get("/alarms")
def get_alarms(n: int = Query(default=20, ge=0, le=ta.MAX_INCIDENTS)):
    try:
        return ta.alarms(config, n=n)
    except Exception as e:
        raise internal_error(logger, "timing alarms", e)
