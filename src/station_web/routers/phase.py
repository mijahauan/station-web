"""
Phase and Doppler analysis API endpoints.

Provides:
- Phase time series (unwrapped carrier phase)
- Doppler shift (from phase rate)
- Phase scintillation index (sigma_phi)
- Summary of current phase/Doppler state
"""

from fastapi import APIRouter, Query, HTTPException
from typing import Optional
import logging

from station_web.errors import internal_error
from station_web.services.phase_service import PhaseBusy, PhaseRequestError, PhaseService
from station_web.config import config
from station_web.timeutil import parse_window

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/phase", tags=["phase"])

# Replies never carry exception text, which can echo client input.
BAD_REQUEST = "invalid request: check start, end and the other parameters"
BUSY = "phase data busy; try again"
PHASE_TYPES = ('phase_rad', 'carrier_phase_rad', 'dc_carrier_phase_rad')

def _service() -> PhaseService:
    """Built per request, never at import.

    ``config`` is a proxy: the settings behind it change when the process is
    reconfigured and when a test swaps them.  A data root read at import time
    would freeze the first value the module ever saw.  Construction is cheap —
    the service holds paths, and opens its reader on demand.
    """
    return PhaseService(config.data_root)


def _parse_time_range(start: str, end: str):
    """Parse ``start``/``end`` with station_web.timeutil (zone-less = UTC).

    Only the parse may answer 400, and with fixed text: the parser's message
    would echo the client's input.
    """
    try:
        return parse_window(start, end)
    except ValueError:
        logger.warning("bad phase time range", exc_info=True)
        raise HTTPException(status_code=400, detail=BAD_REQUEST)


def _call(what: str, fn, *args, **kwargs):
    """Run a service view; map its refusals to 400/503 and its faults to 500."""
    try:
        return fn(*args, **kwargs)
    except PhaseRequestError as e:
        raise HTTPException(status_code=400, detail=str(e))     # fixed text only
    except PhaseBusy:
        raise HTTPException(status_code=503, detail=BUSY)
    except Exception as e:
        raise internal_error(logger, what, e)


@router.get("/timeseries")
def get_phase_timeseries(
    start: str = Query("-1h", description="Start time (ISO8601 or relative like '-1h', '-24h')"),
    end: str = Query("now", description="End time (ISO8601 or 'now')"),
    channel: Optional[str] = Query(None, description="Channel name (e.g., SHARED_10000, WWV_20000)"),
    station: Optional[str] = Query(None, description="Station name (WWV, WWVH, BPM)"),
    phase_type: str = Query("carrier_phase_rad", description="Phase field: phase_rad, carrier_phase_rad, dc_carrier_phase_rad"),
    unwrap: bool = Query(True, description="Unwrap phase for continuous tracking"),
):
    """
    Get phase time series from L2/tick_phase data.

    Returns per-channel, per-station phase traces suitable for plotting.
    Unwrapped carrier_phase_rad is the primary ionospheric observable.
    """
    start_time, end_time = _parse_time_range(start, end)
    if phase_type not in PHASE_TYPES:
        raise HTTPException(status_code=400, detail="invalid phase_type")
    return _call("phase timeseries", _service().get_phase_timeseries,
                 start_time, end_time, channel=channel, station=station,
                 phase_type=phase_type, unwrap=unwrap)



@router.get("/doppler")
def get_doppler(
    start: str = Query("-1h", description="Start time"),
    end: str = Query("now", description="End time"),
    channel: Optional[str] = Query(None, description="Channel name"),
    station: Optional[str] = Query(None, description="Station name"),
    smoothing: float = Query(30.0, description="Smoothing window in seconds"),
):
    """
    Get Doppler shift derived from carrier phase rate.

    f_Doppler = -(1/2pi) * dphi/dt

    For 10 MHz carrier: 1 Hz Doppler = ~30 m/s ionospheric motion.
    """
    start_time, end_time = _parse_time_range(start, end)
    return _call("phase Doppler", _service().get_doppler,
                 start_time, end_time, channel=channel, station=station,
                 smoothing_seconds=smoothing)



@router.get("/scintillation")
def get_scintillation(
    start: str = Query("-1h", description="Start time"),
    end: str = Query("now", description="End time"),
    channel: Optional[str] = Query(None, description="Channel name"),
    station: Optional[str] = Query(None, description="Station name"),
    window: float = Query(60.0, description="Scintillation window in seconds"),
):
    """
    Get phase scintillation index (sigma_phi) time series.

    sigma_phi = std(detrended phase) over sliding windows.
    High sigma_phi (> 0.3 rad) indicates ionospheric irregularities.
    """
    start_time, end_time = _parse_time_range(start, end)
    return _call("phase scintillation", _service().get_scintillation,
                 start_time, end_time, channel=channel, station=station,
                 window_seconds=window)



@router.get("/combined")
def get_phase_combined(
    start: str = Query("-1h", description="Start time"),
    end: str = Query("now", description="End time"),
    channel: Optional[str] = Query(None, description="Channel name"),
    station: Optional[str] = Query(None, description="Station name"),
    phase_type: str = Query("carrier_phase_rad", description="Phase field for the phase view"),
    smoothing: float = Query(30.0, description="Doppler smoothing window in seconds"),
    window: float = Query(60.0, description="Scintillation window in seconds"),
):
    """
    Phase, DC carrier phase, Doppler and sigma_phi in one reply.

    All four come from one read of L2_tick_phase, so the phase page needs one
    request where it used to fire four.
    """
    start_time, end_time = _parse_time_range(start, end)
    if phase_type not in PHASE_TYPES:
        raise HTTPException(status_code=400, detail="invalid phase_type")
    return _call("combined phase view", _service().get_combined,
                 start_time, end_time, channel=channel, station=station,
                 phase_type=phase_type, smoothing_seconds=smoothing, window_seconds=window)



@router.get("/channels")
def get_available_channels():
    """
    List all channels that have tick_phase data, and the stations heard lately.

    Used by the frontend to populate channel/station filter dropdowns.
    """
    try:
        return _service().get_available_channels()
    except Exception as e:
        raise internal_error(logger, "phase channel listing", e)


@router.get("/summary")
def get_phase_summary():
    """
    Get current phase/Doppler state across all channels.

    Returns latest Doppler, scintillation, and SNR for each active channel/station pair.
    """
    try:
        return _service().get_phase_summary()
    except Exception as e:
        raise internal_error(logger, "phase summary", e)
