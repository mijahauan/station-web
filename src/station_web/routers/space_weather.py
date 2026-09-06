"""
Space Weather API endpoints.

Reads hamsci-physics's own space-weather snapshot cache
(``<data_root>/iono_cache/space_weather.json``) — station-web never fetches
from NOAA/GFZ itself and never runs a poller.  The X-ray, Kp, proton and SID
time series NOAA used to supply have no equivalent product yet, so those
routes say so honestly rather than serving a value nobody produced.
"""

from fastapi import APIRouter, Query, HTTPException
from datetime import datetime, timedelta
import logging

from station_web.services.space_weather_cache import SpaceWeatherCache
from station_web.config import config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/space-weather", tags=["space-weather"])


@router.get("/current")
async def get_current_conditions():
    """Current space weather as hamsci-physics last cached it."""
    return SpaceWeatherCache(config.data_root).current()


@router.get("/xray")
async def get_xray_flux(
    hours: int = Query(24, ge=1, le=168, description="Hours of history (max 168)")
):
    """X-ray flux time series — no such product exists yet."""
    return SpaceWeatherCache.series_unavailable("xray")


@router.get("/kp")
async def get_kp_index(
    hours: int = Query(24, ge=1, le=168, description="Hours of history")
):
    """Kp index time series — no such product exists yet."""
    return SpaceWeatherCache.series_unavailable("kp")


@router.get("/protons")
async def get_proton_flux(
    hours: int = Query(24, ge=1, le=168, description="Hours of history")
):
    """Proton flux time series — no such product exists yet."""
    return SpaceWeatherCache.series_unavailable("protons")


@router.get("/events/sid")
async def get_sid_events(
    hours: int = Query(24, ge=1, le=168, description="Hours of history")
):
    """Sudden Ionospheric Disturbance events — depend on an X-ray time series that doesn't exist yet."""
    return SpaceWeatherCache.series_unavailable("sid_events")


@router.get("/summary")
async def get_summary(
    hours: int = Query(24, ge=1, le=168, description="Hours of history")
):
    """
    Space weather summary.

    ``current`` comes from hamsci-physics's cache; the X-ray/Kp/proton/SID
    statistics NOAA used to supply have no product-backed equivalent, so
    they say so rather than reporting zeros as if they were measured.
    """
    current = SpaceWeatherCache(config.data_root).current()
    return {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "period_hours": hours,
        "current": current,
        "xray": SpaceWeatherCache.series_unavailable("xray"),
        "geomagnetic": SpaceWeatherCache.series_unavailable("kp"),
        "protons": SpaceWeatherCache.series_unavailable("protons"),
        "events": SpaceWeatherCache.series_unavailable("sid_events"),
    }


