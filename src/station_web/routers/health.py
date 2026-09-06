"""
Health monitoring API endpoints.
"""

import sys
from pathlib import Path
from fastapi import APIRouter, HTTPException
from typing import Dict, Any

from station_web.models.health import SystemHealth
from station_web.services.health_service import HealthService
from station_web.services.channels import discover_channel_infos
from station_web.config import config

# The /storage inventory this backed read host quota state and has already
# gone; the import stays as the marker Task 11 clears, guarded because
# hf_timestd is not installed alongside station-web.
try:
    from hf_timestd.quota_manager import QuotaManager  # noqa: F401
except ImportError:
    QuotaManager = None

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/system", response_model=SystemHealth)
async def get_system_health():
    """
    Get overall system health status.
    
    Returns system health including:
    - Overall status (healthy/degraded/error)
    - Channel statuses
    - Process statuses
    - Disk usage
    - Data completeness
    """
    try:
        service = HealthService(
            data_root=config.data_root,
            channels=discover_channel_infos(config)
        )
        return service.get_system_health()
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/grape")
async def get_grape_status():
    """
    GRAPE daily pipeline status: last run, upload health, pending retries.

    Reads the grape_status.json written by ``grape daily`` and the upload
    queue to give a single view of pipeline + upload health.
    """
    import json
    result: Dict[str, Any] = {
        'pipeline': None,
        'upload_queue': None,
    }

    # Pipeline status file
    status_file = config.data_root / 'upload' / 'grape_status.json'
    if status_file.exists():
        try:
            with open(status_file, 'r') as f:
                result['pipeline'] = json.load(f)
        except Exception:
            pass

    # Upload queue summary
    queue_file = config.data_root / 'upload' / 'queue.json'
    if queue_file.exists():
        try:
            with open(queue_file, 'r') as f:
                tasks = json.load(f)
            counts = {'completed': 0, 'pending': 0, 'failed': 0, 'uploading': 0}
            for t in tasks:
                s = t.get('status', 'pending')
                counts[s] = counts.get(s, 0) + 1
            result['upload_queue'] = {
                'total': len(tasks),
                **counts,
            }
        except Exception:
            pass

    return result


@router.get("/channels")
async def get_channel_status():
    """
    Get status for all channels.
    
    Returns detailed status for each configured channel including:
    - Channel name and frequency
    - Active/inactive status
    - Last update time
    - Signal quality metrics
    """
    try:
        service = HealthService(
            data_root=config.data_root,
            channels=discover_channel_infos(config)
        )
        health = service.get_system_health()
        return {"channels": health['channels']}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
