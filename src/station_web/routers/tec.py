"""
TEC (Total Electron Content) API endpoints for v6.5.0.
"""

from fastapi import APIRouter, Query, HTTPException
from typing import Optional, List
import logging
from pathlib import Path

from datetime import timedelta

from station_web import heavy, projected
from station_web.broadcast import DEFAULT_STATIONS
from station_web.errors import internal_error
from station_web.services.tec_service import TECService
from station_web.config import config
from station_web.timeutil import iso_z, parse_window, utcnow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tec", tags=["ionosphere"])

def _service() -> TECService:
    """Built per request, never at import.

    ``config`` is a proxy: the settings behind it change when the process is
    reconfigured and when a test swaps them.  A data root read at import time
    would freeze the first value the module ever saw.  Construction is cheap —
    the service holds paths, and opens its reader on demand.
    """
    return TECService(config.data_root)


#: Longest windows the routes serve.  dTEC runs one row per station per
#: frequency per minute (~26k a day); dtec.html asks for a day at most.
#: L3_tec runs one row per station per 5 minutes.
DTEC_MAX_SPAN = timedelta(days=2)
TEC_HISTORY_MAX_SPAN = timedelta(days=31)

#: The L3_dtec columns /dtec uses.
_DTEC_COLUMNS = ('minute_boundary', 'mean_snr_db', 'station', 'frequency_mhz',
                 'dtec_rate_tecu_per_s', 'dtec_mean_tecu', 'is_anchored')

#: Stations a /tec/station/{station} request may name: the broadcast
#: catalogue's, not a hand-kept list (which still carried CHU).
VALID_STATIONS = tuple(st.name for st in DEFAULT_STATIONS)


@router.get("/dtec")
def get_dtec_timeseries(
    start: str = Query("-6h", description="Start time (ISO8601 or relative like '-6h', '-1d')"),
    end: str = Query("now", description="End time (ISO8601 or 'now')"),
    station: Optional[str] = Query(None, description="Filter by station (WWV, WWVH, BPM)"),
    freq_mhz: Optional[float] = Query(None, description="Filter by frequency in MHz"),
    min_snr_db: float = Query(6.0, description="Minimum mean_snr_db to include"),
    downsample: int = Query(1, description="Keep every Nth point (1=all, 5=every 5th)"),
):
    """
    Multi-station carrier-phase dTEC time series.

    Returns per-station, per-frequency dtec_rate_tecu_per_s and integrated
    dtec_mean_tecu for the requested window.  Suitable for a multi-path
    overlay chart showing correlated TID signatures across all paths.

    Each series is keyed as '<STATION>_<freq>MHz', e.g. 'WWV_10.0MHz'.
    """
    try:
        t0, t1 = parse_window(start, end, max_span=DTEC_MAX_SPAN)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        ts0 = int(t0.timestamp())
        ts1 = int(t1.timestamp())

        # The (channel, timestamp_utc) index narrows the read, which carries
        # only the columns used; minute_boundary is checked in Python because
        # timestamp_utc carries sub-second slop.
        with heavy.guard("dtec"):
            rows = projected.read_rows('L3_dtec', 'AGGREGATED', t0, t1, _DTEC_COLUMNS)

        if not rows:
            return {
                "status": "no_data",
                "message": "No dTEC rows in window",
                "series": {},
            }

        series: dict = {}
        n_total = 0
        station_filter = station.upper() if station else None
        for row in rows:
            mb = row.get('minute_boundary')
            if mb is None or mb < ts0 or mb > ts1:
                continue
            snr = row.get('mean_snr_db')
            if snr is None or snr < min_snr_db:
                continue
            s = row.get('station')
            f = row.get('frequency_mhz')
            if s is None or f is None:
                continue
            if station_filter and s != station_filter:
                continue
            if freq_mhz is not None and abs(f - freq_mhz) > 0.01:
                continue
            key = f"{s}_{f:.1f}MHz"
            entry = series.setdefault(key, {
                "station": s,
                "frequency_mhz": float(f),
                "timestamps": [],
                "dtec_rate_tecu_per_s": [],
                "dtec_mean_tecu": [],
                "mean_snr_db": [],
                "is_anchored": [],
            })
            entry["timestamps"].append(mb)
            entry["dtec_rate_tecu_per_s"].append(row.get('dtec_rate_tecu_per_s'))
            entry["dtec_mean_tecu"].append(row.get('dtec_mean_tecu'))
            entry["mean_snr_db"].append(snr)
            entry["is_anchored"].append(bool(row.get('is_anchored')))
            n_total += 1

        # Sort each series by timestamp; apply downsample on the final list
        for key, entry in series.items():
            order = sorted(range(len(entry["timestamps"])),
                           key=lambda i: entry["timestamps"][i])
            if downsample > 1:
                order = order[::downsample]
            for field in ("timestamps", "dtec_rate_tecu_per_s", "dtec_mean_tecu",
                          "mean_snr_db", "is_anchored"):
                entry[field] = [entry[field][i] for i in order]

        return {
            "status": "ok",
            "time_range": {"start": iso_z(t0), "end": iso_z(t1)},
            "n_series": len(series),
            "n_points_total": n_total,
            "series": series,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise internal_error(logger, "dTEC read", e)


@router.get("/current")
def get_current_tec():
    """
    Get the most recent TEC estimates.

    Returns the newest TEC value per station within the last 2 h, and the
    ``source`` channel (REANALYZED or AGGREGATED) it came from.
    """
    try:
        data = _service().get_current_tec()

        if data is None:
            return {
                "status": "no_data",
                "message": "No TEC row within the last 2 h",
                "source": None,
                "timestamp": iso_z(utcnow())
            }

        # TECService.get_current_tec() keys its payload by STATION, under
        # 'stations'.  This route used to read 'paths', which the service has
        # never produced, so /current answered 200 with an empty body on a
        # station that had TEC.  No page reads this endpoint (only
        # /api/propagation/tec, a different router, uses 'paths'), so the
        # response key follows the product instead of the other way round.
        stations = data.get('stations', {})
        return {
            "status": "ok",
            "timestamp": data.get('timestamp', ''),
            "source": data.get('source'),
            "stations": stations,
            "n_stations": len(stations)
        }

    except Exception as e:
        raise internal_error(logger, "Current TEC read", e)


@router.get("/history")
def get_tec_history(
    start: str = Query("-24h", description="Start time (ISO8601 or relative like '-24h')"),
    end: str = Query("now", description="End time (ISO8601 or 'now')"),
    station: Optional[str] = Query(None, description="Filter by station (WWV, WWVH, BPM)")
):
    """
    Get TEC history for a time range.

    Returns TEC values over time for all paths or filtered by station.
    """
    try:
        start_time, end_time = parse_window(start, end, max_span=TEC_HISTORY_MAX_SPAN)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        data = _service().get_tec_history(start_time, end_time, station=station)
    except Exception as e:
        raise internal_error(logger, "TEC history read", e)
    return {
        "status": "ok",
        **data
    }


@router.get("/station/{station}")
def get_tec_by_station(
    station: str,
    hours: int = Query(24, description="Number of hours of history")
):
    """
    Get TEC data for a specific station.

    Returns TEC values for all frequencies from the specified station.
    """
    try:
        station = station.upper()
        if station not in VALID_STATIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid station: {station}. Must be one of {', '.join(VALID_STATIONS)}"
            )

        data = _service().get_tec_by_station(station, hours)

        return {
            "status": "ok",
            "station": station,
            **data
        }

    except HTTPException:
        raise
    except Exception as e:
        raise internal_error(logger, f"TEC read for {station}", e)
