"""
Correlation Analysis API endpoints.

Provides correlation analysis between space weather and HF propagation metrics.
"""

from fastapi import APIRouter, Query, HTTPException
from datetime import datetime, timedelta
from typing import Optional
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from functools import partial

from station_web.services.correlation_service import CorrelationService
from station_web.services.space_weather_cache import SpaceWeatherCache
from station_web.broadcast import DEFAULT_STATIONS
from station_web.config import config
from station_web.timeutil import iso_z, utcnow
from station_web.errors import internal_error

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/correlations", tags=["correlations"])

def _service() -> CorrelationService:
    """Built per request, never at import.

    Reads ``config.data_root`` on every call so a reconfigured process (or a
    test that swaps settings) is honoured.
    """
    return CorrelationService(config.data_root, space_weather=SpaceWeatherCache(config.data_root))

# Thread pool for running sync correlation functions with timeout
_executor = ThreadPoolExecutor(max_workers=2)
CORRELATION_TIMEOUT_S = 15


def _run_with_timeout(func, *args, **kwargs):
    """Run a sync function in a thread pool with a timeout.

    These routes are sync (FastAPI runs them in its own threadpool), so the
    timeout comes from ``Future.result(timeout=)`` rather than
    ``asyncio.wait_for``.  Both abandon a slow call without cancelling it —
    a running thread is not interruptible either way — so the semantics are
    unchanged; only the caller's colour is.
    """
    try:
        return _executor.submit(partial(func, *args, **kwargs)).result(
            timeout=CORRELATION_TIMEOUT_S)
    except FutureTimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"Correlation analysis timed out after {CORRELATION_TIMEOUT_S}s — try a shorter time range"
        )

# Transmitter coordinates, from the broadcast catalogue (whose numbers come
# from hamsci_dsp.stations).  The hand-kept copy here was a second truth and
# still listed CHU after it left the fleet.
STATION_COORDS = {st.name: (st.latitude, st.longitude) for st in DEFAULT_STATIONS}


@router.get("/snr-solar")
def get_snr_solar_correlation(
    station: str = Query(..., description="Station ID (WWV, WWVH, BPM)"),
    frequency: float = Query(..., description="Frequency in MHz"),
    hours: int = Query(24, ge=1, le=168, description="Hours of history")
):
    """
    Analyze correlation between SNR and solar zenith angle.

    Expected: Strong positive correlation (r > 0.5) for F-layer propagation.
    SNR should increase when sun is higher (lower zenith angle).

    Returns:
        Correlation statistics, scatter plot data, and interpretation
    """
    try:
        station = station.upper()
        if station not in STATION_COORDS:
            raise HTTPException(status_code=400, detail=f"Invalid station: {station}")

        end = utcnow()
        start = end - timedelta(hours=hours)

        # Get receiver coordinates from config
        rx_coords = (config.station_metadata['latitude'], config.station_metadata['longitude'])
        tx_coords = STATION_COORDS[station]

        result = _run_with_timeout(
            _service().analyze_snr_solar_zenith,
            station=station,
            frequency=frequency,
            start=start,
            end=end,
            station_coords=tx_coords,
            rx_coords=rx_coords
        )

        if 'error' in result:
            raise HTTPException(status_code=404, detail=result['error'])

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise internal_error(logger, "get snr solar correlation", e)


@router.get("/sid-detection")
def get_sid_correlation(
    hours: int = Query(24, ge=1, le=168, description="Hours of history")
):
    """
    Detect Sudden Ionospheric Disturbance (SID) events.

    Correlates X-ray flares with SNR drops across all frequencies.
    SID events cause increased D-layer absorption, particularly on lower frequencies.

    Returns:
        List of SID events with correlated SNR drops on affected channels
    """
    try:
        end = utcnow()
        start = end - timedelta(hours=hours)

        result = _run_with_timeout(
            _service().detect_sid_correlation, start, end
        )

        if 'error' in result:
            raise HTTPException(status_code=404, detail=result['error'])

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise internal_error(logger, "get sid correlation", e)


