"""
GRAPE router - spectrograms, decimation status, pipeline status.
"""

import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pathlib import Path

from station_web.services.grape_service import GrapeService
from station_web.config import config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/grape", tags=["grape"])

def _service() -> GrapeService:
    """Built per request, never at import.

    ``config`` is a proxy: the settings behind it change when the process is
    reconfigured and when a test swaps them.  A data root read at import time
    would freeze the first value the module ever saw.  Construction is cheap —
    the service holds paths, and opens its reader on demand.
    """
    return GrapeService(data_root=str(config.data_root))


@router.get("/summary")
def grape_summary():
    """Overall GRAPE pipeline summary."""
    return _service().get_summary()


@router.get("/channels")
def grape_channels():
    """List channels with decimated data."""
    return {"channels": _service().get_channels()}


@router.get("/decimation")
def decimation_status():
    """Decimation status for all channels."""
    return _service().get_decimation_status()


@router.get("/spectrograms/{channel}")
def spectrogram_dates(channel: str):
    """List available spectrogram dates for a channel."""
    dates = _service().get_spectrogram_dates(channel)
    return {"channel": channel, "dates": dates}


@router.get("/spectrograms/{channel}/{date_str}")
def get_spectrogram(channel: str, date_str: str):
    """Serve a spectrogram PNG image."""
    path = _service().get_spectrogram_path(channel, date_str)
    if path is None:
        raise HTTPException(status_code=404, detail=f"No spectrogram for {channel}/{date_str}")
    return FileResponse(path, media_type="image/png")


@router.get("/uploads")
def upload_history():
    """Packaged dates and the latest daily pipeline run (grape_status.json)."""
    svc = _service()
    return {
        "packaged_dates": svc.get_upload_dates(),
        "pipeline": svc.get_pipeline_status(),
    }
