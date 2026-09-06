"""Health = product freshness.  No processes, no disk, no upload queue."""
from fastapi import APIRouter

from station_web import app as appmod
from station_web.config import config
from station_web.services import freshness
from station_web.services.channels import discover_channels

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/system")
async def get_system_health():
    t = freshness.table(config)
    appmod.STATUS["products"] = {k: v["verdict"] for k, v in t["products"].items()}
    appmod.STATUS["updated_at"] = t["timestamp"]
    return t


@router.get("/channels")
async def get_channel_status():
    return {"channels": discover_channels(config)}