@router.get("/tec-f107")
def get_tec_f107_correlation(
    station: str = Query(..., description="Station ID (WWV, WWVH, BPM)"),
    days: int = Query(30, ge=7, le=90, description="Days of history")
):
    """
    Analyze correlation between TEC and F10.7 solar flux.

    Expected: Positive correlation (r > 0.6) between daily F10.7 and daytime TEC.
    Higher solar flux → more EUV radiation → higher F-layer ionization → higher TEC.

    Returns:
        TEC-F10.7 correlation statistics and trend analysis
    """
    try:
        station = station.upper()
        if station not in STATION_COORDS:
            raise HTTPException(status_code=400, detail=f"Invalid station: {station}")

        result = _run_with_timeout(
            _service().analyze_tec_f107_correlation,
            station=station,
            days=days
        )

        if 'error' in result:
            raise HTTPException(status_code=404, detail=result['error'])

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise internal_error(logger, "get tec f107 correlation", e)


@router.get("/propagation-kp")
def get_propagation_kp_correlation(
    hours: int = Query(72, ge=24, le=168, description="Hours of history")
):
    """
    Analyze relationship between propagation modes and Kp index.

    Expected effects:
    - High Kp (>5): Auroral absorption on high-latitude paths
    - High Kp: Enhanced E-layer, F-layer irregularities
    - High Kp: Increased scintillation and fading

    Returns:
        Propagation statistics binned by Kp level (quiet/unsettled/storm)
    """
    try:
        end = utcnow()
        start = end - timedelta(hours=hours)

        result = _run_with_timeout(
            _service().analyze_propagation_mode_kp, start, end
        )

        if 'error' in result:
            raise HTTPException(status_code=404, detail=result['error'])

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise internal_error(logger, "get propagation kp correlation", e)


@router.get("/summary")
def get_correlation_summary(
    station: Optional[str] = Query(None, description="Station ID (optional)"),
    hours: int = Query(24, ge=6, le=168, description="Hours of history")
):
    """
    Get comprehensive correlation summary.

    Combines multiple correlation analyses into a single dashboard view:
    - SID event detection
    - Propagation-Kp relationship
    - Current space weather impact assessment

    Returns:
        Multi-faceted correlation summary for dashboard display
    """
    try:
        end = utcnow()
        start = end - timedelta(hours=hours)

        # Get SID correlation
        sid_result = _run_with_timeout(
            _service().detect_sid_correlation, start, end
        )

        # Get propagation-Kp correlation
        kp_result = _run_with_timeout(
            _service().analyze_propagation_mode_kp, start, end
        )

        # Both series-dependent analyses currently always come back as the
        # {"available": False, ...} marker — station-web has no X-ray or Kp
        # time series product yet.  Surface that at the top level so a
        # consumer can't mistake it for quiet conditions (detected: 0,
        # kp_bins: {}); the product-only parts of the summary stay as-is.
        both_series_unavailable = (
            sid_result.get('available') is False
            and kp_result.get('available') is False
        )

        summary = {
            'timestamp': iso_z(utcnow()),
            'period': {
                'start': iso_z(start),
                'end': iso_z(end),
                'hours': hours
            },
            'sid_events': {
                'detected': sid_result.get('sid_events_detected', 0),
                'correlated': sid_result.get('correlated_events', 0),
                'recent_events': sid_result.get('events', [])[:5]  # Last 5
            },
            'geomagnetic_effects': {
                'kp_bins': kp_result.get('kp_bins', {}),
                'interpretation': kp_result.get('interpretation', '')
            },
            'recommendations': []
        }

        if both_series_unavailable:
            summary['available'] = False
            summary['reason'] = 'space weather time series not available from products'

        # Add recommendations based on findings
        if sid_result.get('correlated_events', 0) > 0:
            summary['recommendations'].append({
                'type': 'SID_DETECTED',
                'message': 'Recent solar flares detected with propagation impacts',
                'action': 'Monitor lower frequencies for absorption'
            })

        # Inert until a Kp product exists: kp_result never carries 'kp_bins' today.
        if kp_result.get('kp_bins', {}).get('storm', {}).get('channels'):
            summary['recommendations'].append({
                'type': 'GEOMAGNETIC_STORM',
                'message': 'Geomagnetic storm conditions affecting propagation',
                'action': 'Expect degraded high-latitude paths'
            })

        return summary

    except HTTPException:
        raise                      # a 504 timeout is its own answer
    except Exception as e:
        raise internal_error(logger, "get correlation summary", e)