@router.get("/solar/elevation")
async def get_solar_elevation(
    start: str = Query("-6h", description="Start time (relative like '-6h' or ISO8601)"),
    end: str = Query("now", description="End time"),
    interval_minutes: int = Query(5, ge=1, le=60, description="Sample interval in minutes"),
):
    """
    Solar elevation angle at the path midpoint for each station (WWV, WWVH, CHU, BPM).

    Uses the receiver grid square from station config and the existing
    hamsci_physics.solar_zenith to compute elevation at the geographic midpoint
    of each propagation path. Elevation correlates with D-layer absorption.
    """
    try:
        import math

        # Resolve receiver grid from station config
        meta = config.station_metadata
        grid = meta.get("grid_square", "")
        lat, lon = meta.get("latitude", 0.0), meta.get("longitude", 0.0)
        if not grid or (lat == 0.0 and lon == 0.0):
            raise HTTPException(
                status_code=422,
                detail="station has no grid square / coordinates configured; "
                       "solar elevation needs a receiver location",
            )

        # Parse time window
        now = datetime.utcnow()
        def _parse(s):
            s = s.strip()
            if s == "now":
                return now
            if s.startswith("-"):
                val = float(s[1:-1])
                u = s[-1]
                if u == "h": return now - timedelta(hours=val)
                if u == "d": return now - timedelta(days=val)
                if u == "m": return now - timedelta(minutes=val)
            return datetime.fromisoformat(s.replace("Z", ""))

        t0 = _parse(start)
        t1 = _parse(end)

        # Solar geometry moved to hamsci-physics in the 2026-08-24 split
        from hamsci_physics.solar_zenith import (
            grid_to_latlon, calculate_midpoint, solar_position,
            WWV_LOCATION, WWVH_LOCATION, CHU_LOCATION, BPM_LOCATION,
        )

        import math as _math

        rx_lat, rx_lon = grid_to_latlon(grid)

        def _gc_point(lat1, lon1, lat2, lon2, f=0.5):
            """Point at fraction f along the great-circle from (lat1,lon1) to (lat2,lon2)."""
            lat1r, lon1r = _math.radians(lat1), _math.radians(lon1)
            lat2r, lon2r = _math.radians(lat2), _math.radians(lon2)
            d = _math.acos(max(-1.0, min(1.0,
                _math.sin(lat1r)*_math.sin(lat2r) +
                _math.cos(lat1r)*_math.cos(lat2r)*_math.cos(lon2r - lon1r)
            )))
            if d < 1e-10:
                return lat1, lon1
            A = _math.sin((1 - f) * d) / _math.sin(d)
            B = _math.sin(f * d) / _math.sin(d)
            x = A*_math.cos(lat1r)*_math.cos(lon1r) + B*_math.cos(lat2r)*_math.cos(lon2r)
            y = A*_math.cos(lat1r)*_math.sin(lon1r) + B*_math.cos(lat2r)*_math.sin(lon2r)
            z = A*_math.sin(lat1r) + B*_math.sin(lat2r)
            lat = _math.degrees(_math.atan2(z, _math.sqrt(x*x + y*y)))
            lon = _math.degrees(_math.atan2(y, x))
            return lat, lon

        def _path_midpoint(rx_lat, rx_lon, tx_lat, tx_lon):
            """
            Return the ionospheric path midpoint.
            For paths > 60 deg GC distance (e.g. BPM at ~103 deg), the true
            geographic midpoint passes over the Arctic and is not representative
            of the D-layer above the propagation path near the receiver.
            Use the 1/4-path point instead so the solar elevation reflects the
            ionosphere in the receiver's hemisphere.
            """
            lat1r, lon1r = _math.radians(rx_lat), _math.radians(rx_lon)
            lat2r, lon2r = _math.radians(tx_lat), _math.radians(tx_lon)
            d_deg = _math.degrees(_math.acos(max(-1.0, min(1.0,
                _math.sin(lat1r)*_math.sin(lat2r) +
                _math.cos(lat1r)*_math.cos(lat2r)*_math.cos(lon2r - lon1r)
            ))))
            f = 0.25 if d_deg > 60 else 0.5
            return _gc_point(rx_lat, rx_lon, tx_lat, tx_lon, f)

        midpoints = {
            "WWV":  _path_midpoint(rx_lat, rx_lon, *WWV_LOCATION),
            "WWVH": _path_midpoint(rx_lat, rx_lon, *WWVH_LOCATION),
            "CHU":  _path_midpoint(rx_lat, rx_lon, *CHU_LOCATION),
            "BPM":  _path_midpoint(rx_lat, rx_lon, *BPM_LOCATION),
        }

        timestamps = []
        elevations = {s: [] for s in midpoints}

        current = t0
        dt = timedelta(minutes=interval_minutes)
        while current <= t1:
            timestamps.append(int(current.timestamp()))
            for station, (mlat, mlon) in midpoints.items():
                _, el = solar_position(current, mlat, mlon)
                elevations[station].append(round(el, 2))
            current += dt

        return {
            "status": "ok",
            "receiver_grid": grid,
            "receiver_lat": round(rx_lat, 4),
            "receiver_lon": round(rx_lon, 4),
            "interval_minutes": interval_minutes,
            "timestamps": timestamps,
            "elevations": elevations,
            "midpoints": {s: {"lat": round(v[0], 4), "lon": round(v[1], 4)}
                          for s, v in midpoints.items()},
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error computing solar elevation: {e}")
        raise HTTPException(status_code=500, detail=str(e))
