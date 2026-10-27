"""Station status: the Station page's data and the "is it working?" check,
read from the snapshots sigmond writes (see ``services/snapshot.py``).

The one write: an empty request file asking sigmond for a fresh check.
"""
import logging
import os

from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request

from station_web.config import config
from station_web.errors import internal_error
from station_web.services import station_summary as ss

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/status", tags=["status"])

CHECK_NOW_FORBIDDEN = "check requests must come from this page"
CHECK_NOW_UNAVAILABLE = "check requests are not available on this station"


@router.get("/station")
def get_station():
    try:
        return ss.station_summary(config)
    except Exception as e:
        raise internal_error(logger, "station summary", e)


@router.get("/check")
def get_check():
    try:
        return ss.check_view(config)
    except Exception as e:
        raise internal_error(logger, "station check", e)


def _same_origin(request: Request) -> bool:
    """Refuse a browser request another site started.  A client that sends
    neither header (curl) passes; a browser always sends one."""
    site = request.headers.get("sec-fetch-site")
    if site is not None and site != "same-origin":
        return False
    origin = request.headers.get("origin")
    if origin is not None:
        return urlsplit(origin).netloc == request.headers.get("host")
    return True


@router.post("/check-now", status_code=202)
def post_check_now(request: Request):
    if not _same_origin(request):
        raise HTTPException(status_code=403, detail=CHECK_NOW_FORBIDDEN)
    # One fixed name: repeated presses coalesce and cannot fill /run.
    path = config.snapshot_request_dir / "check-now"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o660)
        os.close(fd)
    except FileExistsError:
        return {"requested": True, "pending": True}
    except OSError:
        raise HTTPException(status_code=503, detail=CHECK_NOW_UNAVAILABLE)
    return {"requested": True}
